"""`delete_nodes` / `delete_edges` / `delete_nodes_by_prefix` against live IRIS.

Requires ivg-iris-enterprise:
    IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 \\
        pytest tests/integration/test_delete_nodes_graph_scoped.py

Found migrating a 3.2.0-era install to 4.0.0. `IRISGraphStore.delete_nodes`
deleted by node id with no graph predicate, so one graph's delete took the same id
out of every graph; built one unbounded `IN` list, which failed to prepare at 2000
ids; left `^KG` adjacency answering neighbour queries for the deleted nodes; and
returned `len(node_ids)`.

These tests assert through the stores, not through the returned count alone.
"""

from __future__ import annotations

import json
import os

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"

pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

A = "delscope_a"
B = "delscope_b"


@pytest.fixture()
def engine(iris_connection, iris_master_cleanup):
    from iris_vector_graph.engine import IRISGraphEngine

    eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
    if not eng._store._has_eraser_method("EraseNodeIds"):
        pytest.skip("Graph.KG.Eraser.EraseNodeIds not deployed in this namespace")
    yield eng
    for g in (A, B):
        try:
            eng.erase_graph(g)
        except Exception:
            pass


def _kg(engine, *subs):
    v = engine._iris_obj().classMethodValue("Graph.KG.Meta", "GetKG", *[str(s) for s in subs])
    return "" if v is None else str(v)


def _count(engine, table, graph, where="", params=()):
    cursor = engine.conn.cursor()
    sql = f"SELECT COUNT(*) FROM {table} WHERE COALESCE(graph_id, '') = COALESCE(?, '')"
    if where:
        sql += f" AND {where}"
    cursor.execute(sql, [graph, *params])
    return int(cursor.fetchone()[0])


def _neighbours(engine, node, graph, pred=""):
    raw = engine._iris_obj().classMethodValue("Graph.KG.EdgeScan", "MatchEdges", node, pred, 0, graph)
    return {row.get("o") for row in json.loads(str(raw))}


def _version(engine):
    v = _kg(engine, "__version")
    return int(v) if v else 0


def _seed_pair(engine):
    """The same three ids in A and in B: hub -R-> x -R-> y, labelled and propertied."""
    for g in (A, B):
        for n in ("hub", "x", "y"):
            engine.create_node(n, labels=["Thing"], properties={"name": f"{n}@{g}"}, graph=g)
        engine.create_edge("hub", "R", "x", graph=g)
        engine.create_edge("x", "R", "y", graph=g)
    engine.conn.commit()


def test_delete_in_one_graph_leaves_the_other_intact(engine):
    _seed_pair(engine)
    assert "x" in _neighbours(engine, "hub", A)
    assert "x" in _neighbours(engine, "hub", B)
    before = _version(engine)

    result = engine._store.delete_nodes(["x"], graph=A)

    assert result.rows == [[1]]
    # Graph A: the node and everything hanging off it are gone.
    assert _count(engine, "Graph_KG.nodes", A, "node_id = ?", ["x"]) == 0
    assert _count(engine, "Graph_KG.rdf_labels", A, "s = ?", ["x"]) == 0
    assert _count(engine, "Graph_KG.rdf_props", A, "s = ?", ["x"]) == 0
    assert _count(engine, "Graph_KG.rdf_edges", A, "(s = ? OR o_id = ?)", ["x", "x"]) == 0
    assert "x" not in _neighbours(engine, "hub", A)
    assert _neighbours(engine, "x", A) == set()
    assert _kg(engine, "in", A, "y", "R", "x") == ""
    assert _kg(engine, "prop", A, "x", "name") == ""
    assert _kg(engine, "label", A, "Thing", "x") == ""
    # hub's out-degree in A is gone with its only edge.
    assert _kg(engine, "deg", A, "hub") == ""
    # Graph B: untouched.
    assert _count(engine, "Graph_KG.nodes", B, "node_id = ?", ["x"]) == 1
    assert _count(engine, "Graph_KG.rdf_labels", B, "s = ?", ["x"]) == 1
    assert _count(engine, "Graph_KG.rdf_props", B, "s = ?", ["x"]) >= 1
    assert _count(engine, "Graph_KG.rdf_edges", B, "(s = ? OR o_id = ?)", ["x", "x"]) == 2
    assert _neighbours(engine, "hub", B) == {"x"}
    assert _neighbours(engine, "x", B) == {"y"}
    assert _kg(engine, "deg", B, "hub") == "1"
    # The Arno cache stamp moved.
    assert _version(engine) > before


