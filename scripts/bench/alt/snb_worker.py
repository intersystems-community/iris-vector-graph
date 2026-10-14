"""IVG half of the LDBC SNB benchmark: load the raw CSVs into one side's namespace, then run the query set.

Runs as a script under either side's tree (PYTHONPATH=<tree>), so it uses only the stdlib, `iris` and that tree's engine:

    PYTHONPATH=/tmp/ivg-src-A python scripts/bench/alt/snb_worker.py load  --ns SNBBENCHA --root /tmp/snb1/out --out load.json
    PYTHONPATH=/tmp/ivg-src-A python scripts/bench/alt/snb_worker.py query --ns SNBBENCHA --root /tmp/snb1/out --out q.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
import time
from pathlib import Path

try:
    from scripts.bench.alt import snb_model as model
    from scripts.bench.alt import snb_queries as queries
except ImportError:
    import snb_model as model
    import snb_queries as queries


def node_batches(nodes, size=20_000):
    batch = []
    for n in nodes:
        batch.append({"id": model.node_key(n["label"], n["id"]), "labels": [n["label"]], "properties": n["props"]})
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def edge_batches(edges, size=50_000):
    batch = []
    for e in edges:
        batch.append({"s": model.node_key(*e["src"]), "p": e["type"], "o": model.node_key(*e["dst"])})
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _norm(v):
    if isinstance(v, float) and v == int(v):
        return int(v)
    return v


def signature(rows):
    """Order-insensitive digest of a result set; 1.0 and 1 compare equal (engines differ on numeric types)."""
    parts = sorted(json.dumps([_norm(c) for c in row], default=str, sort_keys=True) for row in rows)
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


def plan_leg(first_ms, params, warmup, reps):
    """(parameter sets, warm-up passes, timed passes): a slow first call shrinks the leg so it still finishes."""
    if first_ms <= 1_000:
        return params, warmup, reps
    if first_ms <= 10_000:
        return min(params, 5), 0, 1
    return 1, 0, 1


def call_with_cap(fn, cap_s):
    """(finished, value). A call that outlives the cap is abandoned; the caller then exits the process to drop its connection."""
    import threading

    box = {}

    def run():
        try:
            box["v"] = fn()
        except Exception as ex:  # surfaced to the caller as a finished call with the exception as value
            box["v"] = ex

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(cap_s)
    if t.is_alive():
        return False, None
    return True, box.get("v")


def _engine(ns):
    import iris
    from iris_vector_graph.engine import IRISGraphEngine

    conn = iris.connect(
        os.environ.get("IRIS_HOST", "localhost"), int(os.environ.get("IRIS_PORT", "31972")), ns,
        os.environ.get("IRIS_USERNAME", "_SYSTEM"), os.environ.get("IRIS_PASSWORD", "SYS"),
    )
    return conn, IRISGraphEngine(conn)


def run_load(args):
    conn, engine = _engine(args.ns)
    phases = {}
    t0 = time.perf_counter()
    n_nodes = 0
    for batch in node_batches(model.iter_nodes(args.root)):
        engine.bulk_create_nodes(batch)
        n_nodes += len(batch)
        print(f"  nodes {n_nodes:,}  {time.perf_counter() - t0:.0f}s", flush=True)
    phases["nodes_s"] = round(time.perf_counter() - t0, 2)
    t1 = time.perf_counter()
    n_edges = 0
    with engine.bulk_load_session() as session:
        for batch in edge_batches(model.iter_edges(args.root)):
            session.add_edges(batch)
            n_edges += len(batch)
            print(f"  edges {n_edges:,}  {time.perf_counter() - t1:.0f}s", flush=True)
    phases["edges_s"] = round(time.perf_counter() - t1, 2)
    phases["session_stats"] = dict(session.stats)
    phases["total_s"] = round(time.perf_counter() - t0, 2)
    out = {"ns": args.ns, "root": args.root, "nodes": n_nodes, "edges": n_edges, "phases": phases}
    Path(args.out).write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps(out, default=str))


def run_query(args):
    conn, engine = _engine(args.ns)
    plist = queries.sample_params(args.root, n=args.params, seed=args.seed)
    results = {}
    names = args.only or list(queries.QUERIES)
    for name in names:
        spec = queries.QUERIES[name]
        cypher = queries.ivg_cypher(spec)
        keep = spec.get("ivg_client_limit")
        sets = [{k: p[k] for k in spec["params"]} for p in plist] if spec["params"] else [{}]
        sigs, counts, times, err = [], [], [], None

        def one(ps):
            t = time.perf_counter()
            res = engine.execute_cypher(cypher, ps)
            rows = res.rows if hasattr(res, "rows") else res["rows"]
            if keep:
                rows = rows[:keep]
            return rows, (time.perf_counter() - t) * 1000

        finished, first = call_with_cap(lambda: one(sets[0]), args.cap_s)
        if not finished:
            err = f"timeout: first call still running after {args.cap_s:.0f}s"
        elif isinstance(first, Exception):
            err = f"{type(first).__name__}: {str(first)[:300]}"
        else:
            n_sets, warmup, reps = plan_leg(first[1], len(sets), args.warmup, args.reps)
            sets = sets[:n_sets]
            try:
                for rnd in range(warmup + reps):
                    for i, ps in enumerate(sets):
                        rows, ms = first if (rnd == 0 and i == 0) else one(ps)
                        if rnd >= warmup:
                            times.append(ms)
                        if rnd == 0:
                            sigs.append(signature(rows))
                            counts.append(len(rows))
            except Exception as ex:  # an unsupported query is a result, not a crash
                err = f"{type(ex).__name__}: {str(ex)[:300]}"
        results[name] = {"values_ms": times, "rows": counts, "signatures": sigs, "error": err}
        if err:
            print(f"  {name:28s} ERROR {err[:120]}", flush=True)
        else:
            print(f"  {name:28s} n={len(times):4d} p50={statistics.median(times):9.3f}ms", flush=True)
        if err and err.startswith("timeout"):
            break
    Path(args.out).write_text(json.dumps({"ns": args.ns, "params": plist, "results": results}, indent=2))
    sys.stdout.flush()
    os._exit(0)  # a call abandoned by the cap may still be running on the server; exiting drops the connection


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("load", "query"):
        p = sub.add_parser(name)
        p.add_argument("--ns", required=True)
        p.add_argument("--root", required=True)
        p.add_argument("--out", required=True)
        if name == "query":
            p.add_argument("--params", type=int, default=20)
            p.add_argument("--seed", type=int, default=42)
            p.add_argument("--warmup", type=int, default=1)
            p.add_argument("--reps", type=int, default=3)
            p.add_argument("--only", nargs="+")
            p.add_argument("--cap-s", type=float, default=60.0, help="a first call slower than this ends the leg as a timeout")
    args = ap.parse_args(argv)
    {"load": run_load, "query": run_query}[args.cmd](args)


if __name__ == "__main__":
    main()
