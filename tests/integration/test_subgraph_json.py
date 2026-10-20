"""Line coverage for Graph.KG.Subgraph: SubgraphJson and PPRGuidedJson over ^KG.

Runs in the scratch namespace IVG236 on the baseline chain a -T-> b -T-> c -T-> d, plus
a -U-> z so an edge-type filter has something to exclude. The sentinel is asserted
before every write (research R3: a sync can flip the handle to USER).
"""

from __future__ import annotations

import json

import pytest

from tests.integration._236_world import PRED, World

EMPTY_SUB = {"nodes": [], "edges": [], "properties": {}, "labels": {}}


@pytest.fixture
def world(ivg_236_scratch):
    w = World(ivg_236_scratch)
    w.reset()
    e = w.engine()
    e.create_edge("a", "U", "z")
    w.commit()
    yield w
    native = ivg_236_scratch.native()
    if ivg_236_scratch.sentinel_ok(native):
        native.classMethodValue("Graph.KG.Eraser", "EraseAll")


def sub(world, seeds, hops=2, types="", max_nodes=10000):
    raw = world.native().classMethodValue(
        "Graph.KG.Subgraph", "SubgraphJson", seeds, hops, types, max_nodes
    )
    return json.loads(str(raw))


def guided(world, seeds, *args):
    raw = world.native().classMethodValue("Graph.KG.Subgraph", "PPRGuidedJson", seeds, *args)
    return json.loads(str(raw))


def edge_set(result, s="s", p="p", o="o"):
    return {(e[s], e[p], e[o]) for e in result["edges"]}


# --------------------------------------------------------------------- SubgraphJson


@pytest.mark.parametrize("seeds", ["", "not json", "[]", '["nobody"]'])
def test_empty_or_unusable_seeds_give_an_empty_subgraph(world, seeds):
    assert sub(world, seeds) == EMPTY_SUB


def test_one_hop_reaches_the_direct_neighbours_only(world):
    r = sub(world, '["a"]', hops=1)
    assert r["nodes"] == ["a", "b", "z"]
    assert edge_set(r) == {("a", PRED, "b"), ("a", "U", "z")}


def test_two_hops_follow_the_chain(world):
    r = sub(world, '["a"]', hops=2)
    assert r["nodes"] == ["a", "b", "c", "z"]
    assert ("b", PRED, "c") in edge_set(r)
    assert "d" not in r["nodes"]


def test_the_walk_stops_early_when_nothing_new_is_found(world):
    assert sub(world, '["a"]', hops=50)["nodes"] == ["a", "b", "c", "d", "z"]


def test_an_edge_type_filter_excludes_other_predicates(world):
    r = sub(world, '["a"]', hops=3, types=json.dumps([PRED]))
    assert "z" not in r["nodes"] and ("a", "U", "z") not in edge_set(r)
    assert r["nodes"] == ["a", "b", "c", "d"]
    # an empty or unparseable filter means no filter
    assert "z" in sub(world, '["a"]', hops=1, types="[]")["nodes"]
    assert "z" in sub(world, '["a"]', hops=1, types="oops")["nodes"]


def test_max_nodes_caps_the_walk(world):
    r = sub(world, '["a"]', hops=5, max_nodes=2)
    assert len(r["nodes"]) == 2 and r["nodes"][0] == "a"


def test_a_seed_with_only_incoming_edges_is_accepted(world):
    r = sub(world, '["d"]', hops=1)
    assert r["nodes"] == ["d"] and r["edges"] == []


def test_properties_and_labels_ride_along(world):
    # spec 252 T024: properties are read from rdf_props, not a ^KG("prop") mirror
    cur = world.engine().conn.cursor()
    cur.execute("INSERT INTO Graph_KG.rdf_props (s, \"key\", val) VALUES ('a', 'color', 'v1')")
    cur.execute("INSERT INTO Graph_KG.rdf_labels (s, label) VALUES ('b', 'Person')")
    world.commit()
    r = sub(world, '["a"]', hops=1)
    assert r["properties"] == {"a": {"color": "v1"}}
    assert r["labels"].get("b") == ["Person"]
    assert "a" not in r["labels"]


# ------------------------------------------------------------------- PPRGuidedJson

EMPTY_GUIDED = {
    "nodes": [],
    "edges": [],
    "ppr_scores": [],
    "seed_ids": [],
    "nodes_before_pruning": 0,
    "nodes_after_pruning": 0,
}


@pytest.mark.parametrize("seeds", ["", "garbage", "[]"])
def test_guided_unusable_seeds_give_the_empty_shape(world, seeds):
    assert guided(world, seeds) == EMPTY_GUIDED


def test_guided_scores_rank_the_seed_first_and_decay_down_the_chain(world):
    r = guided(world, '["a"]')
    assert r["seed_ids"] == ["a"]
    scores = [(s["id"], s["score"]) for s in r["ppr_scores"]]
    assert scores[0][0] == "a"
    assert [v for _, v in scores] == sorted((v for _, v in scores), reverse=True)
    assert r["nodes_before_pruning"] >= r["nodes_after_pruning"] >= 1
    assert set(r["nodes"]) >= {"a", "b"}
    assert all({"src", "type", "dst"} <= set(e) for e in r["edges"])


def test_guided_top_k_prunes_before_the_walk(world):
    r = guided(world, '["a"]', 0.15, 0.00001, 2, 5)
    assert r["nodes_after_pruning"] == 2
    assert len(r["ppr_scores"]) == 2
    assert r["nodes_before_pruning"] > 2


def test_guided_eps_drops_low_scoring_nodes(world):
    loose = guided(world, '["a"]', 0.15, 0.00001, 50, 5)
    tight = guided(world, '["a"]', 0.15, 0.9, 50, 5)
    assert tight["nodes_after_pruning"] < loose["nodes_after_pruning"]


def test_guided_edge_type_filter(world):
    r = guided(world, '["a"]', 0.15, 0.00001, 50, 5, json.dumps([PRED]))
    assert all(e["type"] == PRED for e in r["edges"])
    assert guided(world, '["a"]', 0.15, 0.00001, 50, 5, "oops")["edges"]


def test_guided_with_several_seeds_and_a_dangling_node(world):
    r = guided(world, '["a", "d"]')
    assert r["seed_ids"] == ["a", "d"]
    ids = {s["id"] for s in r["ppr_scores"]}
    assert {"a", "d"} <= ids
