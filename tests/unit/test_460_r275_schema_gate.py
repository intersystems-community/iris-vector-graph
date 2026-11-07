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
        "temporal_edge_write", "cypher_one_hop", "cypher_var_length", "pagerank", "pagerank_cached", "ledger_commit",
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
            "--worker", str(worker), "--python", sys.executable, "--base-code", str(tmp_path), "--no-iris-checks", "--fake", json.dumps(slow)]
    return gate.main(argv), out


def test_a_gate_whose_sides_are_level_exits_zero_and_writes_both_files(tmp_path):
    code, out = _run_gate(tmp_path, {})
    assert code == 0
    report = json.loads((out / "gate.json").read_text())
    assert report["passed"] is True and len(report["verdicts"]) == 2 * 2
    assert {s["version"] for s in report["sides"].values()} == {"fake-base", "fake-default", "fake-alt"}
    assert "PASS" in (out / "gate.md").read_text()


def test_one_path_six_percent_slower_on_one_schema_exits_non_zero(tmp_path):
    code, out = _run_gate(tmp_path, {"alt": {"cypher_one_hop": 1.06}})
    assert code == 1
    report = json.loads((out / "gate.json").read_text())
    bad = [v for v in report["verdicts"] if not v["ok"]]
    assert [(v["path"], v["side"]) for v in bad] == [("cypher_one_hop", "alt")]


def test_an_allowed_path_within_its_cost_passes_end_to_end_and_says_so(tmp_path):
    code, out = _run_gate(tmp_path, {"default": {"pagerank": 1.5}})
    assert code == 0 and "allowed (by design)" in (out / "gate.md").read_text()
    assert [v["allowed"] for v in json.loads((out / "gate.json").read_text())["verdicts"] if v["path"] == "pagerank"
            and v["side"] == "default"] == [True]


def test_exactly_five_percent_slower_passes_end_to_end(tmp_path):
    code, _ = _run_gate(tmp_path, {"default": {"cypher_one_hop": 1.05}})
    assert code == 0


def test_a_missing_base_checkout_is_refused_before_anything_runs(tmp_path):
    assert gate.main(["--out", str(tmp_path / "g"), "--base-code", str(tmp_path / "absent"), "--no-iris-checks"]) == 2
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


def test_every_side_tunes_the_same_tables_after_seeding():
    """A plan follows the statistics: each side tunes its graph tables once the read graph is in."""
    from scripts.bench import schema_gate_worker as worker

    assert {"nodes", "rdf_edges", "rdf_labels", "rdf_props"} <= set(worker.Side.TUNED)
    src = (gate.REPO / "scripts/bench/schema_gate_worker.py").read_text()
    setup = src[src.index("    def setup(self"):src.index("    def tune(self")]
    assert setup.index("self.e.sync()") < setup.index("self.tune()")


# --- the two slowdowns Tom allowed, by design (2026-11-07) ---------------------------------------------------------
def test_exactly_three_paths_are_allowed_each_with_its_cost_and_reason():
    assert set(gate.ALLOWED) == {"collect_node_property", "pagerank", "pagerank_cached"}
    for path, entry in gate.ALLOWED.items():
        assert 0 < entry["cost"] < 1 and entry["reason"] and entry["fix"]
    assert "R313" in gate.ALLOWED["pagerank_cached"]["fix"] and "R306" in gate.ALLOWED["pagerank_cached"]["fix"]
    assert "R262" in gate.ALLOWED["collect_node_property"]["fix"]
    assert "R306" in gate.ALLOWED["pagerank"]["fix"]


def test_an_allowed_path_within_its_cost_is_allowed_not_passed():
    cost = gate.ALLOWED["pagerank"]["cost"]
    v = gate.compare(timings([1.0] * 5, default=[1.0 + cost] * 5, alt=[1.0] * 5, path="pagerank"))
    d = {x.side: x for x in v}
    assert d["default"].ok and d["default"].allowed and "allowed (by design" in d["default"].reason
    assert d["alt"].ok and not d["alt"].allowed  # within 5%: an ordinary pass
    md = gate.render_markdown(v, {"runs": 5})
    assert "allowed (by design" in md and gate.gate_passes(v)


def test_an_allowed_path_more_than_twice_its_cost_fails():
    cost = gate.ALLOWED["collect_node_property"]["cost"]
    v = gate.compare(timings([1.0] * 5, default=[1.0 + 2 * cost + 0.01] * 5, path="collect_node_property"))
    d = next(x for x in v if x.side == "default")
    assert not d.ok and "twice" in d.reason and not gate.gate_passes(v)
    at_limit = gate.compare(timings([1.0] * 5, default=[1.0 + 2 * cost] * 5, path="collect_node_property"))
    assert next(x for x in at_limit if x.side == "default").ok


