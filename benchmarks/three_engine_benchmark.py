#!/usr/bin/env python3
"""
Three-Engine Benchmark
======================
Compares identical Cypher queries routed through IRISGraphEngine
with three different storage backends.

  IVG-SQL        IRISGraphEngine(conn)                       — default SQL/globals
  IVG-Arno/fjall IRISGraphEngine(conn, store=ArnoGraphStore) — arno + fjall LSM
  IVG-Arno/^KG   IRISGraphEngine(conn, store=ArnoGraphStore( — arno + IRIS ^KG globals
                     backend="iris"))                          (requires --features iris)

All three engines receive the same dataset loaded via their own write_nodes/write_edges.
All three run the same Cypher strings. Numbers are median latency over REPS iterations.

Usage:
    cd ~/ws/iris-vector-graph
    python benchmarks/three_engine_benchmark.py [--edges N] [--reps N]
"""

import argparse
import csv
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "arno-graph"))

import iris as _iris

from iris_vector_graph.engine import IRISGraphEngine

# ── CLI args ─────────────────────────────────────────────────────────────────

parser = argparse.ArgumentParser(description="Three-engine IVG benchmark")
parser.add_argument("--edges", type=int, default=10_000, help="SF10 edges to load (default 10k)")
parser.add_argument("--reps", type=int, default=20, help="Repetitions per query (default 20)")
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, default=2972)
parser.add_argument("--user", default="_SYSTEM")
parser.add_argument("--password", default="SYS")
parser.add_argument("--namespace", default="USER")
args = parser.parse_args()

REPS = args.reps
EDGES = args.edges
SF10_PATH = (
    Path(__file__).parent.parent
    / "social_network-sf10-CsvBasic-LongDateFormatter"
    / "dynamic"
    / "person_knows_person_0_0.csv"
)

SEEDS = [
    "Person:933",
    "Person:36226",
    "Person:51934",
    "Person:6597069780295",
    "Person:10995116282236",
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def ms(t: float) -> str:
    return f"{t:.2f}ms" if t >= 1 else f"{t * 1000:.0f}µs"


def bench(fn: Callable) -> float:
    times: List[float] = []
    for _ in range(REPS):
        t0 = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t0) * 1000)
    return statistics.median(times)


def load_sf10() -> Tuple[List[dict], List[dict]]:
    if not SF10_PATH.exists():
        print(f"[WARN] SF10 file not found: {SF10_PATH}")
        print("       Using 500-node synthetic graph instead.")
        return _synthetic_graph(500, 3)

    nodes_set: set = set()
    edges: List[dict] = []
    with open(SF10_PATH) as f:
        reader = csv.reader(f, delimiter="|")
        next(reader)
        for i, row in enumerate(reader):
            if i >= EDGES:
                break
            s, d = f"Person:{row[0]}", f"Person:{row[1]}"
            nodes_set.add(s)
            nodes_set.add(d)
            edges.append({"source_id": s, "predicate": "KNOWS", "target_id": d})

    nodes = [{"id": n, "labels": ["Person"], "properties": {}} for n in nodes_set]
    return nodes, edges


def _synthetic_graph(n: int, avg_degree: int) -> Tuple[List[dict], List[dict]]:
    nodes = [{"id": f"Person:{i}", "labels": ["Person"], "properties": {}} for i in range(n)]
    edges = []
    for src in range(n):
        for d in range(avg_degree):
            dst = (src + d * 7 + 1) % n
            edges.append({"source_id": f"Person:{src}", "predicate": "KNOWS", "target_id": f"Person:{dst}"})
    return nodes, edges


# ── Engine factory ────────────────────────────────────────────────────────────

def make_ivg_sql(conn) -> IRISGraphEngine:
    return IRISGraphEngine(conn, embedding_dimension=0)


def make_ivg_arno_fjall(conn, tmpdir: str) -> Optional[IRISGraphEngine]:
    try:
        from arno_graph import ArnoGraphStore
        store = ArnoGraphStore(tmpdir)
        return IRISGraphEngine(conn, store=store, embedding_dimension=0)
    except ImportError:
        print("[SKIP] arno_graph not importable — run `maturin develop` in ~/ws/arno-graph")
        return None


def make_ivg_arno_globals(conn, install_dir: str = "/opt/iris20252cust") -> Optional[IRISGraphEngine]:
    try:
        from arno_graph import IrisGraphStore
        store = IrisGraphStore(
            install_dir,
            namespace=args.namespace,
            prefix="AG",
            graph_id="bench",
            username=args.user,
            password=args.password,
        )
        return IRISGraphEngine(conn, store=store, embedding_dimension=0)
    except ImportError:
        print("[SKIP] IrisGraphStore not available — rebuild with --features iris,python")
        return None
    except Exception as e:
        print(f"[SKIP] IrisGraphStore connect failed: {e}")
        return None


# ── Queries to benchmark ──────────────────────────────────────────────────────

