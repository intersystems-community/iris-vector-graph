"""LDBC SNB on sides A and B (and the same data for Neo4j and Memgraph via scripts/bench/competitors/run_snb.py).

    python -m scripts.bench.alt.snb setup
    python -m scripts.bench.alt.snb load  --root /tmp/snb1/out --out docs/performance/alt-bench/results/snb-1
    python -m scripts.bench.alt.snb query --root /tmp/snb1/out --out docs/performance/alt-bench/results/snb-1 --reps 3 --runs 3
"""

from __future__ import annotations

import argparse
import os
import subprocess
import time
from pathlib import Path

from scripts.bench.alt import kgbench as kg
from scripts.bench.alt import sides as sd

WORKER = Path(__file__).resolve().parent / "snb_worker.py"


def snb_sides(prefix="SNBBENCH"):
    return kg.prefixed_sides(prefix)


def run_worker(side, *args, log):
    env = {**os.environ, "PYTHONPATH": side.tree, "PYTHONDONTWRITEBYTECODE": "1", "IRIS_PORT": str(sd.PORT), "IRIS_HOST": sd.HOST,
           "IVG_IGNORE_NAMESPACE_CHECK": "1"}
    with open(log, "w") as f:
        return subprocess.run([kg.PYTHON, str(WORKER), *args], cwd=side.tree, env=env, stdout=f, stderr=subprocess.STDOUT, text=True).returncode


def query_legs(sides, args, out):
    """One worker process per query and side, interleaved A, B per query so drift hits both; results merged per run."""
    import json

    from scripts.bench.alt import snb_queries

    names = args.only or list(snb_queries.QUERIES)
    for run in range(1, args.runs + 1):
        merged = {letter: {"results": {}} for letter in sides}
        for name in names:
            plan = None  # the first side's plan: the second runs the same parameter sets
            for letter, side in sides.items():
                part = out / f"query_{letter}_{run}_{name}.json"
                t0 = time.time()
                extra = ["--plan", ",".join(str(x) for x in plan)] if plan else []
                rc = run_worker(side, "query", "--ns", side.namespace, "--root", args.root, "--out", str(part), "--reps", str(args.reps),
                                "--cap-s", str(args.cap_s), "--only", name, *extra, log=str(out / f"query_{letter}_{run}_{name}.txt"))
                print(f"[{letter}] {name} run {run}: rc={rc} {time.time() - t0:.0f}s", flush=True)
                if part.exists():
                    d = json.loads(part.read_text())
                    plan = plan or (d["results"].get(name) or {}).get("plan")
                    merged[letter]["ns"] = d["ns"]
                    merged[letter]["params"] = d["params"]
                    merged[letter]["results"].update(d["results"])
        for letter, doc in merged.items():
            (out / f"query_{letter}_{run}.json").write_text(json.dumps(doc, indent=2))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("step", choices=["setup", "load", "query", "update", "weights"])
    ap.add_argument("--side", nargs="+", choices=["A", "B"], default=["A", "B"])
    ap.add_argument("--root", default=sd.data_root("snb1"))
    ap.add_argument("--out", default="docs/performance/alt-bench/results/snb-1")
    ap.add_argument("--runs", type=int, default=1, help="query: interleaved repetitions of the whole query set per side")
    ap.add_argument("--reps", type=int, default=3, help="query: passes over the parameter sets inside one run")
    ap.add_argument("--only", nargs="+")
    ap.add_argument("--n", type=int, default=200, help="update: statements per update type")
    ap.add_argument("--id-offset", type=int, default=0, help="update: shift the new ids so a repeated run never recreates earlier nodes")
    ap.add_argument("--seed", type=int, default=42, help="update: run r draws its parameters with seed + r - 1, so repeated runs add different edges")
    ap.add_argument("--cap-s", type=float, default=60.0)
    ap.add_argument("--prefix", default="SNBBENCH", help="namespace prefix: SNBBENCH (SF0.1, engine-loaded) or SNB1 (SF1, bulk-loaded)")
    args = ap.parse_args(argv)
    sides = {k: v for k, v in snb_sides(args.prefix).items() if k in args.side}
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if args.step == "setup":
        kg.setup(sides)
        return 0
    if args.step == "query":
        return query_legs(sides, args, out)
    for run in range(1, args.runs + 1):
        for letter, side in sides.items():
            tag = f"{args.step}_{letter}_{run}"
            extra = ["--root", args.root, "--out", str(out / f"{tag}.json")]
            if args.step == "update":
                extra += ["--n", str(args.n), "--id-offset", str(args.id_offset + (run - 1) * 1_000_000), "--seed", str(args.seed + run - 1)]
            if args.step == "query":
                extra += ["--reps", str(args.reps)] + (["--only", *args.only] if args.only else [])
            t0 = time.time()
            rc = run_worker(side, args.step, "--ns", side.namespace, *extra, log=str(out / f"{tag}.txt"))
            print(f"[{letter}] {args.step} run {run}: rc={rc} {time.time() - t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
