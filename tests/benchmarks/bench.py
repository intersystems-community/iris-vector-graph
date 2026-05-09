import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

import iris
from bench_utils import (
    detect_arno, get_highest_degree_seed, load_graph_to_iris,
    pick_shortest_path_pair, run_timed,
)

DATASET_PARAMS = {
    "S": (1_000, 5_000),
    "M": (10_000, 50_000),
    "L": (100_000, 500_000),
}

IRIS_HOST = os.environ.get("IRIS_HOST", "localhost")
IRIS_PORT = int(os.environ.get("IRIS_PORT", "1972"))
IRIS_NS   = os.environ.get("IRIS_NAMESPACE", "USER")
IRIS_USER = os.environ.get("IRIS_USERNAME", "_SYSTEM")
IRIS_PASS = os.environ.get("IRIS_PASSWORD", "SYS")


def connect_iris():
    conn = iris.connect(hostname=IRIS_HOST, port=IRIS_PORT, namespace=IRIS_NS,
                        username=IRIS_USER, password=IRIS_PASS)
    iris_obj = iris.createIRIS(conn)
    return conn, iris_obj


def run_bfs_os(iris_obj, seed, depth):
    from bench_utils import call_classmethod_large
    raw = call_classmethod_large(iris_obj, "Graph.KG.Traversal", "BFSFastJson", seed, "", depth)
    results = json.loads(str(raw))
    nodes = {r["o"] for r in results}
    return results, len(nodes)


def run_bfs_arno(iris_obj, seed, depth):
    from bench_utils import call_classmethod_large
    raw = call_classmethod_large(iris_obj, "Graph.KG.NKGAccel", "BFSJson", seed, "[]", depth, 0)
    results = json.loads(str(raw))
    nodes = {r["o"] for r in results}
    return results, len(nodes)


def run_q1(engine, seed):
    result = engine.execute_cypher(
        "MATCH (s)-[:R]->(n) WHERE s.node_id = $id RETURN count(n) AS cnt",
        {"id": seed}
    )
    rows = result.get("rows", ())
    count = rows[0][0] if rows else 0
    return result, count


def run_shortest_path(engine, src, dst):
    result = engine.execute_cypher(
        "MATCH p = shortestPath((a {node_id: $a})-[*..8]-(b {node_id: $b})) RETURN length(p) AS hops",
        {"a": src, "b": dst}
    )
    rows = result.get("rows", ())
    hops = rows[0][0] if rows else None
    return result, 1 if hops is not None else 0


def run_weighted_sp(engine, src, dst):
    result = engine.execute_cypher(
        "CALL ivg.shortestPath.weighted($a, $b, 'weight', 9999, 10) YIELD totalCost RETURN totalCost",
        {"a": src, "b": dst}
    )
    rows = result.get("rows", ())
    cost = rows[0][0] if rows else None
    return result, 1 if cost is not None else 0


def check_correctness(os_results, arno_results, query_id, dataset):
    os_nodes = {r["o"] for r in os_results} if os_results else set()
    arno_nodes = {r["o"] for r in arno_results} if arno_results else set()
    spurious = arno_nodes - os_nodes
    if spurious:
        status = f"FAIL: arno returned {len(spurious)} nodes not in os result"
    else:
        status = f"PASS (arno={len(arno_nodes)} ⊆ os={len(os_nodes)})"
    return {"dataset": dataset, "query": query_id, "ivg_os_vs_arno": status,
            "result_count_os": len(os_nodes), "result_count_arno": len(arno_nodes)}


def print_table(all_results, arno_available):
    print()
    print("IVG Arno Acceleration Benchmark Results")
    print("=" * 80)
    header = f"{'Query':<12} {'Dataset':<8} {'ivg-os p50':>12} {'ivg-arno p50':>14} {'speedup':>9}"
    print(header)
    print("-" * 80)
    by_key = {(r["dataset"], r["query"], r["path"]): r for r in all_results}
    for ds in ["S", "M", "L"]:
        for q in ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6"]:
            os_r = by_key.get((ds, q, "ivg-os"))
            arno_r = by_key.get((ds, q, "ivg-arno"))
            if not os_r:
                continue
            os_p50 = os_r.get("hot_p50_ms", "n/a")
            arno_p50 = arno_r.get("hot_p50_ms", "n/a") if arno_r else ("n/a" if not arno_available else "n/a")
            speedup = ""
            if isinstance(os_p50, (int, float)) and isinstance(arno_p50, (int, float)) and arno_p50 > 0:
                speedup = f"{os_p50 / arno_p50:.1f}x"
            print(f"{q:<12} {ds:<8} {str(os_p50) + ' ms':>12} {str(arno_p50) + ' ms':>14} {speedup:>9}")
    print()


def write_results(results, correctness, meta, outdir):
    os.makedirs(outdir, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = os.path.join(outdir, f"bench_{ts}.json")
    payload = {"meta": meta, "results": results, "correctness": correctness}
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"Results written to {path}")
    return path


