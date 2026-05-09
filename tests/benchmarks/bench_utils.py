import json
import time
import statistics
from typing import Optional


def load_graph_to_iris(conn, iris_obj, nodes: int, edges_list: list, dataset_label: str) -> dict:
    cursor = conn.cursor()
    print(f"  [{dataset_label}] Clearing existing graph data...")
    for tbl in ["rdf_edges", "rdf_props", "rdf_labels", "nodes"]:
        cursor.execute(f"DELETE FROM SQLUser.{tbl}")
    conn.commit()

    print(f"  [{dataset_label}] Inserting {nodes} nodes...")
    for i in range(nodes):
        cursor.execute("INSERT INTO SQLUser.nodes (node_id) VALUES (?)", [f"node_{i}"])

    print(f"  [{dataset_label}] Inserting {len(edges_list)} edges...")
    batch = [(f"node_{s}", "R", f"node_{o}") for s, o in edges_list]
    for i in range(0, len(batch), 1000):
        conn.cursor().executemany(
            "INSERT INTO SQLUser.rdf_edges (s, p, o_id, qualifiers) VALUES (?, ?, ?, '{}')",
            batch[i:i+1000]
        )
    conn.commit()

    print(f"  [{dataset_label}] Building ^KG...")
    t0 = time.perf_counter()
    iris_obj.classMethodValue("Graph.KG.Traversal", "BuildKG")
    kg_ms = round((time.perf_counter() - t0) * 1000, 1)

    print(f"  [{dataset_label}] Building ^NKG...")
    nkg_ms = None
    t0 = time.perf_counter()
    try:
        iris_obj.classMethodValue("Graph.KG.Traversal", "BuildNKG")
        nkg_ms = round((time.perf_counter() - t0) * 1000, 1)
    except Exception as e:
        print(f"  [{dataset_label}] ^NKG build skipped: {e}")

    return {"nodes": nodes, "edges": len(edges_list), "kg_build_ms": kg_ms, "nkg_build_ms": nkg_ms}


def detect_arno(iris_obj) -> dict:
    try:
        caps_json = iris_obj.classMethodValue("Graph.KG.NKGAccel", "Capabilities")
        caps = json.loads(str(caps_json))
        bfs_ready = bool(caps.get("bfs", False)) and bool(caps.get("nkg_data", False))
        return {"available": True, "bfs": bfs_ready, "ppr": bool(caps.get("ppr", False)), "raw": str(caps_json)}
    except Exception as e:
        return {"available": False, "bfs": False, "ppr": False, "raw": str(e)}


def get_highest_degree_seed(iris_obj) -> str:
    try:
        seed = str(iris_obj.classMethodValue("Graph.KG.NKGAccel", "GetFirstNKGNode"))
        if seed and seed != "0":
            return seed
    except Exception:
        pass
    return "node_0"


def pick_shortest_path_pair(iris_obj, seed: str, target_distance: int = 4):
    try:
        raw = iris_obj.classMethodValue("Graph.KG.Traversal", "BFSFastJson", seed, "", min(target_distance, 3))
        results = json.loads(str(raw))
        candidates = [r for r in results if r.get("step") == target_distance]
        if candidates:
            return seed, candidates[0]["o"]
        if results:
            return seed, max(results, key=lambda r: r.get("step", 0))["o"]
    except Exception as e:
        print(f"  Warning: pick_shortest_path_pair failed: {e}")
    return seed, None


def run_timed(fn, warmup: int = 3, runs: int = 10) -> dict:
    for _ in range(warmup):
        try:
            fn()
        except Exception:
            pass

    latencies, result_count, errors = [], 0, 0
    for _ in range(runs):
        t0 = time.perf_counter()
        try:
            _, count = fn()
            result_count = count
        except Exception:
            errors += 1
        latencies.append((time.perf_counter() - t0) * 1000)

    if not latencies:
        return {"error": "all runs failed", "errors": errors}

    latencies.sort()
    n = len(latencies)
    hot = latencies[1:] if n > 1 else latencies

    def pct(arr, p):
        m = len(arr)
        return round(arr[min(int(m * p / 100), m - 1)], 2)

    return {
        "min_ms": round(min(latencies), 2),
        "cold_p50_ms": round(latencies[0], 2),
        "hot_p50_ms": pct(hot, 50),
        "hot_p90_ms": pct(hot, 90),
        "hot_p99_ms": pct(hot, 99),
        "max_ms": round(max(latencies), 2),
        "mean_ms": round(statistics.mean(latencies), 2),
        "result_count": result_count,
        "errors": errors,
        "values_ms": [round(v, 2) for v in latencies],
    }


def call_classmethod_large(iris_obj, cls: str, method: str, *args) -> str:
    raw = str(iris_obj.classMethodValue(cls, method, *args))
    if not raw.startswith("CHUNKED:"):
        return raw
    _, tag, n_str = raw.split(":", 2)
    n = int(n_str)
    return "".join(
        str(iris_obj.classMethodValue(cls, "ReadLargeOutChunk", tag, i))
        for i in range(1, n + 1)
    )