def make_queries(engine: IRISGraphEngine, seed: str) -> List[Tuple[str, Callable]]:
    return [
        (
            "IC2: 1-hop KNOWS",
            lambda: engine.execute_cypher(
                "MATCH (a:Person {id: $seed})-[:KNOWS]->(b:Person) RETURN b.id",
                parameters={"seed": seed},
            ),
        ),
        (
            "IC3: 2-hop KNOWS",
            lambda: engine.execute_cypher(
                "MATCH (a:Person {id: $seed})-[:KNOWS*1..2]->(b:Person) RETURN DISTINCT b.id",
                parameters={"seed": seed},
            ),
        ),
        (
            "IC13: shortestPath",
            lambda: engine.execute_cypher(
                "MATCH p = shortestPath((a:Person {id: $src})-[:KNOWS*..6]-(b:Person {id: $dst})) "
                "RETURN length(p)",
                parameters={"src": seed, "dst": SEEDS[1]},
            ),
        ),
        (
            "COUNT friends",
            lambda: engine.execute_cypher(
                "MATCH (a:Person {id: $seed})-[:KNOWS]->(b:Person) RETURN count(b) AS c",
                parameters={"seed": seed},
            ),
        ),
    ]


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\nThree-Engine Benchmark  |  edges={EDGES:,}  reps={REPS}  seed={SEEDS[0]}")
    print("=" * 70)

    # Connect
    conn = _iris.connect(args.host, args.port, args.namespace, args.user, args.password)
    print(f"Connected to IRIS at {args.host}:{args.port}/{args.namespace}")

    print(f"\nLoading SF10 ({EDGES:,} edges)...")
    nodes, edges = load_sf10()
    print(f"  {len(nodes):,} nodes, {len(edges):,} edges loaded from CSV")

    seed = SEEDS[0]
    results: dict = {}

    def run_engine(label: str, engine: IRISGraphEngine, write_via_store: bool = False):
        if write_via_store:
            print(f"\n  Writing data via store (write_nodes + write_edges)...")
            from arno_graph import IrisGraphStore as _IrisGS
            from iris_vector_graph.result import IVGResult
            store = engine._store
            store.write_nodes([{"id": n["id"], "labels": n.get("labels", []), "properties": {}} for n in nodes])
            store.write_edges(edges)
        else:
            print(f"\n  Writing data via engine (bulk_create_nodes + bulk_create_edges)...")
            engine.bulk_create_nodes(nodes)
            engine.bulk_create_edges(edges)
        print(f"  Warming up...")
        for _, fn in make_queries(engine, seed):
            try:
                fn()
            except Exception:
                pass
        print(f"  Benchmarking...")
        results[label] = {}
        for qlabel, fn in make_queries(engine, seed):
            try:
                t = bench(fn)
                results[label][qlabel] = t
                print(f"    {qlabel:<24}  {ms(t)}")
            except Exception as e:
                results[label][qlabel] = None
                short = str(e).split("\n")[0][:80]
                print(f"    {qlabel:<24}  SKIP ({short})")

    print("\n[1/3] IVG-SQL  (IRISGraphEngine + IRISGraphStore/SQL)")
    run_engine("IVG-SQL", make_ivg_sql(conn))

    print("\n[2/3] IVG-Arno/fjall  (IRISGraphEngine + ArnoGraphStore/fjall)")
    tmpdir = tempfile.mkdtemp()
    engine_fjall = make_ivg_arno_fjall(conn, tmpdir)
    if engine_fjall is not None:
        run_engine("IVG-Arno/fjall", engine_fjall)
    else:
        results["IVG-Arno/fjall"] = {}

    print("\n[3/3] IVG-Arno/globals  (IRISGraphEngine + IrisGraphStore/NICHE ^AG Callin)")
    engine_kg = make_ivg_arno_globals(conn)
    if engine_kg is not None:
        store_kg = engine_kg._store
        print(f"\n  Writing data via store.write_nodes + store.write_edges...")
        store_kg.write_nodes([{"id": n["id"], "labels": n.get("labels", []), "properties": {}} for n in nodes])
        store_kg.write_edges(edges)
        print(f"  Warming up...")
        for _, fn in make_queries(engine_kg, seed):
            try: fn()
            except Exception: pass
        print(f"  Benchmarking...")
        results["IVG-Arno/globals"] = {}
        for qlabel, fn in make_queries(engine_kg, seed):
            try:
                t = bench(fn)
                results["IVG-Arno/globals"][qlabel] = t
                print(f"    {qlabel:<24}  {ms(t)}")
            except Exception as e:
                results["IVG-Arno/globals"][qlabel] = None
                short = str(e).split("\n")[0][:80]
                print(f"    {qlabel:<24}  SKIP ({short})")
    else:
        results["IVG-Arno/globals"] = {}

    all_labels = list(dict.fromkeys(
        lbl for er in results.values() for lbl in er
    ))
    engines = list(results.keys())
    col_w, eng_w = 26, 18

    print(f"\n{'─' * (col_w + eng_w * len(engines) + 4)}")
    print(f"{'Query':<{col_w}}" + "".join(f"{e:>{eng_w}}" for e in engines))
    print(f"{'─' * (col_w + eng_w * len(engines) + 4)}")
    for lbl in all_labels:
        row = f"{lbl:<{col_w}}"
        for eng in engines:
            v = results[eng].get(lbl)
            row += f"{ms(v) if v is not None else 'SKIP':>{eng_w}}"
        print(row)
    print(f"{'─' * (col_w + eng_w * len(engines) + 4)}")
    print(f"\nDataset: {len(nodes):,} nodes, {len(edges):,} edges | reps={REPS} | median latency")

    # Save JSON results
    out = Path(__file__).parent / "results" / "three_engine_latest.json"
    out.parent.mkdir(exist_ok=True)
    with open(out, "w") as f:
        json.dump({"edges": EDGES, "reps": REPS, "seed": seed, "results": {
            engine: {lbl: v for lbl, v in vals.items()}
            for engine, vals in results.items()
        }}, f, indent=2)
    print(f"\nResults saved to {out}")


if __name__ == "__main__":
    main()
