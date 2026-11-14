"""4.7.1 (H3): every migration runs, and commits, on a connection whose driver autocommit is off.

4.7.0's `commit_step` refused at each step's commit when `$TLEVEL` was above 0. With `setAutoCommit(False)` and nothing
written by the caller, `$TLEVEL` is 0 at the migration's entry, so the entry refusal lets it run, as designed; the
migration's own first write then raises `$TLEVEL` to 1, and the commit refused the migration's own work with
`CallerTransactionOpenError` ("Nothing was done") while the step's writes were still open. Each case below starts
from a state the migration has work in, built as that migration's existing E2E builds it:

  3.2.0 install   tests/e2e/test_227_migration.py's frozen-DDL rebuild of `Graph_KG` (`upgrade_to_4_0_0`,
                  `migrate_to_graph_scoped_embeddings`): two vectors in one graph, which a single-graph install places
                  in that graph (US5 1)
  3.2.0 docs      tests/e2e/test_230_migration.py's scratch schema with 3.2.0-shaped `docs` and `kg_EdgeEmbeddings`
                  (`migrate_docs_and_edge_vectors`): its known placements and quarantines
  flat ^KG        a `^KG("label", label, s)` entry in 3.2.0's layout (`rekey_kg_node_stores`, which writes globals and
                  no SQL rows, so `$TLEVEL` stays 0 and 4.7.0 already completed it; this pins that it still does)

Completes: the caller turns autocommit off and writes nothing; the migration returns; the caller rolls back (which
would take back anything left uncommitted); a second connection reads the known answer.
Refused: the caller turns autocommit off and inserts a row of its own first; the migration raises
`CallerTransactionOpenError` before writing anything (the starting state is still there, read from a second
connection); the caller still sees its row, `$TLEVEL` is still 1, a second connection reading at READ COMMITTED cannot
read the row (it is locked by an open transaction, SQLCODE -114), and after the caller's rollback it is gone.

The 3.2.0 rebuild replaces `Graph_KG`'s tables, so this file has a namespace of its own (tests/e2e/gate.py `r471mig`):
after it the namespace is at 4.0.0's shape but with 3.2.0's 8-wide embedding tables and no adjacency index, on which
other files' writes fail (measured: test_470_caller_txn_e2e, 9 failures run after it in the same namespace).

    IVG_R471_MIG_NS=IVG470H3 pytest tests/e2e/test_471_migration_autocommit_off_e2e.py
"""

from __future__ import annotations

import contextlib
import os
import uuid

import pytest

from tests.e2e import reading_role460 as rr
from tests.e2e import scratch_vars

NS = os.environ.get("IVG_R471_MIG_NS", "")
CALLER_TABLE = "SQLUser.ivg471m_caller"
GRAPH = "ivg471mig-a"
DOCS_SCHEMA = "IVG471MigDocs"
FLAT_LABEL = "IVG471FLAT"

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not rr.container_running(), reason=f"{rr.CONTAINER} is not running"),
    scratch_vars.requires("IVG_R471_MIG_NS"),
]


def _connect():
    import iris

    return iris.connect("localhost", rr.PORT, NS, "_SYSTEM", "SYS")


def _level(conn) -> int:
    cur = conn.cursor()
    cur.execute("SELECT $TLEVEL")
    return int(cur.fetchone()[0])


def _rows(conn, sql, params=()) -> list:
    cur = conn.cursor()
    try:
        cur.execute(sql, list(params))
        return [tuple(r) for r in (cur.fetchall() or [])]
    finally:
        cur.close()


def _columns(conn, schema: str, table: str) -> set:
    return {
        str(r[0]).lower()
        for r in _rows(
            conn,
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?",
            [schema, table],
        )
    }


def _uid() -> str:
    return "t471m" + uuid.uuid4().hex[:10]


@pytest.fixture(scope="module")
def observer():
    """A second connection, which reads what the caller's connection left."""
    conn = _connect()
    cur = conn.cursor()
    with contextlib.suppress(Exception):  # absent on the first run
        cur.execute(f"DROP TABLE {CALLER_TABLE}")
    cur.execute(f"CREATE TABLE {CALLER_TABLE} (k VARCHAR(80) PRIMARY KEY)")
    yield conn
    cur.execute(f"DROP TABLE {CALLER_TABLE}")
    conn.close()


@pytest.fixture(scope="module")
def committed_reader():
    """A third connection reading at READ COMMITTED, which cannot read a row another transaction holds open."""
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
    cur.execute("SET OPTION LOCK_TIMEOUT = 2")
    yield conn
    conn.close()


@pytest.fixture
def caller():
    conn = _connect()
    yield conn
    try:
        conn.rollback()
        conn.setAutoCommit(True)
    finally:
        conn.close()


