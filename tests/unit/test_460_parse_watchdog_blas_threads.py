"""4.6.0: the parse watchdog's child runs numpy's BLAS on one thread, so a normal parse stays under the CPU budget on a
many-core host.

The watchdog (tests/unit/_parse_watchdog.py) bounds the child's CPU time (user + system, every thread) at 5 s. The
child imports the parser, which imports numpy, and an OpenBLAS build starts one thread per core and spins them: on
dpgenai1 (128 cores, Linux, OpenBLAS) the import alone spent 4.6 s of CPU in 0.4 s of wall time, and 39 parses were
killed "on time" (the 2026-11-07 sharded gate). With OPENBLAS_NUM_THREADS, OMP_NUM_THREADS and MKL_NUM_THREADS at 1 in
the child, the same import spends 0.4 s. The parser needs no BLAS: the child sets them whatever the caller's
environment asks for. The runaway and hang checks are the module's own self tests (test_parser_union_terminates.py).

On a host whose numpy uses Accelerate (macOS) the budget test passes either way; it bites on a many-core Linux host.
"""

from __future__ import annotations

import json

import pytest

from tests.unit import _parse_watchdog as wd

THREAD_VARS = ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS")
_ENV_CHILD = "import json, os; print(json.dumps({k: os.environ.get(k) for k in %r}))" % (THREAD_VARS,)


@pytest.fixture
def many_threads_asked(monkeypatch):
    for var in THREAD_VARS:
        monkeypatch.setenv(var, "128")


def test_the_child_runs_blas_on_one_thread(many_threads_asked):
    r = wd.run_guarded(_ENV_CHILD)
    assert r.status == "done", r.stderr
    assert json.loads(r.stdout.strip().splitlines()[-1]) == {v: "1" for v in THREAD_VARS}


def test_a_normal_parse_stays_well_under_the_cpu_budget(many_threads_asked):
    r = wd.run_guarded_parse("MATCH (a) RETURN a.id AS i UNION MATCH (b) WITH b RETURN b.id AS i")
    assert r.status == "done", (r.status, r.cpu_s, r.stderr[-500:])
    assert r.cpu_s < wd.TIME_LIMIT_S / 2, f"a normal parse spent {r.cpu_s:.2f} s of CPU"
    assert r.payload()["outcome"] == "ok"


def test_the_caller_environment_is_otherwise_passed_on(monkeypatch):
    monkeypatch.setenv("IVG_WATCHDOG_PROBE", "kept")
    r = wd.run_guarded("import os; print(os.environ.get('IVG_WATCHDOG_PROBE'))")
    assert r.stdout.strip() == "kept"
