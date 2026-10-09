"""Line coverage for Graph.KG.Communities.LeidenJson, the ObjectScript local-moving pass.

Two triangles {a,b,c} and {x,y,z} joined by the single bridge c-x, in the scratch
namespace IVG236 (sentinel asserted before every write). The call needs ^NKG, so each
case builds it first; without it the method answers a labelled error, not an empty list.
"""

from __future__ import annotations

import json

import pytest

from tests.integration._236_world import PRED, World

TRIANGLES = [("a", "b"), ("b", "c"), ("c", "a"), ("x", "y"), ("y", "z"), ("z", "x"), ("c", "x")]


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


def leiden(world, *args):
    out = str(world.native().classMethodValue("Graph.KG.Communities", "LeidenJson", *args))
    assert out.startswith("OK:"), out
    return json.loads(out[3:])


def test_without_nkg_the_method_says_so(world):
    out = str(world.native().classMethodValue("Graph.KG.Communities", "LeidenJson"))
    assert out == "ERROR:^NKG not built"


def test_two_triangles_split_into_two_communities(world):
    build(world, TRIANGLES)
    rows = leiden(world)
    comm = {r["id"]: r["community"] for r in rows}
    assert set(comm) == {"a", "b", "c", "x", "y", "z"}
    assert comm["a"] == comm["b"] and comm["x"] == comm["y"] == comm["z"]
    assert comm["a"] != comm["x"]
    # community ids are dense and ordered by size, largest first
    assert sorted(set(comm.values())) == list(range(len(set(comm.values()))))
    sizes = {c: sum(1 for v in comm.values() if v == c) for c in set(comm.values())}
    assert all(r["size"] == sizes[r["community"]] for r in rows)


def test_a_low_gamma_merges_and_a_high_gamma_splits(world):
    build(world, TRIANGLES)
    merged = {r["community"] for r in leiden(world, 10, 0.0)}
    split = {r["community"] for r in leiden(world, 10, 50.0)}
    assert len(merged) <= len(split)
    assert len(split) >= 2


def test_max_levels_one_still_assigns_every_node(world):
    build(world, TRIANGLES)
    rows = leiden(world, 1)
    assert {r["id"] for r in rows} == {"a", "b", "c", "x", "y", "z"}


def test_top_k_stops_after_the_community_that_fills_it(world):
    build(world, TRIANGLES)
    rows = leiden(world, 10, 1.0, 0.0001, 3)
    assert 3 <= len(rows) < 6
    assert {r["community"] for r in rows} == {0}


def test_auto_falls_back_to_the_objectscript_pass_without_python_packages(world):
    build(world, TRIANGLES)
    out = str(world.native().classMethodValue("Graph.KG.Communities", "LeidenJsonAuto"))
    assert out != "PYUNAVAIL"
    assert out.startswith(("OK:", "[", "{"))
