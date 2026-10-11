"""Time the 3.2.0 -> 4.0.0 embeddings migration, step by step, on a synthetic legacy namespace.

    python scripts/bench_migration_4_0_0.py --nodes 20000 --vectors 20000 --dim 768 [--apply] [--embedded]

Seeds ``IVGLEGACY`` with the frozen v3.2.0 DDL (the namespace test_upgrade_dry_run_2x.py uses), then
runs ``migrate_to_graph_scoped_embeddings`` and reports the time and statement count of each step.
``--embedded`` is for a process inside IRIS (irispython); otherwise it connects to
``IVG_PORT`` (default 31972) on localhost. It wipes ``Graph_KG`` in IVGLEGACY: never point it at a real namespace.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

NAMESPACE = "IVGLEGACY"
# The 3.2.0 table this script builds is keyed `id`; named once here so the 4.0.0 hygiene gate (which
# rejects a bare `id` in embedding SQL) can tell a deliberate legacy fixture from a stale script.
LEGACY_KEY = "id"
SCHEMA = "Graph_KG"


def connect(embedded: bool):
    if embedded:
        import iris

        iris.cls("%SYSTEM.Process").SetNamespace(NAMESPACE)
        from iris_vector_graph.embedded import EmbeddedConnection

        return EmbeddedConnection()
    import iris

    return iris.connect("localhost", int(os.environ.get("IVG_PORT", "31972")), NAMESPACE, "_SYSTEM", "SYS")


def quietly(cursor, sql, params=None):
    try:
        cursor.execute(sql, params or [])
    except Exception:
        pass


def wipe(conn):
    cursor = conn.cursor()
    cursor.execute("SELECT TABLE_NAME FROM INFORMATION_SCHEMA.VIEWS WHERE TABLE_SCHEMA = ?", [SCHEMA])
    for row in cursor.fetchall():
        quietly(cursor, f"DROP VIEW {SCHEMA}.{row[0]}")
    for _ in range(6):
        cursor.execute(
            "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = ? AND TABLE_TYPE = 'BASE TABLE'",
            [SCHEMA],
        )
        left = [str(r[0]) for r in cursor.fetchall()]
        if not left:
            break
        for table in left:
            quietly(cursor, f"DROP TABLE {SCHEMA}.{table} CASCADE")
    conn.commit()


def seed(conn, nodes: int, vectors: int, dim: int, docs: int = 0, edge_vectors: int = 0, labels: bool = True):
    from tests.e2e.fixtures.old_releases import OLD_RELEASES

    release = [r for r in OLD_RELEASES if r.tag == "v3.2.0"][0]
    cursor = conn.cursor()
    for statement in release.ddl_statements():
        quietly(cursor, statement)
    for table in ("kg_NodeEmbeddings", "kg_NodeEmbeddings_optimized"):
        quietly(cursor, f"ALTER TABLE {SCHEMA}.{table} ALTER COLUMN emb VECTOR(DOUBLE, {dim})")
    t = time.perf_counter()
    batch = 500
    for start in range(0, nodes, batch):
        rows = [(f"bench:{i}", "") for i in range(start, min(start + batch, nodes))]
        cursor.executemany(f"INSERT INTO {SCHEMA}.nodes (node_id, graph_id) VALUES (?, ?)", rows)
    vec = ",".join(["0.1"] * dim)
    for start in range(0, vectors, batch):
        rows = [(f"bench:{i}", vec) for i in range(start, min(start + batch, vectors))]
        cursor.executemany(
            f"INSERT INTO {SCHEMA}.kg_NodeEmbeddings ({LEGACY_KEY}, emb) VALUES (?, TO_VECTOR(?, DOUBLE))", rows
        )
    if labels:
        for start in range(0, nodes, batch):
            rows = [(f"bench:{i}", "Bench") for i in range(start, min(start + batch, nodes))]
            cursor.executemany(f"INSERT INTO {SCHEMA}.rdf_labels (s, label) VALUES (?, ?)", rows)
    for start in range(0, docs, batch):
        rows = [(f"bench:{i}", "text") for i in range(start, min(start + batch, docs))]
        cursor.executemany(f"INSERT INTO {SCHEMA}.docs (id, text) VALUES (?, ?)", rows)
    if edge_vectors:
        quietly(cursor, f"ALTER TABLE {SCHEMA}.kg_EdgeEmbeddings ALTER COLUMN emb VECTOR(DOUBLE, {dim})")
    for start in range(0, edge_vectors, batch):
        rows = [(f"e:{i}", "R", f"e:{i + 1}", vec) for i in range(start, min(start + batch, edge_vectors))]
        cursor.executemany(
            f"INSERT INTO {SCHEMA}.kg_EdgeEmbeddings (s, p, o_id, emb) VALUES (?, ?, ?, TO_VECTOR(?, DOUBLE))", rows
        )
    conn.commit()
    print(f"seeded {nodes} nodes, {vectors} vectors (dim {dim}) in {time.perf_counter() - t:.1f}s", flush=True)


def instrument():
    from iris_vector_graph.migrations import graph_scoped_embeddings as m

    seconds, calls = defaultdict(float), defaultdict(int)
    for name in ("rekey", "_plan", "_drain", "_reshape", "_redistribute", "_rewrite_registry",
                 "_install_procedures", "_observed", "_predicted", "_graphs_of", "_move", "_keys"):
        original = getattr(m._Migrator, name)

        def timed(self, *a, __o=original, __n=name, **k):
            t0 = time.perf_counter()
            try:
                return __o(self, *a, **k)
            finally:
                seconds[__n] += time.perf_counter() - t0
                calls[__n] += 1

        setattr(m._Migrator, name, timed)
    return seconds, calls


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nodes", type=int, default=5000)
    ap.add_argument("--vectors", type=int, default=5000)
    ap.add_argument("--dim", type=int, default=768)
    ap.add_argument("--docs", type=int, default=0)
    ap.add_argument("--edge-vectors", type=int, default=0)
    ap.add_argument("--steps", default="embeddings", help="comma list from upgrade_to_4_0_0, or 'all'")
    ap.add_argument("--apply", action="store_true", help="run the migration for real (default: dry run)")
    ap.add_argument("--embedded", action="store_true")
    args = ap.parse_args()

    conn = connect(args.embedded)
    wipe(conn)
    seed(conn, args.nodes, args.vectors, args.dim, args.docs, args.edge_vectors)
    # What a consumer does after pip-upgrading and before upgrade_to_4_0_0: initialize_schema adds the
    # quarantine tables and the new columns and leaves the legacy tables for the upgrade to re-shape.
    from iris_vector_graph.engine import IRISGraphEngine

    IRISGraphEngine(conn, embedding_dimension=args.dim).initialize_schema(auto_deploy_objectscript=args.apply)
    seconds, calls = instrument()
    from iris_vector_graph.migrations import upgrade as up

    original_step = up._run_step

    def timed_step(name, conn, **kw):
        t0 = time.perf_counter()
        try:
            return original_step(name, conn, **kw)
        finally:
            print(f"  step {name:16s} {time.perf_counter() - t0:8.1f}s", flush=True)

    up._run_step = timed_step
    steps = None if args.steps == "all" else args.steps.split(",")
    t = time.perf_counter()
    report = up.upgrade_to_4_0_0(conn, steps=steps, dry_run=not args.apply)
    total = time.perf_counter() - t
    print(f"{'apply' if args.apply else 'dry run'}: {total:.1f}s total for {args.vectors} vectors "
          f"({1000 * total / max(args.vectors, 1):.2f} ms/vector)")
    for name in sorted(seconds, key=seconds.get, reverse=True):
        print(f"  {name:22s} {seconds[name]:8.2f}s  {calls[name]:>8d} calls")
    print("refused:", getattr(report, "refused", None))


if __name__ == "__main__":
    main()