def test_returned_count_is_rows_removed(engine):
    _seed_pair(engine)
    result = engine._store.delete_nodes(["x", "y", "never-existed"], graph=A)
    assert result.rows == [[2]]
    assert engine._store.delete_nodes(["x"], graph=A).rows == [[0]]


def test_delete_of_2500_ids_succeeds(engine):
    ids = [f"bulk:{i:05d}" for i in range(2500)]
    cursor = engine.conn.cursor()
    cursor.executemany(
        "INSERT INTO Graph_KG.nodes (node_id, graph_id) VALUES (?, ?)",
        [[i, A] for i in ids],
    )
    engine.conn.commit()
    engine.create_edge(ids[0], "R", ids[1], graph=A)
    engine.create_node(ids[0], graph=B)
    engine.conn.commit()

    result = engine._store.delete_nodes(ids, graph=A)

    assert result.rows == [[2500]]
    assert _count(engine, "Graph_KG.nodes", A, "node_id %STARTSWITH ?", ["bulk:"]) == 0
    assert _count(engine, "Graph_KG.nodes", B, "node_id = ?", [ids[0]]) == 1


def test_delete_edges_is_graph_scoped(engine):
    _seed_pair(engine)
    result = engine._store.delete_edges([("hub", "R", "x")], graph=A)
    assert result.rows == [[1]]
    assert "x" not in _neighbours(engine, "hub", A)
    assert _neighbours(engine, "hub", B) == {"x"}
    assert _count(engine, "Graph_KG.rdf_edges", B, "s = ? AND o_id = ?", ["hub", "x"]) == 1
    # Nodes survive an edge delete.
    assert _count(engine, "Graph_KG.nodes", A, "node_id = ?", ["x"]) == 1


def test_prefix_erase_removes_only_matching_nodes_in_that_graph(engine):
    for g in (A, B):
        engine.create_node("keep:1", labels=["K"], graph=g)
        for n in ("tmp:1", "tmp:2"):
            engine.create_node(n, labels=["T"], properties={"v": n}, graph=g)
        engine.create_edge("keep:1", "R", "tmp:1", graph=g)
        engine.create_edge("tmp:1", "R", "tmp:2", graph=g)
        engine.create_edge("tmp:2", "R", "keep:1", graph=g)
    engine.conn.commit()
    for g in (A, B):
        for n in ("keep:1", "tmp:1"):
            engine.store_embedding(n, [0.1, 0.2, 0.3, 0.4], graph=g)
    engine.conn.commit()
    assert engine.get_embedding("tmp:1", graph=A) is not None
    before = _version(engine)

    removed = engine.delete_nodes_by_prefix("tmp:", graph=A)

    assert removed == 2
    assert _count(engine, "Graph_KG.nodes", A, "node_id %STARTSWITH ?", ["tmp:"]) == 0
    assert _count(engine, "Graph_KG.nodes", A, "node_id = ?", ["keep:1"]) == 1
    assert _count(engine, "Graph_KG.rdf_edges", A) == 0
    assert _neighbours(engine, "keep:1", A) == set()
    assert _kg(engine, "out", A, "tmp:1", "R", "tmp:2") == ""
    assert _kg(engine, "in", A, "keep:1", "R", "tmp:2") == ""
    assert _kg(engine, "in", A, "tmp:1", "R", "keep:1") == ""
    assert engine.get_embedding("tmp:1", graph=A) is None
    assert engine.get_embedding("keep:1", graph=A) is not None
    # Graph B keeps all of it.
    assert _count(engine, "Graph_KG.nodes", B, "node_id %STARTSWITH ?", ["tmp:"]) == 2
    assert _count(engine, "Graph_KG.rdf_edges", B) == 3
    assert _neighbours(engine, "keep:1", B) == {"tmp:1"}
    assert engine.get_embedding("tmp:1", graph=B) is not None
    assert _version(engine) > before


