"""NKGAccelAdjacency.ExportAdjacencyFromSeed, a private method nothing in iris_src calls.

It is exercised through a throwaway subclass compiled into the scratch namespace IVG236,
which is the only way to reach a Private class method. Chain a->b->c->d plus x-U->a.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from tests.integration._236_world import PRED, World

CONTAINER = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")
PROBE = """Class IVG236Tmp.AdjProbe Extends Graph.KG.NKGAccelAdjacency
{

ClassMethod Probe(pSeed As %String, pHops As %Integer, pPreds As %String) As %String
{
    Quit ..ExportAdjacencyFromSeed(pSeed, pHops, pPreds)
}

}
"""


@pytest.fixture(scope="module")
def probe_class(tmp_path_factory):
    path = tmp_path_factory.mktemp("probe") / "AdjProbe.cls"
    path.write_text(PROBE)
    subprocess.run(["docker", "cp", str(path), f"{CONTAINER}:/tmp/AdjProbe.cls"], check=True, capture_output=True)
    out = subprocess.run(
        ["docker", "exec", "-i", CONTAINER, "iris", "session", "IRIS", "-U", "IVG236"],
        input='write $system.OBJ.Load("/tmp/AdjProbe.cls","ck"),!\nhalt\n',
        capture_output=True, text=True, timeout=120,
    ).stdout
    assert "Load finished successfully" in out, out


@pytest.fixture
def world(ivg_236_scratch, probe_class):
    w = World(ivg_236_scratch)
    w.native().classMethodValue("Graph.KG.Eraser", "EraseAll")
    w.renew()
    e = w.engine()
    for s, o in (("a", "b"), ("b", "c"), ("c", "d")):
        e.create_edge(s, PRED, o)
    e.create_edge("x", "U", "a")
    w.commit()
    w.native().classMethodValue("Graph.KG.Traversal", "BuildNKG")
    w.renew()
    yield w
    native = ivg_236_scratch.native()
    if ivg_236_scratch.sentinel_ok(native):
        native.classMethodValue("Graph.KG.Eraser", "EraseAll")


def probe(world, seed, hops=2, preds="[]"):
    native = world.native()
    out = native.classMethodValue("IVG236Tmp.AdjProbe", "Probe", seed, hops, preds)
    world.scratch.require_scratch(native)
    out = "" if out is None else str(out)
    return {tuple(line.split("\x1f")) for line in out.split("\n") if line}


def test_two_hops_collect_the_nodes_in_reach_and_the_edges_among_them(world):
    got = probe(world, "a", 2)
    # inbound traversal reaches x, outbound b then c; d is three hops away
    assert (("a", PRED, "b")) in got and (("b", PRED, "c")) in got and ("x", "U", "a") in got
    assert not any("d" in edge for edge in got)


def test_more_hops_reach_further(world):
    assert ("c", PRED, "d") in probe(world, "a", 3)


def test_a_predicate_filter_limits_the_outbound_walk(world):
    got = probe(world, "a", 3, '["U"]')
    assert ("a", PRED, "b") not in got and ("c", PRED, "d") not in got


def test_an_unknown_seed_or_a_bad_filter(world):
    assert probe(world, "ghost") == set()
    assert ("a", PRED, "b") in probe(world, "a", 1, "{not json")
