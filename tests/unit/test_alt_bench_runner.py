"""Pure parts of scripts/bench/alt/run_matrix.py: quiet-machine filter, ordering, env, metric extraction,
manifest layout (spec 250). No IRIS, no subprocess of a real benchmark."""

import json
import os
import sys

import pytest

from scripts.bench.alt import run_matrix as rm
from scripts.bench.alt import sides as sd
from scripts.bench.alt import suites as su

PS = """\
  101 /usr/bin/python3 -m pytest tests/e2e/test_x.py -q
  102 /bin/zsh -c source ~/.zshrc && eval 'cd /x && pytest tests/unit' < /dev/null
  103 zsh -c pytest tests/foo
  104 grep pytest
  105 /Users/me/.venv/bin/python tests/benchmarks/bench.py --datasets M
  106 /Users/me/.venv/bin/python scripts/bench/alt/run_matrix.py --suites a
  107 /usr/bin/vim notes.md
  108 /bin/bash -c "python benchmarks/neo4j_comparison.py"
  109 /Users/me/.venv/bin/python -m pytest tests/perf/test_head_to_head.py
  110 claude --agent bench-runner
"""


def test_parse_ps_splits_pid_and_command():
    rows = rm.parse_ps(PS)
    assert (101, "/usr/bin/python3 -m pytest tests/e2e/test_x.py -q") in rows
    assert len(rows) == 10


def test_busy_ignores_shell_wrappers_grep_and_own_pids():
    busy = rm.busy_processes(rm.parse_ps(PS), own_pids={106, 109})
    pids = sorted(p for p, _ in busy)
    assert pids == [101, 105]  # real pytest and a bench script; wrappers/grep/own/editor are not busy


def test_busy_counts_another_runner_but_not_this_one():
    rows = rm.parse_ps(PS)
    assert 106 in [p for p, _ in rm.busy_processes(rows, own_pids=set())]
    assert 106 not in [p for p, _ in rm.busy_processes(rows, own_pids={106})]


def test_wait_quiet_returns_when_quiet_and_reports_load(monkeypatch):
    calls = {"n": 0}

    def fake_ps():
        calls["n"] += 1
        return PS if calls["n"] < 3 else "  500 /usr/bin/vim x\n"

    sleeps = []
    info = rm.wait_quiet(ps_fn=fake_ps, sleep_fn=sleeps.append, poll_s=7, max_wait_s=100, own_pids=set())
    assert info["quiet"] is True
    assert info["polls"] == 3
    assert sleeps == [7, 7]
    assert "load_before" in info and "load_after" in info


def test_wait_quiet_gives_up_and_says_so():
    t = {"now": 0.0}

    def sleep(s):
        t["now"] += s

    info = rm.wait_quiet(
        ps_fn=lambda: PS, sleep_fn=sleep, poll_s=10, max_wait_s=30, own_pids=set(), clock=lambda: t["now"]
    )
    assert info["quiet"] is False
    assert info["busy"]


def test_interleave_order_is_a_b_a_b_per_suite():
    order = rm.interleave_order(["s1", "s2"], ["A", "B"], 2)
    assert order == [
        ("s1", "A", 1),
        ("s1", "B", 1),
        ("s1", "A", 2),
        ("s1", "B", 2),
        ("s2", "A", 1),
        ("s2", "B", 1),
        ("s2", "A", 2),
        ("s2", "B", 2),
    ]


def test_build_env_selects_side_tree_namespace_and_port():
    suite = su.Suite(id="x", argv=["{py}", "x.py"], env={"EXTRA": "1"})
    side = sd.SIDES["B"]
    env = rm.build_env(side, suite, base={"PATH": "/bin", "PYTHONPATH": "/should/not/survive"})
    assert env["IRIS_NAMESPACE"] == "IVGBENCHB"
    assert env["IVG_NAMESPACE"] == "IVGBENCHB"
    assert env["IRIS_PORT"] == "31972" and env["IVG_PORT"] == "31972"
    assert env["IVG_TEST_CONTAINER"] == "ivg-iris-enterprise"
    assert env["PYTHONPATH"].split(os.pathsep)[0] == side.tree
    assert "/should/not/survive" not in env["PYTHONPATH"]
    assert env["EXTRA"] == "1"
    assert env["IVG_BENCH_SIDE"] == "B"
    assert env["PATH"] == "/bin"


