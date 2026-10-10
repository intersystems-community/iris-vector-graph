"""Line coverage for Graph.KG.NKGAccelAdjacency: the NKG export, the large-result store and the
k-hop reader. Scratch namespace IVG236; the sentinel is asserted before every write.

The exports park their chunks in process-private globals, so a case reads them back on
the same Native API connection that produced them.
"""

from __future__ import annotations

import json

import pytest

from tests.integration._236_world import PRED, World

ADJ = "Graph.KG.NKGAccelAdjacency"


def text(value) -> str:
    """The Native API hands back None for an empty string."""
    return "" if value is None else str(value)


@pytest.fixture
def world(ivg_236_scratch):
    w = World(ivg_236_scratch)
    w.native().classMethodValue("Graph.KG.Eraser", "EraseAll")
    w.renew()
    yield w
    native = ivg_236_scratch.native()
    if ivg_236_scratch.sentinel_ok(native):
        native.kill("ArnoKG")
        native.classMethodValue("Graph.KG.Eraser", "EraseAll")


def build(world, edges):
    e = world.engine()
    for s, o in edges:
        e.create_edge(s, PRED, o)
    world.commit()
    world.native().classMethodValue("Graph.KG.Traversal", "BuildNKG")
    world.renew()


def test_export_of_an_empty_index_is_empty(world):
    assert world.native().classMethodValue(ADJ, "ExportAdjacency") in ("", None)


def test_export_chunks_a_built_index_with_a_node_map_first(world):
    build(world, [("a", "b"), ("b", "c")])
    out = str(world.native().classMethodValue(ADJ, "ExportAdjacency"))
    assert out == "CHUNKED:2|NODEMAP"


def test_export_with_predicates_lists_every_edge_with_its_names(world):
    build(world, [("a", "b"), ("b", "c")])
    out = str(world.native().classMethodValue(ADJ, "ExportAdjacencyWithPreds"))
    rows = {tuple(line.split("\x1f")) for line in out.strip("\n").split("\n")}
    assert rows == {("a", PRED, "b"), ("b", PRED, "c")}


def test_export_with_a_size_cap_reports_oversize(world):
    build(world, [("a", "b"), ("b", "c")])
    assert str(world.native().classMethodValue(ADJ, "ExportAdjacencyWithPreds", "", 5)) == "OVERSIZE"


def test_small_results_come_back_unchunked_and_large_ones_in_chunks(world):
    n = world.native()
    assert str(n.classMethodValue(ADJ, "StoreLargeOut", "T1", "short")) == "short"
    big = "x" * 20000
    assert str(n.classMethodValue(ADJ, "StoreLargeOut", "T1", big)) == "CHUNKED:T1:3"
    chunks = [text(n.classMethodValue(ADJ, "ReadLargeOutChunk", "T1", i)) for i in (1, 2, 3, 4)]
    assert [len(c) for c in chunks] == [9000, 9000, 2000, 0]
    assert "".join(chunks) == big


def test_the_bfs_tag_reads_the_arno_result_global(world):
    n = world.native()
    world.scratch.require_scratch(n)
    n.set("payload", "ArnoKG", "bfs_result", 1)
    assert str(n.classMethodValue(ADJ, "ReadLargeOutChunk", "BFS", 1)) == "payload"
    assert text(n.classMethodValue(ADJ, "ReadLargeOutChunk", "BFS", 9)) == ""


def test_khop_results_are_read_ordered_by_distance_then_consumed(world):
    n = world.native()
    world.scratch.require_scratch(n)
    n.set(1, "ArnoKG", "khop_r", "tg", 2, 'q"uote')
    n.set(1, "ArnoKG", "khop_r", "tg", 1, "b")
    n.set(1, "ArnoKG", "khop_r", "tg", 1, "a")
    out = str(n.classMethodValue(ADJ, "ReadKHopResults", "tg"))
    rows = json.loads("[" + out + "]")
    assert rows == [{"id": "a", "dist": 1}, {"id": "b", "dist": 1}, {"id": 'q"uote', "dist": 2}]
    assert text(n.classMethodValue(ADJ, "ReadKHopResults", "tg")) == ""


def test_a_node_with_thousands_of_neighbours_is_split_across_chunks(world):
    e = world.engine()
    for i in range(3200):
        e.create_edge("hub", PRED, f"spoke-with-a-long-enough-name-{i:05d}")
    world.commit()
    world.native().classMethodValue("Graph.KG.Traversal", "BuildNKG")
    world.renew()
    out = str(world.native().classMethodValue(ADJ, "ExportAdjacency"))
    prefix, rest = out.split(":", 1)
    assert prefix == "CHUNKED"
    total, _ = rest.split("|")
    assert int(total) >= 3  # the node map plus at least two pieces of the hub's neighbour list
