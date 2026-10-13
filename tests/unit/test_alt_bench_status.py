"""Run status: a failed timing gate still yields valid metrics and must not hide a rep (spec 250)."""

import json

from scripts.bench.alt import compare
from scripts.bench.alt import run_matrix as rm
from scripts.bench.alt import suites as su

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" errors="0" failures="1" tests="2">
<testcase classname="tests.perf.test_x" name="test_budget" time="1.2"><failure message="assert 1.17 &lt;= 1.1">trace</failure></testcase>
<testcase classname="tests.perf.test_x" name="test_same" time="0.1"/>
<testcase classname="tests.perf.test_x" name="test_err" time="0.1"><error message="boom">trace</error></testcase>
<testcase classname="tests.perf.test_x" name="test_skip" time="0.0"><skipped message="s"/></testcase>
</testsuite></testsuites>
"""


def test_status_for_pytest_and_plain_scripts():
    pytest_suite = su.SUITES_BY_ID["perf-227-single-graph-latency"]
    script_suite = su.SUITES_BY_ID["cypher-parser-bench"]
    assert rm.status_for(0, pytest_suite) == "ok"
    assert rm.status_for(1, pytest_suite) == "tests-failed"
    assert rm.status_for(5, pytest_suite) == "failed"  # nothing collected
    assert rm.status_for(2, pytest_suite) == "failed"  # interrupted
    assert rm.status_for(1, script_suite) == "failed"
    assert rm.status_for(None, pytest_suite) == "failed"  # timeout


def test_failed_tests_from_junit(tmp_path):
    p = tmp_path / "junit.xml"
    p.write_text(JUNIT)
    assert rm.failed_tests_from_junit(p) == ["tests.perf.test_x::test_budget", "tests.perf.test_x::test_err"]
    assert rm.failed_tests_from_junit(tmp_path / "missing.xml") == []


def test_227_line_without_space_before_ms():
    line = "[227 SC-010] 300 vectors of width 768 in kg_NodeEmbeddings: 3.2.0 scan median 2.03ms, 4.0.0 scoped median 2.38ms, ratio 1.171"
    m = su.PARSERS["227-latency"](line, su.context("/x", "/x", 0))
    assert m["legacy_scan_median"] == 2.03 and m["scoped_median"] == 2.38


def test_tests_failed_reps_keep_their_metrics_but_are_listed(tmp_path):
    def rep(side, status, val):
        (tmp_path / f"s_{side}_1.json").write_text(
            json.dumps({"suite": "s", "side": side, "rep": 1, "status": status, "metrics": {"m": val}, "failed_tests": ["t::gate"] if status != "ok" else []})
        )

    rep("A", "tests-failed", 10.0)
    rep("B", "ok", 11.0)
    r = compare.load_results(tmp_path)
    assert r["s"]["A"]["m"] == [10.0]
    assert compare.load_failures(tmp_path) == [("s", "A", 1)]
    text = compare.render_report(tmp_path)
    assert "tests-failed" in text and "t::gate" in text


def test_hard_failed_reps_lose_their_metrics(tmp_path):
    (tmp_path / "s_A_1.json").write_text(json.dumps({"suite": "s", "side": "A", "rep": 1, "status": "failed", "metrics": {"m": 1.0}}))
    assert compare.load_results(tmp_path) == {}
    assert compare.load_failures(tmp_path) == [("s", "A", 1)]


def test_build_env_silences_the_namespace_warning():
    from scripts.bench.alt import sides as sd

    env = rm.build_env(sd.SIDES["A"], su.Suite(id="x", argv=["x"]), base={})
    assert env["IVG_IGNORE_NAMESPACE_CHECK"] == "1"


def test_run_command_captures_output_and_exit_code(tmp_path):
    import sys

    rc, text, timed_out = rm.run_command([sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"], str(tmp_path), {}, 30)
    assert rc == 3 and not timed_out
    assert "out" in text and "err" in text


def test_run_command_kills_the_whole_process_group_on_timeout(tmp_path):
    import os
    import sys
    import time

    pidfile = tmp_path / "child.pid"
    code = (
        "import subprocess, sys, time;"
        f"p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']);"
        f"open(r'{pidfile}', 'w').write(str(p.pid)); print('started', flush=True); time.sleep(60)"
    )
    t0 = time.time()
    rc, text, timed_out = rm.run_command([sys.executable, "-c", code], str(tmp_path), {}, 3)
    assert timed_out and time.time() - t0 < 20
    assert "started" in text
    child = int(pidfile.read_text())
    time.sleep(0.5)
    try:
        os.kill(child, 0)
        alive = True
    except ProcessLookupError:
        alive = False
    assert not alive, "grandchild survived the timeout"


def test_blocked_suites_carry_a_reason_and_are_not_quick():
    blocked = [s for s in su.SUITES if s.blocked]  # none today: the executemany generator was fixed
    for s in blocked:
        assert len(s.blocked) > 20, s.id
        assert s.id not in su.quick_ids()


def test_runnable_ids_exclude_blocked_unless_asked(monkeypatch):
    import dataclasses

    patched = {s.id: (dataclasses.replace(s, blocked="hangs for a documented reason here") if s.id == "index-maintenance-20k" else s) for s in su.SUITES}
    monkeypatch.setattr(su, "SUITES_BY_ID", patched)
    ids = rm.select_ids(["index-maintenance-20k", "cypher-parser-bench"], include_blocked=False)
    assert ids == (["cypher-parser-bench"], ["index-maintenance-20k"])
    ids = rm.select_ids(["index-maintenance-20k", "cypher-parser-bench"], include_blocked=True)
    assert ids == (["index-maintenance-20k", "cypher-parser-bench"], [])
