"""Live: a 3.x `rdf_edges` rescue resumes after a failure, and duplicates collapse.

Runs against `ivg-iris-enterprise` in a scratch namespace, `IVGRESCUE`, backed by its
own database (`/usr/irissys/mgr/ivgrescue/`) — not USER, and not a namespace mapped
onto USER's globals, because these tests drop and re-create `Graph_KG.rdf_edges`. The
namespace is created on first use and left in place for the next run.

The rescue table built here has the shape `stage_class_owned_rdf_edges` writes, with the
rows a 3.x install actually carries: full duplicates (v3.2.0's bulk loader inserted with
`%NOINDEX %NOCHECK`, skipping the class's `uspo` unique index) under NULL, CHAR(0) and
'' spellings of the default graph, a same-key row whose qualifiers differ only by case,
and an edge whose endpoint is not in the graph it claims.

    export IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972
    .venv/bin/pytest tests/integration/test_ivg400_rescue_resume_live.py -p no:cacheprovider
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from iris_vector_graph.schema import (
    RDF_EDGES_COMPAT_VIEW_DDL,
    RDF_EDGES_DDL,
    RESCUE_STAGING_TABLE,
    RESCUE_UNPLACED_TABLE,
    GraphSchema,
    RdfEdgesRescueError,
)
from tests.conftest import (
    container_hostname_matches,
    docker_container_hostname,
    docker_container_id,
    probe_instance_hostname,
)

CONTAINER = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")
PORT = int(os.environ.get("IVG_PORT", "31972"))
NAMESPACE = "IVGRESCUE"
STAGING = f"Graph_KG.{RESCUE_STAGING_TABLE}"
UNPLACED = f"Graph_KG.{RESCUE_UNPLACED_TABLE}"

pytestmark = pytest.mark.integration


def _running(name: str) -> bool:
    out = subprocess.run(
        ["docker", "ps", "--filter", f"name=^{name}$", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
    ).stdout.split()
    return name in out


def _ensure_namespace() -> None:
    """Create the IVGRESCUE database and namespace, idempotently."""
    script = (
        'Set dir="/usr/irissys/mgr/ivgrescue/"\n'
        'If \'##class(Config.Namespaces).Exists("IVGRESCUE") { '
        "Do ##class(%File).CreateDirectoryChain(dir)  "
        "Set db=##class(SYS.Database).%New()  Set db.Directory=dir  Set sc=db.%Save()  "
        'Kill p  Set p("Directory")=dir  Set sc=##class(Config.Databases).Create("IVGRESCUE",.p)  '
        'Kill p  Set p("Globals")="IVGRESCUE",p("Routines")="IVGRESCUE"  '
        'Set sc=##class(Config.Namespaces).Create("IVGRESCUE",.p) }\n'
        'Write "IVGRESCUE_EXISTS=",##class(Config.Namespaces).Exists("IVGRESCUE"),!\n'
        "Halt\n"
    )
    out = subprocess.run(
        ["docker", "exec", "-i", CONTAINER, "iris", "session", "IRIS", "-U", "%SYS"],
        input=script,
        capture_output=True,
        text=True,
        timeout=120,
    ).stdout
    if "IVGRESCUE_EXISTS=1" not in out:
        pytest.skip(f"could not create namespace {NAMESPACE} in {CONTAINER}: {out[-400:]}")


def _connect():
    import iris.dbapi as dbapi

    return dbapi.connect(
        hostname="localhost", port=PORT, namespace=NAMESPACE, username="_SYSTEM", password="SYS"
    )


def _native():
    import iris

    conn = iris.connect(
        hostname="localhost", port=PORT, namespace=NAMESPACE, username="_SYSTEM", password="SYS"
    )
    return conn, iris.createIRIS(conn)


@pytest.fixture(scope="module")
def rescue_namespace():
    if not CONTAINER.startswith("ivg-iris"):
        pytest.skip(f"{CONTAINER} is not one of this project's containers")
    if not _running(CONTAINER):
        pytest.skip(f"{CONTAINER} not running")
    _ensure_namespace()
    conn = _connect()
    try:
        reported = probe_instance_hostname(conn)
        if not container_hostname_matches(
            reported, docker_container_hostname(CONTAINER), docker_container_id(CONTAINER)
        ):
            pytest.fail(f"localhost:{PORT} answered as {reported!r}, not {CONTAINER}")
    finally:
        conn.close()
    yield NAMESPACE


def _try(cursor, sql: str) -> None:
    try:
        cursor.execute(sql)
    except Exception:
        pass


def _scalar(sql: str):
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(sql)
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _rows(sql: str) -> list[tuple]:
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(sql)
        return [tuple(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _table_exists(name: str) -> bool:
    return bool(
        _scalar(
            "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'Graph_KG' "
            f"AND TABLE_NAME = '{name}' AND TABLE_TYPE = 'BASE TABLE'"
        )
    )


def _initialize_schema(auto_deploy: bool = False) -> None:
    """A fresh connection per call: the engine may call createIRIS on it."""
    from iris_vector_graph.engine import IRISGraphEngine

    conn = _connect()
    try:
        IRISGraphEngine(conn, embedding_dimension=4).initialize_schema(
            auto_deploy_objectscript=auto_deploy
        )
    finally:
        conn.close()


def _delete_edge_class() -> None:
    nconn, irs = _native()
    try:
        for cls in ("Graph.KG.Edge", "Graph.KG.TestEdge"):
            # "e": the extent goes too, or a later compile inherits its index entries.
            irs.classMethodValue("%SYSTEM.OBJ", "Delete", cls, "e-d")
    finally:
        nconn.close()


#: (s, p, o_id, qualifiers, graph_id-as-SQL). Staged order is the %ID order.
RESCUED = [
    ("rsq:a", "r", "rsq:b", "'{\"w\":1}'", "NULL"),  # placed
    ("rsq:a", "r", "rsq:b", "'{\"w\":1}'", "CHAR(0)"),  # collapsed
    ("rsq:a", "r", "rsq:b", "'{\"w\":1}'", "''"),  # collapsed
    ("rsq:a", "r", "rsq:c", "NULL", "''"),  # placed
    ("rsq:a", "r", "rsq:c", "NULL", "NULL"),  # collapsed (NULL qualifiers match)
    ("rsq:b", "r", "rsq:c", "'{\"A\":1}'", "''"),  # placed
    ("rsq:b", "r", "rsq:c", "'{\"a\":1}'", "''"),  # same key, different qualifiers
    ("rsq:c", "r", "rsq:a", "'{}'", "'g1'"),  # rsq:c has no node in g1
    ("rsq:b", "knows", "rsq:a", "'plain text'", "NULL"),  # placed
]
EXPECTED_PLACED = sorted(
    [
        ("rsq:a", "r", "rsq:b", "", '{"w":1}'),
        ("rsq:a", "r", "rsq:c", "", None),
        ("rsq:b", "r", "rsq:c", "", '{"A":1}'),
        ("rsq:b", "knows", "rsq:a", "", "plain text"),
    ],
    key=repr,
)
EXPECTED_QUARANTINED = sorted(
    [("rsq:b", "r", "rsq:c", "", '{"a":1}'), ("rsq:c", "r", "rsq:a", "g1", "{}")], key=repr
)
EXPECTED_COUNTS = {"staged": 9, "restored": 4, "collapsed": 3, "quarantined": 2}


def _reset_and_seed_nodes() -> None:
    """Back to a clean 4.0.0 schema in the scratch namespace, with this test's nodes."""
    _delete_edge_class()
    conn = _connect()
    try:
        cur = conn.cursor()
        _try(cur, "DROP VIEW SQLUser.rdf_edges")
        for table in ("Graph_KG.rdf_edges", STAGING, UNPLACED):
            _try(cur, f"DROP TABLE {table}")
    finally:
        conn.close()
    _initialize_schema()
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM Graph_KG.nodes WHERE node_id LIKE 'rsq:%'")
        for node, graph in (("rsq:a", ""), ("rsq:b", ""), ("rsq:c", ""), ("rsq:a", "g1")):
            cur.execute(f"INSERT INTO Graph_KG.nodes (node_id, graph_id) VALUES ('{node}', '{graph}')")
    finally:
        conn.close()


