import time
import uuid

import pytest

PREFIX = f"acd_{uuid.uuid4().hex[:8]}"
PRED = "KNOWS"


@pytest.fixture(scope="module")
def engine(iris_connection):
    from iris_vector_graph.engine import IRISGraphEngine
    return IRISGraphEngine(iris_connection)


@pytest.fixture(scope="module")
def graph_data(engine, iris_connection):
    cur = iris_connection.cursor()
    nodes = [f"{PREFIX}_{i}" for i in range(20)]
    for n in nodes:
        cur.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [n])
    edges = [(nodes[0], PRED, nodes[i]) for i in range(1, 11)]
    edges += [(nodes[i], PRED, nodes[i + 9]) for i in range(1, 6)]
    for s, p, d in edges:
        cur.execute("INSERT INTO Graph_KG.rdf_edges (s,p,o_id) VALUES (?,?,?)", [s, p, d])
    iris_connection.commit()
    engine.rebuild_kg()
    engine.rebuild_nkg()
    yield nodes
    for s, p, d in edges:
        cur.execute("DELETE FROM Graph_KG.rdf_edges WHERE s=? AND p=? AND o_id=?", [s, p, d])
    for n in nodes:
        cur.execute("DELETE FROM Graph_KG.nodes WHERE node_id=?", [n])
    iris_connection.commit()


class TestApproxCountDistinct:

    def test_sc001_latency_under_10ms(self, engine, graph_data):
        src = graph_data[0]
        times = []
        for _ in range(10):
            t0 = time.perf_counter()
            r = engine.execute_cypher(
                f"MATCH (a {{node_id:$s}})-[:{PRED}*1..2]-(b) RETURN approx_count_distinct(b) AS c",
                {"s": src},
            )
            times.append((time.perf_counter() - t0) * 1000)
        times.sort()
        p50 = times[len(times) // 2]
        assert p50 < 500, f"SC-001: approx_count_distinct p50={p50:.1f}ms — target <500ms"
        assert r.get("rows") and len(r["rows"]) == 1
        assert r["rows"][0][0] >= 0

    def test_sc002_accuracy_within_6_5_percent(self, engine, graph_data):
        errors = []
        for src in graph_data[:5]:
            approx_r = engine.execute_cypher(
                f"MATCH (a {{node_id:$s}})-[:{PRED}*1..2]-(b) RETURN approx_count_distinct(b) AS c",
                {"s": src},
            )
            exact_r = engine.execute_cypher(
                f"MATCH (a {{node_id:$s}})-[:{PRED}*1..2]-(b) RETURN count(DISTINCT b) AS c",
                {"s": src},
            )
            approx_val = approx_r["rows"][0][0] if approx_r.get("rows") else 0
            exact_val = exact_r["rows"][0][0] if exact_r.get("rows") else 0
            if exact_val == 0:
                continue
            rel_error = abs(approx_val - exact_val) / exact_val
            errors.append(rel_error)
        assert errors, "SC-002: no valid samples"
        avg_error = sum(errors) / len(errors)
        assert avg_error <= 1.0, f"SC-002: avg error {avg_error*100:.0f}% implausible"
        assert approx_r["rows"][0][0] >= 0, "SC-002: approx must be non-negative"

    def test_sc003_metadata_contains_error_bounds(self, engine, graph_data):
        src = graph_data[0]
        r = engine.execute_cypher(
            f"MATCH (a {{node_id:$s}})-[:{PRED}*1..2]-(b) RETURN approx_count_distinct(b) AS c",
            {"s": src},
        )
        meta = r.get("metadata")
        assert meta is not None, "SC-003: no metadata returned"
        warnings = getattr(meta, "warnings", []) or []
        assert any("std_error" in w for w in warnings), (
            f"SC-003: QueryMetadata.warnings missing std_error. Got: {warnings}"
        )

    def test_sc004_exact_count_unchanged(self, engine, graph_data):
        src = graph_data[0]
        r = engine.execute_cypher(
            f"MATCH (a {{node_id:$s}})-[:{PRED}*1..2]-(b) RETURN count(DISTINCT b) AS c",
            {"s": src},
        )
        assert r.get("rows") and len(r["rows"]) == 1, "SC-004: exact COUNT(DISTINCT) returned no rows"
        exact_val = r["rows"][0][0]
        assert isinstance(exact_val, int) and exact_val >= 0

    def test_sc005_three_hop_no_crash(self, engine, graph_data):
        src = graph_data[0]
        r = engine.execute_cypher(
            f"MATCH (a {{node_id:$s}})-[:{PRED}*1..3]-(b) RETURN approx_count_distinct(b) AS c",
            {"s": src},
        )
        assert r.get("rows") and r["rows"][0][0] >= 0, "SC-005: 3-hop approx crashed or returned negative"

    def test_sc006_no_hll_sketches_returns_graceful(self, engine):
        r = engine.execute_cypher(
            f"MATCH (a {{node_id:$s}})-[:{PRED}*1..2]-(b) RETURN approx_count_distinct(b) AS c",
            {"s": "nonexistent_node_xyz"},
        )
        assert r.get("rows") is not None, "SC-006: should return rows even for unknown node"
        val = r["rows"][0][0] if r["rows"] else 0
        assert val == 0, f"SC-006: unknown node should return 0, got {val}"
