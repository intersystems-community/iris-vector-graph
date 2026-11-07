#!/usr/bin/env python3
"""Spec 262's speed gate (T066, research R8, FR-020a; 4.6.0 R275): no path more than 5% slower than `a42db224`.

Three sides, each a worker process (`schema_gate_worker.py`) running one IVG code state against its own namespace:

    base     a42db224 (the last code state before spec 262)   IVG262BASE      Graph_KG
    default  this checkout                                       --default-ns   Graph_KG
    alt      this checkout                                       --alt-ns       IVGALT

Every path runs on all three, interleaved (one run of a path on each side in turn, the sides' order rotated from run to
run, so no side always goes first after another path's writes), at least 5 runs each. The gate compares each side's
median with the base's median and fails if any path on either schema is more than 5% slower. Results go to
`<out>/gate.json` and `<out>/gate.md` (default `~/.cache/ivg-bench/spec262/gate/`); the exit status is 0 when every
path passed, 1 otherwise, 2 when a side could not be set up.

    python scripts/bench/schema_gate.py                       # the release gate (quiet container)
    python scripts/bench/schema_gate.py --scale small --runs 5 # the harness's own end-to-end check

The base's code is the worktree `scripts/fixtures/build_release_namespace.py a42db224 IVG262BASE` leaves under
`~/.cache/ivg-bench/releases/a42db224`; the namespaces must exist and be set up first (see the worker's docstring).
Any argument this script does not know is passed to every worker.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

REPO = Path(__file__).resolve().parents[2]
BASE_COMMIT = "a42db224"
THRESHOLD = 0.05
MIN_RUNS = 5
SIDES = ("base", "default", "alt")
COMPARED = ("default", "alt")
#: FR-020a's paths, then R275's two.
PATHS = (
    "node_write_row",
    "edge_write_row",
    "node_write_bulk",
    "edge_write_bulk",
    "bare_node_retire",
    "graph_erase",
    "temporal_edge_write",
    "cypher_one_hop",
    "cypher_var_length",
    "pagerank",
    "ledger_commit",
    "collect_node_property",
    "nkg_first_read_after_erase",
)
#: What a path's timed body is, where the sides differ (said in the report's row).
NOTES = {
    "nkg_first_read_after_erase": "the first correct read after an erase: base BuildNKG + SCCJson (a42db224 has no "
                                  "EnsureNKG); current SCCJson, its rebuild inside",
}
#: Paths whose answer changed by design since a42db224: a different answer is reported, not failed.
#: pagerank: 4.6.0 scores every node, a node with no edge too (R225, R306).
ANSWER_MAY_DIFFER = {"pagerank"}
DEFAULT_OUT = Path.home() / ".cache" / "ivg-bench" / "spec262" / "gate"


# ------------------------------------------------------------------------------------------------- the comparison
@dataclass
class Verdict:
    path: str
    side: str
    ok: bool
    reason: str
    median: Optional[float]
    base_median: Optional[float]
    ratio: Optional[float]
    runs: List[Optional[float]]
    base_runs: List[Optional[float]]


def slower_than_allowed(base: float, cand: float, threshold: float = THRESHOLD) -> bool:
    """Whether `cand` is more than `threshold` slower than `base`. Exactly at the limit is allowed, whatever the
    floating point rounding of `base * (1 + threshold)`."""
    limit = base * (1.0 + threshold)
    return cand > limit and not math.isclose(cand, limit, rel_tol=1e-9)


def _check_runs(runs: Optional[Sequence[Optional[float]]], min_runs: int, who: str) -> Optional[str]:
    if not runs:
        return f"{who} not measured"
    if any(r is None for r in runs):
        return f"{who}: {sum(r is None for r in runs)} of {len(runs)} runs failed"
    if len(runs) < min_runs:
        return f"{who}: {len(runs)} runs, the gate needs {min_runs}"
    return None


def compare(timings: Dict[str, Dict[str, List[Optional[float]]]], threshold: float = THRESHOLD,
            min_runs: int = MIN_RUNS, answers: Optional[Dict[str, Dict[str, list]]] = None) -> List[Verdict]:
    """One verdict per (path, compared side): its median against the base's median of the same path.

    `answers` (side -> path -> one answer per run, a count the timed body produced) guards the comparison: a side
    whose answers are not the base's did other work, and its path fails, unless the path is in ANSWER_MAY_DIFFER."""
    answers = answers or {}
    paths = sorted({p for side in timings.values() for p in side})
    out = []
    for path in paths:
        base_runs = timings.get("base", {}).get(path)
        for side in COMPARED:
            runs = timings.get(side, {}).get(path)
            problem = _check_runs(base_runs, min_runs, "base") or _check_runs(runs, min_runs, side)
            if problem:
                out.append(Verdict(path, side, False, problem, None, None, None, list(runs or []),
                                   list(base_runs or [])))
                continue
            med, base_med = statistics.median(runs), statistics.median(base_runs)
            ratio = med / base_med if base_med else math.inf
            slow = slower_than_allowed(base_med, med, threshold)
            reason = (f"{(ratio - 1) * 100:+.1f}% against {BASE_COMMIT}: slower than the {threshold:.0%} allowed"
                      if slow else f"{(ratio - 1) * 100:+.1f}%")
            ok = not slow
            base_ans, side_ans = answers.get("base", {}).get(path), answers.get(side, {}).get(path)
            if base_ans is not None and side_ans is not None and base_ans != side_ans:
                by_design = path in ANSWER_MAY_DIFFER
                reason += (f"; answer {side_ans} where the base answered {base_ans}"
                           + (" (different by design)" if by_design else ": not the same work"))
                ok = ok and by_design
            out.append(Verdict(path, side, ok, reason, med, base_med, ratio, list(runs), list(base_runs)))
    return out