def _build_post_failure_state(leftover: str) -> None:
    """What a 4.0.0 run whose restore failed leaves: rescue table, no class.

    ``leftover``: ``"missing"`` — no rdf_edges at all; ``"empty"`` — the empty DDL
    table a failed rebuild or the base DDL script leaves, under the compat view;
    ``"subset"`` — the same, holding a row that is also in the rescue table.
    """
    _reset_and_seed_nodes()
    conn = _connect()
    try:
        cur = conn.cursor()
        _try(cur, "DROP VIEW SQLUser.rdf_edges")
        cur.execute("DROP TABLE Graph_KG.rdf_edges")
        cur.execute(
            f"CREATE TABLE {STAGING} (s VARCHAR(256) %EXACT, p VARCHAR(128) %EXACT, "
            "o_id VARCHAR(256) %EXACT, qualifiers VARCHAR(64000), graph_id VARCHAR(256) %EXACT)"
        )
        for s, p, o, q, g in RESCUED:
            cur.execute(
                f"INSERT INTO {STAGING} (s, p, o_id, qualifiers, graph_id) "
                f"VALUES ('{s}', '{p}', '{o}', {q}, {g})"
            )
        if leftover in ("empty", "subset"):
            cur.execute(RDF_EDGES_DDL)
            cur.execute(RDF_EDGES_COMPAT_VIEW_DDL)
        if leftover == "subset":
            cur.execute(
                "INSERT INTO Graph_KG.rdf_edges (s, p, o_id, qualifiers, graph_id) "
                "VALUES ('rsq:a', 'r', 'rsq:b', '{\"w\":1}', '')"
            )
    finally:
        conn.close()
    assert not _scalar(
        "SELECT COUNT(*) FROM %Dictionary.CompiledClass WHERE Name = 'Graph.KG.Edge'"
    )
    assert _scalar(f"SELECT COUNT(*) FROM {STAGING}") == len(RESCUED)
    assert _scalar(f"SELECT COUNT(*) FROM {STAGING} WHERE graph_id IS NULL") == 3


