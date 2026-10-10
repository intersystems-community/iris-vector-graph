"""Line coverage for Graph.KG.TraversalBFS: the JSON, chunked and paged result forms.

Scratch namespace IVG236 (sentinel asserted before every write). Graph, predicate T
unless noted:  a->b, a->c, b->c, b->e, c->d, a-U->z, and x->y in the named graph g1.
Chunked and sorted results are parked in ^ArnoKG, so a case reads them back by tag.
"""

from __future__ import annotations

import json

import pytest

from tests.integration._236_world import PRED, World

BFS = "Graph.KG.TraversalBFS"
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
    e.create_edge("x", PRED, "y", graph="g1")
    w.commit()
    yield w
    native = ivg_236_scratch.native()
    if ivg_236_scratch.sentinel_ok(native):
        native.kill("ArnoKG")
        native.classMethodValue("Graph.KG.Eraser", "EraseAll")


def call(world, method, *args):
    out = world.native().classMethodValue(BFS, method, *args)
    return "" if out is None else out


def rows(world, method="BFSFastJson", *args):
    return json.loads(call(world, method, *args))


def edges(result):
    return {(r["s"], r["p"], r["o"], r["step"]) for r in result}


# ------------------------------------------------------------------- BFSFastJson


def test_one_hop_lists_every_out_edge_with_its_weight(world):
    r = rows(world, "BFSFastJson", "a", "", 1)
    assert edges(r) == {("a", PRED, "b", 1), ("a", PRED, "c", 1), ("a", "U", "z", 1)}
    assert all(x["w"] == 1 for x in r)


def test_two_hops_reach_the_second_ring(world):
    got = edges(rows(world, "BFSFastJson", "a", "", 2))
    assert ("b", PRED, "e", 2) in got and ("c", PRED, "d", 2) in got
    assert ("b", PRED, "c", 2) in got  # an edge into an already-seen node is still a result


def test_empty_source_or_zero_hops_give_no_rows(world):
    assert rows(world, "BFSFastJson", "", "", 2) == []
    assert rows(world, "BFSFastJson", "a", "", 0) == []
    assert rows(world, "BFSFastJson", "nobody", "", 2) == []


def test_predicate_filter_and_bad_filters(world):
    only_t = rows(world, "BFSFastJson", "a", json.dumps([PRED]), 1)
    assert {r["o"] for r in only_t} == {"b", "c"}
    for unusable in ("null", "{not json"):
        assert {r["o"] for r in rows(world, "BFSFastJson", "a", unusable, 1)} == {"b", "c", "z"}


def test_last_predicate_is_reused_for_later_hops_and_prunes_dead_ends(world):
    # ["T", "T"]: hop 1 looks ahead; a node with no outgoing T edge is not expanded further
    got = edges(rows(world, "BFSFastJson", "a", json.dumps([PRED, PRED]), 2))
    assert ("c", PRED, "d", 2) in got and all(e[1] == PRED for e in got)


def test_direction_in_and_both(world):
    assert {r["s"] for r in rows(world, "BFSFastJson", "d", "", 1, "", "in")} == {"d"}
    assert {r["o"] for r in rows(world, "BFSFastJson", "d", "", 1, "", "in")} == {"c"}
    both = rows(world, "BFSFastJson", "c", "", 1, "", "both")
    assert {r["o"] for r in both} == {"d", "a", "b"}
    inbound = rows(world, "BFSFastJson", "c", json.dumps([PRED]), 2, "", "in")
    assert ("c", PRED, "b", 1) in edges(inbound)


def test_destination_label_filter(world):
    world.native().set(1, "KG", "label", 0, "Hub", "c")
    assert {r["o"] for r in rows(world, "BFSFastJson", "a", "", 1, "Hub")} == {"c"}
    assert rows(world, "BFSFastJson", "a", "", 1, "NoSuchLabel") == []


def test_a_named_graph_is_walked_on_its_own(world):
    assert edges(rows(world, "BFSFastJson", "x", "", 1, "", "out", "g1")) == {("x", PRED, "y", 1)}
    assert rows(world, "BFSFastJson", "x", "", 1) == []


def test_direct_form_matches_the_dynamic_object_form(world):
    for hops in (1, 2, 3):
        assert rows(world, "BFSFastJsonDirect", "a", "", hops) == rows(world, "BFSFastJson", "a", "", hops)
    assert rows(world, "BFSFastJsonDirect", "nobody", "", 2) == []
    assert rows(world, "BFSFastJsonDirect", "a", "{bad", 1) == rows(world, "BFSFastJson", "a", "", 1)


