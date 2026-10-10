"""Line coverage for Graph.KG.BenchFormat, the adjacency-format benchmark helper.

It seeds a legacy-layout ^KG, so it runs only in the scratch namespace IVG236 and
Compare() kills ^KG when it finishes; the sentinel is asserted before each call.
"""

from __future__ import annotations

import re

import pytest

from tests.integration._236_world import World

BF = "Graph.KG.BenchFormat"


@pytest.fixture
def world(ivg_236_scratch):
    w = World(ivg_236_scratch)
    w.native().classMethodValue("Graph.KG.Eraser", "EraseAll")
    w.renew()
    yield w
    native = ivg_236_scratch.native()
    if ivg_236_scratch.sentinel_ok(native):
        native.classMethodValue("Graph.KG.Eraser", "EraseAll")
        native.kill("ArnoKG")


def call(world, method, *args):
    native = world.native()
    out = native.classMethodValue(BF, method, *args)
    world.scratch.require_scratch(native)
    return "" if out is None else out


def test_the_tsv_has_one_line_per_seeded_edge(world):
    call(world, "Seed", 20, 2)
    tsv = str(call(world, "BuildAdjacencyTSV"))
    lines = [line for line in tsv.split("\n") if line]
    assert lines and all(line.count("\t") == 1 for line in lines)
    walk = str(call(world, "TimeOrderWalk"))
    assert walk.endswith(f"({len(lines)} edges)")
    assert re.match(r"^[\d.,]+ms \(\d+ edges\)$", walk)


def test_an_empty_graph_walks_zero_edges(world):
    assert call(world, "BuildAdjacencyTSV") == ""
    assert str(call(world, "TimeOrderWalk")).endswith("(0 edges)")


def test_compare_reports_each_format_and_clears_the_graph(world):
    out = str(call(world, "Compare", 30))
    assert re.match(r"^n=30 \| \$ORDER: .*ms \(\d+ edges\) \| JSON: [\d.,]+ms \(\d+KB\) \| TSV: [\d.,]+ms \(\d+KB\)$", out)
    assert world.native().isDefined("KG", "out") == 0, "Compare left its seeded adjacency behind"