def gate_passes(verdicts: Sequence[Verdict]) -> bool:
    return bool(verdicts) and all(v.ok for v in verdicts)


def schedule(paths: Sequence[str], sides: Sequence[str], runs: int) -> List[tuple]:
    """`(run, path, side)` in order: for each run, each path on every side back to back, the sides rotated per run."""
    order = []
    for run in range(runs):
        k = run % len(sides)
        rotated = list(sides[k:]) + list(sides[:k])
        for path in paths:
            order.extend((run, path, side) for side in rotated)
    return order


# ------------------------------------------------------------------------------------------------- the report
def verdicts_json(verdicts: Sequence[Verdict]) -> List[dict]:
    return [asdict(v) for v in verdicts]


def _fmt(x: Optional[float]) -> str:
    return "-" if x is None else f"{x:.4f}"


def render_markdown(verdicts: Sequence[Verdict], meta: dict) -> str:
    passed = gate_passes(verdicts)
    lines = [
        f"# Spec 262 speed gate: {'PASS' if passed else 'FAIL'}",
        "",
        f"Base `{meta.get('base_commit', BASE_COMMIT)}`; {meta.get('runs', '?')} runs per path and side, interleaved; "
        f"median seconds; a path fails above +{THRESHOLD:.0%}. Scale: {meta.get('scale', '?')}. "
        f"Started {meta.get('started', '?')}, {meta.get('elapsed_s', '?')} s.",
        "",
        "| path | side | base median | median | change | verdict |",
        "| ---- | ---- | ----------- | ------ | ------ | ------- |",
    ]
    for v in verdicts:
        change = "-" if v.ratio is None else f"{(v.ratio - 1) * 100:+.1f}%"
        note = f" ({NOTES[v.path]})" if v.path in NOTES else ""
        lines.append(f"| {v.path} | {v.side} | {_fmt(v.base_median)} | {_fmt(v.median)} | {change} | "
                     f"{'ok' if v.ok else 'FAIL: ' + v.reason}{note} |")
    for side, info in (meta.get("sides") or {}).items():
        lines.append("")
        lines.append(f"- {side}: {info.get('namespace')} ({info.get('schema')}), code {info.get('version')}")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------------------------------------- the workers
