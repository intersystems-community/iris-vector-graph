#!/usr/bin/env python3
"""
Load M-scale benchmark graph onto ivg-iris-enterprise (port 31972),
build ^KG + ^NKG, then run KHopNeighbors queries.
Run standalone — imported by neo4j_comparison.py for the NKG leg.
"""
import json
import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import iris as _iris


NODES = 10_000
EDGES = 50_000
SEED_INT = 42

ENT_HOST = "localhost"
ENT_PORT = 31972
ENT_NS = "USER"
ENT_USER = "_SYSTEM"
ENT_PASS = "SYS"


def generate_edges(n_nodes=NODES, n_edges=EDGES, seed=SEED_INT):
    rng = random.Random(seed)
    edges = set()
    for i in range(1, min(n_nodes, 1000)):
        edges.add((i, rng.randint(0, i - 1)))
    attempts = 0
    while len(edges) < n_edges and attempts < n_edges * 10:
        s = rng.randint(0, n_nodes - 1)
        o = rng.randint(0, n_nodes - 1)
        if s != o:
            edges.add((s, o))
        attempts += 1
    return list(edges)


def highest_degree_node(edges):
    deg = {}
    for s, o in edges:
        deg[s] = deg.get(s, 0) + 1
        deg[o] = deg.get(o, 0) + 1
    return max(deg, key=lambda k: deg[k])


def load_enterprise(conn, iris_obj, edges):
    cursor = conn.cursor()
    print("  [ENT] Clearing tables...")
    for tbl in ["rdf_edges", "rdf_props", "rdf_labels", "nodes"]:
        try:
            cursor.execute(f"DELETE FROM Graph_KG.{tbl}")
        except Exception:
            pass
    conn.commit()

    print(f"  [ENT] Inserting {NODES} nodes...")
    batch = [[f"node_{i}"] for i in range(NODES)]
    for i in range(0, len(batch), 500):
        conn.cursor().executemany(
            "INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", batch[i:i+500]
        )
    conn.commit()

    print(f"  [ENT] Inserting {len(edges)} edges...")
    eb = [[f"node_{s}", "R", f"node_{o}"] for s, o in edges]
    for i in range(0, len(eb), 1000):
        conn.cursor().executemany(
            "INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, ?, ?)", eb[i:i+1000]
        )
    conn.commit()

    print("  [ENT] Building ^KG index...")
    t0 = time.perf_counter()
    iris_obj.classMethodVoid("Graph.KG.Traversal", "BuildKG")
    print(f"         ^KG done in {(time.perf_counter()-t0)*1000:.0f}ms")

    print("  [ENT] Building ^NKG index...")
    t0 = time.perf_counter()
    iris_obj.classMethodValue("Graph.KG.TraversalBuild", "BuildNKG")
    print(f"         ^NKG done in {(time.perf_counter()-t0)*1000:.0f}ms")


def timed(fn, warmup=5, reps=20):
    for _ in range(warmup):
        fn()
    times = []
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t0) * 1000)
    times.sort()
    return {"p50": statistics.median(times), "p95": times[int(len(times) * 0.95)], "min": times[0]}


MAX_NODES = 50_000  # cap for KHopNeighbors (must be > expected result set)


def nkg_q1(iris_obj, seed):
    r = json.loads(str(iris_obj.classMethodValue(
        "Graph.KG.NKGAccelTraversal", "KHopNeighbors", seed, 1, MAX_NODES
    )))
    return r["totalNodes"] - 1  # exclude seed


def nkg_q2a(iris_obj, seed):
    r = json.loads(str(iris_obj.classMethodValue(
        "Graph.KG.NKGAccelTraversal", "KHopNeighbors", seed, 2, MAX_NODES
    )))
    # depth-2 nodes only (not also reachable at depth 1)
    nodes_at = {n["id"]: n["dist"] for n in r["nodes"]}
    return sum(1 for nid, dist in nodes_at.items() if dist == 2)


def nkg_q3(iris_obj, seed):
    r = json.loads(str(iris_obj.classMethodValue(
        "Graph.KG.NKGAccelTraversal", "KHopNeighbors", seed, 3, MAX_NODES
    )))
    return r["totalNodes"] - 1  # exclude seed


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--skip-load", action="store_true")
    p.add_argument("--reps", type=int, default=20)
    args = p.parse_args()

    edges = generate_edges()
    seed_idx = highest_degree_node(edges)
    seed_str = f"node_{seed_idx}"
    print(f"Seed: {seed_str}, edges: {len(edges)}")

    conn = _iris.connect(hostname=ENT_HOST, port=ENT_PORT, namespace=ENT_NS,
                         username=ENT_USER, password=ENT_PASS)
    ir = _iris.createIRIS(conn)

    if not args.skip_load:
        load_enterprise(conn, ir, edges)

    print("\nVerifying NKG counts...")
    print(f"  Q1:  {nkg_q1(ir, seed_str)}")
    print(f"  Q2a: {nkg_q2a(ir, seed_str)}")
    print(f"  Q3:  {nkg_q3(ir, seed_str)}")

    print(f"\nBenchmarking (5 warmup + {args.reps} reps)...")
    for label, fn in [
        ("Q1  1-hop", lambda: nkg_q1(ir, seed_str)),
        ("Q2a 2-hop", lambda: nkg_q2a(ir, seed_str)),
        ("Q3  BFS 1..3", lambda: nkg_q3(ir, seed_str)),
    ]:
        r = timed(fn, reps=args.reps)
        def fmt(t): return f"{t*1000:.0f}µs" if t < 1 else f"{t:.2f}ms"
        print(f"  {label}: p50={fmt(r['p50'])}  p95={fmt(r['p95'])}")
