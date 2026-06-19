#!/usr/bin/env python
"""Benchmark: per-row index maintenance vs deferred batch rebuild.

Question under test (architecture review, 2026-06): would adopting a LIVE
functional index on Graph_KG.rdf_edges (so ^KG/^NKG auto-maintain on every SQL
mutation) cost more at bulk-load time than the current deferred strategy
(insert rows, then one BuildKG/BuildNKG at the end)?

A functional index fires its InsertIndex callback once per inserted row — which
is precisely the per-row cost of EdgeScan.WriteAdjacency that create_edge()
already pays today. So we benchmark the two strategies the engine already
supports, as a faithful proxy for the functional-index decision:

  Strategy A  PER-ROW  : create_edge() per edge — WriteAdjacency fires per row.
                         (== what a live functional index would cost)
  Strategy B  DEFERRED : raw bulk SQL insert, then ONE sync()/BuildKG rebuild.
                         (== current bulk_load_session path)

Hypothesis (to falsify): A is > 2x slower than B at 100k edges.

Usage:
    IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 \
        python benchmarks/bench_index_maintenance_strategy.py --edges 100000

Writes a JSON result next to this file.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path


def _connect():
    """Attach to the sanctioned test container via iris_devtester.

    Uses the same container-name path the pytest iris_connection fixture uses,
    so credentials/namespace match and we never accidentally hit los-iris.
    """
    container = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")
    from iris_devtester import IRISContainer
    c = IRISContainer.attach(container)
    c._connection = None
    return c.get_connection()


def _cleanup(conn, prefix):
    cur = conn.cursor()
    for tbl, col in [("rdf_edges", "s"), ("rdf_edges", "o_id"), ("nodes", "node_id")]:
        try:
            cur.execute(f"DELETE FROM Graph_KG.{tbl} WHERE {col} LIKE '{prefix}%'")
        except Exception:
            pass
    conn.commit()
    cur.close()


def _seed_nodes(engine, prefix, n_nodes):
    nodes = [{"id": f"{prefix}_n{i}", "labels": ["Bench"]} for i in range(n_nodes)]
    engine.bulk_create_nodes(nodes, disable_indexes=False)


def bench_per_row(engine, prefix, edges):
    """Strategy A: create_edge per edge (WriteAdjacency fires per row)."""
    t0 = time.perf_counter()
    for s, p, o in edges:
        engine.create_edge(s, p, o)
    elapsed = time.perf_counter() - t0
    return elapsed


def bench_deferred(engine, conn, prefix, edges):
    """Strategy B: raw bulk SQL insert, then one sync() rebuild."""
    cur = conn.cursor()
    t0 = time.perf_counter()
    cur.executemany(
        "INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
        [[s, p, o] for s, p, o in edges],
    )
    conn.commit()
    insert_elapsed = time.perf_counter() - t0
    t1 = time.perf_counter()
    engine.sync()
    sync_elapsed = time.perf_counter() - t1
    cur.close()
    return insert_elapsed + sync_elapsed, insert_elapsed, sync_elapsed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--edges", type=int, default=100_000)
    ap.add_argument("--per-row-cap", type=int, default=20_000,
                    help="cap per-row run (it is O(n) slow); extrapolate beyond")
    args = ap.parse_args()

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from iris_vector_graph.engine import IRISGraphEngine

    conn = _connect()
    engine = IRISGraphEngine(conn, embedding_dimension=4)

    n_edges = args.edges
    n_nodes = max(1000, n_edges // 10)
    results = {"edges": n_edges, "nodes": n_nodes}

    # ---- Strategy B: deferred (full target size) ----
    prefix_b = f"benchB_{uuid.uuid4().hex[:6]}"
    _seed_nodes(engine, prefix_b, n_nodes)
    edges_b = [
        (f"{prefix_b}_n{i % n_nodes}", "REL", f"{prefix_b}_n{(i * 7 + 1) % n_nodes}")
        for i in range(n_edges)
    ]
    total_b, ins_b, sync_b = bench_deferred(engine, conn, prefix_b, edges_b)
    results["deferred"] = {
        "total_s": round(total_b, 3),
        "insert_s": round(ins_b, 3),
        "sync_s": round(sync_b, 3),
        "edges_per_s": round(n_edges / total_b, 1),
    }
    _cleanup(conn, prefix_b)
    print(f"[B deferred ] {n_edges} edges: {total_b:.2f}s "
          f"(insert {ins_b:.2f}s + sync {sync_b:.2f}s) = {n_edges/total_b:.0f} edges/s")

    # ---- Strategy A: per-row (capped, then extrapolated) ----
    cap = min(args.per_row_cap, n_edges)
    prefix_a = f"benchA_{uuid.uuid4().hex[:6]}"
    _seed_nodes(engine, prefix_a, n_nodes)
    edges_a = [
        (f"{prefix_a}_n{i % n_nodes}", "REL", f"{prefix_a}_n{(i * 7 + 1) % n_nodes}")
        for i in range(cap)
    ]
    per_row_elapsed = bench_per_row(engine, prefix_a, edges_a)
    per_row_rate = cap / per_row_elapsed
    extrapolated = n_edges / per_row_rate
    results["per_row"] = {
        "measured_edges": cap,
        "measured_s": round(per_row_elapsed, 3),
        "edges_per_s": round(per_row_rate, 1),
        "extrapolated_total_s": round(extrapolated, 1),
    }
    _cleanup(conn, prefix_a)
    print(f"[A per-row  ] {cap} edges: {per_row_elapsed:.2f}s = {per_row_rate:.0f} edges/s "
          f"(extrapolated to {n_edges}: {extrapolated:.0f}s)")

    ratio = extrapolated / total_b if total_b else float("inf")
    results["ratio_per_row_over_deferred"] = round(ratio, 2)
    results["hypothesis_per_row_2x_slower"] = ratio > 2.0
    print(f"\nRATIO per-row / deferred = {ratio:.1f}x  "
          f"({'CONFIRMS' if ratio > 2 else 'FALSIFIES'} '>2x slower' hypothesis)")

    out = Path(__file__).resolve().parent / f"index_maintenance_strategy_{n_edges}.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