def test_prefix_erase_is_exact_not_case_folded(engine):
    engine.create_node("TMP:upper", graph=A)
    engine.create_node("tmp:lower", graph=A)
    engine.conn.commit()
    assert engine.delete_nodes_by_prefix("tmp:", graph=A) == 1
    assert _count(engine, "Graph_KG.nodes", A, "node_id = ?", ["TMP:upper"]) == 1


def test_empty_prefix_is_refused(engine):
    engine.create_node("n1", graph=A)
    engine.conn.commit()
    with pytest.raises(ValueError, match="erase_graph"):
        engine.delete_nodes_by_prefix("", graph=A)
    # And refused by the ObjectScript method itself, for a caller that bypasses Python.
    with pytest.raises(Exception, match="empty prefix"):
        engine._iris_obj().classMethodValue("Graph.KG.Eraser", "EraseNodes", A, "")
    assert _count(engine, "Graph_KG.nodes", A, "node_id = ?", ["n1"]) == 1


def test_ppr_and_neighbours_stop_seeing_deleted_nodes(engine):
    for n in ("s", "m", "t"):
        engine.create_node(n, graph=A)
    engine.create_edge("s", "R", "m", graph=A)
    engine.create_edge("m", "R", "t", graph=A)
    engine.conn.commit()
    before = engine.kg_PERSONALIZED_PAGERANK(["s"], graph=A)
    assert "m" in before and "t" in before

    engine._store.delete_nodes(["m"], graph=A)

    after = engine.kg_PERSONALIZED_PAGERANK(["s"], graph=A)
    assert "m" not in after
    assert "t" not in after, "t is only reachable through the deleted node"
    assert _neighbours(engine, "s", A) == set()


def test_default_graph_delete_leaves_named_graph(engine):
    engine.create_node("d1", graph=None)
    engine.create_node("d1", graph=A)
    engine.conn.commit()
    try:
        assert engine._store.delete_nodes(["d1"]).rows == [[1]]
        assert _count(engine, "Graph_KG.nodes", "", "node_id = ?", ["d1"]) == 0
        assert _count(engine, "Graph_KG.nodes", A, "node_id = ?", ["d1"]) == 1
    finally:
        engine._store.delete_nodes(["d1"], graph="")


def test_sql_fallback_is_scoped_and_chunked_on_live_iris(engine):
    """The path an install without the Eraser classes takes: same scoping, same bound."""
    _seed_pair(engine)
    ids = ["x"] + [f"absent:{i}" for i in range(2400)]
    engine._store._eraser_methods = {"EraseNodeIds": False, "EraseEdges": False}
    try:
        assert engine._store.delete_nodes(ids, graph=A).rows == [[1]]
        assert engine._store.delete_edges([("hub", "R", "x")], graph=B).rows == [[1]]
    finally:
        engine._store._eraser_methods = {}
    assert _count(engine, "Graph_KG.nodes", A, "node_id = ?", ["x"]) == 0
    assert _count(engine, "Graph_KG.rdf_edges", A, "(s = ? OR o_id = ?)", ["x", "x"]) == 0
    assert _count(engine, "Graph_KG.nodes", B, "node_id = ?", ["x"]) == 1
    assert _count(engine, "Graph_KG.rdf_edges", B, "s = ? AND o_id = ?", ["x", "y"]) == 1
    assert _count(engine, "Graph_KG.rdf_edges", B, "s = ? AND o_id = ?", ["hub", "x"]) == 0
