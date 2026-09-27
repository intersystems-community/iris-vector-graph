"""The 4.0.0 structural re-key on live IRIS, against a 3.x install born in 2.x.

Runs in a scratch namespace (``IVGREKEY`` by default, ``IVG_REKEY_NAMESPACE`` to
override) because every test drops and rebuilds ``Graph_KG``'s core tables. It never
touches ``USER``: the namespace is refused if it is ``USER`` or holds anything but
the tables built here. Skips when the enterprise container is not reachable.

The install is the shape a real 3.2.0-era upgrade reached:

* ``nodes`` declared ``node_id ... PRIMARY KEY`` inline, so IRIS keys it as
  ``NODES_PKEY1``; ``graph_id`` was added later ``NOT NULL DEFAULT ''`` (stored as
  ``$c(0)``), next to 3.2.0's ``uq_nodes_nodeid``.
* ``rdf_labels`` / ``rdf_props`` never had an enforced ``(s, label)`` / ``(s, key)``
  key, so they hold exact copies.
* ``rdf_edges.graph_id`` is nullable and holds both ``NULL`` and ``''``.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from iris_vector_graph.migrations.graph_scoped_embeddings import _Migrator

pytestmark = pytest.mark.integration

NAMESPACE = os.environ.get("IVG_REKEY_NAMESPACE", "IVGREKEY")
PORT = int(os.environ.get("IVG_PORT", "31972"))
TABLES = ("rdf_edges", "rdf_labels", "rdf_props", "nodes")
LONG = "x" * 60000  # a val near VARCHAR(64000)'s limit still compares exactly


@pytest.fixture
def conn():
    if NAMESPACE.upper() == "USER":
        pytest.fail("the re-key test rebuilds core tables; never point it at USER")
    try:
        import iris

        c = iris.connect("localhost", PORT, NAMESPACE, "_SYSTEM", "SYS")
    except Exception as e:  # pragma: no cover - environment-dependent
        pytest.skip(f"scratch namespace {NAMESPACE} on port {PORT} unreachable: {e}")
    cur = c.cursor()
    cur.execute(
        "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'Graph_KG'"
    )
    foreign = sorted({str(r[0]) for r in cur.fetchall()} - set(TABLES))
    if foreign:
        pytest.fail(f"{NAMESPACE} holds tables this test did not build: {foreign}")
    _drop(cur)
    yield c
    _drop(c.cursor())
    c.commit()
    c.close()


def _drop(cur):
    for table in TABLES:
        try:
            cur.execute(f"DROP TABLE Graph_KG.{table} CASCADE")
        except Exception:
            pass


def _run(cur, *statements):
    for sql in statements:
        cur.execute(sql)


def build_legacy_install(conn, *, props=None):
    cur = conn.cursor()
    _run(
        cur,
        "CREATE TABLE Graph_KG.nodes (node_id VARCHAR(256) %EXACT PRIMARY KEY, "
        "created_at TIMESTAMP)",
        "ALTER TABLE Graph_KG.nodes ADD COLUMN graph_id VARCHAR(256) %EXACT NOT NULL "
        "DEFAULT ''",
        "ALTER TABLE Graph_KG.nodes ADD CONSTRAINT uq_nodes_nodeid UNIQUE (node_id)",
        "CREATE TABLE Graph_KG.rdf_labels (s VARCHAR(256) %EXACT NOT NULL, "
        "label VARCHAR(128) %EXACT NOT NULL, CONSTRAINT fk_labels_node "
        "FOREIGN KEY (s) REFERENCES Graph_KG.nodes (node_id))",
        'CREATE TABLE Graph_KG.rdf_props (s VARCHAR(256) %EXACT NOT NULL, "key" '
        "VARCHAR(128) %EXACT NOT NULL, val VARCHAR(64000) %EXACT)",
        "CREATE TABLE Graph_KG.rdf_edges (edge_id BIGINT IDENTITY, "
        "s VARCHAR(256) %EXACT NOT NULL, p VARCHAR(128) %EXACT NOT NULL, "
        "o_id VARCHAR(256) %EXACT NOT NULL, graph_id VARCHAR(256) %EXACT NULL, "
        "CONSTRAINT fk_edges_source FOREIGN KEY (s) REFERENCES Graph_KG.nodes (node_id), "
        "CONSTRAINT fk_edges_dest FOREIGN KEY (o_id) REFERENCES Graph_KG.nodes (node_id))",
    )
    for node, graph in (("a", ""), ("b", ""), ("c", "g1")):
        cur.execute("INSERT INTO Graph_KG.nodes (node_id, graph_id) VALUES (?, ?)", [node, graph])
    for s, label in (("a", "Patient"), ("a", "Patient"), ("b", "Patient"), ("c", "Doc"),
                     ("c", "Doc")):
        cur.execute("INSERT INTO Graph_KG.rdf_labels (s, label) VALUES (?, ?)", [s, label])
    rows = props if props is not None else [
        ("a", "name", "Ann"), ("a", "name", "Ann"),
        ("a", "nick", None), ("a", "nick", None),
        ("b", "bio", LONG), ("b", "bio", LONG),
        ("c", "title", "T"),
    ]
    for s, key, val in rows:
        cur.execute('INSERT INTO Graph_KG.rdf_props (s, "key", val) VALUES (?, ?, ?)',
                    [s, key, val])
    for s, o, graph in (("a", "b", None), ("b", "a", ""), ("c", "c", "g1")):
        cur.execute("INSERT INTO Graph_KG.rdf_edges (s, p, o_id, graph_id) VALUES (?, 'r', ?, ?)",
                    [s, o, graph])
    conn.commit()


def migrator(conn, *, dry_run=False):
    m = _Migrator(conn, resolver=None, dry_run=dry_run)
    m.engine = SimpleNamespace(_t=lambda n: f"Graph_KG.{n}", _schema_prefix="Graph_KG")
    return m


class KillOnce:
    """A cursor that dies on the first statement containing ``needle`` — a killed run."""

    def __init__(self, cursor, needle):
        self._cursor, self._needle = cursor, needle

    def execute(self, sql, params=None):
        if self._needle and self._needle in sql:
            self._needle = None
            raise RuntimeError("[SQLCODE: <-99>] the migration process was killed")
        return self._cursor.execute(sql, params or [])

    def __getattr__(self, name):
        return getattr(self._cursor, name)


def keys(conn):
    cur = conn.cursor()
    cur.execute(
        "SELECT TABLE_NAME, CONSTRAINT_NAME, CONSTRAINT_TYPE, COLUMN_NAME, ORDINAL_POSITION, "
        "REFERENCED_CONSTRAINT_NAME FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE "
        "WHERE TABLE_SCHEMA = 'Graph_KG'"
    )
    out = {}
    for table, name, kind, col, pos, ref in cur.fetchall():
        entry = out.setdefault((table, name), {"type": kind, "cols": [], "ref": ref})
        entry["cols"].append((pos, col))
    for entry in out.values():
        entry["cols"] = [c for _p, c in sorted(entry["cols"])]
    return out


def nullable(conn, table, column):
    cur = conn.cursor()
    cur.execute(
        "SELECT IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = 'Graph_KG' "
        "AND TABLE_NAME = ? AND COLUMN_NAME = ?",
        [table, column],
    )
    row = cur.fetchone()
    return row[0] if row else None


def assert_fully_rekeyed(conn):
    k = keys(conn)
    node_id_only = [
        n for (t, n), e in k.items()
        if t == "nodes" and e["type"] in ("PRIMARY KEY", "UNIQUE") and e["cols"] == ["node_id"]
    ]
    assert node_id_only == [], f"nodes still keyed on node_id alone: {node_id_only}"
    assert k[("nodes", "pk_nodes_graph")]["type"] == "PRIMARY KEY"
    assert k[("nodes", "pk_nodes_graph")]["cols"] == ["node_id", "graph_id"]
    assert k[("nodes", "uq_nodes_graph_node")]["cols"] == ["graph_id", "node_id"]
    for table, pk, tail in (("rdf_labels", "pk_labels", "label"),
                            ("rdf_props", "pk_props", "key")):
        assert nullable(conn, table, "graph_id") == "NO", table
        assert k[(table, pk)]["type"] == "PRIMARY KEY"
        assert k[(table, pk)]["cols"] == ["graph_id", "s", tail]
    for table, fk, cols in (("rdf_labels", "fk_labels_node", ["graph_id", "s"]),
                            ("rdf_edges", "fk_edges_source", ["graph_id", "s"]),
                            ("rdf_edges", "fk_edges_dest", ["graph_id", "o_id"])):
        assert k[(table, fk)]["cols"] == cols, (table, fk)
        assert k[(table, fk)]["ref"] == "uq_nodes_graph_node", (table, fk)


def assert_same_node_id_writes_into_a_second_graph(conn):
    cur = conn.cursor()
    cur.execute("INSERT INTO Graph_KG.nodes (node_id, graph_id) VALUES ('a', 'g2')")
    cur.execute("INSERT INTO Graph_KG.rdf_labels (graph_id, s, label) VALUES ('g2', 'a', 'Patient')")
    cur.execute('INSERT INTO Graph_KG.rdf_props (graph_id, s, "key", val) '
                "VALUES ('g2', 'a', 'name', 'Other Ann')")
    conn.commit()
    cur.execute("SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id = 'a'")
    assert cur.fetchone()[0] == 2
    with pytest.raises(Exception, match="-119|UNIQUE|PRIMARY"):
        cur.execute("INSERT INTO Graph_KG.rdf_labels (graph_id, s, label) "
                    "VALUES ('g2', 'a', 'Patient')")


def rows(conn, sql):
    cur = conn.cursor()
    cur.execute(sql)
    return [tuple(r) for r in cur.fetchall()]


# --- scenario (a) ---------------------------------------------------------------------


def test_a_legacy_install_rekeys_and_takes_a_node_id_in_two_graphs(conn):
    build_legacy_install(conn)

    outcome = migrator(conn).rekey(conn.cursor())

    assert not outcome.refused, outcome
    assert outcome.complete
    assert outcome.legacy_node_keys == ["NODES_PKEY1", "uq_nodes_nodeid"]
    assert outcome.duplicates_removed == {"rdf_labels": 2, "rdf_props": 3}
    assert_fully_rekeyed(conn)
    assert rows(conn, "SELECT graph_id, s, label FROM Graph_KG.rdf_labels ORDER BY s") == [
        ("", "a", "Patient"), ("", "b", "Patient"), ("g1", "c", "Doc"),
    ]
    props = rows(conn, 'SELECT graph_id, s, "key", LENGTH(val) FROM Graph_KG.rdf_props '
                       'ORDER BY s, "key"')
    assert props == [
        ("", "a", "name", 3), ("", "a", "nick", None), ("", "b", "bio", 60000),
        ("g1", "c", "title", 1),
    ]
    assert_same_node_id_writes_into_a_second_graph(conn)


def test_a_dry_run_predicts_the_rekey_and_writes_nothing(conn):
    build_legacy_install(conn)
    before = keys(conn)

    outcome = migrator(conn, dry_run=True).rekey(conn.cursor())

    assert outcome.duplicates_removed == {"rdf_labels": 2, "rdf_props": 3}
    assert outcome.legacy_node_keys == ["NODES_PKEY1", "uq_nodes_nodeid"]
    assert not outcome.complete
    assert keys(conn) == before
    assert rows(conn, "SELECT COUNT(*) FROM Graph_KG.rdf_labels") == [(5,)]


def test_props_that_disagree_on_the_value_refuse_the_rekey(conn):
    build_legacy_install(conn, props=[("a", "name", "Ann"), ("a", "name", "Anne"),
                                      ("b", "name", ""), ("b", "name", None)])
    before = keys(conn)

    outcome = migrator(conn).rekey(conn.cursor())

    assert outcome.refused
    assert outcome.key_conflicts == {"rdf_props": [("a", "name"), ("b", "name")]}
    assert keys(conn) == before
    assert nullable(conn, "rdf_labels", "graph_id") is None, "a refused re-key wrote DDL"
    assert rows(conn, "SELECT COUNT(*) FROM Graph_KG.rdf_props") == [(4,)]


# --- scenario (b) ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "killed_at",
    [
        "ALTER TABLE Graph_KG.rdf_props ADD COLUMN graph_id",
        "ALTER TABLE Graph_KG.rdf_labels ADD CONSTRAINT pk_labels",
        "ADD CONSTRAINT fk_edges_source",
    ],
)
def test_a_run_killed_after_labels_gained_graph_id_resumes_to_the_full_rekey(conn, killed_at):
    build_legacy_install(conn)
    with pytest.raises(RuntimeError, match="killed"):
        migrator(conn).rekey(KillOnce(conn.cursor(), killed_at))
    conn.commit()
    assert nullable(conn, "rdf_labels", "graph_id") is not None, "killed before the tripwire"

    outcome = migrator(conn).rekey(conn.cursor())

    assert not outcome.refused, outcome
    assert_fully_rekeyed(conn)
    assert rows(conn, "SELECT COUNT(*) FROM Graph_KG.rdf_labels") == [(3,)]
    assert rows(conn, "SELECT COUNT(*) FROM Graph_KG.rdf_props") == [(4,)]
    assert_same_node_id_writes_into_a_second_graph(conn)


def test_a_finished_rekey_is_recognised_and_not_redone(conn):
    build_legacy_install(conn)
    migrator(conn).rekey(conn.cursor())
    before = keys(conn)

    seen = []

    class Recording(KillOnce):
        def execute(self, sql, params=None):
            seen.append(sql)
            return super().execute(sql, params)

    outcome = migrator(conn).rekey(Recording(conn.cursor(), None))

    assert outcome.complete
    assert [s for s in seen if not s.lstrip().upper().startswith("SELECT")] == []
    assert keys(conn) == before