def _edges() -> list[tuple]:
    return sorted(
        _rows("SELECT s, p, o_id, graph_id, qualifiers FROM Graph_KG.rdf_edges WHERE s LIKE 'rsq:%'"),
        key=repr,
    )


def _assert_fully_restored() -> None:
    assert _edges() == EXPECTED_PLACED
    assert sorted(
        _rows(f"SELECT s, p, o_id, graph_id, qualifiers FROM {UNPLACED}"), key=repr
    ) == EXPECTED_QUARANTINED
    assert not _table_exists(RESCUE_STAGING_TABLE), "the rescue table outlived a full restore"
    # The compat view is rebuilt over the new table, not left dangling.
    assert _scalar("SELECT COUNT(*) FROM SQLUser.rdf_edges WHERE s LIKE 'rsq:%'") == 4
    # And the rebuilt table is the 4.0.0 one: edge_id and u_spo_graph.
    assert _scalar("SELECT COUNT(edge_id) FROM Graph_KG.rdf_edges WHERE s LIKE 'rsq:%'") == 4


@pytest.mark.parametrize("leftover", ["missing", "empty", "subset"])
def test_initialize_schema_resumes_a_left_behind_rescue(rescue_namespace, leftover, caplog):
    _build_post_failure_state(leftover)
    with caplog.at_level(logging.WARNING, logger="iris_vector_graph.schema"):
        _initialize_schema()
    _assert_fully_restored()
    assert "resuming the restore" in caplog.text
    assert "3 exact duplicate(s) collapsed" in caplog.text


def test_the_restore_reports_the_collapsed_count(rescue_namespace):
    _build_post_failure_state("empty")
    conn = _connect()
    try:
        result = GraphSchema.restore_rescued_rdf_edges(conn.cursor(), None)
    finally:
        conn.close()
    assert result == EXPECTED_COUNTS
    _assert_fully_restored()


class _FailOn:
    """A cursor that raises once on the first statement containing ``needle``."""

    def __init__(self, cursor, needle: str):
        self._cursor = cursor
        self._needle = needle
        self.fired = False

    def execute(self, sql, *args):
        if not self.fired and self._needle in sql:
            self.fired = True
            raise RuntimeError("forced failure mid-restore")
        return self._cursor.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._cursor, name)


def test_a_restore_that_fails_midway_resumes_on_the_next_run(rescue_namespace):
    _build_post_failure_state("missing")
    conn = _connect()
    try:
        failing = _FailOn(conn.cursor(), "INSERT INTO Graph_KG.rdf_edges ")
        with pytest.raises(RdfEdgesRescueError, match=RESCUE_STAGING_TABLE):
            GraphSchema.restore_rescued_rdf_edges(failing, None)
        assert failing.fired
    finally:
        conn.close()
    # The failure left the rebuilt table empty and every staged row in place.
    assert _table_exists("rdf_edges")
    assert _scalar("SELECT COUNT(*) FROM Graph_KG.rdf_edges") == 0
    assert _scalar(f"SELECT COUNT(*) FROM {STAGING}") == len(RESCUED)

    _initialize_schema()
    _assert_fully_restored()


