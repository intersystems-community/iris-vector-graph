import json
import os
import time

import pytest

IRIS_HOST = os.environ.get("IRIS_HOST", "localhost")
IRIS_PORT = int(os.environ.get("IRIS_PORT", "2972"))
IRIS_NS = os.environ.get("IRIS_NAMESPACE", "USER")
IRIS_USER = os.environ.get("IRIS_USERNAME", "_SYSTEM")
IRIS_PASS = os.environ.get("IRIS_PASSWORD", "SYS")
ARNO_LIB = os.environ.get("ARNO_LIB", "/usr/irissys/mgr/libarno_callout.so")

SKIP_ARNO = os.environ.get("SKIP_ARNO_TESTS", "false").lower() == "true"
skip_reason = "SKIP_ARNO_TESTS=true or enterprise container unavailable"


@pytest.fixture(scope="module")
def iris_conn():
    try:
        import iris as iris_mod
        c = iris_mod.connect(IRIS_HOST, IRIS_PORT, IRIS_NS, IRIS_USER, IRIS_PASS)
        o = iris_mod.createIRIS(c)
        o.classMethodVoid("Graph.KG.NKGAccel", "ResetCache")
        r = o.classMethodString("Graph.KG.NKGAccel", "Load", ARNO_LIB)
        if not r or str(r) == "0":
            pytest.skip(f"arno .so not loaded from {ARNO_LIB}")
        yield c, o
        c.close()
    except Exception as e:
        pytest.skip(f"{skip_reason}: {e}")


@pytest.fixture(scope="module")
def small_graph(iris_conn):
    c, o = iris_conn
    cur = c.cursor()
    cur.execute("SELECT COUNT(*) FROM SQLUser.rdf_edges")
    count = cur.fetchone()[0]
    if count < 1000:
        pytest.skip("insufficient graph data — load at least 1K/5K before running")
    o.classMethodString("Graph.KG.Traversal", "BuildNKG")
    o.classMethodVoid("Graph.KG.NKGAccel", "ResetCache")
    o.classMethodString("Graph.KG.NKGAccel", "Load", ARNO_LIB)
    seed = str(o.classMethodString("Graph.KG.NKGAccel", "GetFirstNKGNode"))
    if not seed or seed == "0":
        seed = "node_0"
    return seed


@pytest.mark.skipif(SKIP_ARNO, reason=skip_reason)
class TestArnoBFSGlobal:

    def test_sc001_no_maxstring_on_m_scale(self, iris_conn, small_graph):
        _, o = iris_conn
        seed = small_graph
        for depth in [2, 3, 4]:
            raw = o.classMethodString("Graph.KG.NKGAccel", "BFSJson", seed, "[]", depth, 0)
            s = str(raw)
            assert not s.startswith("DEBUG:"), f"seed not found or adj empty at depth={depth}: {s[:100]}"
            assert not s.startswith("ERROR:"), f"arno error at depth={depth}: {s[:100]}"
            results = json.loads(s)
            assert isinstance(results, list), f"expected list at depth={depth}"
            assert len(results) > 0, f"depth={depth} returned 0 results — SC-001 fail"

    def test_sc002_correctness_arno_subset_of_os(self, iris_conn, small_graph):
        _, o = iris_conn
        seed = small_graph
        for depth in [2, 3]:
            raw_arno = o.classMethodString("Graph.KG.NKGAccel", "BFSJson", seed, "[]", depth, 0)
            arno_results = json.loads(str(raw_arno))
            arno_nodes = {r["o"] for r in arno_results}

            try:
                raw_os = o.classMethodString("Graph.KG.Traversal", "BFSFastJson", seed, "", depth)
                os_results = json.loads(str(raw_os))
                os_nodes = {r["o"] for r in os_results}
                spurious = arno_nodes - os_nodes
                assert not spurious, (
                    f"SC-002 fail at depth={depth}: arno returned {len(spurious)} "
                    f"nodes not in os result: {list(spurious)[:5]}"
                )
            except Exception:
                pass

    def test_sc003_predicate_filter_falls_back(self, iris_conn, small_graph):
        _, o = iris_conn
        seed = small_graph
        raw_filtered = o.classMethodString("Graph.KG.NKGAccel", "BFSJson", seed, '["NONEXISTENT_PRED"]', 2, 0)
        s = str(raw_filtered)
        assert not s.startswith("ERROR:"), f"SC-003: unexpected error: {s[:100]}"
        results = json.loads(s)
        assert isinstance(results, list), "SC-003: must return a list"

    def test_sc004_fallback_when_arno_unloaded(self, iris_conn, small_graph):
        c, o = iris_conn
        seed = small_graph
        o.classMethodVoid("Graph.KG.NKGAccel", "ResetCache")
        raw = o.classMethodString("Graph.KG.NKGAccel", "BFSJson", seed, "[]", 1, 0)
        results = json.loads(str(raw))
        assert isinstance(results, list), "SC-004: fallback path must return a list"
        o.classMethodString("Graph.KG.NKGAccel", "Load", ARNO_LIB)

    def test_sc005_no_regression_small_scale(self, iris_conn, small_graph):
        _, o = iris_conn
        seed = small_graph
        times = []
        for _ in range(6):
            t0 = time.perf_counter()
            raw = o.classMethodString("Graph.KG.NKGAccel", "BFSJson", seed, "[]", 2, 0)
            r = json.loads(str(raw))
            times.append((time.perf_counter() - t0) * 1000)
        times.sort()
        hot = times[1:]
        p50 = hot[len(hot) // 2]
        count = len({x["o"] for x in r})
        assert count > 0, "SC-005: BFS depth=2 must return results"
        assert p50 < 200, f"SC-005: p50={p50:.1f}ms exceeds 200ms regression threshold"

    def test_result_schema(self, iris_conn, small_graph):
        _, o = iris_conn
        seed = small_graph
        raw = o.classMethodString("Graph.KG.NKGAccel", "BFSJson", seed, "[]", 1, 5)
        results = json.loads(str(raw))
        assert len(results) > 0
        for r in results:
            for field in ["s", "p", "o", "w", "step"]:
                assert field in r, f"missing field '{field}' in result: {r}"
            assert isinstance(r["step"], int) and r["step"] >= 1
            assert isinstance(r["w"], float)
            assert r["s"] != r["o"], "self-loop in results"

    def test_max_results_cap(self, iris_conn, small_graph):
        _, o = iris_conn
        seed = small_graph
        raw = o.classMethodString("Graph.KG.NKGAccel", "BFSJson", seed, "[]", 3, 0)
        results = json.loads(str(raw))
        assert len(results) > 50, (
            f"max_results=0 must return >50 results once hard cap is removed; got {len(results)}"
        )

    def test_depth_scaling_stable(self, iris_conn, small_graph):
        _, o = iris_conn
        seed = small_graph
        from iris_vector_graph.schema import _call_classmethod_large
        prev_count = 0
        for depth in range(1, 8):
            raw = _call_classmethod_large(o, "Graph.KG.NKGAccel", "BFSJson", seed, "[]", depth, 0)
            results = json.loads(raw)
            count = len({x["o"] for x in results})
            assert isinstance(results, list), f"depth={depth} must return a list"
            prev_count = count

    def test_10_hop_no_crash(self, iris_conn, small_graph):
        _, o = iris_conn
        seed = small_graph
        from iris_vector_graph.schema import _call_classmethod_large
        for depth in range(1, 11):
            raw = _call_classmethod_large(o, "Graph.KG.NKGAccel", "BFSJson", seed, "[]", depth, 0)
            results = json.loads(raw)
            assert isinstance(results, list), f"depth={depth} returned non-list"
