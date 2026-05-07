import os
import statistics
import time

import pytest

ENTERPRISE_CONTAINER = "iris-enterprise-2026"
SEEDS = ["p_28587302384882", "p_10995116278184", "p_10995116279040"]
PRED = "KNOWS"


@pytest.fixture(scope="module")
def enterprise_engine():
    try:
        import iris
        from iris_vector_graph.engine import IRISGraphEngine

        conn = iris.connect("localhost", 4972, "USER", "_SYSTEM", "SYS")
        o = iris.createIRIS(conn)
        o.classMethodValue("Graph.KG.ArnoAccel", "Load")
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id LIKE 'p_%'")
        if cur.fetchone()[0] < 1000:
            pytest.skip("LDBC SF10 data not loaded on enterprise container")
        eng = IRISGraphEngine(conn)
        yield eng, o
        conn.close()
    except Exception as e:
        pytest.skip(f"Enterprise IRIS unavailable: {e}")


@pytest.fixture(scope="module")
def with_exact_stats(enterprise_engine):
    eng, o = enterprise_engine
    o.classMethodValue("Graph.KG.Traversal", "Build2HopExactStats")
    yield eng, o


@pytest.mark.e2e
def test_khop2_count_exact_matches_khop2_count(with_exact_stats):
    eng, o = with_exact_stats
    from iris_vector_graph.schema import _call_classmethod_large
    for seed in SEEDS:
        exact = int(o.classMethodValue("Graph.KG.Traversal", "KHop2CountExact", seed, PRED))
        slow = int(_call_classmethod_large(o, "Graph.KG.Traversal", "KHop2Count", seed, PRED))
        assert exact == slow, f"KHop2CountExact({seed}) = {exact} != KHop2Count = {slow}"


@pytest.mark.e2e
def test_khop2_count_exact_under_1ms(with_exact_stats):
    eng, o = with_exact_stats
    seed = SEEDS[0]
    times = []
    for _ in range(10):
        o.classMethodValue("Graph.KG.Traversal", "KHop2CountExact", seed, PRED)
    for _ in range(30):
        t0 = time.perf_counter()
        o.classMethodValue("Graph.KG.Traversal", "KHop2CountExact", seed, PRED)
        times.append((time.perf_counter() - t0) * 1000)
    p50 = statistics.median(times)
    assert p50 < 1.0, f"KHop2CountExact p50={p50:.3f}ms exceeds 1ms target"


@pytest.mark.e2e
def test_execute_cypher_2hop_count_uses_exact(with_exact_stats):
    eng, o = with_exact_stats
    seed = SEEDS[0]
    from iris_vector_graph.schema import _call_classmethod_large
    expected = int(_call_classmethod_large(o, "Graph.KG.Traversal", "KHop2Count", seed, PRED))
    times = []
    for _ in range(5):
        o.classMethodValue("Graph.KG.Traversal", "KHop2CountExact", seed, PRED)
    for _ in range(20):
        t0 = time.perf_counter()
        result = eng.execute_cypher(
            f"MATCH (s {{node_id:$id}})-[:{PRED}*2]->(n) RETURN count(n) AS cnt",
            {"id": seed},
        )
        times.append((time.perf_counter() - t0) * 1000)
    p50 = statistics.median(times)
    cnt = result.rows[0][0]
    assert int(cnt) == expected, f"execute_cypher returned {cnt}, expected {expected}"
    assert p50 < 1.0, f"execute_cypher 2-hop COUNT p50={p50:.3f}ms exceeds 1ms"


@pytest.mark.e2e
def test_khop2_count_exact_fallback(enterprise_engine):
    eng, o = enterprise_engine
    import iris
    conn = eng.conn
    iris_obj = iris.createIRIS(conn)
    iris_obj.classMethodVoid("Graph.KG.Traversal", "Build2HopExactStats")
    iris_obj.classMethodValue("%SYSTEM.Process", "Xecute", 'Kill ^KG("deg2p_exact")')
    seed = SEEDS[0]
    from iris_vector_graph.schema import _call_classmethod_large
    exact = int(iris_obj.classMethodValue("Graph.KG.Traversal", "KHop2CountExact", seed, PRED))
    slow = int(_call_classmethod_large(iris_obj, "Graph.KG.Traversal", "KHop2Count", seed, PRED))
    assert exact == slow, f"Fallback KHop2CountExact={exact} != KHop2Count={slow}"


@pytest.mark.e2e
def test_rebuild_nkg_under_30s(enterprise_engine):
    eng, o = enterprise_engine
    t0 = time.time()
    ok = eng.rebuild_nkg()
    elapsed = time.time() - t0
    assert ok, "rebuild_nkg() returned False"
    assert elapsed <= 30.0, f"rebuild_nkg took {elapsed:.1f}s, exceeds 30s target"
