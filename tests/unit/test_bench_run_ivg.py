"""scripts/bench/competitors/run_ivg.py: IVG behind the competitor harness's engine interface.

run_queries.run_spec times `eng.run(cypher, params, timeout)` and reads a count from the rows it returns, and records an
error where run raises. IVG's execute_cypher reports a failure on the result (`.error`), not by raising, so the adapter
has to raise: a swallowed error timed as an empty answer would read as a fast wrong one.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "bench" / "competitors"))

import run_ivg  # noqa: E402


class _Engine:
    def __init__(self, result):
        self.result, self.calls = result, []

    def execute_cypher(self, cypher, params):
        self.calls.append((cypher, params))
        return self.result


def test_rows_come_back_as_lists():
    eng = run_ivg.IVG(_Engine(SimpleNamespace(rows=[(3,)], error=None)), "ivg")
    assert eng.run("MATCH (n) RETURN count(n)", {"x": 1}) == [[3]]
    assert eng.engine.calls == [("MATCH (n) RETURN count(n)", {"x": 1})]


def test_an_error_on_the_result_raises():
    eng = run_ivg.IVG(_Engine(SimpleNamespace(rows=[], error="SQLCODE -400")), "ivg")
    with pytest.raises(RuntimeError, match="SQLCODE -400"):
        eng.run("MATCH (n) RETURN n")


def test_the_record_comes_out_of_run_spec(tmp_path):
    import run_queries

    eng = run_ivg.IVG(_Engine(SimpleNamespace(rows=[[7]], error=None)), "ivg-native")
    spec = run_queries.QuerySpec("D_Q1", "drkg", "MATCH (n) RETURN count(n) AS cnt", {}, expect=7)
    out = tmp_path / "r.jsonl"
    rec = run_queries.run_spec(eng, spec, 1, 3, 10.0, out, {"dataset": "DRKG"}, log=lambda *_: None)
    assert rec["engine"] == "ivg-native" and len(rec["values_ms"]) == 3 and rec["params"]["match"] is True
