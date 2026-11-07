"""Spec 262 T066 / FR-020a / 4.6.0 R275: the speed gate's own logic, with fixed timings (no IRIS).

`scripts/bench/schema_gate.py` times each path on three sides: `a42db224` (the last code state before spec 262) in
IVG262BASE, and the current build on the default IVG Schema and on a non-default one. It compares each side's median
with the base's and fails when any path is more than 5% slower. These tests pin the comparison, the threshold at its
edge, the median under noise, too few runs, the interleaved schedule, the report, and the exit code through a fake
worker, so a gate that passes is a gate that compared.
"""

from __future__ import annotations

import json
import sys
import textwrap

import pytest

from scripts.bench import schema_gate as gate


def timings(base, default=None, alt=None, path="p"):
    return {"base": {path: list(base)}, "default": {path: list(default or base)}, "alt": {path: list(alt or base)}}


# --- the threshold ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "base, cand, slower",
    [
        (100.0, 105.0, False),  # exactly 5% slower: allowed (the rule is "more than 5%")
        (0.2, 0.21, False),  # exactly 5% in floats that do not multiply out exactly
        (0.3, 0.315, False),
        (1.0, 1.0500001, True),  # just over
        (100.0, 99.0, False),  # faster
        (100.0, 105.01, True),
    ],
)
def test_slower_than_allowed_is_more_than_five_percent(base, cand, slower):
    assert gate.slower_than_allowed(base, cand) is slower


def test_the_median_is_robust_to_one_noisy_run():
    """One run 10x slower (a checkpoint, a journal flush) does not fail a path whose other runs are level."""
    v = gate.compare(timings([1.0, 1.01, 0.99, 1.0, 1.02], default=[1.0, 1.02, 10.0, 0.99, 1.01]))
    d = next(x for x in v if x.side == "default")
    assert d.ok and d.median == pytest.approx(1.01) and d.base_median == pytest.approx(1.0)


def test_a_slower_median_fails_even_when_the_fastest_run_is_level():
    v = gate.compare(timings([1.0] * 5, alt=[1.0, 1.2, 1.2, 1.2, 1.3]))
    alt = next(x for x in v if x.side == "alt")
    assert not alt.ok and alt.ratio == pytest.approx(1.2) and "slower" in alt.reason
    assert not gate.gate_passes(v)


def test_each_side_is_compared_with_the_base_on_its_own():
    v = gate.compare(timings([2.0] * 5, default=[2.0] * 5, alt=[2.2] * 5))
    by_side = {x.side: x for x in v}
    assert by_side["default"].ok and not by_side["alt"].ok
    assert {x.side for x in v} == {"default", "alt"}


def test_too_few_runs_is_a_failure_not_a_pass():
    v = gate.compare(timings([1.0] * 4))
    assert v and not any(x.ok for x in v) and all("runs" in x.reason for x in v)


def test_a_path_one_side_did_not_measure_fails():
    t = timings([1.0] * 5)
    del t["alt"]["p"]
    v = gate.compare(t)
    assert not next(x for x in v if x.side == "alt").ok
    assert not gate.gate_passes(v)


def test_a_failed_run_is_not_a_timing():
    """A worker that raised answers None for the run: the path fails with the error, it is not timed as fast."""
    v = gate.compare(timings([1.0] * 5, default=[1.0, 1.0, None, 1.0, 1.0]))
    d = next(x for x in v if x.side == "default")
    assert not d.ok and "failed" in d.reason


def test_an_empty_gate_does_not_pass():
    assert not gate.gate_passes([])


# --- the schedule -------------------------------------------------------------------------------------------------
def test_the_schedule_interleaves_sides_within_each_run_and_rotates_their_order():
    s = gate.schedule(["a", "b"], ["base", "default", "alt"], runs=3)
    assert len(s) == 2 * 3 * 3
    for path in "ab":
        for side in ("base", "default", "alt"):
            assert sum(1 for r, p, sd in s if p == path and sd == side) == 3
    first = [[sd for r, p, sd in s if r == run and p == "a"] for run in range(3)]
    assert first[0] != first[1] != first[2] and {tuple(x[:1]) for x in first} == {("base",), ("default",), ("alt",)}
    # the sides of one path in one run are adjacent: interleaved, not one side's runs back to back
    assert [p for r, p, sd in s[:3]] == ["a", "a", "a"]


def test_the_paths_are_fr_020a_and_r275():
    assert set(gate.PATHS) == {
        "node_write_row", "edge_write_row", "node_write_bulk", "edge_write_bulk", "bare_node_retire", "graph_erase",
        "temporal_edge_write", "cypher_one_hop", "cypher_var_length", "pagerank", "ledger_commit",
        "collect_node_property", "nkg_first_read_after_erase",
    }
    assert gate.THRESHOLD == 0.05 and gate.MIN_RUNS == 5


# --- the report ---------------------------------------------------------------------------------------------------
def test_the_report_names_every_path_and_side_and_says_fail():
    v = gate.compare(timings([1.0] * 5, alt=[1.3] * 5))
    md = gate.render_markdown(v, {"base_commit": "a42db224", "runs": 5})
    assert "a42db224" in md and "FAIL" in md and "| p | alt |" in md and "+30.0%" in md
    data = gate.verdicts_json(v)
    assert {d["side"] for d in data} == {"default", "alt"} and all("runs" in d for d in data)


