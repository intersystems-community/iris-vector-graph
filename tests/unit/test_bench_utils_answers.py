"""bench.py must not time a failed or empty shortest-path call as a success."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests" / "benchmarks"))

import bench_utils  # noqa: E402


def test_weighted_sp_query_inlines_the_node_ids_so_it_runs_on_engines_that_drop_call_parameters():
    q = bench_utils.weighted_sp_query("node_1", "node_2")
    assert q == "CALL ivg.shortestPath.weighted('node_1', 'node_2', 'weight', 9999, 10) YIELD totalCost RETURN totalCost"
    assert "''" in bench_utils.weighted_sp_query("a'b", "c")  # quotes are escaped


def test_answer_or_raise_returns_rows_and_raises_on_error_or_empty():
    ok = SimpleNamespace(rows=[["{}", 3.0]], error=None, columns=["path", "totalCost"])
    assert bench_utils.answer_or_raise(ok) is ok
    with pytest.raises(RuntimeError, match="boom"):
        bench_utils.answer_or_raise(SimpleNamespace(rows=[], error="boom", columns=[]))
    with pytest.raises(RuntimeError, match="no rows"):
        bench_utils.answer_or_raise(SimpleNamespace(rows=[], error=None, columns=[]))
