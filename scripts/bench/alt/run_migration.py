"""Run scripts/bench_migration_4_0_0.py from side A's and side B's trees, interleaved, and write harness-style results.

    python -m scripts.bench.alt.run_migration --run-id migration-1 --reps 3 --nodes 50000 --vectors 50000

The script seeds the scratch namespace IVGLEGACY with the frozen 3.2.0 DDL, wipes Graph_KG there, and runs the 4.0.0 upgrade; it
reads the library from the tree it is run from, so running it from /tmp/ivg-src-A and /tmp/ivg-src-B compares the two
migration code paths. It cannot go through the harness connection shim (that refuses a namespace outside the benchmark
namespaces), so this driver runs it directly and writes the same ``<suite>_<side>_<rep>.json`` files.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

PY = sys.executable
TREES = {"A": "/tmp/ivg-src-A", "B": "/tmp/ivg-src-B"}
SUITE = "migration-4-0-0"


def parse(text):
    metrics = {}
    for m in re.finditer(r"^\s+step (\w+)\s+([\d.]+)s", text, re.M):
        metrics[f"step.{m.group(1)}"] = round(float(m.group(2)) * 1000, 3)
    m = re.search(r"^apply: ([\d.]+)s total", text, re.M)
    if m:
        metrics["apply.total"] = round(float(m.group(1)) * 1000, 3)
    for m in re.finditer(r"^\s+(_?\w+)\s+([\d.]+)s\s+\d+ calls", text, re.M):
        metrics[f"profile.{m.group(1)}"] = round(float(m.group(2)) * 1000, 3)
    refused = re.search(r"^refused: (.*)$", text, re.M)
    if refused and refused.group(1).strip() not in ("None", ""):
        metrics["refused"] = 1.0
    return metrics


def run(side, rep, outdir, args):
    argv = [
        PY, "-I", "scripts/bench_migration_4_0_0.py", "--nodes", str(args.nodes), "--vectors", str(args.vectors),
        "--dim", str(args.dim), "--docs", str(args.docs), "--edge-vectors", str(args.edge_vectors), "--steps", "all", "--apply",
    ]
    t0 = time.perf_counter()
    proc = subprocess.run(argv, cwd=TREES[side], capture_output=True, text=True, timeout=3600)
    elapsed = time.perf_counter() - t0
    text = proc.stdout + proc.stderr
    metrics = parse(text)
    status = "ok" if proc.returncode == 0 and "apply.total" in metrics else "failed"
    (outdir / f"{SUITE}_{side}_{rep}.txt").write_text(text)
    (outdir / f"{SUITE}_{side}_{rep}.json").write_text(
        json.dumps({"suite": SUITE, "side": side, "rep": rep, "status": status, "metrics": metrics, "returncode": proc.returncode, "elapsed_s": round(elapsed, 1), "argv": argv})
    )
    print(f"  {SUITE} {side}#{rep}: {status} {elapsed:.0f}s apply={metrics.get('apply.total')}", flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--nodes", type=int, default=50000)
    ap.add_argument("--vectors", type=int, default=50000)
    ap.add_argument("--dim", type=int, default=128)
    ap.add_argument("--docs", type=int, default=10000)
    ap.add_argument("--edge-vectors", type=int, default=10000)
    ap.add_argument("--settle", type=float, default=0.0, help="seconds to wait after every run, the same for both sides, so IRIS's background work on the dropped/created tables can finish before the next run's DDL")
    ap.add_argument("--order", default="AB", help="side order within each rep; AB then BA across reps cancels an ordering effect")
    args = ap.parse_args(argv)
    outdir = Path("docs/performance/alt-bench/results") / args.run_id
    outdir.mkdir(parents=True, exist_ok=True)
    for rep in range(1, args.reps + 1):
        order = args.order if rep % 2 else args.order[::-1]  # alternate AB, BA, AB, ...
        for side in order:
            run(side, rep, outdir, args)
            if args.settle:
                time.sleep(args.settle)


if __name__ == "__main__":
    main()
