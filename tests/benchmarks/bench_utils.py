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
    """(seed, node): a node reached from the seed at the requested distance, else the farthest one, never the seed itself
    (a pair (seed, seed) made Q5/Q6 time a zero-hop answer)."""
    try:
        raw = call_classmethod_large(iris_obj, "Graph.KG.Traversal", "BFSFastJson", seed, "", min(target_distance, 3))
        results = [r for r in json.loads(str(raw)) if r.get("o") != seed]
        candidates = [r for r in results if r.get("step") == target_distance]
        if candidates:
            return seed, candidates[0]["o"]
        if results:
            return seed, max(results, key=lambda r: r.get("step", 0))["o"]
    except Exception as e:
        print(f"  Warning: pick_shortest_path_pair failed: {e}")
    return seed, None


def weighted_sp_query(src: str, dst: str) -> str:
    """The weighted shortest-path CALL with the node ids inline: a `$parameter` argument was read as the text "a" on 4.2.3 and
    the call answered zero rows."""
    esc = lambda v: str(v).replace("'", "''")  # noqa: E731
    return f"CALL ivg.shortestPath.weighted('{esc(src)}', '{esc(dst)}', 'weight', 9999, 10) YIELD totalCost RETURN totalCost"


def answer_or_raise(result):
    """A call that carries an engine error, or finds nothing, is an error to run_timed rather than a fast success."""
    err = getattr(result, "error", None)
    if err:
        raise RuntimeError(str(err)[:200])
    if not result.rows:
        raise RuntimeError("no rows")
    return result


def run_timed(fn, warmup: int = 3, runs: int = 10) -> dict:
    for _ in range(warmup):
        try:
            fn()
        except Exception:
            pass

    latencies, result_count, errors, last_error = [], 0, 0, ""
    for _ in range(runs):
        t0 = time.perf_counter()
        try:
            _, count = fn()
            result_count = count
        except Exception as exc:
            errors += 1
            last_error = f"{type(exc).__name__}: {exc}"[:300]
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
        **({"last_error": last_error} if errors else {}),
        "values_ms": [round(v, 2) for v in latencies],
    }


_OVERFLOW = ("MAXSTRING", "STRINGSTACK", "<THROW>")
_STREAM_VARIANT = {("Graph.KG.Traversal", "BFSFastJson"): "BFSFastStream"}


def _read_chunks(iris_obj, cls: str, marker: str) -> str:
    _, tag, n = marker.split(":")
    return "".join(str(iris_obj.classMethodValue(cls, "ReadLargeOutChunk", tag, i)) for i in range(1, int(n) + 1))


def call_classmethod_large(iris_obj, cls: str, method: str, *args) -> str:
    """Same reassembly as the library (CHUNKED: and SORTED: replies become the JSON they stand for), plus the same fallback on
    both sides of a comparison: a BFS answer longer than IRIS's 3.6 MB string limit is staged in chunks by `BFSFastStream` /
    `ReadBFSResultsStream` and read back, only after the string call overflowed, so every answer that fits is the string call's own.
    The benchmark needs this on the baseline too, whose library has no such path."""
    from iris_vector_graph.schema import _call_classmethod_large

    try:
        return _call_classmethod_large(iris_obj, cls, method, *args)
    except Exception as exc:
        if not any(m in str(exc) for m in _OVERFLOW):
            raise
        stream = _STREAM_VARIANT.get((cls, method))
        if stream is not None:
            marker = str(iris_obj.classMethodValue(cls, stream, *args))
        elif (cls, method) == ("Graph.KG.NKGAccel", "BFSJson"):
            raw = str(iris_obj.classMethodValue(cls, method, *args))
            if not raw.startswith("SORTED:"):
                raise
            marker = str(iris_obj.classMethodValue("Graph.KG.Traversal", "ReadBFSResultsStream", raw.split(":")[1]))
            cls = "Graph.KG.Traversal"
        else:
            raise
        if not marker.startswith("CHUNKED:"):
            return marker
        rows = _read_chunks(iris_obj, cls, marker)
        return "[" + rows + "]" if rows else "[]"
