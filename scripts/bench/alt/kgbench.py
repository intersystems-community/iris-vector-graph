"""KGBENCH-scale temporal graph for sides A and B: ingest the RCAEval traces/logs/metrics, then run the query benchmark.

The 535M-edge KGBENCH namespace of `scripts/bench/bench_full.py` was built by `~/ws/iris-datasets/ingest_all.py` from the converted
RE2-TT, RE2-OB and RE1-TT files. This module builds the same data into `KGBENCHA`/`KGBENCHB` (one per side, own databases,
journaling off) and times the ingest and the `bench_full.py` query section on each, so the baseline and the alternative are
compared on the same temporal graph at the same size.

    python -m scripts.bench.alt.kgbench setup
    python -m scripts.bench.alt.kgbench ingest --side A --limit 5000000 --out docs/performance/alt-bench/results/kgbench-1
    python -m scripts.bench.alt.kgbench query --side A --out docs/performance/alt-bench/results/kgbench-1
"""

from __future__ import annotations

from dataclasses import replace

from scripts.bench.alt import sides as sd
from scripts.bench.alt.kg_worker import BATCH, DATASETS, FILES, ingest, parse_bench_full, read_edges  # noqa: F401


def prefixed_sides(prefix):
    """Sides A and B with namespaces ``<prefix>A``/``<prefix>B``; trees and refs are the ones of the other suites."""
    return {k: replace(v, namespace=f"{prefix}{k}") for k, v in sd.SIDES.items()}


def kg_sides():
    return prefixed_sides("KGBENCH")


import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from scripts.bench.alt import setup_sides as su

REPO = Path(__file__).resolve().parents[3]
WORKER = REPO / "scripts" / "bench" / "alt" / "kg_worker.py"
PYTHON = su.VENV_PYTHON


def journal_off_script(ns):
    """ObjectScript (run in %SYS): turn global journaling off for the namespace's own database (a benchmark scratch DB)."""
    sd.check_namespace(ns)
    return f"""Set db=##class(SYS.Database).%OpenId("/usr/irissys/mgr/{ns.lower()}/")
Set db.GlobalJournalState=2
Set sc=db.%Save()
Write "RESULT journal ",db.GlobalJournalState," ",$System.Status.IsOK(sc),!
Halt
"""


def setup(sides):
    for letter, side in sides.items():
        print(f"[{letter}] namespace", su.create_namespace(side.namespace), flush=True)
        out = su.session("%SYS", journal_off_script(side.namespace))
        print(f"[{letter}]", [ln for ln in out.splitlines() if ln.startswith("RESULT")][-1:], flush=True)
        print(f"[{letter}] schema", su.init_schema(side), flush=True)
        print(f"[{letter}] deploy", su.deploy_classes(side), flush=True)


def _worker_env(side):
    return {**os.environ, "PYTHONPATH": side.tree, "PYTHONDONTWRITEBYTECODE": "1", "IRIS_PORT": str(sd.PORT), "IRIS_HOST": sd.HOST,
            "IVG_IGNORE_NAMESPACE_CHECK": "1"}


def run_worker(side, *args, log=None):
    cmd = [PYTHON, str(WORKER), *args]
    out = open(log, "w") if log else None
    try:
        return subprocess.run(cmd, cwd=side.tree, env=_worker_env(side), stdout=out, stderr=subprocess.STDOUT if out else None, text=True).returncode
    finally:
        if out:
            out.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("step", choices=["setup", "ingest", "query"])
    ap.add_argument("--side", choices=["A", "B"], nargs="+", default=["A", "B"])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--skip", type=int, default=0)
    ap.add_argument("--files", type=int)
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--rep-start", type=int, default=1, help="number of the first repetition (to add reps to an existing run)")
    ap.add_argument("--chunk", type=int, help="ingest: edges per chunk; chunks alternate between the sides")
    ap.add_argument("--out", default="docs/performance/alt-bench/results/kgbench-1")
    args = ap.parse_args(argv)
    sides = {k: v for k, v in kg_sides().items() if k in args.side}
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if args.step == "setup":
        setup(sides)
        return 0
    if args.step == "ingest" and args.chunk:
        # alternate A and B chunk by chunk so machine drift hits both; resumable: a finished chunk's json is not redone
        from scripts.bench.alt.kg_worker import chunk_plan

        for k, (fi, skip, n) in enumerate(chunk_plan(args.chunk, args.limit)):
            for letter, side in sides.items():
                done = out / f"ingest_{letter}_c{k:03d}.json"
                if done.exists():
                    continue
                t0 = time.time()
                rc = run_worker(side, "ingest", "--ns", side.namespace, "--file-index", str(fi), f"--skip={skip}", f"--limit={n}",
                                "--report-every", str(n), "--out", str(done), log=str(out / f"ingest_{letter}_c{k:03d}.txt"))
                print(f"[{letter}] chunk {k} file {fi} skip {skip:,} n {n:,}: rc={rc} {time.time() - t0:.0f}s", flush=True)
                if rc != 0:
                    return rc
        return 0
    for rep in range(args.rep_start, args.rep_start + args.reps):
        for letter, side in sides.items():
            tag = f"{args.step}_{letter}_{rep}"
            extra = ["--out", str(out / f"{tag}.json")] if args.step == "ingest" else []
            if args.step == "ingest":
                extra += [f"--{k}={v}" for k, v in (("limit", args.limit), ("files", args.files)) if v] + [f"--skip={args.skip}"]
            t0 = time.time()
            rc = run_worker(side, args.step, "--ns", side.namespace, *extra, log=str(out / f"{tag}.txt"))
            print(f"[{letter}] {args.step} rep {rep}: rc={rc} {time.time() - t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
