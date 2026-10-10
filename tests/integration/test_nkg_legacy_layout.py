"""NKGAccel readers on the legacy no-shard ^KG layout, the branch taken when ^NKG is not built.

BenchFormat.Seed writes that layout (^KG("out", node, pred, dst)), which is also what the
class headers say these fallbacks read. The cases run in scratch namespace IVG236 with
the process-private NKG switch reset, so UseNKG() answers false.
"""

from __future__ import annotations

import json

import pytest

from tests.integration._236_world import World

ADJ = "Graph.KG.NKGAccelAdjacency"
TRAV = "Graph.KG.NKGAccelTraversal"


@pytest.fixture
def world(ivg_236_scratch):
    w = World(ivg_236_scratch)
    n = w.native()
    n.classMethodValue("Graph.KG.Eraser", "EraseAll")
    n.classMethodValue("Graph.KG.BenchFormat", "Seed", 15, 2)
    # a fixed chain on top of the random seed, so the answers below are exact
    for s, d in (("L1", "L2"), ("L2", "L3"), ("L3", "L4")):
        n.set(1, "KG", "out", s, "REL", d)
        n.set(1, "KG", "in", d, "REL", s)
        n.set(1, "KG", "deg", s)
    n.kill("NKG")  # Seed drops the index but can leave its meta node behind
    n.classMethodVoid("Graph.KG.NKGAccelLoader", "ResetCache")
    yield w
    native = ivg_236_scratch.native()
    if ivg_236_scratch.sentinel_ok(native):
        native.classMethodValue("Graph.KG.Eraser", "EraseAll")


def call(world, cls, method, *args):
    native = world.native()
    out = native.classMethodValue(cls, method, *args)
    world.scratch.require_scratch(native)
    return "" if out is None else out


def test_the_nkg_switch_is_off_without_a_built_index(world):
    assert call(world, "Graph.KG.NKGAccelLoader", "UseNKG") == 0


def test_export_reads_the_legacy_layout_and_lists_every_node(world):
    out = str(call(world, ADJ, "ExportAdjacency"))
    header, _, body = out.partition("---\n")
    assert header.startswith("NODEMAP:")
    mapped = dict(line.split("=", 1) for line in header.splitlines()[1:])
    assert {"L1", "L2", "L3", "L4"} <= set(mapped.values())
    ids = {v: k for k, v in mapped.items()}
    adjacency = dict(line.split(":", 1) for line in body.splitlines() if line)
    assert ids["L2"] in adjacency[ids["L1"]].split(",")


def test_k_hop_follows_the_chain_in_the_legacy_layout(world):
    out = json.loads(str(call(world, TRAV, "KHopNeighbors", "L1", 2, 100)))
    found = {(n["id"] if isinstance(n, dict) else n): n.get("hop", n.get("dist")) if isinstance(n, dict) else None
             for n in out["nodes"]}
    assert {"L1", "L2", "L3"} <= set(found) and "L4" not in found


def test_k_hop_stops_at_the_node_budget(world):
    out = json.loads(str(call(world, TRAV, "KHopNeighbors", "L1", 3, 2)))
    assert len(out["nodes"]) <= 2


def test_sorted_k_hop_parks_its_rows_by_tag(world):
    out = str(call(world, TRAV, "KHopNeighborsSorted", "L1", 3, 100))
    prefix, tag, count = out.split(":")[:3]
    assert prefix == "SORTED" and int(count) >= 3
    rows = json.loads("[" + str(call(world, ADJ, "ReadKHopResults", tag)) + "]")
    assert {r["id"]: r["dist"] for r in rows if r["id"].startswith("L")} == {"L1": 0, "L2": 1, "L3": 2, "L4": 3}


def test_sorted_k_hop_budget(world):
    out = str(call(world, TRAV, "KHopNeighborsSorted", "L1", 3, 2))
    assert out.startswith("SORTED:")


def test_personalised_pagerank_runs_on_the_legacy_layout(world):
    out = json.loads(str(call(world, TRAV, "PPRNative", "L1", 0.85, 5)))
    assert out["seed"] == "L1" and out["scores"]
    assert out["scores"][0]["id"] == "L1"
    assert out["alpha"] == pytest.approx(0.85)


def test_ppr_top_k_cuts_the_list(world):
    full = json.loads(str(call(world, TRAV, "PPRNative", "L1", 0.85, 5, 100)))["scores"]
    assert len(full) == 3
    cut = json.loads(str(call(world, TRAV, "PPRNative", "L1", 0.85, 5, 2)))["scores"]
    assert len(cut) == 2, "topK was ignored"
    assert [s["id"] for s in cut] == [s["id"] for s in full[:2]]
