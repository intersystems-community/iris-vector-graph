"""User.funcSTRSPLIT, User.Exec and Graph.KG.Benchmark: three small classes nothing else exercised."""

from __future__ import annotations

import json
import os

import pytest

from iris_vector_graph.engine import IRISGraphEngine
from tests.integration import _tcp
from tests.integration._236_world import PRED, World
from tests.helpers.dev_classes import BENCHMARK, EXEC, ensure_dev_classes

NS236 = os.environ.get("IVG_NS_IVG236", "")  # the fixture refuses to run without it (R287)


@pytest.fixture
def obj(iris_connection):
    # User.Exec ships in iris_src/dev only (spec 262 T055)
    return ensure_dev_classes(IRISGraphEngine(iris_connection, embedding_dimension=768)._store._iris_obj(), EXEC)


@pytest.mark.parametrize(
    "s,delim,expected",
    [("a,b,c", ",", ["a", "b", "c"]), ("a", ",", ["a"]), ("a,,c", ",", ["a", "", "c"]), ("", ",", [])],
)
def test_str_split_returns_a_json_array(obj, s, delim, expected):
    out = obj.classMethodValue("User.funcSTRSPLIT", "STRSPLIT", s, delim)
    assert json.loads(str(out)) == expected


def test_exec_runs_a_command_string_and_returns_empty(obj):
    obj.kill("IVGExecProbe")
    try:
        out = obj.classMethodValue("User.Exec", "Run", 'Set ^IVGExecProbe("x")=42')
        assert out in (None, "")
        assert int(obj.get("IVGExecProbe", "x")) == 42
    finally:
        obj.kill("IVGExecProbe")


def test_benchmark_compares_the_two_bfs_forms_on_a_small_graph(ivg_236_scratch):
    w = World(ivg_236_scratch)
    ensure_dev_classes(w.native(), BENCHMARK)  # iris_src/dev only (spec 262 T055)
    w.native().classMethodValue("Graph.KG.Eraser", "EraseAll")
    w.renew()
    e = w.engine()
    for s, o in (("alice", "b"), ("b", "c"), ("c", "d")):
        e.create_edge(s, PRED, o)
    w.commit()
    try:
        out = _tcp.session(['do ##class(Graph.KG.Benchmark).Run("alice",3)'], NS236, timeout=120, check=True)
        assert "Standard BFS (Objects):" in out and "Fast BFS (PPG):" in out, out
        assert "Count: 3" in out.split("Standard BFS")[1].split("\n")[0]
    finally:
        native = ivg_236_scratch.native()
        if ivg_236_scratch.sentinel_ok(native):
            native.classMethodValue("Graph.KG.Eraser", "EraseAll")
