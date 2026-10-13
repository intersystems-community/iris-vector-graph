#!/usr/bin/env python3
"""Run benchmark suites against side A (baseline) and side B (alternative), interleaved (spec 250).

For every suite, for rep 1..N, for side in A, B: wait for a quiet machine, run the suite once in the side's tree with
the side's namespace and Python sources, and store

    <results>/<run-id>/<suite>_<side>_<rep>.txt     stdout and stderr
    <results>/<run-id>/<suite>_<side>_<rep>.json    {suite, side, rep, status, metrics{name: ms}, ...}
    <results>/<run-id>/<suite>_<side>_<rep>.out/    the suite's own artefacts (JSON, junit.xml)
    <results>/<run-id>/manifest.json                commits, env, load averages

Runs are serial. Suites that need a dataset get it loaded into each side's namespace once (sentinel
``^IVGBench("dataset")``); a suite that wipes or dirties the namespace invalidates the sentinel, and data-dependent
suites are scheduled first so that rarely happens.

    python scripts/bench/alt/run_matrix.py --list
    python scripts/bench/alt/run_matrix.py --suites bench-q1q6-s cypher-parser-bench --reps 3 --run-id 20261013-a
    python scripts/bench/alt/run_matrix.py --quick --reps 1 --run-id smoke
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.bench.alt import compare, setup_sides  # noqa: E402
from scripts.bench.alt import sides as sd  # noqa: E402
from scripts.bench.alt import suites as su  # noqa: E402
from scripts.bench.alt.metrics import (  # noqa: E402,F401  (re-exported for callers and tests)
    flatten_numbers,
    metrics_from_pytest_durations,
    metrics_from_stdout,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
RESULTS_ROOT = REPO_ROOT / "docs" / "performance" / "alt-bench" / "results"
PYTHON = setup_sides.VENV_PYTHON
SHIM_DIR = "scripts/bench/alt/shim"

# ----------------------------------------------------------------------------------------- quiet machine

_WRAPPER_PREFIXES = (
    "zsh -c",
    "/bin/zsh -c",
    "bash -c",
    "/bin/bash -c",
    "sh -c",
    "/bin/sh -c",
    "-zsh",
    "-bash",
    "grep ",
    "/usr/bin/grep ",
    "rg ",
    "tail ",
    "less ",
    "vim ",
    "/usr/bin/vim ",
    "ps ",
)
_BUSY_RE = re.compile(r"pytest|benchmark|bench[_\w]*\.py|/bench\b|stress|ldbc|load_drkg|run_matrix|scale_benchmark|three_engine|neo4j_comparison|index_maintenance")
_NOT_BUSY_EXE = {"claude", "node", "grep", "rg", "vim", "nvim", "less", "tail", "cat", "ps", "sed", "awk", "git", "tmux", "ssh"}


def parse_ps(text: str):
    rows = []
    for line in text.splitlines():
        m = re.match(r"\s*(\d+)\s+(.*\S)\s*$", line)
        if m:
            rows.append((int(m.group(1)), m.group(2)))
    return rows


def busy_processes(rows, own_pids):
    busy = []
    for pid, cmd in rows:
        if pid in own_pids:
            continue
        if cmd.startswith(_WRAPPER_PREFIXES):
            continue
        exe = os.path.basename(cmd.split()[0])
        if exe in _NOT_BUSY_EXE:
            continue
        if _BUSY_RE.search(cmd):
            busy.append((pid, cmd))
    return busy


def ps_snapshot() -> str:
    return subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True).stdout


def _load():
    try:
        return [round(x, 2) for x in os.getloadavg()]
    except OSError:
        return []


def wait_quiet(ps_fn=ps_snapshot, sleep_fn=time.sleep, poll_s=30, max_wait_s=1800, own_pids=None, clock=time.monotonic):
    own = set(own_pids) if own_pids is not None else {os.getpid(), os.getppid()}
    info = {"quiet": False, "polls": 0, "busy": [], "load_before": _load()}
    start = clock()
    while True:
        info["polls"] += 1
        busy = busy_processes(parse_ps(ps_fn()), own)
        info["busy"] = [f"{p} {c[:120]}" for p, c in busy]
        if not busy:
            info["quiet"] = True
            break
        if clock() - start >= max_wait_s:
            break
        sleep_fn(poll_s)
    info["waited_s"] = round(clock() - start, 1)
    info["load_after"] = _load()
    return info


# ----------------------------------------------------------------------------------------- planning


def select_ids(ids, include_blocked=False):
    """(ids to run, ids skipped because the suite is blocked on this machine)."""
    keep, blocked = [], []
    for i in ids:
        (blocked if (su.SUITES_BY_ID[i].blocked and not include_blocked) else keep).append(i)
    return keep, blocked


def interleave_order(suite_ids, side_letters, reps):
    return [(s, side, rep) for s in suite_ids for rep in range(1, reps + 1) for side in side_letters]


def schedule(suites):
    """Data-dependent suites first, grouped by dataset, wiping suites last; otherwise registry order."""

    def key(item):
        i, s = item
        return (0 if s.data else (2 if s.wipes else 1), list(su.DATASETS).index(s.data) if s.data else 0, i)

    return [s for _, s in sorted(enumerate(suites), key=key)]


def build_env(side, suite, base=None, arno_lib=None):
    sd.check_namespace(side.namespace)
    env = dict(os.environ if base is None else base)
    env.pop("PYTHONPATH", None)
    env.update(
        {
            "PYTHONPATH": os.pathsep.join([side.tree, os.path.join(side.tree, SHIM_DIR)]),
            "PYTHONDONTWRITEBYTECODE": "1",
            "IRIS_HOST": sd.HOST,
            "IRIS_PORT": str(sd.PORT),
            "IRIS_NAMESPACE": side.namespace,
            "IRIS_USERNAME": sd.USER,
            "IRIS_PASSWORD": sd.PASSWORD,
            "IVG_NAMESPACE": side.namespace,
            "IVG_PORT": str(sd.PORT),
            "IVG_TEST_CONTAINER": sd.CONTAINER,
            "IVG_BENCH_SIDE": side.letter,
            "IVG_BENCH_NS": side.namespace,
            "IVG_BENCH_PORT": str(sd.PORT),
            "IVG_BENCH_HOST": sd.HOST,
            "IVG_BENCH_CONTAINER": sd.CONTAINER,
            # The stores warn when the connection's namespace is not USER; here it never is.
            "IVG_IGNORE_NAMESPACE_CHECK": "1",
        }
    )
    if arno_lib:
        env["ARNO_LIB"] = arno_lib
        env["IVG_ARNO_SO_PATH"] = arno_lib
    env.update(suite.env)
    return env


def expand_argv(argv, side, outdir):
    subs = {"{py}": PYTHON, "{out}": str(outdir), "{ns}": side.namespace, "{port}": str(sd.PORT), "{tree}": side.tree}
    out = []
    for a in argv:
        for k, v in subs.items():
            a = a.replace(k, v)
        out.append(a)
    return out


# ----------------------------------------------------------------------------------------- results


def write_rep(results_dir, *, suite, side, rep, status, stdout, metrics, extra=None):
    results_dir = Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    base = f"{suite}_{side}_{rep}"
    (results_dir / f"{base}.txt").write_text(stdout)
    doc = {"suite": suite, "side": side, "rep": rep, "status": status, "metrics": metrics, **(extra or {})}
    (results_dir / f"{base}.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return doc


def write_manifest(results_dir, *, run_id, sides, suites, reps, env, quiet, extra=None):
    results_dir = Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    doc = {
        "run_id": run_id,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "sides": sides,
        "suites": suites,
        "reps": reps,
        "env": env,
        "quiet": quiet,
        "python": sys.version.split()[0],
        **(extra or {}),
    }
    (results_dir / "manifest.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return doc


# ----------------------------------------------------------------------------------------- data

SENTINEL = ("IVGBench", "dataset")


def _native(side):
    import iris

    conn = iris.connect(sd.HOST, sd.PORT, side.namespace, sd.USER, sd.PASSWORD)
    return conn, iris.createIRIS(conn)


def reset_derived(side):
    """Clear the derived stores and caches in the side's namespace, and forget the loaded dataset (its adjacency is gone)."""
    conn, nat = _native(side)
    try:
        for name in ("^KG", "^NKG", "^IVG.AlgoCache", "^ArnoKG"):
            nat.kill(name)
    finally:
        conn.close()
    set_sentinel(side, None)