def test_no_other_path_is_allowed_past_five_percent():
    v = gate.compare(timings([1.0] * 5, default=[1.06] * 5, path="cypher_one_hop"))
    assert not next(x for x in v if x.side == "default").ok


def test_cached_pagerank_is_allowed_up_to_twice_its_cost():
    cost = gate.ALLOWED["pagerank_cached"]["cost"]
    within = gate.compare(timings([1.0] * 5, default=[1.0 + cost] * 5, path="pagerank_cached"))
    past = gate.compare(timings([1.0] * 5, default=[1.0 + 2 * cost + 0.01] * 5, path="pagerank_cached"))
    assert next(x for x in within if x.side == "default").allowed
    assert not next(x for x in past if x.side == "default").ok


# --- Write Daemon stalls: runs inside one are IRIS's, not the code's ---------------------------------------------
WD_LOG = """\
11/07/26-06:03:02:324 (926) 2 [Utility.Event] [SYSTEM MONITOR] WriteDaemon Alert: Write Daemon still on pass 168 after 91.1 seconds.
11/07/26-06:03:32:511 (926) 0 [Utility.Event] [SYSTEM MONITOR] WriteDaemon Alert: Write Daemon completed a pass.
11/07/26-06:10:25:663 (1472) 3 [Generic.Event] Process 1472 caught signal 11.
11/07/26-06:13:04:351 (952) 2 [Utility.Event] [SYSTEM MONITOR] WriteDaemon Alert: Write Daemon still on pass 6 after 132.5 seconds.
"""


def _utc(hms):
    import datetime

    h, m, s = (float(x) for x in hms.split(":"))
    return datetime.datetime(2026, 11, 7, int(h), int(m), tzinfo=datetime.timezone.utc).timestamp() + s


def test_write_daemon_windows_start_at_the_stall_and_end_at_the_completed_pass():
    windows = gate.write_daemon_windows(WD_LOG)
    assert len(windows) == 2
    (s1, e1), (s2, e2) = windows
    assert abs(s1 - (_utc("06:03:02.324") - 91.1)) < 1e-3 and abs(e1 - _utc("06:03:32.511")) < 1e-3
    assert abs(s2 - (_utc("06:13:04.351") - 132.5)) < 1e-3 and e2 is None  # still open: no completed pass yet


def test_a_run_overlapping_a_stall_is_marked_and_the_verdict_is_given_both_ways():
    t = timings([1.0] * 7, default=[1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 9.0])
    spans = {s: {"p": [(100.0 + 10 * i, 101.0 + 10 * i) for i in range(7)]} for s in gate.SIDES}
    windows = [(160.5, 162.0)]  # overlaps run 6 on every side
    v = {x.side: x for x in gate.compare(t, spans=spans, windows=windows)}
    d = v["default"]
    assert d.stalled_runs == [6] and d.ok  # without the stalled run the median is level
    assert d.median_all is not None and d.median == 1.0


def test_excluding_stalled_runs_that_changes_the_verdict_or_leaves_too_few_says_rerun():
    t = timings([1.0] * 7, default=[1.2, 1.2, 1.2, 1.2, 1.0, 1.0, 1.0])
    spans = {s: {"p": [(10.0 * i, 10.0 * i + 1) for i in range(7)]} for s in gate.SIDES}
    windows = [(0.0, 31.0)]  # runs 0-3 inside: three left, fewer than MIN_RUNS
    d = next(x for x in gate.compare(t, spans=spans, windows=windows) if x.side == "default")
    assert not d.ok and "rerun" in d.reason


def test_the_gate_refuses_to_start_on_an_open_stall_or_a_swapping_vm():
    """Measured 2026-11-07: 40.9 GB of swap 'used' both during the 06:10 stall and after the gate stopped (pages stay
    in swap until touched: history, not pressure); after the stop, 0 pages/s out and 11-300 pages/s in at load 20-34.
    So the check is the rate over a sample: any page out, or more than 1000 in a second (3x the idle burst)."""
    assert gate.preflight_problems(swap_in_per_s=12.8, swap_out_per_s=0.0, wd_open=False) == []
    assert gate.preflight_problems(swap_in_per_s=300.3, swap_out_per_s=0.0, wd_open=False) == []
    assert gate.preflight_problems(swap_in_per_s=0.0, swap_out_per_s=4.0, wd_open=False)
    assert gate.preflight_problems(swap_in_per_s=1500.0, swap_out_per_s=0.0, wd_open=False)
    assert gate.preflight_problems(swap_in_per_s=0.0, swap_out_per_s=0.0, wd_open=True)


