#!/usr/bin/env python3
"""
IVG vs Neo4j Comparison Benchmark
===================================
Loads an M-scale synthetic graph (10k nodes, 50k edges) into both
IRIS (ivg-iris, port 21972) and Neo4j (neo4j-ivg-bench, port 7688),
then runs identical traversal queries against both.

Queries:
  Q1  — 1-hop neighbor count from highest-degree node
  Q2a — 2-hop traversal (count distinct nodes at depth 2)
  Q3  — BFS 1..3 (count reachable nodes up to depth 3)

Usage:
    python benchmarks/neo4j_comparison.py [--edges N] [--reps N] [--skip-load]
"""

import argparse
import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import iris as _iris
from neo4j import GraphDatabase

from iris_vector_graph.engine import IRISGraphEngine

# ── Config ───────────────────────────────────────────────────────────────────

parser = argparse.ArgumentParser()
parser.add_argument("--nodes", type=int, default=10_000)
parser.add_argument("--edges", type=int, default=50_000)
parser.add_argument("--reps", type=int, default=20)
parser.add_argument("--warmup", type=int, default=5)
parser.add_argument("--skip-load", action="store_true", help="Skip data load, assume graph already present")
args = parser.parse_args()

NODES = args.nodes
EDGES = args.edges
REPS = args.reps
WARMUP = args.warmup

NEO4J_URI = "bolt://localhost:7688"
NEO4J_USER = "neo4j"
NEO4J_PASS = "ivgbenchpw"

IRIS_HOST = "localhost"
IRIS_PORT = 21972
IRIS_NS = "USER"
IRIS_USER = "_SYSTEM"
IRIS_PASS = "SYS"


# ── Utilities ─────────────────────────────────────────────────────────────────

def fmt(t_ms: float) -> str:
    if t_ms < 0.01:
        return f"{t_ms * 1000:.0f}ns"
    if t_ms < 1:
        return f"{t_ms * 1000:.0f}µs"
    return f"{t_ms:.2f}ms"


def timed(fn, warmup=WARMUP, reps=REPS):
    for _ in range(warmup):
        fn()
    times = []
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t0) * 1_000)
    times.sort()
    return {
        "p50": statistics.median(times),
        "p95": times[int(len(times) * 0.95)],
        "min": times[0],
    }


# ── Graph generation ──────────────────────────────────────────────────────────

def generate_edges(n_nodes: int, n_edges: int, seed: int = 42) -> list[tuple[int, int]]:
    """Power-law-ish graph: each new node connects to a random existing node (preferential attachment lite)."""
    rng = random.Random(seed)
    edges = set()
    node_ids = list(range(n_nodes))
    # Start with a sparse backbone
    for i in range(1, min(n_nodes, 1000)):
        target = rng.randint(0, i - 1)
        edges.add((i, target))
    # Fill to n_edges with random pairs
    attempts = 0
    while len(edges) < n_edges and attempts < n_edges * 10:
        s = rng.randint(0, n_nodes - 1)
        o = rng.randint(0, n_nodes - 1)
        if s != o:
            edges.add((s, o))
        attempts += 1
    return list(edges)


def highest_degree_seed(edge_list: list[tuple[int, int]]) -> int:
    degree = {}
    for s, o in edge_list:
        degree[s] = degree.get(s, 0) + 1
        degree[o] = degree.get(o, 0) + 1
    return max(degree, key=lambda k: degree[k])


# ── IRIS load + queries ───────────────────────────────────────────────────────

def load_iris(conn, iris_obj, edge_list: list[tuple[int, int]]):
    cursor = conn.cursor()
    print("  [IRIS] Clearing tables...")
    for tbl in ["rdf_edges", "rdf_props", "rdf_labels", "nodes"]:
        try:
            cursor.execute(f"DELETE FROM Graph_KG.{tbl}")
        except Exception:
            pass
    conn.commit()

    print(f"  [IRIS] Inserting {NODES} nodes...")
    batch = [[f"node_{i}"] for i in range(NODES)]
    for i in range(0, len(batch), 500):
        conn.cursor().executemany(
            "INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", batch[i:i+500]
        )
    conn.commit()

    print(f"  [IRIS] Inserting {len(edge_list)} edges...")
    edge_batch = [[f"node_{s}", "R", f"node_{o}"] for s, o in edge_list]
    for i in range(0, len(edge_batch), 1000):
        conn.cursor().executemany(
            "INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
            edge_batch[i:i+1000],
        )
    conn.commit()

    print("  [IRIS] Building ^KG index...")
    iris_obj.classMethodVoid("Graph.KG.Traversal", "BuildKG")

    print("  [IRIS] Building ^NKG index...")
    try:
        iris_obj.classMethodVoid("Graph.KG.NKGAccel", "BuildNKG")
    except Exception as e:
        print(f"  [IRIS] ^NKG build skipped: {e}")