def _caller_writes_first(caller, u: str) -> None:
    caller.setAutoCommit(False)
    assert _level(caller) == 0
    caller.cursor().execute(f"INSERT INTO {CALLER_TABLE} (k) VALUES (?)", [u])
    assert _level(caller) == 1


def _assert_callers_row_still_uncommitted(caller, observer, committed_reader, u: str) -> None:
    assert _level(caller) == 1, "the refusal ended the caller's transaction"
    assert _rows(caller, f"SELECT k FROM {CALLER_TABLE} WHERE k = ?", [u]) == [(u,)], "the caller lost its own row"
    with pytest.raises(Exception, match=r"-114"):
        _rows(committed_reader, f"SELECT %ID, k FROM {CALLER_TABLE} WHERE k = ?", [u])
    caller.rollback()
    assert _rows(observer, f"SELECT k FROM {CALLER_TABLE} WHERE k = ?", [u]) == [], (
        "the caller's row outlived the caller's rollback: it was committed"
    )


def _refused(call, name: str):
    from iris_vector_graph.errors import CallerTransactionOpenError

    with pytest.raises(CallerTransactionOpenError) as info:
        call()
    assert (info.value.operation, info.value.level) == (f"{name}()", 1), str(info.value)


# ------------------------------------------------------------------------------------------- the 3.2.0 install


@pytest.fixture
def env320():
    """test_227_migration's rebuilt 3.2.0 `Graph_KG`, with n1 and n2 in GRAPH and a vector each; 4.0.0 afterwards."""
    from tests.e2e.test_227_migration import Env320, _assert_is_320, _rebuild_320, _restore_400

    conn = _connect()
    _rebuild_320(conn)
    _assert_is_320(conn)
    env = Env320(conn)
    env.node("n1", GRAPH)
    env.node("n2", GRAPH)
    env.label("n1")
    env.vector("n1", 0.1)
    env.vector("n2", 0.2)
    env.commit()
    yield env
    env.cursor.close()
    # a refused (or 4.7.0's stopped) migration leaves 3.2.0's tables, which setup alone does not reshape
    from iris_vector_graph.migrations import upgrade_to_4_0_0

    upgrade_to_4_0_0(conn)
    _restore_400(conn)
    assert "graph_id" in _columns(conn, "Graph_KG", "rdf_labels"), "Graph_KG was not put back at 4.0.0"
    conn.close()


def _placed(observer) -> set:
    return {(str(r[0] or ""), str(r[1])) for r in _rows(observer, "SELECT graph_id, node_id FROM Graph_KG.kg_NodeEmbeddings")}


def test_migrate_to_graph_scoped_embeddings_with_autocommit_off_commits(env320, caller, observer):
    from iris_vector_graph.migrations import migrate_to_graph_scoped_embeddings

    caller.setAutoCommit(False)
    assert _level(caller) == 0
    report = migrate_to_graph_scoped_embeddings(caller)
    assert report.rows_placed == {(GRAPH, "kg_NodeEmbeddings"): 2}, report.rows_placed
    caller.rollback()
    assert _placed(observer) == {(GRAPH, "n1"), (GRAPH, "n2")}


def test_upgrade_to_4_0_0_with_autocommit_off_commits_every_step(env320, caller, observer):
    from iris_vector_graph.migrations import UPGRADE_STEPS, upgrade_to_4_0_0

    caller.setAutoCommit(False)
    assert _level(caller) == 0
    report = upgrade_to_4_0_0(caller)
    assert [step.name for step in report.steps] == list(UPGRADE_STEPS)
    caller.rollback()
    assert _placed(observer) == {(GRAPH, "n1"), (GRAPH, "n2")}
    assert "graph_id" in _columns(observer, "Graph_KG", "rdf_labels")


@pytest.mark.parametrize("name", ["upgrade_to_4_0_0", "migrate_to_graph_scoped_embeddings"])
def test_a_callers_write_first_is_refused_on_the_320_install(env320, caller, observer, committed_reader, name):
    import importlib

    from tests.e2e.test_227_migration import _assert_is_320

    fn = getattr(importlib.import_module("iris_vector_graph.migrations"), name)
    u = _uid()
    _caller_writes_first(caller, u)
    _refused(lambda: fn(caller), name)
    _assert_is_320(observer)  # nothing of the migration was written
    _assert_callers_row_still_uncommitted(caller, observer, committed_reader, u)


# ------------------------------------------------------------------------------------- the 3.2.0 docs and edges


@pytest.fixture
def docs320():
    """test_230_migration's pre-migration scratch schema: four `docs` rows and three edge vectors."""
    from tests.e2e import test_230_migration as t230

    conn = _connect()
    t230._build_pre_migration(conn, DOCS_SCHEMA)
    yield t230
    t230._drop_schema(conn, DOCS_SCHEMA)
    conn.close()