def test_build_env_refuses_foreign_namespace():
    bad = sd.Side("A", "x", "USER", "/tmp/x")
    with pytest.raises(ValueError):
        rm.build_env(bad, su.Suite(id="x", argv=["x"]), base={})


def test_expand_argv_substitutes_placeholders():
    side = sd.SIDES["A"]
    argv = rm.expand_argv(["{py}", "bench.py", "--outdir", "{out}", "--ns", "{ns}", "--port", "{port}"], side, "/o")
    assert argv[0] == rm.PYTHON
    assert argv[3:] == ["/o", "--ns", "IVGBENCHA", "--port", "31972"]


def test_extract_metrics_reads_bench_metric_lines():
    out = "noise\nBENCH_METRIC q1 = 1.5\nBENCH_METRIC q2.p50=2\nBENCH_METRIC bad=abc\nBENCH_METRIC  hops 3 = 7.25\n"
    m = rm.metrics_from_stdout(out)
    assert m == {"q1": 1.5, "q2.p50": 2.0, "hops 3": 7.25}


def test_extract_metrics_reads_pytest_durations():
    out = """\
============ slowest durations ============
2.50s call     tests/perf/test_x.py::test_a
0.10s setup    tests/perf/test_x.py::test_a
0.75s call     tests/perf/test_x.py::TestC::test_b[param-1]
0.01s teardown tests/perf/test_x.py::test_a
"""
    m = rm.metrics_from_pytest_durations(out)
    assert m == {"tests/perf/test_x.py::test_a": 2500.0, "tests/perf/test_x.py::TestC::test_b[param-1]": 750.0}


def test_flatten_json_numbers_with_selection():
    doc = {"results": {"S": {"Q1": {"p50": 0.5, "p99": 2, "n": 20}}}, "meta": {"seed": 42}}
    flat = rm.flatten_numbers(doc, include=("p50", "p99"))
    assert flat == {"results.S.Q1.p50": 0.5, "results.S.Q1.p99": 2.0}


def test_rep_record_and_filenames(tmp_path):
    rec = rm.write_rep(tmp_path, suite="s-1", side="A", rep=2, status="ok", stdout="hello", metrics={"m": 1.0}, extra={"rc": 0})
    assert (tmp_path / "s-1_A_2.txt").read_text() == "hello"
    doc = json.loads((tmp_path / "s-1_A_2.json").read_text())
    assert doc["suite"] == "s-1" and doc["side"] == "A" and doc["rep"] == 2 and doc["metrics"] == {"m": 1.0}
    assert doc["rc"] == 0 and rec == doc


def test_manifest_records_commits_env_and_load(tmp_path):
    rm.write_manifest(
        tmp_path,
        run_id="r1",
        sides={"A": {"ref": "6f17be0f", "commit": "x" * 40, "namespace": "IVGBENCHA", "tree": "/tmp/ivg-src-A"}},
        suites=["s"],
        reps=3,
        env={"IRIS_PORT": "31972"},
        quiet={"load_before": [1, 2, 3]},
    )
    m = json.loads((tmp_path / "manifest.json").read_text())
    assert m["run_id"] == "r1" and m["reps"] == 3 and m["sides"]["A"]["commit"] == "x" * 40
    assert m["quiet"]["load_before"] == [1, 2, 3]
    assert m["python"] == sys.version.split()[0]


def test_every_registered_suite_is_well_formed():
    ids = [s.id for s in su.SUITES]
    assert len(ids) == len(set(ids)), "duplicate suite ids"
    for s in su.SUITES:
        assert s.id == s.id.lower() and " " not in s.id and "_" not in s.id, s.id
        assert s.argv, s.id
        assert s.parser in su.PARSERS, s.id
        assert s.data is None or s.data in su.DATASETS, (s.id, s.data)
        # Nothing registered may name a namespace other than the side's own.
        assert not any(ns in " ".join(s.argv) for ns in ("IVGTEST", "IVGSEC", "IVGLEGACY", "IVGPERF")), s.id


