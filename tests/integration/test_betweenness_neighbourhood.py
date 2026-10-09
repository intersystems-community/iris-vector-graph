"""NKGAccelCentrality.BetweennessNeighborhood: the argument errors and the ObjectScript fallback.

Scratch namespace IVG236, where the Arno library is not loaded, so the neighbourhood is
scored by the $BIT-adjacency Brandes workers rather than the Rust reader. A path graph
a-b-c-d-e: the middle node lies on every shortest path between the two halves.
"""

from __future__ import annotations

import json

import pytest

from tests.integration._236_world import PRED, World

CEN = "Graph.KG.NKGAccelCentrality"


@pytest.fixture
def world(ivg_236_scratch):
    w = World(ivg_236_scratch)
    w.native().classMethodValue("Graph.KG.Eraser", "EraseAll")
    w.renew()
    yield w
    native = ivg_236_scratch.native()
    if ivg_236_scratch.sentinel_ok(native):
        native.classMethodValue("Graph.KG.Eraser", "EraseAll")


def build(world, edges):
    e = world.engine()
    for s, o in edges:
        e.create_edge(s, PRED, o)
    world.commit()
    world.native().classMethodValue("Graph.KG.Traversal", "BuildNKG")
    world.renew()


def betweenness(world, *args):
    native = world.native()
    out = str(native.classMethodValue(CEN, "BetweennessNeighborhood", *args))
    world.scratch.require_scratch(native)
    return out


def test_without_an_nkg_the_method_says_so(world):
    assert betweenness(world, "a") == "ERROR:^NKG not built"


def test_an_unknown_seed_is_named_in_the_error(world):
    build(world, [("a", "b"), ("b", "c")])
    assert betweenness(world, "ghost") == "ERROR:seed node not found: ghost"


def test_the_middle_of_a_path_scores_highest(world):
    build(world, [("a", "b"), ("b", "c"), ("c", "d"), ("d", "e")])
    out = betweenness(world, "a", 4, 100, 10)  # the walk follows out-edges, so seed at one end
    assert out.startswith("OK:"), out
    scores = {r["id"]: r["score"] for r in json.loads(out[3:])}
    assert scores["c"] == max(scores.values())
    assert "a" not in scores and "e" not in scores  # the ends lie on no shortest path between others
    assert scores["b"] == scores["d"] < scores["c"]


def test_top_k_and_a_sample_smaller_than_the_neighbourhood(world):
    build(world, [("a", "b"), ("b", "c"), ("c", "d"), ("d", "e")])
    out = betweenness(world, "a", 4, 2, 1)
    assert out.startswith("OK:"), out
    assert len(json.loads(out[3:])) <= 1