def compare_files(path_a, path_b):
    with open(path_a) as f:
        a = json.load(f)
    with open(path_b) as f:
        b = json.load(f)
    a_map = {(r["dataset"], r["query"], r["path"]): r for r in a["results"]}
    b_map = {(r["dataset"], r["query"], r["path"]): r for r in b["results"]}
    all_keys = sorted(set(a_map) | set(b_map))
    print(f"\nDelta: {os.path.basename(path_a)} → {os.path.basename(path_b)}")
    print(f"{'Key':<30} {'Before p50':>12} {'After p50':>12} {'Delta':>10}")
    print("-" * 70)
    for key in all_keys:
        ra = a_map.get(key, {})
        rb = b_map.get(key, {})
        p50a = ra.get("hot_p50_ms", "n/a")
        p50b = rb.get("hot_p50_ms", "n/a")
        delta = ""
        if isinstance(p50a, (int, float)) and isinstance(p50b, (int, float)):
            delta = f"{((p50b - p50a) / p50a * 100):+.1f}%"
        print(f"{str(key):<30} {str(p50a) + ' ms':>12} {str(p50b) + ' ms':>12} {delta:>10}")


def main():
    parser = argparse.ArgumentParser(description="IVG Arno Acceleration Benchmark")
    parser.add_argument("--datasets", nargs="+", default=["M"], choices=["S", "M", "L"])
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--skip-load", action="store_true")
    parser.add_argument("--outdir", default=os.path.join(os.path.dirname(__file__), "results"))
    parser.add_argument("--compare", nargs=2, metavar=("FILE_A", "FILE_B"))
    args = parser.parse_args()

    if args.compare:
        compare_files(args.compare[0], args.compare[1])
        return

    conn, iris_obj = connect_iris()

    from iris_vector_graph.engine import IRISGraphEngine
    engine = IRISGraphEngine(conn)

    arno = detect_arno(iris_obj)
    print(f"Arno available: {arno['available']}  bfs={arno['bfs']}  ppr={arno['ppr']}")
    if not arno["available"]:
        print(f"  Reason: {arno['raw'][:120]}")

    version_info = ""
    try:
        version_info = str(iris_obj.classMethodValue("%SYSTEM.Version", "GetVersion"))
    except Exception:
        pass

    all_results = []
    correctness = []

    for ds_label in args.datasets:
        nodes, edge_count = DATASET_PARAMS[ds_label]
        print(f"\n{'='*60}")
        print(f"Dataset {ds_label}: {nodes} nodes / {edge_count} edges")
        print(f"{'='*60}")

        if not args.skip_load:
            from graph_gen import RMATGenerator
            gen = RMATGenerator(seed=args.seed)
            edges = gen.generate_edges(nodes, edge_count)
            load_info = load_graph_to_iris(conn, iris_obj, nodes, edges, ds_label)
            print(f"  Load complete: ^KG built in {load_info['kg_build_ms']}ms, ^NKG: {load_info['nkg_build_ms']}ms")
            try:
                iris_obj.classMethodVoid("Graph.KG.NKGAccel", "ResetCache")
                iris_obj.classMethodValue("Graph.KG.NKGAccel", "Load", "/usr/irissys/mgr/libarno_callout.so")
                iris_obj.classMethodVoid("Graph.KG.NKGAccel", "InvalidateAdjCache")
            except Exception:
                pass
        else:
            print("  Skipping data load (--skip-load)")
            try:
                iris_obj.classMethodValue("Graph.KG.Traversal", "BuildNKG")
                iris_obj.classMethodVoid("Graph.KG.NKGAccel", "ResetCache")
                iris_obj.classMethodValue("Graph.KG.NKGAccel", "Load", "/usr/irissys/mgr/libarno_callout.so")
                print("  ^NKG rebuilt, arno reloaded")
            except Exception as e:
                print(f"  NKG rebuild skipped: {e}")

        seed_node = get_highest_degree_seed(iris_obj) or "node_0"
        print(f"  Seed node: {seed_node}")
        sp_src, sp_dst = pick_shortest_path_pair(iris_obj, seed_node, target_distance=4)
        print(f"  SP pair: {sp_src} → {sp_dst}")

        print(f"\n  Q1: 1-hop expand (ivg-os only)...")
        try:
            q1_stats = run_timed(lambda: run_q1(engine, seed_node), warmup=args.warmup, runs=args.runs)
            q1_stats.update({"dataset": ds_label, "query": "Q1", "path": "ivg-os"})
            all_results.append(q1_stats)
            print(f"    ivg-os  p50={q1_stats['hot_p50_ms']}ms  count={q1_stats['result_count']}")
        except Exception as e:
            print(f"    Q1 failed: {e}")

        for depth, q_id in [(2, "Q2"), (3, "Q3"), (4, "Q4")]:
            print(f"\n  {q_id}: {depth}-hop BFS...")
            os_raw = None
            os_stats = None
            try:
                os_stats = run_timed(lambda d=depth: run_bfs_os(iris_obj, seed_node, d),
                                     warmup=args.warmup, runs=args.runs)
                if os_stats.get("errors", 0) == args.runs:
                    os_stats["note"] = "MAXSTRING: result set too large for BFSFastJson string return"
                    print(f"    ivg-os  MAXSTRING (result set exceeds IRIS 3.6MB string limit at depth={depth})")
                else:
                    try:
                        raw = iris_obj.classMethodValue("Graph.KG.Traversal", "BFSFastJson", seed_node, "", depth)
                        os_raw = json.loads(str(raw))
                    except Exception:
                        pass
                    print(f"    ivg-os  p50={os_stats['hot_p50_ms']}ms  count={os_stats['result_count']}  errors={os_stats['errors']}")
                os_stats.update({"dataset": ds_label, "query": q_id, "path": "ivg-os"})
                all_results.append(os_stats)
            except Exception as e:
                print(f"    ivg-os {q_id} failed: {e}")

            arno_raw = None
            if arno["bfs"]:
                try:
                    arno_stats = run_timed(lambda d=depth: run_bfs_arno(iris_obj, seed_node, d),
                                           warmup=args.warmup, runs=args.runs)
                    try:
                        raw = iris_obj.classMethodValue("Graph.KG.NKGAccel", "BFSJson", seed_node, "[]", depth, 0)
                        arno_raw = json.loads(str(raw))
                    except Exception:
                        pass
                    arno_stats.update({"dataset": ds_label, "query": q_id, "path": "ivg-arno"})
                    all_results.append(arno_stats)
                    os_p50 = os_stats["hot_p50_ms"] if os_stats else None
                    speedup = round(os_p50 / arno_stats["hot_p50_ms"], 1) if os_p50 and arno_stats.get("hot_p50_ms") else "n/a"
                    print(f"    ivg-arno p50={arno_stats['hot_p50_ms']}ms  count={arno_stats['result_count']}  speedup={speedup}x")
                except Exception as e:
                    print(f"    ivg-arno {q_id} failed: {e}")
            else:
                print(f"    ivg-arno skipped (arno BFS not available)")

            if os_raw is not None and arno_raw is not None:
                c = check_correctness(os_raw, arno_raw, q_id, ds_label)
                correctness.append(c)
                print(f"    Correctness: {c['ivg_os_vs_arno']}")

        print(f"\n  Q5: shortestPath...")
        if sp_dst:
            try:
                q5_stats = run_timed(lambda: run_shortest_path(engine, sp_src, sp_dst),
                                     warmup=args.warmup, runs=args.runs)
                q5_stats.update({"dataset": ds_label, "query": "Q5", "path": "ivg-os"})
                all_results.append(q5_stats)
                print(f"    ivg-os  p50={q5_stats['hot_p50_ms']}ms")
            except Exception as e:
                print(f"    Q5 failed: {e}")
        else:
            print("    Q5 skipped (no SP pair found)")

        print(f"\n  Q6: weighted shortestPath...")
        if sp_dst:
            try:
                q6_stats = run_timed(lambda: run_weighted_sp(engine, sp_src, sp_dst),
                                     warmup=args.warmup, runs=args.runs)
                q6_stats.update({"dataset": ds_label, "query": "Q6", "path": "ivg-os"})
                all_results.append(q6_stats)
                print(f"    ivg-os  p50={q6_stats['hot_p50_ms']}ms")
            except Exception as e:
                print(f"    Q6 failed: {e}")
        else:
            print("    Q6 skipped (no SP pair found)")

    print_table(all_results, arno["available"])

    if correctness:
        print("Correctness Summary:")
        for c in correctness:
            print(f"  {c['dataset']} {c['query']}: {c['ivg_os_vs_arno']}")
    print()

    meta = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "iris_version": version_info,
        "arno_available": arno["available"],
        "arno_bfs": arno["bfs"],
        "seed": args.seed,
        "runs": args.runs,
        "warmup": args.warmup,
        "datasets": {ds: {"nodes": DATASET_PARAMS[ds][0], "edges": DATASET_PARAMS[ds][1]} for ds in args.datasets},
    }

    out_path = write_results(all_results, correctness, meta, args.outdir)

    failing = []
    for r in all_results:
        q, ds, path = r.get("query"), r.get("dataset"), r.get("path")
        p50 = r.get("hot_p50_ms")
        if ds == "M" and path == "ivg-arno" and isinstance(p50, (int, float)):
            targets = {"Q2": 5, "Q3": 30, "Q4": 60}
            if q in targets and p50 > targets[q]:
                sc_map = {"Q2": "008", "Q3": "009", "Q4": "010"}
                sc_id = sc_map.get(q, "???")
                failing.append(f"SC-{sc_id}: {q} arno p50={p50}ms > target {targets[q]}ms")
        if ds == "M" and path == "ivg-os" and q == "Q1" and isinstance(p50, (int, float)) and p50 > 1:
            failing.append(f"SC-011: Q1 os p50={p50}ms > 1ms target")
    for c in correctness:
        if "FAIL" in c.get("ivg_os_vs_arno", ""):
            failing.append(f"SC-012: {c['dataset']} {c['query']} correctness {c['ivg_os_vs_arno']}")

    if failing:
        print("FAILING acceptance criteria:")
        for f in failing:
            print(f"  ✗ {f}")
    else:
        print("All measured acceptance criteria: PASS")

    conn.close()


if __name__ == "__main__":
    main()
