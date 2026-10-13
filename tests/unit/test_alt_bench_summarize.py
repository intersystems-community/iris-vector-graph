"""summarize.py: verdict counts and the list of slower metrics, from a harness results directory."""

import json

from scripts.bench.alt import summarize as sm


def write_run(d, suite, side, rep, metrics, status="ok"):
    (d / f"{suite}_{side}_{rep}.json").write_text(
        json.dumps({"suite": suite, "side": side, "rep": rep, "status": status, "metrics": metrics, "elapsed_s": 1})
    )


def make(tmp_path):
    for rep in (1, 2, 3):
        write_run(tmp_path, "s1", "A", rep, {"m.fast": 10.0, "m.same": 5.0, "m.slow": 1.0, "wall_total": 3.0})
        write_run(tmp_path, "s1", "B", rep, {"m.fast": 5.0, "m.same": 5.2, "m.slow": 2.0 + rep * 0.01, "wall_total": 3.0})
    return tmp_path


def test_verdicts_follow_the_protocol_rule(tmp_path):
    rows = sm.compare_run(make(tmp_path))
    by = {r["metric"]: r["verdict"] for r in rows}
    assert by["m.fast"] == "faster" and by["m.same"] == "parity" and by["m.slow"] == "slower"


def test_slower_rows_carry_absolute_difference_and_reps(tmp_path):
    rows = sm.compare_run(make(tmp_path))
    slow = [r for r in rows if r["metric"] == "m.slow"][0]
    assert abs(slow["delta"] - 1.02) < 1e-6
    assert slow["a_reps"] == [1.0, 1.0, 1.0] and len(slow["b_reps"]) == 3


def test_sub_floor_metrics_are_judged_by_absolute_difference(tmp_path):
    for rep in (1, 2, 3):
        write_run(tmp_path, "s2", "A", rep, {"call.p50": 0.0004})
        write_run(tmp_path, "s2", "B", rep, {"call.p50": 0.0006})  # 1.5x but 0.0002 ms = 0.2 microseconds
    rows = sm.compare_run(tmp_path, floor_ms=0.00025)
    assert rows[0]["verdict"] == "parity (floor)"


def test_markdown_lists_counts_then_every_slower_row(tmp_path):
    text = sm.render({"run-x": sm.compare_run(make(tmp_path))})
    assert "run-x" in text and "slower" in text and "m.slow" in text
    assert text.index("| run-x |") < text.index("m.slow")