def test_count_distinct_counts_targets_not_edges(world):
    assert call(world, "BFSFastCountDistinct", "a", "", 2) == 5  # b c z e d
    assert call(world, "BFSFastCountDistinct", "a", "bad json", 1) == 3
    assert call(world, "BFSFastCountDistinct", "nobody", "", 2) == 0


# --------------------------------------------------------------- chunked results


def test_chunked_form_parks_rows_by_tag_and_reads_them_back_once(world):
    head = str(call(world, "BFSFastJsonChunked", "a", "", 2))
    prefix, tag, n = head.split(":")
    assert prefix == "CHUNKED" and int(n) >= 1
    body = "".join(str(call(world, "ReadBFSChunk", tag, i)) for i in range(1, int(n) + 1))
    assert edges(json.loads("[" + body + "]")) == edges(rows(world, "BFSFastJson", "a", "", 2))
    assert call(world, "ReadBFSChunk", tag, 1) == ""  # consumed


def test_chunked_form_without_rows_and_with_a_cap(world):
    assert call(world, "BFSFastJsonChunked", "nobody", "", 2) == "CHUNKED:0"
    _, tag, n = str(call(world, "BFSFastJsonChunked", "a", "", 2, "", "out", 2)).split(":")
    body = "".join(str(call(world, "ReadBFSChunk", tag, i)) for i in range(1, int(n) + 1))
    assert len(json.loads("[" + body + "]")) == 2


def test_chunked_form_splits_a_large_result(world):
    e = world.engine()
    for i in range(400):
        e.create_edge("hub", PRED, f"leaf-with-a-rather-long-name-{i:04d}")
    world.commit()
    _, tag, n = str(call(world, "BFSFastJsonChunked", "hub", "", 1)).split(":")
    assert int(n) >= 2
    body = "".join(str(call(world, "ReadBFSChunk", tag, i)) for i in range(1, int(n) + 1))
    assert len(json.loads("[" + body + "]")) == 400


# ----------------------------------------------------------------- sorted results


def test_sorted_form_reads_back_ordered_by_step_then_target(world):
    head = str(call(world, "BFSFastJsonSorted", "a", "", 2))
    prefix, tag, count = head.split(":")
    assert prefix == "SORTED" and int(count) == len(rows(world, "BFSFastJson", "a", "", 2))
    got = json.loads(str(call(world, "ReadBFSResults", tag)))
    assert [(r["step"], r["o"]) for r in got] == sorted((r["step"], r["o"]) for r in got)
    assert json.loads(str(call(world, "ReadBFSResults", tag))) == []  # consumed


def test_sorted_form_without_rows_and_with_a_cap(world):
    assert call(world, "BFSFastJsonSorted", "nobody", "", 2) == "SORTED:0"
    _, tag, count = str(call(world, "BFSFastJsonSorted", "a", "", 2, "", "out", 2)).split(":")
    assert int(count) == 2
    assert len(json.loads(str(call(world, "ReadBFSResults", tag)))) == 2


def test_paging_walks_the_sorted_result_in_order_and_finishes(world):
    _, tag, count = str(call(world, "BFSFastJsonSorted", "a", "", 2)).split(":")
    seen, step, o, pages = [], "", "", 0
    while True:
        page = json.loads(str(call(world, "ReadBFSPage", tag, step, o, 2)))
        pages += 1
        seen += [(r["step"], r["o"]) for r in page["items"]]
        if page["done"]:
            break
        step, o = str(page["next_step"]), page["next_o"]
    assert len(seen) == int(count) and seen == sorted(seen)
    assert pages >= 2


def test_paging_an_unknown_tag_is_an_empty_final_page(world):
    page = json.loads(str(call(world, "ReadBFSPage", "no-such-tag", "", "")))
    assert page == {"items": [], "next_step": -1, "next_o": "", "done": True}


# -------------------------------------------------------------- the object forms


def objects(world, method, *args):
    out = world.native().classMethodObject(BFS, method, *args)
    return json.loads(str(out.invoke("%ToJSON")))


def test_object_form_numbers_each_path(world):
    r = objects(world, "BFS", "a", "", 2)
    assert [x["id"] for x in r] == list(range(1, len(r) + 1))
    assert {x["o"] for x in r if x["step"] == 1} == {"b", "c", "z"}
    assert objects(world, "BFSJSON", "a", "", 1) == objects(world, "BFS", "a", "", 1)


def test_object_form_label_filter_and_empty_inputs(world):
    world.native().set(1, "KG", "label", 0, "Hub", "c")
    assert {x["o"] for x in objects(world, "BFS", "a", "", 1, "Hub")} == {"c"}
    assert objects(world, "BFS", "", "", 2) == []
    assert objects(world, "BFS", "a", "", 0) == []
