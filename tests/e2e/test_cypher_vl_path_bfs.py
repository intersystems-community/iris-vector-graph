import json
import os
import time

import pytest

IRIS_HOST = os.environ.get("IRIS_HOST", "localhost")
IRIS_PORT = int(os.environ.get("IRIS_PORT", "4972"))
IRIS_NS = os.environ.get("IRIS_NAMESPACE", "USER")
IRIS_USER = os.environ.get("IRIS_USERNAME", "_SYSTEM")
IRIS_PASS = os.environ.get("IRIS_PASSWORD", "SYS")
SKIP = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"


@pytest.fixture(scope="module")
def engine():
    try:
        import iris
        from iris_vector_graph.engine import IRISGraphEngine
        c = iris.connect(IRIS_HOST, IRIS_PORT, IRIS_NS, IRIS_USER, IRIS_PASS)
        eng = IRISGraphEngine(c)
        yield eng
        c.close()
    except Exception as e:
        pytest.skip(f"IRIS unavailable: {e}")


@pytest.fixture(scope="module")
def knows_data(engine):
    cur = engine.conn.cursor()
    for pred in ('KNOWS', 'R', None):
        if pred:
            cur.execute(f"SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE p = '{pred}'")
        else:
            cur.execute("SELECT COUNT(*) FROM Graph_KG.rdf_edges")
        n = cur.fetchone()[0]
        if n >= 100:
            if pred:
                cur.execute(f"SELECT TOP 1 s FROM Graph_KG.rdf_edges WHERE p = '{pred}'")
            else:
                cur.execute("SELECT TOP 1 s FROM Graph_KG.rdf_edges")
            row = cur.fetchone()
            return str(row[0]) if row else None
    pytest.skip("No graph data loaded — load LDBC SF10 or any graph first")


PRED = os.environ.get("IVG_TEST_PRED", "KNOWS")


@pytest.mark.skipif(SKIP, reason="SKIP_IRIS_TESTS=true")
class TestCypherVLPathBFS:

    def test_sc001_vl_path_under_10ms(self, engine, knows_data):
        src = knows_data
        t0 = time.perf_counter()
        r = engine.execute_cypher(
            "MATCH (a {node_id:$src})-[:KNOWS*1..2]-(b) RETURN DISTINCT b.node_id LIMIT 50",
            {"src": src}
        )
        ms = (time.perf_counter() - t0) * 1000
        assert ms < 500, (
            f"SC-001: [*1..2] took {ms:.0f}ms — likely still using SQL JOIN chain not BFS. "
            f"Target: <500ms (BFS path). Rust arno path achieves <10ms."
        )
        assert len(r.get("rows", [])) > 0, "SC-001: must return results"
        assert r.get("columns") == ["b_node_id"], (
            f"SC-001: expected column ['b_node_id'], got {r.get('columns')}"
        )

    def test_sc002_vl_path_depth3_no_crash(self, engine, knows_data):
        src = knows_data
        r = engine.execute_cypher(
            "MATCH (a {node_id:$src})-[:KNOWS*1..3]-(b) RETURN count(DISTINCT b) AS c",
            {"src": src}
        )
        rows = r.get("rows", [])
        assert rows and rows[0][0] > 0, (
            "SC-002: [*1..3] must not crash (SQLCODE -400) and must return count > 0"
        )

    def test_sc003_results_match_bfs(self, engine, knows_data):
        src = knows_data
        import iris
        from iris_vector_graph.schema import _call_classmethod_large
        o = iris.createIRIS(engine.conn)
        bfs_raw = _call_classmethod_large(o, "Graph.KG.NKGAccel", "BFSJson", src, '["KNOWS"]', 2, 0)
        import json
        bfs_nodes = {r["o"] for r in json.loads(bfs_raw)}

        vl_r = engine.execute_cypher(
            "MATCH (a {node_id:$src})-[:KNOWS*1..2]-(b) RETURN DISTINCT b.node_id",
            {"src": src}
        )
        vl_nodes = {r[0] for r in vl_r.get("rows", [])}

        assert len(vl_nodes) > 0, "SC-003: VL path must return results"
        assert vl_nodes.issubset(bfs_nodes | {src}), (
            f"SC-003: VL path returned nodes not reachable by BFS. "
            f"Extra in VL: {list(vl_nodes - bfs_nodes)[:5]}"
        )

    def test_sc004_distinct_works(self, engine, knows_data):
        src = knows_data
        r = engine.execute_cypher(
            "MATCH (a {node_id:$src})-[:KNOWS*1..2]-(b) RETURN DISTINCT b.node_id LIMIT 20",
            {"src": src}
        )
        rows = r.get("rows", [])
        node_ids = [row[0] for row in rows]
        assert len(node_ids) == len(set(node_ids)), (
            f"SC-004: DISTINCT returned duplicates: {len(node_ids)} rows but {len(set(node_ids))} unique"
        )
        assert len(rows) <= 20, f"SC-004: LIMIT 20 not respected, got {len(rows)} rows"

    def test_sc005_depth4_no_crash(self, engine, knows_data):
        src = knows_data
        r = engine.execute_cypher(
            "MATCH (a {node_id:$src})-[:KNOWS*1..4]-(b) RETURN count(DISTINCT b) AS c",
            {"src": src}
        )
        assert r.get("rows"), "SC-005: [*1..4] must not crash"

    def test_no_regression_single_hop(self, engine, knows_data):
        src = knows_data
        t0 = time.perf_counter()
        r = engine.execute_cypher(
            "MATCH (a {node_id:$src})-[:KNOWS]->(b) RETURN b.node_id LIMIT 10",
            {"src": src}
        )
        ms = (time.perf_counter() - t0) * 1000
        assert ms < 100, f"Single-hop regression: {ms:.0f}ms"
        assert r.get("rows"), "Single-hop must return results"

    def test_vl_path_exact_hops(self, engine, knows_data):
        src = knows_data
        r = engine.execute_cypher(
            "MATCH (a {node_id:$src})-[:KNOWS*2]-(b) RETURN count(b) AS c",
            {"src": src}
        )
        assert r.get("rows"), "Exact hop [*2] must work"

    def test_vl_path_no_upper_bound(self, engine, knows_data):
        src = knows_data
        r = engine.execute_cypher(
            "MATCH (a {node_id:$src})-[:KNOWS*]-(b) RETURN count(DISTINCT b) AS c",
            {"src": src}
        )
        assert r.get("rows"), "Unbounded [*] must work (default max_hops=10)"
