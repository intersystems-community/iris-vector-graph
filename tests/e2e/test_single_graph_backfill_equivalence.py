"""The single-graph backfill shortcut leaves the same child tables as the correlated one.

Live counterpart of `tests/unit/test_single_graph_backfill.py`. The same 3.2.0 install is
built twice on `ivg-iris-enterprise`: once migrated with the shortcut in place, once with
it forced off (the correlated UPDATE the statement list carries). The rdf_labels and
rdf_props contents, and the node rows, are read back and compared.

Each run starts from a rebuilt 3.2.0 namespace (the helpers `test_227_migration` uses)
and the spy on `_single_graph` records which path the run took, so a pass that quietly
fell back on both sides cannot look like equivalence.

Uncompiled against IRIS by the author; the lead runs this file in the serial e2e pass.
"""

from __future__ import annotations

import contextlib
import os

import pytest

from iris_vector_graph.migrations import migrate_to_graph_scoped_embeddings
from iris_vector_graph.migrations.graph_scoped_embeddings import _Migrator
from tests.e2e.test_227_migration import _assert_is_320, _rebuild_320, _restore_400

#: One graph per run, the same data each time; the default graph is '' (DEFAULT '').
NAMED = "ivg241sgb-a"
DEFAULT = ""


def _populate(conn, graph: str) -> None:
    cursor = conn.cursor()
    for node in ("n1", "n2", "n3"):
        cursor.execute(
            "INSERT INTO Graph_KG.nodes (node_id, graph_id) VALUES (?, ?)", [node, graph]
        )
    for s, label in (("n1", "Patient"), ("n2", "Patient"), ("n2", "Visit"), ("n3", "Visit")):
        cursor.execute("INSERT INTO Graph_KG.rdf_labels (s, label) VALUES (?, ?)", [s, label])
    for s, key, val in (("n1", "name", "Ann"), ("n2", "name", "Bob"), ("n3", "age", "41")):
        cursor.execute(
            'INSERT INTO Graph_KG.rdf_props (s, "key", val) VALUES (?, ?, ?)', [s, key, val]
        )
    conn.commit()


def _child_tables(conn) -> tuple:
    cursor = conn.cursor()
    cursor.execute("SELECT s, label, graph_id FROM Graph_KG.rdf_labels")
    labels = sorted((str(r[0]), str(r[1]), str(r[2] or "")) for r in cursor.fetchall() or [])
    cursor.execute('SELECT s, "key", val, graph_id FROM Graph_KG.rdf_props')
    props = sorted(
        (str(r[0]), str(r[1]), str(r[2]), str(r[3] or "")) for r in cursor.fetchall() or []
    )
    cursor.execute("SELECT node_id, graph_id FROM Graph_KG.nodes")
    nodes = sorted((str(r[0]), str(r[1] or "")) for r in cursor.fetchall() or [])
    return labels, props, nodes


def _migrate(conn, graph: str, monkeypatch, *, shortcut: bool) -> tuple:
    _rebuild_320(conn)
    _assert_is_320(conn)
    _populate(conn, graph)

    seen: list = []
    original = _Migrator._single_graph

    def spy(self, cursor, table):
        result = original(self, cursor, table)
        seen.append((table, result))
        return result

    monkeypatch.setattr(_Migrator, "_single_graph", spy)
    if not shortcut:
        monkeypatch.setattr(_Migrator, "_single_graph", lambda self, cursor, table: None)
    migrate_to_graph_scoped_embeddings(conn)
    return _child_tables(conn), seen


@pytest.fixture
def live(iris_connection):
    if os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true":
        pytest.fail("the graph_id backfill is a storage shape change; it needs IRIS")
    if iris_connection is None:
        pytest.fail("no live IRIS connection for the backfill equivalence test")
    yield iris_connection
    with contextlib.suppress(Exception):
        _restore_400(iris_connection)


@pytest.mark.e2e
@pytest.mark.parametrize("graph", [NAMED, DEFAULT], ids=["named-graph", "default-graph"])
def test_shortcut_and_correlated_backfill_leave_the_same_child_tables(live, graph, monkeypatch):
    fast, fast_seen = _migrate(live, graph, monkeypatch, shortcut=True)
    monkeypatch.undo()
    slow, slow_seen = _migrate(live, graph, monkeypatch, shortcut=False)

    assert ("rdf_labels", graph) in fast_seen, fast_seen
    assert ("rdf_props", graph) in fast_seen, fast_seen
    assert all(result == graph for _table, result in fast_seen), fast_seen
    assert all(result is None for _table, result in slow_seen), slow_seen
    assert fast == slow
    assert {g for _s, _l, g in fast[0]} == {graph}