def test_migrate_docs_and_edge_vectors_with_autocommit_off_commits(docs320, caller, observer):
    from iris_vector_graph.migrations.docs_and_edge_vectors import migrate_docs_and_edge_vectors

    t = docs320
    caller.setAutoCommit(False)
    assert _level(caller) == 0
    migrate_docs_and_edge_vectors(caller, t.DIM, schema=DOCS_SCHEMA)
    caller.rollback()
    # test_230's known placements: a node in one graph places its document there; a node two graphs hold, or none,
    # quarantines it; an edge vector goes to the one graph asserting its triple
    docs = {(str(g or ""), str(i)) for g, i in _rows(observer, f"SELECT graph_id, id FROM {DOCS_SCHEMA}.docs")}
    assert docs == {("", t.DOC_DEFAULT), (t.GRAPH_A, t.DOC_A)}
    quarantined = {str(r[0]) for r in _rows(observer, f"SELECT doc_id FROM {DOCS_SCHEMA}.docs_quarantine")}
    assert quarantined == {t.DOC_SHARED, t.DOC_ORPHAN}
    edges = set(_rows(observer, f"SELECT graph_id, s, p, o_id FROM {DOCS_SCHEMA}.kg_EdgeEmbeddings"))
    assert edges == {(t.GRAPH_A, *t.EDGE_PLACED)}


def test_a_callers_write_first_is_refused_before_the_docs_migration(docs320, caller, observer, committed_reader):
    from iris_vector_graph.migrations.docs_and_edge_vectors import migrate_docs_and_edge_vectors

    t = docs320
    u = _uid()
    _caller_writes_first(caller, u)
    _refused(lambda: migrate_docs_and_edge_vectors(caller, t.DIM, schema=DOCS_SCHEMA), "migrate_docs_and_edge_vectors")
    # nothing of the migration was written: docs is still 3.2.0's, keyed `id` with no graph_id, all four rows there
    assert "graph_id" not in _columns(observer, DOCS_SCHEMA, "docs")
    assert len(_rows(observer, f"SELECT id FROM {DOCS_SCHEMA}.docs")) == 4
    _assert_callers_row_still_uncommitted(caller, observer, committed_reader, u)


# ---------------------------------------------------------------------------------------------- the flat ^KG


@pytest.fixture
def flat_kg():
    """One node `u` with label FLAT_LABEL in the default graph, whose label is held only in 3.2.0's layout: its entry
    ^KG("label", FLAT_LABEL, u) is set and the 4.0.0 one, ^KG("label", 0, FLAT_LABEL, u) (0 is the default graph's
    subscript, Graph.KG.GraphKey.ForIndex), is killed, so only the rekey's rebuild can bring it back. A connection of
    its own: the rekey recompiles Graph.KG, after which a connection's earlier cached statements fail <SYNTAX>."""
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = _connect()
    u = _uid()
    assert IRISGraphEngine(conn, embedding_dimension=rr.DIM).create_node(u, labels=[FLAT_LABEL])
    native = iris.createIRIS(conn)
    native.set("", "^KG", "label", FLAT_LABEL, u)
    native.kill("^KG", "label", 0, FLAT_LABEL, u)
    yield native, u
    native.kill("^KG", "label", FLAT_LABEL, u)
    cleanup = _connect()
    cur = cleanup.cursor()
    cur.execute("DELETE FROM Graph_KG.rdf_labels WHERE s = ?", [u])
    cur.execute("DELETE FROM Graph_KG.nodes WHERE node_id = ?", [u])
    cleanup.close()
    conn.close()


def test_rekey_kg_node_stores_with_autocommit_off_completes(flat_kg, caller):
    from iris_vector_graph.migrations.kg_node_stores import rekey_kg_node_stores

    native, u = flat_kg
    caller.setAutoCommit(False)
    assert _level(caller) == 0
    rekey_kg_node_stores(caller)
    caller.rollback()
    # the flat entry is dropped and the server rebuilt the label from rdf_labels, keyed by graph
    assert native.isDefined("^KG", "label", FLAT_LABEL, u) == 0
    assert native.isDefined("^KG", "label", 0, FLAT_LABEL, u) in (1, 11)


def test_a_callers_write_first_is_refused_before_the_rekey(flat_kg, caller, observer, committed_reader):
    from iris_vector_graph.migrations.kg_node_stores import rekey_kg_node_stores

    native, n = flat_kg
    u = _uid()
    _caller_writes_first(caller, u)
    _refused(lambda: rekey_kg_node_stores(caller), "rekey_kg_node_stores")
    assert native.isDefined("^KG", "label", FLAT_LABEL, n) in (1, 11), "the refused rekey killed the flat entry"
    assert native.isDefined("^KG", "label", 0, FLAT_LABEL, n) == 0, "the refused rekey rebuilt the label store"
    _assert_callers_row_still_uncommitted(caller, observer, committed_reader, u)