# --- end to end with a fake worker --------------------------------------------------------------------------------
FAKE_WORKER = textwrap.dedent(
    """
    import json, sys
    side = sys.argv[sys.argv.index("--side") + 1]
    slow = json.loads(sys.argv[sys.argv.index("--fake") + 1])
    for line in sys.stdin:
        req = json.loads(line)
        if req["op"] == "setup":
            print(json.dumps({"ok": True, "version": "fake-" + side}), flush=True)
        elif req["op"] == "run":
            seconds = 1.0 * slow.get(side, {}).get(req["path"], 1.0)
            print(json.dumps({"ok": True, "path": req["path"], "seconds": seconds}), flush=True)
        elif req["op"] == "quit":
            break
    """
)


def _run_gate(tmp_path, slow):
    worker = tmp_path / "fake_worker.py"
    worker.write_text(FAKE_WORKER)
    out = tmp_path / "gate"
    argv = ["--out", str(out), "--runs", "5", "--paths", "cypher_one_hop,pagerank",
            "--worker", str(worker), "--python", sys.executable, "--base-code", str(tmp_path), "--fake", json.dumps(slow)]
    return gate.main(argv), out


def test_a_gate_whose_sides_are_level_exits_zero_and_writes_both_files(tmp_path):
    code, out = _run_gate(tmp_path, {})
    assert code == 0
    report = json.loads((out / "gate.json").read_text())
    assert report["passed"] is True and len(report["verdicts"]) == 2 * 2
    assert {s["version"] for s in report["sides"].values()} == {"fake-base", "fake-default", "fake-alt"}
    assert "PASS" in (out / "gate.md").read_text()


def test_one_path_six_percent_slower_on_one_schema_exits_non_zero(tmp_path):
    code, out = _run_gate(tmp_path, {"alt": {"pagerank": 1.06}})
    assert code == 1
    report = json.loads((out / "gate.json").read_text())
    bad = [v for v in report["verdicts"] if not v["ok"]]
    assert [(v["path"], v["side"]) for v in bad] == [("pagerank", "alt")]


def test_exactly_five_percent_slower_passes_end_to_end(tmp_path):
    code, _ = _run_gate(tmp_path, {"default": {"cypher_one_hop": 1.05}})
    assert code == 0


def test_a_missing_base_checkout_is_refused_before_anything_runs(tmp_path):
    assert gate.main(["--out", str(tmp_path / "g"), "--base-code", str(tmp_path / "absent")]) == 2
    assert not (tmp_path / "g" / "gate.json").exists()


def test_the_worker_times_every_gate_path():
    from scripts.bench import schema_gate_worker as worker

    assert all(callable(getattr(worker.Side, p, None)) for p in gate.PATHS)
    assert set(worker.SCALES) == {"small", "full"} and set(worker.SCALES["small"]) == set(worker.SCALES["full"])


def test_the_worker_answers_on_its_own_stdout_and_reports_a_failure(tmp_path):
    """A request the worker cannot serve (no setup yet) comes back as an error line, not a timing."""
    import subprocess

    r = subprocess.run([sys.executable, "-m", "scripts.bench.schema_gate_worker", "--side", "default",
                        "--namespace", "X", "--schema", "Graph_KG"],
                       input='{"op": "run", "path": "pagerank"}\n{"op": "quit"}\n', capture_output=True, text=True,
                       timeout=60, cwd=str(gate.REPO))
    answers = [json.loads(line) for line in r.stdout.splitlines()]
    assert len(answers) == 1 and answers[0]["ok"] is False and "error" in answers[0]


def test_dry_plan_prints_the_schedule_and_touches_nothing(tmp_path, capsys):
    """`--dry-plan`: the paths, the sides and the interleaved order, with no worker started and no file written."""
    code = gate.main(["--dry-plan", "--runs", "5", "--paths", "pagerank,graph_erase", "--out", str(tmp_path / "g"),
                      "--worker", str(tmp_path / "no_such_worker.py"), "--base-code", str(tmp_path / "absent")])
    out = capsys.readouterr().out
    assert code == 0 and not (tmp_path / "g").exists()
    assert out.count("\n") >= 5 * 2 * 3 and "pagerank" in out and "graph_erase" in out
    assert "run 0" in out and "run 4" in out and "IVG262BASE" in out and "IVGALT" in out


def test_the_nkg_row_says_what_each_side_times():
    v = gate.compare(timings([1.0] * 5, path="nkg_first_read_after_erase"))
    md = gate.render_markdown(v, {"runs": 5})
    assert "BuildNKG + SCCJson" in md and "rebuild inside" in md


# --- the answers: a side that is fast because it did other work is not a pass ------------------------------------
def test_a_side_whose_answer_differs_from_the_base_fails_the_path():
    t = timings([1.0] * 5)
    answers = {"base": {"p": [10] * 5}, "default": {"p": [10] * 5}, "alt": {"p": [10, 10, 9, 10, 10]}}
    v = {x.side: x for x in gate.compare(t, answers=answers)}
    assert v["default"].ok and not v["alt"].ok and "answer" in v["alt"].reason


def test_a_path_whose_answer_may_differ_by_design_is_reported_not_failed():
    t = timings([1.0] * 5, path="pagerank")
    answers = {"base": {"pagerank": [3] * 5}, "default": {"pagerank": [4] * 5}, "alt": {"pagerank": [4] * 5}}
    v = gate.compare(t, answers=answers)
    assert all(x.ok for x in v) and all("answer" in x.reason for x in v)
    assert "pagerank" in gate.ANSWER_MAY_DIFFER
