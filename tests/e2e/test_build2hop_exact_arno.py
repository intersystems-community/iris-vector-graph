"""NKGAccelTraversal.Build2HopExact with the Arno library: the exact two-hop sketch over ^NKG.

The library's readers walk USER's ^KG, so this runs in USER, on the enterprise container where the
library is installed (IVG_REQUIRE_ARNO=1 makes its absence a failure instead of a skip). The graph is
erased before and after, as the Arno reader gate does.
"""

from __future__ import annotations

import os

import pytest

from iris_vector_graph.engine import IRISGraphEngine

REQUIRE = os.environ.get("IVG_REQUIRE_ARNO") == "1"


@pytest.fixture
def arno_graph(iris_connection):
    eng = IRISGraphEngine(iris_connection, embedding_dimension=768)
    obj = eng._store._iris_obj()
    lib = obj.get("ArnoAccel", "lib_path")
    if not lib or not obj.classMethodValue("%File", "Exists", lib):
        if REQUIRE:
            pytest.fail("the Arno library is not installed on this container")
        pytest.skip("the Arno library is not installed on this container")
    obj.classMethodValue("Graph.KG.Eraser", "EraseAll")
    obj.classMethodValue("Graph.KG.ArnoAccel", "Load", lib)
    obj.classMethodValue("Graph.KG.NKGAccelLoader", "Load", lib)
    for s, o in (("a", "b"), ("b", "c"), ("b", "d"), ("c", "e"), ("d", "e")):
        eng.create_edge(s, "T", o)
    iris_connection.commit()
    obj.classMethodValue("Graph.KG.Traversal", "BuildKG")
    yield eng, obj
    obj.classMethodValue("Graph.KG.Eraser", "EraseAll")


def test_the_exact_two_hop_sketch_is_written_per_source_and_predicate(arno_graph):
    _, obj = arno_graph
    assert int(obj.classMethodValue("Graph.KG.NKGAccelTraversal", "Build2HopExact")) == 1
    # a reaches c and d in two hops; b reaches e (via c and d: counted once); c and d reach nothing further
    assert int(obj.get("KG", "deg2p_exact_merged", "a", "T")) == 2
    assert int(obj.get("KG", "deg2p_exact_merged", "b", "T")) == 1
    assert obj.get("KG", "deg2p_exact_merged", "e", "T") is None


def test_a_rebuild_replaces_the_previous_sketch(arno_graph):
    eng, obj = arno_graph
    assert int(obj.classMethodValue("Graph.KG.NKGAccelTraversal", "Build2HopExact")) == 1
    obj.set(99, "KG", "deg2p_exact_merged", "stale", "T")
    assert int(obj.classMethodValue("Graph.KG.NKGAccelTraversal", "Build2HopExact")) == 1
    assert obj.get("KG", "deg2p_exact_merged", "stale", "T") is None


# ------------------------------------------------------------- the other library-backed readers

EDGES = {("a", "b"), ("b", "c"), ("b", "d"), ("c", "e"), ("d", "e")}


def test_khop_sample_returns_the_edges_of_each_layer(arno_graph):
    import json

    _, obj = arno_graph
    out = json.loads(str(obj.classMethodValue("Graph.KG.ArnoAccel", "KhopSampleJson", '["a"]', 2, 5)))
    assert out["seeds"] == ["a"]
    layers = [{(e["src"], e["dst"]) for e in layer} for layer in out["layers"]]
    assert layers == [{("a", "b")}, {("b", "c"), ("b", "d")}]


def test_neighbour_aggregation_reports_degree_per_seed(arno_graph):
    import json

    _, obj = arno_graph
    out = json.loads(str(obj.classMethodValue("Graph.KG.ArnoAccel", "NeighborAggJson", '["a"]', 2)))
    assert out["a"]["degree"] == 1 and "agg_feat" in out["a"]


def test_random_walks_follow_edges_from_the_seed(arno_graph):
    import json

    _, obj = arno_graph
    walks = json.loads(str(obj.classMethodValue("Graph.KG.ArnoAccel", "RandomWalkJson", '["a"]', 5, 4)))
    assert walks and all(w[0] == "a" for w in walks)
    for walk in walks:
        assert all((u, v) in EDGES for u, v in zip(walk, walk[1:])), walk


def test_native_bfs_answers_in_the_sorted_form_and_the_rows_read_back(arno_graph):
    import json

    _, obj = arno_graph
    out = str(obj.classMethodValue("Graph.KG.NKGAccelTraversal", "BFSJson", "a", "[]", 3, 0))
    assert out.startswith("SORTED:")
    parts = out.split(":")
    if len(parts) == 3:
        rows = json.loads(str(obj.classMethodValue("Graph.KG.Traversal", "ReadBFSResults", parts[1])))
        assert {r["o"] for r in rows} >= {"b", "c", "d"}


def test_the_distinct_count_sketch_is_close_to_the_true_count(arno_graph):
    import json

    _, obj = arno_graph
    out = json.loads(str(obj.classMethodValue("Graph.KG.NKGAccelTraversal", "CountDistinctKHop", "a", "", 2, "both")))
    assert out["registers"] == 256 and abs(out["estimate"] - 3) <= 1