def test_a_leftover_holding_other_rows_is_never_dropped(rescue_namespace):
    _build_post_failure_state("empty")
    conn = _connect()
    try:
        conn.cursor().execute(
            "INSERT INTO Graph_KG.rdf_edges (s, p, o_id, qualifiers, graph_id) "
            "VALUES ('rsq:c', 'written-since', 'rsq:a', NULL, '')"
        )
    finally:
        conn.close()
    with pytest.raises(RdfEdgesRescueError, match="1 row"):
        _initialize_schema()
    assert _edges() == [("rsq:c", "written-since", "rsq:a", "", None)]
    assert _scalar(f"SELECT COUNT(*) FROM {STAGING}") == len(RESCUED)


#: v3.2.0's `Graph.KG.Edge`, as shipped: it owns `Graph_KG.rdf_edges`.
EDGE_320 = """Class Graph.KG.Edge Extends %Persistent [ Final, DdlAllowed, SqlTableName = rdf_edges ]
{
Property s As %String(MAXLEN = 256) [ Required, SqlColumnNumber = 2 ];
Property p As %String(MAXLEN = 128) [ Required, SqlColumnNumber = 3 ];
Property oId As %String(MAXLEN = 256) [ Required, SqlColumnNumber = 4, SqlFieldName = o_id ];
Property qualifiers As %String(MAXLEN = "") [ SqlColumnNumber = 5 ];
Property graphId As %String(COLLATION = "EXACT", MAXLEN = 500) [ SqlColumnNumber = 6, SqlFieldName = graph_id ];
Index uspo On (s, p, oId) [ Unique ];
Index idxS On s;
Index idxP On p;
Index idxSP On (s, p);
Index idxPOid On (p, oId);
}
"""


def test_the_full_upgrade_from_a_class_owned_table(rescue_namespace, caplog):
    """Stage, delete the class, rebuild, restore — the deploy path, end to end."""
    _reset_and_seed_nodes()
    conn = _connect()
    try:
        cur = conn.cursor()
        _try(cur, "DROP VIEW SQLUser.rdf_edges")
        cur.execute("DROP TABLE Graph_KG.rdf_edges")
    finally:
        conn.close()
    nconn, irs = _native()
    try:
        stream = irs.classMethodObject("%Stream.GlobalCharacter", "%New")
        stream.invoke("Write", EDGE_320)
        assert irs.classMethodValue("%SYSTEM.OBJ", "LoadStream", stream, "ck-d") == 1
    finally:
        nconn.close()
    conn = _connect()
    try:
        cur = conn.cursor()
        for i, (s, p, o, q, g) in enumerate(RESCUED):
            # The first copy goes in through the unique index; every later one the way
            # v3.2.0's bulk loader wrote it.
            hint = "" if i == 0 else " %NOINDEX %NOCHECK"
            cur.execute(
                f"INSERT{hint} INTO Graph_KG.rdf_edges (s, p, o_id, qualifiers, graph_id) "
                f"VALUES ('{s}', '{p}', '{o}', {q}, {g})"
            )
        cur.execute(RDF_EDGES_COMPAT_VIEW_DDL)
    finally:
        conn.close()
    # Phase 5 of the v3.2.0 loader: rebuild the indices it skipped. A unique index
    # rebuilt over duplicates does not complain; it just indexes every row.
    nconn, irs = _native()
    try:
        assert irs.classMethodValue("Graph.KG.Edge", "%BuildIndices") == 1
    finally:
        nconn.close()
    assert _scalar("SELECT COUNT(*) FROM Graph_KG.rdf_edges") == len(RESCUED)

    # Only the class delete is real; LoadDir and the source-file deletes would reach
    # outside this namespace (the container's shared source directory).
    def call_classmethod(_target, cls, method, *args):
        if (cls, method) == ("%SYSTEM.OBJ", "Delete"):
            nconn, irs = _native()
            try:
                return irs.classMethodValue(cls, method, *args)
            finally:
                nconn.close()
        return 0

    engine_conn = _connect()
    try:
        with patch("iris_vector_graph.schema._call_classmethod", side_effect=call_classmethod):
            with caplog.at_level(logging.WARNING, logger="iris_vector_graph.schema"):
                GraphSchema.deploy_objectscript_classes(
                    engine_conn.cursor(), Path("/nonexistent"), conn=engine_conn
                )
    finally:
        engine_conn.close()

    assert not _scalar(
        "SELECT COUNT(*) FROM %Dictionary.CompiledClass WHERE Name = 'Graph.KG.Edge'"
    )
    _assert_fully_restored()
    assert "3 exact duplicate(s) collapsed" in caplog.text