def dataset_state(side, key):
    """(sentinel value, node count) of the side's namespace."""
    conn, nat = _native(side)
    try:
        sentinel = nat.get(*SENTINEL)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM SQLUser.nodes")
        return (str(sentinel) if sentinel is not None else None), int(cur.fetchone()[0])
    finally:
        conn.close()


def set_sentinel(side, key):
    conn, nat = _native(side)
    try:
        if key is None:
            nat.kill(*SENTINEL)
        else:
            nat.set(key, *SENTINEL)
    finally:
        conn.close()


def ensure_data(side, key, env, log=print):
    """Load dataset ``key`` into the side's namespace unless it is there (sentinel and node count agree)."""
    ds = su.DATASETS[key]
    sentinel, nodes = dataset_state(side, key)
    if sentinel == key and nodes == ds.expect_nodes:
        return {"dataset": key, "loaded": False}
    log(f"  [{side.letter}] loading dataset {key} (sentinel={sentinel!r}, nodes={nodes}) ...")
    outdir = Path(side.tree) / ".load-out"
    outdir.mkdir(exist_ok=True)
    argv = expand_argv(ds.load_argv, side, outdir)
    t0 = time.perf_counter()
    rc, text, timed_out = run_command(argv, side.tree, env, 7200)
    took = round(time.perf_counter() - t0, 1)
    if rc != 0:
        raise RuntimeError(f"dataset {key} load failed on side {side.letter} (rc={rc}, timed out={timed_out}): {text[-1500:]}")
    _, nodes = dataset_state(side, key)
    if nodes != ds.expect_nodes:
        raise RuntimeError(f"dataset {key} on side {side.letter}: expected {ds.expect_nodes} nodes after load, found {nodes}")
    set_sentinel(side, key)
    return {"dataset": key, "loaded": True, "load_s": took}