def iris_q1(iris_obj, seed: str) -> int:
    """1-hop: use native ObjectScript BFS at depth=1."""
    import json
    raw = iris_obj.classMethodValue("Graph.KG.Traversal", "BFSFastJson", seed, "", 1)
    r = json.loads(str(raw))
    return len({x["o"] for x in r})


def iris_q2a(iris_obj, seed: str) -> int:
    """2-hop: nodes reachable via exactly 2 hops and not already reachable at 1 hop.
    Matches Neo4j semantics for MATCH (s)-[:R*2..2]->(n) RETURN count(DISTINCT n).
    """
    import json
    raw = iris_obj.classMethodValue("Graph.KG.Traversal", "BFSFastJson", seed, "", 2)
    r = json.loads(str(raw))
    depth1 = {x["o"] for x in r if x["step"] == 1}
    depth2 = {x["o"] for x in r if x["step"] == 2}
    return len(depth2 - depth1)


def iris_q3(iris_obj, seed: str) -> int:
    """BFS 1..3: count all distinct reachable nodes up to depth 3."""
    import json
    raw = iris_obj.classMethodValue("Graph.KG.Traversal", "BFSFastJson", seed, "", 3)
    r = json.loads(str(raw))
    return len({x["o"] for x in r})


# ── Neo4j load + queries ──────────────────────────────────────────────────────

def load_neo4j(driver, edge_list: list[tuple[int, int]]):
    with driver.session() as session:
        print("  [Neo4j] Clearing graph...")
        session.run("MATCH (n) DETACH DELETE n")

        print(f"  [Neo4j] Creating {NODES} nodes...")
        for i in range(0, NODES, 1000):
            batch = [{"id": f"node_{j}"} for j in range(i, min(i + 1000, NODES))]
            session.run("UNWIND $batch AS row CREATE (:N {id: row.id})", batch=batch)

        print("  [Neo4j] Creating index...")
        session.run("CREATE INDEX node_id_idx IF NOT EXISTS FOR (n:N) ON (n.id)")

        print(f"  [Neo4j] Creating {len(edge_list)} edges...")
        edge_batch = [{"s": f"node_{s}", "o": f"node_{o}"} for s, o in edge_list]
        for i in range(0, len(edge_batch), 2000):
            chunk = edge_batch[i:i+2000]
            session.run(
                "UNWIND $batch AS row "
                "MATCH (a:N {id: row.s}), (b:N {id: row.o}) "
                "CREATE (a)-[:R]->(b)",
                batch=chunk,
            )
        print("  [Neo4j] Done.")


def neo4j_q1(session, seed: str) -> int:
    r = session.run(
        "MATCH (s:N {id: $id})-[:R]->(n) RETURN count(n) AS cnt", id=seed
    )
    return r.single()["cnt"]


def neo4j_q2a(session, seed: str) -> int:
    r = session.run(
        "MATCH (s:N {id: $id})-[:R*2..2]->(n) RETURN count(DISTINCT n) AS cnt", id=seed
    )
    return r.single()["cnt"]