def test_swap_rates_are_read_from_two_vmstat_samples(monkeypatch):
    samples = iter(["pswpin 1000\npswpout 50\n", "pswpin 1128\npswpout 50\n"])
    monkeypatch.setattr(gate, "container_text", lambda c, *cmd: next(samples))
    monkeypatch.setattr(gate.time, "sleep", lambda s: None)
    assert gate.container_swap_rates("c", seconds=10) == (12.8, 0.0)


def test_a_wd_log_file_stands_in_for_the_containers_log(tmp_path, monkeypatch):
    log = tmp_path / "messages.log"
    log.write_text(WD_LOG)
    monkeypatch.setattr(gate, "container_text", lambda c, *cmd: (_ for _ in ()).throw(AssertionError("no docker")))
    monkeypatch.setattr(gate, "container_swap_rates", lambda c, seconds=10: (0.0, 0.0))
    code = gate.main(["--out", str(tmp_path / "g"), "--base-code", str(tmp_path), "--wd-log", str(log)])
    assert code == 2  # its last stall is open: refused


def test_a_stall_that_decides_the_verdict_says_rerun():
    t = timings([1.0] * 7, default=[1.0, 1.0, 1.0, 1.3, 1.3, 1.3, 1.3])
    spans = {s: {"p": [(10.0 * i, 10.0 * i + 1) for i in range(7)]} for s in gate.SIDES}
    # every run: median 1.3, a fail; without the two stalled runs: median 1.0, a pass
    d = next(x for x in gate.compare(t, spans=spans, windows=[(49.5, 61.5)]) if x.side == "default")
    assert d.stalled_runs == [5, 6] and not d.ok and "rerun needed" in d.reason


def test_a_restart_closes_an_open_stall():
    log = WD_LOG + "11/07/26-06:20:00:000 (901) 0 [WriteDaemon.Started] Write daemon started.\n"
    assert gate.write_daemon_windows(log)[-1][1] == _utc("06:20:00")


def test_the_report_says_which_runs_a_stall_left_out():
    t = timings([1.0] * 7)
    spans = {s: {"p": [(10.0 * i, 10.0 * i + 1) for i in range(7)]} for s in gate.SIDES}
    v = gate.compare(t, spans=spans, windows=[(60.5, 61.5)])
    md = gate.render_markdown(v, {"runs": 7})
    assert "left out" in md and "[6]" in md


def test_main_refuses_to_start_on_a_swapping_vm_and_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(gate, "container_swap_rates", lambda c, seconds=10: (0.0, 25.0))
    monkeypatch.setattr(gate, "container_text", lambda c, *cmd: "")
    code = gate.main(["--out", str(tmp_path / "g"), "--base-code", str(tmp_path)])
    assert code == 2 and "swap" in capsys.readouterr().err and not (tmp_path / "g").exists()


def test_main_marks_runs_inside_a_stall_from_messages_log(tmp_path, monkeypatch):
    """End to end with the fake worker: a stall window covering the whole run leaves every run out: rerun needed."""
    monkeypatch.setattr(gate, "container_swap_rates", lambda c, seconds=10: (0.0, 0.0))
    state = {"calls": 0}

    def text(container, *cmd):
        state["calls"] += 1
        if cmd[0] == "cat" and state["calls"] > 1:  # the read after the runs: a stall over all of them
            import time as _t
            stamp = _t.strftime("%m/%d/%y-%H:%M:%S:000", _t.gmtime(_t.time() + 1))
            return f"{stamp} (1) 2 [Utility.Event] [SYSTEM MONITOR] WriteDaemon Alert: Write Daemon still on pass 1 after 3600.0 seconds.\\n"
        return ""

    monkeypatch.setattr(gate, "container_text", text)
    worker = tmp_path / "fake_worker.py"
    worker.write_text(FAKE_WORKER)
    code = gate.main(["--out", str(tmp_path / "g"), "--runs", "5", "--paths", "cypher_one_hop", "--worker", str(worker),
                      "--python", sys.executable, "--base-code", str(tmp_path), "--fake", "{}"])
    report = json.loads((tmp_path / "g" / "gate.json").read_text())
    assert code == 1 and all("rerun needed" in v["reason"] for v in report["verdicts"])
    assert report["write_daemon_stalls"] and all(v["stalled_runs"] == [0, 1, 2, 3, 4] for v in report["verdicts"])


def test_every_run_has_its_utc_start_and_end_in_gate_json(tmp_path):
    import re as _re

    code, out = _run_gate(tmp_path, {})
    spans = json.loads((out / "gate.json").read_text())["spans_utc"]
    runs = [r for side in spans.values() for path in side.values() for r in path]
    assert len(runs) == 3 * 2 * 5
    stamp = _re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")
    assert all(stamp.match(r["start"]) and stamp.match(r["end"]) and r["start"] <= r["end"] for r in runs)