# ----------------------------------------------------------------------------------------- one run


def is_pytest(suite) -> bool:
    return list(suite.argv[1:3]) == ["-m", "pytest"]


def status_for(returncode, suite) -> str:
    """ok; tests-failed (pytest exit 1: a gate or an assertion failed, timings are still valid); failed."""
    if returncode == 0:
        return "ok"
    if returncode == 1 and is_pytest(suite):
        return "tests-failed"
    if returncode in suite.gate_rc:
        return "tests-failed"
    return "failed"


def failed_tests_from_junit(path):
    import xml.etree.ElementTree as ET

    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return []
    out = []
    for tc in root.iter("testcase"):
        if tc.find("failure") is not None or tc.find("error") is not None:
            out.append(f"{tc.get('classname')}::{tc.get('name')}")
    return out


def run_command(argv, cwd, env, timeout):
    """Run argv in its own process group; on timeout kill the whole group. Returns (returncode, text, timed_out)."""
    import signal

    p = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        out, err = p.communicate(timeout=timeout)
        return p.returncode, out + (("\n--- stderr ---\n" + err) if err else ""), False
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, err = p.communicate()
        return None, (out or "") + (("\n--- stderr ---\n" + err) if err else ""), True


def run_one(suite, side, rep, results_dir, arno_lib=None, base_env=None, log=print, timeout=None):
    base = f"{suite.id}_{side.letter}_{rep}"
    outdir = Path(results_dir) / f"{base}.out"
    outdir.mkdir(parents=True, exist_ok=True)
    env = build_env(side, suite, base=base_env, arno_lib=arno_lib)
    argv = expand_argv(suite.argv, side, outdir)
    if suite.reset_derived:
        reset_derived(side)
    start_wall = time.time()
    t0 = time.perf_counter()
    status, rc, text = "ok", None, ""
    limit = timeout or suite.timeout_s
    rc, text, timed_out = run_command(argv, side.tree, env, limit)
    if timed_out:
        status = "failed"
        text += f"\n--- timeout after {limit}s ---\n"
    else:
        status = status_for(rc, suite)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    metrics = {}
    try:
        ctx = su.context(outdir, side.tree, start_wall)
        metrics = su.PARSERS[suite.parser](text, ctx)
        for pat in suite.collect:
            for f in sorted(Path(side.tree).glob(pat)):
                if f.stat().st_mtime >= start_wall - 1:
                    (outdir / f.name).write_bytes(f.read_bytes())
    except Exception as e:  # a parser problem must not lose the run
        text += f"\n--- metric extraction failed: {e!r} ---\n"
    metrics["wall_total"] = round(elapsed_ms, 1)
    return write_rep(
        results_dir,
        suite=suite.id,
        side=side.letter,
        rep=rep,
        status=status,
        stdout=text,
        metrics=metrics,
        extra={
            "returncode": rc,
            "elapsed_s": round(elapsed_ms / 1000.0, 2),
            "namespace": side.namespace,
            "argv": argv,
            "failed_tests": failed_tests_from_junit(outdir / "junit.xml") if is_pytest(suite) else [],
        },
    )