def neo4j_q3(session, seed: str) -> int:
    r = session.run(
        "MATCH (s:N {id: $id})-[:R*1..3]->(n) RETURN count(DISTINCT n) AS cnt", id=seed
    )
    return r.single()["cnt"]


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\nIVG vs Neo4j Benchmark  —  {NODES:,} nodes / {EDGES:,} edges / {REPS} reps\n")

    # Generate graph
    print("Generating synthetic graph...")
    edge_list = generate_edges(NODES, EDGES)
    seed_idx = highest_degree_seed(edge_list)
    seed_str = f"node_{seed_idx}"
    print(f"  Seed node: {seed_str} (highest out-degree)\n")

    # Connect IRIS
    print("Connecting to IRIS (ivg-iris:21972)...")
    conn = _iris.connect(
        hostname=IRIS_HOST, port=IRIS_PORT, namespace=IRIS_NS,
        username=IRIS_USER, password=IRIS_PASS,
    )
    iris_obj = _iris.createIRIS(conn)
    engine = IRISGraphEngine(conn, embedding_dimension=4)
    engine.initialize_schema()

    # Connect Neo4j
    print("Connecting to Neo4j (localhost:7688)...")
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))
    driver.verify_connectivity()

    # Load data
    if not args.skip_load:
        print("\nLoading data into IRIS...")
        load_iris(conn, iris_obj, edge_list)
        print("\nLoading data into Neo4j...")
        load_neo4j(driver, edge_list)
    else:
        print("  --skip-load: assuming data already present\n")

    # Verify counts match
    print("\nVerifying result counts...")
    with driver.session() as s:
        neo4j_counts = {
            "q1": neo4j_q1(s, seed_str),
            "q2a": neo4j_q2a(s, seed_str),
            "q3": neo4j_q3(s, seed_str),
        }
    iris_counts = {
        "q1": iris_q1(iris_obj, seed_str),
        "q2a": iris_q2a(iris_obj, seed_str),
        "q3": iris_q3(iris_obj, seed_str),
    }
    for q in ("q1", "q2a", "q3"):
        match = "✅" if iris_counts[q] == neo4j_counts[q] else "⚠️ MISMATCH"
        print(f"  {q}: IVG={iris_counts[q]} Neo4j={neo4j_counts[q]} {match}")

    # Benchmark — Neo4j uses a persistent session per query block to avoid session overhead
    print(f"\nBenchmarking ({WARMUP} warmup + {REPS} timed runs each)...\n")
    results = {}

    queries = {
        "Q1  1-hop count": (
            lambda: iris_q1(iris_obj, seed_str),
            lambda: neo4j_q1(neo4j_session, seed_str),
        ),
        "Q2a 2-hop count": (
            lambda: iris_q2a(iris_obj, seed_str),
            lambda: neo4j_q2a(neo4j_session, seed_str),
        ),
        "Q3  BFS 1..3": (
            lambda: iris_q3(iris_obj, seed_str),
            lambda: neo4j_q3(neo4j_session, seed_str),
        ),
    }

    with driver.session() as neo4j_session:
        for label, (ivg_fn, n4j_fn) in queries.items():
            print(f"  {label}")
            ivg = timed(ivg_fn)
            n4j = timed(n4j_fn)
            results[label] = {"ivg": ivg, "neo4j": n4j}
            speedup = n4j["p50"] / ivg["p50"] if ivg["p50"] > 0 else float("inf")
            print(f"    IVG   p50={fmt(ivg['p50'])}  p95={fmt(ivg['p95'])}")
            print(f"    Neo4j p50={fmt(n4j['p50'])}  p95={fmt(n4j['p95'])}")
            print(f"    Speedup: {speedup:.1f}x  {'(IVG faster)' if speedup > 1 else '(Neo4j faster)'}\n")

    # Summary table
    print("=" * 72)
    print(f"{'Query':<22} {'IVG p50':>10} {'Neo4j p50':>12} {'Speedup':>10}")
    print("-" * 72)
    for label, r in results.items():
        ivg_p50 = r["ivg"]["p50"]
        n4j_p50 = r["neo4j"]["p50"]
        speedup = n4j_p50 / ivg_p50 if ivg_p50 > 0 else float("inf")
        direction = "IVG" if speedup > 1 else "Neo4j"
        actual = speedup if speedup >= 1 else 1 / speedup
        print(f"{label:<22} {fmt(ivg_p50):>10} {fmt(n4j_p50):>12} {actual:>7.1f}x {direction}")
    print("=" * 72)
    print(f"\nGraph: {NODES:,} nodes / {len(edge_list):,} edges, seed={seed_str}")
    print(f"IRIS: ivg-iris:21972 (IRIS 2026.1 community, ARM64)")
    print(f"Neo4j: localhost:7688 (neo4j:5.24-community, ARM64)\n")

    driver.close()


if __name__ == "__main__":
    main()