def test_schedule_runs_data_suites_first_and_wipers_last():
    a = su.Suite(id="a", argv=["x"], wipes=True)
    b = su.Suite(id="b", argv=["x"], data="rmat-s")
    c = su.Suite(id="c", argv=["x"])
    d = su.Suite(id="d", argv=["x"], data="rmat-m")
    e = su.Suite(id="e", argv=["x"], data="rmat-s")
    assert [s.id for s in rm.schedule([a, b, c, d, e])] == ["b", "e", "d", "c", "a"]


def test_wiping_suites_never_need_a_dataset():
    for s in su.SUITES:
        assert not (s.wipes and s.data), s.id


def test_quick_ids_exclude_neo4j_and_slow_suites():
    q = su.quick_ids()
    assert "cypher-parser-bench" in q and "bench-q1q6-s" in q
    assert "drkg-load-full" not in q and "bench-q1q6-m" not in q


def test_reset_derived_kills_the_derived_globals_and_invalidates_the_dataset(monkeypatch):
    """A suite that deletes its rows by SQL leaves ^KG behind; on the baseline that residue accumulates across reps
    (stale counters, stale adjacency), so such a suite is reset the same way on both sides before every rep."""
    from scripts.bench.alt import run_matrix as rm

    class FakeConn:
        def close(self):
            pass

    class FakeNative:
        def __init__(self):
            self.killed, self.set_calls = [], []

        def kill(self, *a):
            self.killed.append(a)

        def set(self, *a):
            self.set_calls.append(a)

    nat = FakeNative()
    monkeypatch.setattr(rm, "_native", lambda side: (FakeConn(), nat))
    seen = []
    monkeypatch.setattr(rm, "set_sentinel", lambda side, key: seen.append(key))
    rm.reset_derived(object())
    names = [k[0] for k in nat.killed]
    assert {"^KG", "^NKG", "^IVG.AlgoCache", "^ArnoKG"} <= set(names)
    assert seen == [None]


def test_the_bidirectional_ppr_suite_is_reset_before_each_rep():
    from scripts.bench.alt import suites as su

    assert su.SUITES_BY_ID["integration-bidirectional-ppr-perf"].reset_derived is True


def test_a_loaded_dataset_is_tuned_before_anything_is_timed(monkeypatch):
    """The optimizer plans from table statistics, and a bulk load leaves them as they were on the empty tables: the same SQL ran
    0.5 s in one namespace and 20 s in the other. ensure_data tunes the side's tables right after it loads a dataset."""
    from scripts.bench.alt import run_matrix as rm

    calls = []
    states = iter([(None, 0), ("rmat-s", 1000)])  # before the load, then after it
    monkeypatch.setattr(rm, "dataset_state", lambda side, key: next(states))
    monkeypatch.setattr(rm, "run_command", lambda *a, **k: (0, "", False))
    monkeypatch.setattr(rm, "set_sentinel", lambda side, key: None)
    monkeypatch.setattr(rm, "tune_side", lambda side: calls.append(("tune", side.letter)))
    monkeypatch.setattr(rm.su, "DATASETS", {"rmat-s": type("D", (), {"load_argv": ["x"], "expect_nodes": 1000})()})
    side = type("S", (), {"letter": "A", "tree": "/tmp"})()
    monkeypatch.setattr(rm, "expand_argv", lambda argv, side, out: argv)
    monkeypatch.setattr(rm.Path, "mkdir", lambda self, **k: None)
    out = rm.ensure_data(side, "rmat-s", {}, log=lambda *_: None)
    assert out["loaded"] is True and calls == [("tune", "A")]


def test_a_dataset_already_loaded_is_not_tuned_again(monkeypatch):
    from scripts.bench.alt import run_matrix as rm

    monkeypatch.setattr(rm, "dataset_state", lambda side, key: ("rmat-s", 1000))
    monkeypatch.setattr(rm.su, "DATASETS", {"rmat-s": type("D", (), {"load_argv": ["x"], "expect_nodes": 1000})()})
    monkeypatch.setattr(rm, "tune_side", lambda side: (_ for _ in ()).throw(AssertionError("tuned")))
    assert rm.ensure_data(type("S", (), {"letter": "A", "tree": "/tmp"})(), "rmat-s", {}, log=lambda *_: None)["loaded"] is False
