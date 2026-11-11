"""4.7.0 gate: `create_node` of a node whose old property rows outlived it.

`rdf_props` has no foreign key on `nodes` (a caller may write a property before its node), so a node removed by SQL
(`DELETE FROM nodes` and its labels, as tests/e2e/test_227_inventory.py's teardown does) leaves its property rows. On
4.7.0 before this fix a `create_node` of that id again raised `GraphWriteError` ("IRIS refused 1 of the 1 property
rows"): the property INSERT is guarded by NOT EXISTS, which skipped the old row, and row 14's backstop counted the
skip as a refusal. Before row 14 it answered True and kept the old rows' values, so `properties={"a": 2}` read back
`a` = 1. A new node holds the properties it is created with: the old rows of that id in that graph go first.

    IVG_R470_WR_NS=IVG470RR pytest tests/e2e/test_470_orphan_props_e2e.py
"""

from __future__ import annotations

import os
import uuid

import pytest

from tests.e2e import reading_role460 as rr
from tests.e2e import scratch_vars

NS = os.environ.get("IVG_R470_WR_NS", "")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not rr.container_running(), reason=f"{rr.CONTAINER} is not running"),
    scratch_vars.requires("IVG_R470_WR_NS"),
]


@pytest.fixture
def eng():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = iris.connect("localhost", rr.PORT, NS, "_SYSTEM", "SYS")
    yield IRISGraphEngine(conn, embedding_dimension=rr.DIM)
    conn.close()


def _props(conn, nid, graph):
    cur = conn.cursor()
    cur.execute('SELECT "key", val FROM Graph_KG.rdf_props WHERE s = ? AND graph_id = ?', [nid, graph])
    return sorted((str(k), v) for k, v in cur.fetchall())


def _remove_by_sql(conn, nid, graph):
    """The node row and its labels, not its properties (test_227_inventory's teardown)."""
    cur = conn.cursor()
    cur.execute("DELETE FROM Graph_KG.rdf_labels WHERE s = ? AND graph_id = ?", [nid, graph])
    cur.execute("DELETE FROM Graph_KG.nodes WHERE node_id = ? AND graph_id = ?", [nid, graph])
    conn.commit()


@pytest.mark.parametrize("graph", ("", "g470orphan"), ids=("default_graph", "named_graph"))
def test_create_node_over_orphaned_properties_writes_its_own(eng, graph):
    nid = "t470o" + uuid.uuid4().hex[:8]
    try:
        assert eng.create_node(nid, labels=["Quiet"], properties={"a": 1, "b": "x"}, graph=graph or None)
        _remove_by_sql(eng.conn, nid, graph)
        assert _props(eng.conn, nid, graph) == [("a", 1), ("b", "x"), ("id", nid)], "the setup left no orphan rows"
        assert eng.create_node(nid, labels=["Quiet"], properties={"a": 2}, graph=graph or None) is True
        assert _props(eng.conn, nid, graph) == [("a", 2), ("id", nid)]
        assert eng.create_node(nid, labels=["Quiet"], graph=graph or None) is False  # it exists now
    finally:
        eng.delete_node(nid, graph=graph or None)
        eng.conn.cursor().execute("DELETE FROM Graph_KG.rdf_props WHERE s = ?", [nid])
        eng.conn.commit()


def test_another_graphs_node_of_that_id_keeps_its_properties(eng):
    nid = "t470o" + uuid.uuid4().hex[:8]
    try:
        assert eng.create_node(nid, properties={"a": 9})  # the default graph's
        assert eng.create_node(nid, properties={"a": 1}, graph="g470orphan")
        _remove_by_sql(eng.conn, nid, "g470orphan")
        assert eng.create_node(nid, properties={"a": 2}, graph="g470orphan") is True
        assert _props(eng.conn, nid, "") == [("a", 9), ("id", nid)]
        assert _props(eng.conn, nid, "g470orphan") == [("a", 2), ("id", nid)]
    finally:
        eng.delete_node(nid, graph=None)
        eng.delete_node(nid, graph="g470orphan")
        eng.conn.cursor().execute("DELETE FROM Graph_KG.rdf_props WHERE s = ?", [nid])
        eng.conn.commit()
