"""Verdict rule, results loading and report layout of scripts/bench/alt/compare.py (spec 250).

No IRIS involved: the comparer only reads files.
"""

import json

import pytest

from scripts.bench.alt import compare


def test_verdict_parity_when_ratio_within_ten_percent():
    ratio, v = compare.verdict([100, 102, 101], [108, 109, 110])
    assert v == "parity"
    assert ratio == pytest.approx(109 / 101)


def test_verdict_parity_when_b_median_inside_a_spread_even_if_ratio_high():
    # A varies a lot (50..200); B median 150 is slower than A median 100 by 1.5 but inside the spread.
    ratio, v = compare.verdict([50, 100, 200], [140, 150, 160])
    assert ratio == pytest.approx(1.5)
    assert v == "parity"


def test_verdict_slower_when_outside_spread_and_above_ten_percent():
    _, v = compare.verdict([100, 101, 102], [150, 151, 152])
    assert v == "slower"


def test_verdict_faster_when_below_ninety_percent_and_outside_spread():
    _, v = compare.verdict([100, 101, 102], [50, 51, 52])
    assert v == "faster"


def test_verdict_noise_below_ninety_percent_inside_spread_is_parity():
    _, v = compare.verdict([60, 100, 140], [85, 88, 90])
    assert v == "parity"


def test_verdict_boundaries():
    assert compare.verdict([100], [110])[1] == "parity"  # <= 1.10
    assert compare.verdict([100], [110.1])[1] == "slower"
    assert compare.verdict([100], [90])[1] == "parity"  # not < 0.90
    assert compare.verdict([100], [89.9])[1] == "faster"


def test_verdict_zero_baseline_does_not_divide_by_zero():
    ratio, v = compare.verdict([0.0, 0.0], [5.0, 5.0])
    assert ratio == float("inf")
    assert v == "slower"
    assert compare.verdict([0.0], [0.0]) == (1.0, "parity")


def test_verdict_missing_side_is_unknown():
    assert compare.verdict([], [1.0])[1] == "n/a"
    assert compare.verdict([1.0], [])[1] == "n/a"


def _write_rep(d, suite, side, rep, metrics, status="ok"):
    (d / f"{suite}_{side}_{rep}.json").write_text(
        json.dumps({"suite": suite, "side": side, "rep": rep, "status": status, "metrics": metrics})
    )


def test_load_results_groups_by_suite_side_metric(tmp_path):
    for rep, (a, b) in enumerate([(10, 11), (12, 13), (11, 12)], start=1):
        _write_rep(tmp_path, "bench-q1q6-S", "A", rep, {"q1": a})
        _write_rep(tmp_path, "bench-q1q6-S", "B", rep, {"q1": b})
    _write_rep(tmp_path, "bench-q1q6-S", "B", 4, {"q1": 999}, status="failed")
    (tmp_path / "manifest.json").write_text("{}")
    r = compare.load_results(tmp_path)
    assert r["bench-q1q6-S"]["A"]["q1"] == [10, 12, 11]
    assert r["bench-q1q6-S"]["B"]["q1"] == [11, 13, 12]  # failed rep excluded
    assert compare.load_failures(tmp_path) == [("bench-q1q6-S", "B", 4)]


def test_load_competitors_reads_jsonl_lines(tmp_path):
    p = tmp_path / "competitor_neo4j_bench-q1q6-S.jsonl"
    p.write_text(
        json.dumps({"engine": "neo4j", "suite": "bench-q1q6-S", "metric": "q1", "values_ms": [1.0, 3.0, 2.0]})
        + "\n\n"
        + json.dumps({"engine": "neo4j", "suite": "bench-q1q6-S", "metric": "q2", "values_ms": [4.0]})
        + "\n"
    )
    c = compare.load_competitors(tmp_path)
    assert c["bench-q1q6-S"]["q1"] == {"neo4j": [1.0, 3.0, 2.0]}
    assert c["bench-q1q6-S"]["q2"] == {"neo4j": [4.0]}


def test_load_competitors_skips_malformed_lines(tmp_path):
    (tmp_path / "competitor_gds_x.jsonl").write_text("not json\n" + json.dumps({"engine": "gds"}) + "\n")
    assert compare.load_competitors(tmp_path) == {}


def test_report_has_columns_groups_and_competitor_column(tmp_path):
    for rep in (1, 2, 3):
        _write_rep(tmp_path, "s1", "A", rep, {"m": 100 + rep})
        _write_rep(tmp_path, "s1", "B", rep, {"m": 200 + rep})
        _write_rep(tmp_path, "s2", "A", rep, {"m": 10})
        _write_rep(tmp_path, "s2", "B", rep, {"m": 10})
    (tmp_path / "competitor_neo4j_s1.jsonl").write_text(
        json.dumps({"engine": "neo4j", "suite": "s1", "metric": "m", "values_ms": [50, 60, 70]}) + "\n"
    )
    text = compare.render_report(tmp_path)
    assert "## s1" in text and "## s2" in text
    header = [ln for ln in text.splitlines() if ln.startswith("| metric")]
    assert "A median" in header[0] and "B/A" in header[0] and "verdict" in header[0]
    assert "neo4j" in header[0]
    row = [ln for ln in text.splitlines() if ln.startswith("| m ") and "slower" in ln]
    assert row, text
    assert "60" in row[0]  # neo4j median
    assert any("parity" in ln for ln in text.splitlines() if ln.startswith("| m "))
    # No competitor results for s2: its table has no competitor column.
    s2 = text.split("## s2")[1]
    assert "neo4j" not in s2


def test_report_mentions_failed_reps_and_missing_sides(tmp_path):
    _write_rep(tmp_path, "s1", "A", 1, {"m": 5})
    _write_rep(tmp_path, "s1", "B", 1, {}, status="failed")
    text = compare.render_report(tmp_path)
    assert "failed" in text.lower()
    assert "n/a" in text
