import statistics
import time
import uuid

import pytest


PRED = "KNOWS"
PREFIX = f"ic3_{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def graph(iris_connection):
    from iris_vector_graph.engine import IRISGraphEngine
    import iris as _iris
    eng = IRISGraphEngine(iris_connection)
    o = _iris.createIRIS(iris_connection)
    cur = iris_connection.cursor()
    nodes = [f"{PREFIX}_{i}" for i in range(10)]
    for n in nodes:
        cur.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [n])
    # Star: 0 -> 1..4; 1 -> 5..9 (2-hop from 0 = 5..9)
    edges = [(nodes[0], PRED, nodes[i]) for i in range(1, 5)]
    edges += [(nodes[1], PRED, nodes[i]) for i in range(5, 10)]
    for s, p, d in edges:
        cur.execute("INSERT INTO Graph_KG.rdf_edges (s,p,o_id) VALUES (?,?,?)", [s, p, d])
    iris_connection.commit()
    eng.rebuild_kg()
    eng.rebuild_nkg()
    o.classMethodVoid("Graph.KG.Traversal", "Build2HopExactStats")
    yield eng, o, nodes
    for s, p, d in edges:
        cur.execute("DELETE FROM Graph_KG.rdf_edges WHERE s=? AND p=? AND o_id=?", [s, p, d])
    for n in nodes:
        cur.execute("DELETE FROM Graph_KG.nodes WHERE node_id=?", [n])
    iris_connection.commit()


@pytest.mark.e2e
def test_khop2_count_exact_matches_khop2_count(graph):
    eng, o, nodes = graph
    from iris_vector_graph.schema import _call_classmethod_large
    seed = nodes[0]
    exact = int(o.classMethodValue("Graph.KG.Traversal", "KHop2CountExact", seed, PRED))
    slow = int(_call_classmethod_large(o, "Graph.KG.Traversal", "KHop2Count", seed, PRED))
    assert exact == slow, f"KHop2CountExact={exact} != KHop2Count={slow}"


@pytest.mark.e2e
def test_khop2_count_exact_under_1ms(graph):
    eng, o, nodes = graph
    seed = nodes[0]
    for _ in range(5):
        o.classMethodValue("Graph.KG.Traversal", "KHop2CountExact", seed, PRED)
    times = []
    for _ in range(20):
        t0 = time.perf_counter()
        o.classMethodValue("Graph.KG.Traversal", "KHop2CountExact", seed, PRED)
        times.append((time.perf_counter() - t0) * 1000)
    p50 = statistics.median(times)
    assert p50 < 5.0, f"KHop2CountExact p50={p50:.3f}ms exceeds 5ms target"


@pytest.mark.e2e
def test_execute_cypher_2hop_count_uses_exact(graph):
    eng, o, nodes = graph
    seed = nodes[0]
    from iris_vector_graph.schema import _call_classmethod_large
    expected = int(_call_classmethod_large(o, "Graph.KG.Traversal", "KHop2Count", seed, PRED))
    result = eng.execute_cypher(
        f"MATCH (s {{node_id:$id}})-[:{PRED}*2]->(n) RETURN count(n) AS cnt",
        {"id": seed},
    )
    cnt = result.rows[0][0]
    assert int(cnt) == expected, f"execute_cypher returned {cnt}, expected {expected}"


@pytest.mark.e2e
def test_khop2_count_exact_fallback(graph):
    eng, o, nodes = graph
    from iris_vector_graph.schema import _call_classmethod_large
    seed = nodes[0]
    o.classMethodVoid("Graph.KG.Traversal", "Build2HopExactStats")
    o.kill('^KG("deg2p_exact")')
    exact = int(o.classMethodValue("Graph.KG.Traversal", "KHop2CountExact", seed, PRED))
    slow = int(_call_classmethod_large(o, "Graph.KG.Traversal", "KHop2Count", seed, PRED))
    assert exact == slow, f"Fallback KHop2CountExact={exact} != KHop2Count={slow}"


@pytest.mark.e2e
def test_rebuild_nkg_under_30s(graph):
    eng, o, nodes = graph
    t0 = time.time()
    ok = eng.rebuild_nkg()
    elapsed = time.time() - t0
    assert ok, "rebuild_nkg() returned False"
    assert elapsed <= 30.0, f"rebuild_nkg took {elapsed:.1f}s, exceeds 30s"
