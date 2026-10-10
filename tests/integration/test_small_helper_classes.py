"""User.funcSTRSPLIT, User.Exec and Graph.KG.Benchmark: three small classes nothing else exercised."""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from iris_vector_graph.engine import IRISGraphEngine
from tests.integration._236_world import PRED, World

CONTAINER = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")


@pytest.fixture
def obj(iris_connection):
    return IRISGraphEngine(iris_connection, embedding_dimension=768)._store._iris_obj()


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
    w.native().classMethodValue("Graph.KG.Eraser", "EraseAll")
    w.renew()
    e = w.engine()
    for s, o in (("alice", "b"), ("b", "c"), ("c", "d")):
        e.create_edge(s, PRED, o)
    w.commit()
    try:
        script = 'do ##class(Graph.KG.Benchmark).Run("alice",3)\nhalt\n'
        out = subprocess.run(
            ["docker", "exec", "-i", CONTAINER, "iris", "session", "IRIS", "-U", "IVG236"],
            input=script, capture_output=True, text=True, timeout=120,
        ).stdout
        assert "Standard BFS (Objects):" in out and "Fast BFS (PPG):" in out, out
        assert "Count: 3" in out.split("Standard BFS")[1].split("\n")[0]
    finally:
        native = ivg_236_scratch.native()
        if ivg_236_scratch.sentinel_ok(native):
            native.classMethodValue("Graph.KG.Eraser", "EraseAll")
