"""Worker half of the KGBENCH runner: stdlib only, so it runs under either side's Python tree (PYTHONPATH=<tree>) as a script.

    PYTHONPATH=/tmp/ivg-src-A python scripts/bench/alt/kg_worker.py ingest --ns KGBENCHA --limit 5000000 --out run.json
    PYTHONPATH=/tmp/ivg-src-A python scripts/bench/alt/kg_worker.py query --ns KGBENCHA
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

DATASETS = Path(os.environ.get("IVG_DATASETS", "~/ws/iris-datasets")).expanduser()
# the order of ~/ws/iris-datasets/ingest_all.py: the 535,116,500 edges of KGBENCH
FILES = (
    "re2-tt/converted/all_traces.tsv",
    "re2-ob/converted/all_traces.tsv",
    "re2-tt/converted/all_logs.tsv",
    "re2-ob/converted/all_logs.tsv",
    "re2-tt/converted/all_metrics.tsv",
    "re2-ob/converted/all_metrics.tsv",
    "re1-tt/converted/all_metrics.tsv",
)
# line counts of the converted files (ingest_all.py); they sum to the 535,116,500 edges of KGBENCH
FILE_ROWS = (67_345_051, 34_853_232, 21_291_651, 15_224_545, 202_081_236, 54_113_414, 140_207_371)
BATCH = 500  # edges per bulk call, as ingest_all.py (MAXSTRING safe)


def chunk_plan(chunk, total_limit=None):
    """[(file_index, skip, limit)] covering the files in order in pieces of at most `chunk` edges."""
    plan, done = [], 0
    for i, rows in enumerate(FILE_ROWS):
        pos = 0
        while pos < rows:
            n = min(chunk, rows - pos)
            if total_limit is not None:
                n = min(n, total_limit - done)
                if n <= 0:
                    return plan
            plan.append((i, pos, n))
            pos += n
            done += n
    return plan


def read_edges(path, skip=0, limit=None):
    """Edges of one converted TSV (ts, source, predicate, target, weight); malformed lines are skipped."""
    n = 0
    with open(path, errors="replace") as f:
        for i, line in enumerate(f):
            if i < skip:
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 5:
                continue
            try:
                edge = {"ts": int(parts[0]), "s": parts[1], "p": parts[2], "o": parts[3], "w": float(parts[4])}
            except ValueError:
                continue
            yield edge
            n += 1
            if limit is not None and n >= limit:
                return


def ingest(engine, paths, batch=BATCH, report_every=1_000_000, limit=None, skip=0, clock=time.perf_counter):
    """Stream the files into the engine in batches; returns a progress record every `report_every` edges."""
    progress, buf, total = [], [], 0
    t0 = clock()
    last = 0
    remaining = limit

    def flush():
        nonlocal buf, total, last
        if not buf:
            return
        engine.bulk_create_edges_temporal(buf)
        total += len(buf)
        buf = []
        if total - last >= report_every or not progress:
            now = clock()
            progress.append({"edges": total, "elapsed_s": round(now - t0, 3), "edges_per_s": round(total / max(now - t0, 1e-9), 1)})
            last = total

    for path in paths:
        for edge in read_edges(path, skip=skip, limit=remaining):
            buf.append(edge)
            if len(buf) >= batch:
                flush()
        skip = 0
        if remaining is not None:
            remaining = max(0, limit - total - len(buf))
            if remaining == 0:
                break
    flush()
    now = clock()
    if not progress or progress[-1]["edges"] != total:
        progress.append({"edges": total, "elapsed_s": round(now - t0, 3), "edges_per_s": round(total / max(now - t0, 1e-9), 1)})
    return progress


def parse_bench_full(text):
    """`RES <name> <ms>` lines that bench_full.py prints next to its human-readable table."""
    out = {}
    for line in text.splitlines():
        m = re.match(r"RES (\S+) ([0-9.eE+-]+)$", line.strip())
        if m:
            try:
                out[m.group(1)] = float(m.group(2))
            except ValueError:
                pass
    return out


# ----------------------------------------------------------------------------------------------------- command line


def _connect(ns):
    import iris

    return iris.connect(
        os.environ.get("IRIS_HOST", "localhost"),
        int(os.environ.get("IRIS_PORT", "31972")),
        ns,
        os.environ.get("IRIS_USERNAME", "_SYSTEM"),
        os.environ.get("IRIS_PASSWORD", "SYS"),
    )


def _run_ingest(args):
    from iris_vector_graph.engine import IRISGraphEngine

    engine = IRISGraphEngine(_connect(args.ns))
    if args.file_index is not None:
        paths = [DATASETS / FILES[args.file_index]]
    else:
        paths = [DATASETS / f for f in FILES[: args.files]] if args.files else [DATASETS / f for f in FILES]
    progress = ingest(engine, paths, limit=args.limit, skip=args.skip, report_every=args.report_every)
    for p in progress:
        print(f"  {p['edges']:>13,} edges  {p['elapsed_s']:>9.1f}s  {p['edges_per_s']:>10,.0f} e/s", flush=True)
    Path(args.out).write_text(json.dumps({"ns": args.ns, "files": [str(p) for p in paths], "limit": args.limit, "progress": progress}, indent=2))


def _run_query(args):
    """Run scripts/bench/bench_full.py --query-only with this tree on the import path; prints its RES lines."""
    tree = os.environ["PYTHONPATH"].split(os.pathsep)[0]
    src = Path(args.bench_full).read_text()
    sys.argv = ["bench_full.py", "--query-only"]
    sys.path.insert(0, tree)
    os.environ["IRIS_NAMESPACE"] = args.ns
    exec(compile(src, args.bench_full, "exec"), {"__file__": os.path.join(tree, "scripts", "bench", "bench_full.py"), "__name__": "__main__"})


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    pi = sub.add_parser("ingest")
    pi.add_argument("--ns", required=True)
    pi.add_argument("--limit", type=int)
    pi.add_argument("--skip", type=int, default=0)
    pi.add_argument("--files", type=int, help="only the first N files of the ingest order")
    pi.add_argument("--file-index", type=int, help="exactly this file of the ingest order (with --skip/--limit: one chunk of it)")
    pi.add_argument("--report-every", type=int, default=1_000_000)
    pi.add_argument("--out", required=True)
    pq = sub.add_parser("query")
    pq.add_argument("--ns", required=True)
    pq.add_argument("--bench-full", default=str(Path(__file__).resolve().parents[2] / "bench" / "bench_full.py"))
    args = ap.parse_args(argv)
    {"ingest": _run_ingest, "query": _run_query}[args.cmd](args)


if __name__ == "__main__":
    main()