class Worker:
    """One side: a process with that side's IVG first on its path, answering one JSON line per request."""

    def __init__(self, side: str, python: str, worker: str, code: str, namespace: str, schema: str, scale: str,
                 extra: Sequence[str]):
        env = dict(os.environ, PYTHONPATH=code)
        cmd = [python, "-u", worker, "--side", side, "--namespace", namespace, "--schema", schema, "--scale", scale,
               *extra]
        self.side = side
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env,
                                     cwd=code)

    def ask(self, request: dict) -> dict:
        self.proc.stdin.write(json.dumps(request) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError(f"the {self.side} worker exited (status {self.proc.poll()})")
        return json.loads(line)

    def close(self) -> None:
        try:
            self.proc.stdin.write(json.dumps({"op": "quit"}) + "\n")
            self.proc.stdin.close()
        except (BrokenPipeError, ValueError):
            pass
        self.proc.wait(timeout=60)


def parse(argv):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--out", default=str(DEFAULT_OUT))
    p.add_argument("--runs", type=int, default=MIN_RUNS)
    p.add_argument("--scale", default="full", help="full (the release gate) or small (the harness's own check)")
    p.add_argument("--paths", default=",".join(PATHS))
    p.add_argument("--python", default=sys.executable)
    p.add_argument("--worker", default=str(Path(__file__).with_name("schema_gate_worker.py")))
    p.add_argument("--base-code", default=str(Path.home() / ".cache" / "ivg-bench" / "releases" / BASE_COMMIT))
    p.add_argument("--base-ns", default="IVG262BASE")
    p.add_argument("--default-ns", default="IVG460FXGATE")
    p.add_argument("--alt-ns", default="IVG460FXGALT")
    p.add_argument("--alt-schema", default="IVGALT")
    p.add_argument("--dry-plan", action="store_true", help="print the sides, paths and interleaved order; run nothing")
    return p.parse_known_args(argv)


def main(argv=None) -> int:
    args, extra = parse(argv if argv is not None else sys.argv[1:])
    paths = [p for p in args.paths.split(",") if p]
    unknown = sorted(set(paths) - set(PATHS))
    if unknown:
        print(f"unknown paths: {unknown}", file=sys.stderr)
        return 2
    sides = {
        "base": (args.base_code, args.base_ns, "Graph_KG"),
        "default": (str(REPO), args.default_ns, "Graph_KG"),
        "alt": (str(REPO), args.alt_ns, args.alt_schema),
    }
    if args.dry_plan:
        for side, (code, ns, schema) in sides.items():
            print(f"side {side}: {ns} ({schema}), code {code}")
        for run, path, side in schedule(paths, SIDES, args.runs):
            print(f"run {run}  {path:28s} {side}")
        return 0
    if not Path(args.base_code).is_dir():
        print(f"no {BASE_COMMIT} checkout at {args.base_code}: run scripts/fixtures/build_release_namespace.py "
              f"{BASE_COMMIT} {args.base_ns} first", file=sys.stderr)
        return 2
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    started, t0 = time.strftime("%Y-%m-%dT%H:%M:%S%z"), time.monotonic()
    workers: Dict[str, Worker] = {}
    meta_sides: Dict[str, dict] = {}
    timings: Dict[str, Dict[str, List[Optional[float]]]] = {s: {p: [] for p in paths} for s in SIDES}
    answers: Dict[str, Dict[str, list]] = {s: {p: [] for p in paths} for s in SIDES}
    errors: List[dict] = []
    try:
        for side, (code, ns, schema) in sides.items():
            workers[side] = Worker(side, args.python, args.worker, code, ns, schema, args.scale, extra)
            answer = workers[side].ask({"op": "setup", "paths": paths})
            if not answer.get("ok"):
                print(f"{side}: setup failed: {answer.get('error')}", file=sys.stderr)
                return 2
            meta_sides[side] = {"namespace": ns, "schema": schema, "code": code, "version": answer.get("version")}
        for run, path, side in schedule(paths, SIDES, args.runs):
            answer = workers[side].ask({"op": "run", "path": path, "run": run})
            if answer.get("ok"):
                timings[side][path].append(float(answer["seconds"]))
                answers[side][path].append(answer.get("answer"))
            else:
                timings[side][path].append(None)
                errors.append({"side": side, "path": path, "run": run, "error": answer.get("error")})
    finally:
        for w in workers.values():
            w.close()
    verdicts = compare(timings, answers=answers)
    meta = {"base_commit": BASE_COMMIT, "runs": args.runs, "scale": args.scale, "started": started,
            "elapsed_s": round(time.monotonic() - t0, 1), "sides": meta_sides}
    passed = gate_passes(verdicts)
    (out / "gate.json").write_text(json.dumps(
        {"passed": passed, "threshold": THRESHOLD, **meta, "verdicts": verdicts_json(verdicts), "errors": errors,
         "timings": timings, "answers": answers}, indent=2))
    (out / "gate.md").write_text(render_markdown(verdicts, meta))
    print(render_markdown(verdicts, meta))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
