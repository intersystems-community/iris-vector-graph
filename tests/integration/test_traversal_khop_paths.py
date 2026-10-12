"""Line coverage for Graph.KG.TraversalKHop and Graph.KG.TraversalPaths over ^KG.

Scratch namespace IVG236 (sentinel asserted before every write). Graph, all predicate T
unless noted:  a->b, a->c, b->c, b->e, c->d, and a-U->z.  The edge a->c carries weight 5
and the others weight 1, so Dijkstra prefers a,b,c (cost 2) to the direct hop.
"""

from __future__ import annotations

import json

import pytest

from tests.integration._236_world import PRED, World

KH = "Graph.KG.TraversalKHop"
TP = "Graph.KG.TraversalPaths"
EDGES = [("a", "b"), ("a", "c"), ("b", "c"), ("b", "e"), ("c", "d")]


@pytest.fixture
def world(ivg_236_scratch):
    w = World(ivg_236_scratch)
    w.native().classMethodValue("Graph.KG.Eraser", "EraseAll")
    w.renew()
    e = w.engine()
    for s, o in EDGES:
        e.create_edge(s, PRED, o)
    e.create_edge("a", "U", "z")
    w.commit()
    n = w.native()
    for s, o, weight in [("a", "c", 5)] + [(s, o, 1) for s, o in EDGES if (s, o) != ("a", "c")]:
        n.set(weight, "KG", "out", 0, s, PRED, o)
        n.set(weight, "KG", "in", 0, o, PRED, s)
    yield w
    native = ivg_236_scratch.native()
    if ivg_236_scratch.sentinel_ok(native):
        native.classMethodValue("Graph.KG.Eraser", "EraseAll")


def call(world, cls, method, *args):
    out = world.native().classMethodValue(cls, method, *args)
    return "" if out is None else out


# ------------------------------------------------------------------------- k-hop


def test_khop_count_counts_the_out_entries(world):
    """There is no stored degree to read or to fall back from (spec 249): the count is the `out` entries."""
    assert call(world, KH, "KHopCount", "a") == 3
    assert call(world, KH, "KHopCount", "a", PRED) == 2
    assert call(world, KH, "KHopCount", "nobody") == 0


def test_khop_neighbour_ids_are_newline_separated_by_predicate_then_id(world):
    assert call(world, KH, "KHopNeighborIds", "a") == "b\nc\nz\n"
    assert call(world, KH, "KHopNeighborIds", "a", PRED) == "b\nc\n"
    assert call(world, KH, "KHopNeighborIds", "nobody") == ""


def test_two_hop_count_excludes_the_source_and_first_hop(world):
    assert call(world, KH, "KHop2Count", "a") == 2  # e and d
    assert call(world, KH, "KHop2Count", "a", PRED) == 2
    assert call(world, KH, "KHop2Count", "nobody") == 0


def test_two_hop_ids_and_the_result_cap(world):
    assert call(world, KH, "KHop2NeighborIds", "a") == "e\nd\n"
    assert call(world, KH, "KHop2NeighborIds", "a", PRED) == "e\nd\n"
    assert call(world, KH, "KHop2NeighborIds", "a", "", 1) == "e\n"
    assert call(world, KH, "KHop2NeighborIds", "a", PRED, 1) == "e\n"


def test_fast_and_exact_counts_prefer_the_stored_value_then_fall_back(world):
    assert call(world, KH, "KHop2CountFast", "a", PRED) == 2
    assert call(world, KH, "KHop2CountExact", "a", PRED) == 2
    n = world.native()
    n.set(42, "KG", "deg2p", 0, "a", PRED)
    n.set(7, "KG", "deg2p_exact", 0, "a", PRED)
    assert call(world, KH, "KHop2CountFast", "a", PRED) == 42
    assert call(world, KH, "KHop2CountExact", "a", PRED) == 7
    # without a predicate there is no stored value to read
    assert call(world, KH, "KHop2CountFast", "a") == 2
    assert call(world, KH, "KHop2CountExact", "a") == 2


# ------------------------------------------------------------------- shortest path


def path(world, *args):
    return json.loads(call(world, TP, "ShortestPathJson", *args))


def test_shortest_path_takes_the_fewest_hops(world):
    assert path(world, "a", "d") == [{"nodes": ["a", "c", "d"], "rels": [PRED, PRED], "length": 2}]


def test_shortest_path_of_a_node_to_itself_has_length_zero(world):
    assert path(world, "a", "a") == [{"nodes": ["a"], "rels": [], "length": 0}]


@pytest.mark.parametrize("src,dst", [("", "d"), ("a", ""), ("nobody", "d"), ("d", "nobody")])
def test_shortest_path_with_missing_or_unknown_ends_is_empty(world, src, dst):
    assert path(world, src, dst) == []


def test_shortest_path_respects_the_hop_budget(world):
    assert path(world, "a", "d", 1) == []
    assert len(path(world, "a", "d", 2)) == 1


def test_shortest_path_direction(world):
    assert path(world, "d", "a", 5, "", "out") == []
    both = path(world, "d", "a", 5, "", "both")
    assert [p["nodes"] for p in both] == [["d", "c", "a"]]
    assert both[0]["length"] == 2


def test_shortest_path_predicate_filter(world):
    assert path(world, "a", "z", 3, json.dumps(["U"]))[0]["nodes"] == ["a", "z"]
    assert path(world, "a", "z", 3, json.dumps([PRED])) == []
    # an empty, "[]" or "null" filter means no filter
    for f in ("[]", "null"):
        assert path(world, "a", "z", 3, f)[0]["nodes"] == ["a", "z"]


# ---------------------------------------------------------------------- Dijkstra


def dijkstra(world, *args):
    return json.loads(call(world, TP, "DijkstraJson", *args))


def test_dijkstra_prefers_the_cheaper_longer_route(world):
    r = dijkstra(world, "a", "c")
    assert r["nodes"] == ["a", "b", "c"]
    assert r["rels"] == [PRED, PRED]
    assert r["costs"] == [1, 1]
    assert r["totalCost"] == 2 and r["length"] == 2


def test_dijkstra_same_node_and_missing_ends(world):
    assert dijkstra(world, "a", "a")["totalCost"] == 0
    assert dijkstra(world, "", "c") == {}
    assert dijkstra(world, "a", "") == {}


def test_dijkstra_cost_ceiling_and_unreachable(world):
    assert dijkstra(world, "a", "d", "", 1) == {}
    assert dijkstra(world, "d", "a") == {}  # out-edges only
    assert dijkstra(world, "a", "nobody") == {}


def test_dijkstra_both_directions_walks_against_the_edges(world):
    r = dijkstra(world, "d", "a", "", 9999, 10, "both")
    assert r["nodes"][0] == "d" and r["nodes"][-1] == "a"
    assert r["totalCost"] == pytest.approx(sum(r["costs"]))


def test_dijkstra_treats_a_non_positive_weight_as_one(world):
    world.native().set(0, "KG", "out", 0, "a", PRED, "b")
    r = dijkstra(world, "a", "b")
    assert r["costs"] == [1]
