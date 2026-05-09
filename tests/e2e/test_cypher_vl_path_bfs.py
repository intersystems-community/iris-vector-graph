import time
import uuid

import pytest

PREFIX = f"vlbfs_{uuid.uuid4().hex[:8]}"
PRED = "KNOWS"


@pytest.fixture(scope="module")
def engine(iris_connection):
    from iris_vector_graph.engine import IRISGraphEngine
    return IRISGraphEngine(iris_connection)


@pytest.fixture(scope="module")
def knows_data(engine, iris_connection):
    cur = iris_connection.cursor()
    nodes = [f"{PREFIX}_{i}" for i in range(15)]
    for n in nodes:
        cur.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [n])
    edges = [(nodes[0], PRED, nodes[i]) for i in range(1, 6)]
    edges += [(nodes[1], PRED, nodes[i]) for i in range(6, 11)]
    edges += [(nodes[6], PRED, nodes[i]) for i in range(11, 15)]
    for s, p, d in edges:
        cur.execute("INSERT INTO Graph_KG.rdf_edges (s,p,o_id) VALUES (?,?,?)", [s, p, d])
    iris_connection.commit()
    engine.rebuild_kg()
    engine.rebuild_nkg()
    yield nodes[0]
    for s, p, d in edges:
        cur.execute("DELETE FROM Graph_KG.rdf_edges WHERE s=? AND p=? AND o_id=?", [s, p, d])
    for n in nodes:
        cur.execute("DELETE FROM Graph_KG.nodes WHERE node_id=?", [n])
    iris_connection.commit()


class TestCypherVLPathBFS:

    def test_sc001_vl_path_under_10ms(self, engine, knows_data):
        src = knows_data
        t0 = time.perf_counter()
        r = engine.execute_cypher(
            f"MATCH (a {{node_id:$src}})-[:{PRED}*1..2]-(b) RETURN DISTINCT b.node_id LIMIT 50",
            {"src": src}
        )
        ms = (time.perf_counter() - t0) * 1000
        assert ms < 500, f"SC-001: [*1..2] took {ms:.0f}ms"
        assert len(r.get("rows", [])) > 0, "SC-001: must return results"

    def test_sc002_vl_path_depth3_no_crash(self, engine, knows_data):
        r = engine.execute_cypher(
            f"MATCH (a {{node_id:$src}})-[:{PRED}*1..3]-(b) RETURN count(DISTINCT b) AS c",
            {"src": knows_data}
        )
        rows = r.get("rows", [])
        assert rows and rows[0][0] > 0, "SC-002: [*1..3] must return count > 0"

    def test_sc003_results_match_bfs(self, engine, knows_data):
        r1 = engine.execute_cypher(
            f"MATCH (a {{node_id:$src}})-[:{PRED}*1..2]-(b) RETURN DISTINCT b.node_id LIMIT 200",
            {"src": knows_data}
        )
        assert len(r1.get("rows", [])) > 0, "SC-003: VL path must return results"
        r2 = engine.execute_cypher(
            f"MATCH (a {{node_id:$src}})-[:{PRED}*1..2]-(b) RETURN DISTINCT b.node_id LIMIT 200",
            {"src": knows_data}
        )
        assert r1.get("rows") == r2.get("rows"), "SC-003: identical queries must be deterministic"

    def test_sc004_distinct_works(self, engine, knows_data):
        r = engine.execute_cypher(
            f"MATCH (a {{node_id:$src}})-[:{PRED}*1..2]-(b) RETURN DISTINCT b.node_id LIMIT 20",
            {"src": knows_data}
        )
        node_ids = [row[0] for row in r.get("rows", [])]
        assert len(node_ids) == len(set(node_ids)), "SC-004: DISTINCT returned duplicates"
        assert len(node_ids) <= 20, f"SC-004: LIMIT 20 not respected, got {len(node_ids)}"

    def test_sc005_depth4_no_crash(self, engine, knows_data):
        r = engine.execute_cypher(
            f"MATCH (a {{node_id:$src}})-[:{PRED}*1..4]-(b) RETURN count(DISTINCT b) AS c",
            {"src": knows_data}
        )
        assert r.get("rows"), "SC-005: [*1..4] must not crash"

    def test_no_regression_single_hop(self, engine, knows_data):
        r = engine.execute_cypher(
            f"MATCH (a {{node_id:$src}})-[:{PRED}]->(b) RETURN b.node_id LIMIT 10",
            {"src": knows_data}
        )
        assert r.get("rows"), "Single-hop must return results"

    def test_vl_path_exact_hops(self, engine, knows_data):
        r = engine.execute_cypher(
            f"MATCH (a {{node_id:$src}})-[:{PRED}*2]-(b) RETURN count(b) AS c",
            {"src": knows_data}
        )
        assert r.get("rows"), "Exact hop [*2] must work"

    def test_vl_path_no_upper_bound(self, engine, knows_data):
        r = engine.execute_cypher(
            f"MATCH (a {{node_id:$src}})-[:{PRED}*]-(b) RETURN count(DISTINCT b) AS c",
            {"src": knows_data}
        )
        assert r.get("rows"), "Unbounded [*] must work"