# ----------------------------------------------------------------------------------------- main


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--list", action="store_true", help="list registered suites and exit")
    ap.add_argument("--suites", nargs="+", help="suite ids")
    ap.add_argument("--quick", action="store_true", help="all suites flagged quick")
    ap.add_argument("--sides", nargs="+", default=["A", "B"], choices=["A", "B"])
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--run-id", default=time.strftime("%Y%m%d-%H%M%S"))
    ap.add_argument("--alt-ref", default=sd.DEFAULT_ALT_REF)
    ap.add_argument("--results-root", default=str(RESULTS_ROOT))
    ap.add_argument("--no-wait", action="store_true", help="do not wait for a quiet machine (smoke runs)")
    ap.add_argument("--max-wait", type=int, default=1800)
    ap.add_argument("--allow-neo4j", action="store_true")
    ap.add_argument("--include-blocked", action="store_true", help="also run suites known to hang or fail on this machine")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--timeout", type=int, help="cap every run at this many seconds (launch checks); a capped run is recorded as failed")
    ap.add_argument("--ensure-data", choices=sorted(su.DATASETS), help="load this dataset into the chosen sides and exit")
    args = ap.parse_args(argv)

    if args.ensure_data:
        table = sd.with_alt_ref(args.alt_ref)
        arno_lib = setup_sides.find_arno_so()
        for letter in args.sides:
            side = table[letter]
            state = ensure_data(side, args.ensure_data, build_env(side, su.Suite(id="load", argv=["x"]), arno_lib=arno_lib))
            print(f"[{letter}] {state}")
        return 0

    if args.list:
        for s in su.SUITES:
            flags = ",".join(f for f, on in (("quick", s.quick), ("wipes", s.wipes), ("arno", s.needs_arno), ("neo4j", s.needs_neo4j), (f"data={s.data}", s.data)) if on)
            print(f"{s.id:40s} {flags}")
        return 0

    ids = su.quick_ids() if args.quick else (args.suites or [])
    unknown = [i for i in ids if i not in su.SUITES_BY_ID]
    if unknown or not ids:
        print(f"unknown or no suites: {unknown or ids}; see --list", file=sys.stderr)
        return 2
    ids, blocked = select_ids(ids, args.include_blocked)
    chosen = schedule([su.SUITES_BY_ID[i] for i in ids])
    skipped = [s.id for s in chosen if s.needs_neo4j and not args.allow_neo4j]
    chosen = [s for s in chosen if s.id not in skipped]
    skipped += [f"{b} (blocked)" for b in blocked]

    table = sd.with_alt_ref(args.alt_ref)
    results_dir = Path(args.results_root) / args.run_id
    side_info = {}
    for letter in args.sides:
        side = table[letter]
        marker = Path(side.tree) / sd.MARKER
        if not marker.exists():
            print(f"side {letter}: {side.tree} is not set up; run scripts/bench/alt/setup_sides.py all", file=sys.stderr)
            return 2
        info = json.loads(marker.read_text())
        info.update({"namespace": side.namespace, "tree": side.tree, "imports": setup_sides.imported_package_path(side.tree, PYTHON)})
        side_info[letter] = info

    order = interleave_order([s.id for s in chosen], args.sides, args.reps)
    print(f"run {args.run_id}: {len(chosen)} suites x {args.reps} reps x sides {args.sides} = {len(order)} runs; skipped (neo4j): {skipped}")
    if args.dry_run:
        for o in order:
            print("  ", o)
        return 0

    arno_lib = setup_sides.find_arno_so()
    quiet_log = []
    write_manifest(results_dir, run_id=args.run_id, sides=side_info, suites=[s.id for s in chosen], reps=args.reps,
                   env={"IRIS_PORT": str(sd.PORT), "IVG_TEST_CONTAINER": sd.CONTAINER, "ARNO_LIB": arno_lib}, quiet=quiet_log,
                   extra={"skipped": skipped, "harness_commit": setup_sides.resolve_commit("HEAD", REPO_ROOT)})
    failures = 0
    for sid, letter, rep in order:
        suite, side = su.SUITES_BY_ID[sid], table[letter]
        q = {"suite": sid, "side": letter, "rep": rep}
        if args.no_wait:
            q.update({"quiet": None, "load_before": _load()})
        else:
            q.update(wait_quiet(max_wait_s=args.max_wait))
        try:
            if suite.data:
                q["data"] = ensure_data(side, suite.data, build_env(side, suite, arno_lib=arno_lib))
            doc = run_one(suite, side, rep, results_dir, arno_lib=arno_lib, timeout=args.timeout)
        except Exception as e:
            doc = write_rep(results_dir, suite=sid, side=letter, rep=rep, status="failed", stdout=f"runner error: {e!r}\n", metrics={}, extra={"error": repr(e)})
        q["load_after_run"] = _load()
        quiet_log.append(q)
        if suite.wipes:
            try:
                set_sentinel(side, None)
            except Exception:
                pass
        failures += doc["status"] != "ok"
        print(f"  {sid} {letter}#{rep}: {doc['status']} {doc.get('elapsed_s', '?')}s metrics={len(doc['metrics'])}", flush=True)
        # Keep the manifest current so an interrupted matrix still says what ran.
        write_manifest(results_dir, run_id=args.run_id, sides=side_info, suites=[s.id for s in chosen], reps=args.reps,
                       env={"IRIS_PORT": str(sd.PORT), "IVG_TEST_CONTAINER": sd.CONTAINER, "ARNO_LIB": arno_lib}, quiet=quiet_log,
                       extra={"skipped": skipped, "harness_commit": setup_sides.resolve_commit("HEAD", REPO_ROOT)})
    (results_dir / "report.md").write_text(compare.render_report(results_dir))
    print(f"report: {results_dir / 'report.md'}  ({failures} failed runs)")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
