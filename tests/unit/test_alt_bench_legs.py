"""A benchmark leg that ends at a query's cap leaves the queries after it unrun; run_native.sh starts another leg with
those (scripts.bench.alt.legs), so a SF10 BI11 timeout no longer needs a second leg by hand (aws-sf10-3)."""

import json

from scripts.bench.alt import legs


def _leg(tmp_path, name, results):
    p = tmp_path / name
    p.write_text(json.dumps({"ns": "X", "params": [], "results": results}))
    return str(p)


def test_the_queries_no_leg_ran_are_left(tmp_path):
    a = _leg(tmp_path, "a.json", {"Q1": {"error": None}, "Q2": {"error": "timeout: first call still running after 900s"}})
    assert legs.remaining(["Q1", "Q2", "Q3", "Q4"], [a]) == ["Q3", "Q4"]


def test_a_timed_out_query_is_not_run_again(tmp_path):
    a = _leg(tmp_path, "a.json", {"Q1": {"error": "timeout: x"}})
    b = _leg(tmp_path, "b.json", {"Q2": {"error": None}})
    assert legs.remaining(["Q1", "Q2", "Q3"], [a, b]) == ["Q3"]


def test_a_missing_or_broken_leg_counts_as_nothing_run(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{")
    assert legs.remaining(["Q1"], [str(bad), str(tmp_path / "none.json")]) == ["Q1"]


def test_all_run_leaves_nothing(tmp_path):
    a = _leg(tmp_path, "a.json", {"Q1": {}, "Q2": {}})
    assert legs.remaining(["Q1", "Q2"], [a]) == []
