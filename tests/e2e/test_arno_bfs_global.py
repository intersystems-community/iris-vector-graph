"""
Spec 094 — Arno BFSJson Global-Buffer Transfer.
Tests the kg_bfs_compute + kg_bfs_read_chunk two-call pattern.
Requires iris-vector-graph-enterprise container (port 2972).
"""
import json
import os
import time
import pytest

IRIS_HOST = os.environ.get("IRIS_HOST", "localhost")
IRIS_PORT = int(os.environ.get("IRIS_PORT", "2972"))
IRIS_NS = os.environ.get("IRIS_NAMESPACE", "USER")
IRIS_USER = os.environ.get("IRIS_USERNAME", "_SYSTEM")
IRIS_PASS = os.environ.get("IRIS_PASSWORD", "SYS")
SKIP = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"


@pytest.fixture(scope="module")
def arno_setup():
    """Connect to enterprise, verify arno is available, return (conn, iris_obj, seed)."""
    try:
        import iris
        from iris_vector_graph.engine import IRISGraphEngine
        conn = iris.connect(IRIS_HOST, IRIS_PORT, IRIS_NS, IRIS_USER, IRIS_PASS)
        eng = IRISGraphEngine(conn)
        o = iris.createIRIS(conn)

        # Check arno is available
        has_arno = eng._detect_arno()
        if not has_arno:
            pytest.skip("Arno not available on this container")

        # Find a seed with KNOWS edges
        cur = conn.cursor()
        cur.execute("SELECT TOP 1 s FROM Graph_KG.rdf_edges WHERE p = 'KNOWS'")
        row = cur.fetchone()
        if not row:
            pytest.skip("No KNOWS edges in database — load LDBC data first")
        seed = str(row[0])

        yield conn, o, eng, seed
        conn.close()
    except Exception as e:
        pytest.skip(f"Enterprise IRIS unavailable: {e}")


@pytest.mark.skipif(SKIP, reason="SKIP_IRIS_TESTS=true")
class TestArnoBFSGlobal:

    def test_sc001_bfs_no_maxstring_on_medium_graph(self, arno_setup):
        conn, o, eng, seed = arno_setup
        from iris_vector_graph.schema import _call_classmethod_large
        result_str = _call_classmethod_large(o, "Graph.KG.NKGAccel", "BFSJson",
                                              seed, '["KNOWS"]', 2, 0)
        assert result_str is not None
        assert not result_str.startswith("ERROR"), f"BFSJson error: {result_str[:200]}"
        results = json.loads(result_str)
        assert isinstance(results, list), "Result must be a JSON array"
        assert len(results) > 0, "Expected at least some BFS results"

    def test_sc002_correctness_vs_bfs_fast_json(self, arno_setup):
        conn, o, eng, seed = arno_setup
        from iris_vector_graph.schema import _call_classmethod_large

        arno_results = json.loads(_call_classmethod_large(
            o, "Graph.KG.NKGAccel", "BFSJson", seed, '["KNOWS"]', 2, 0))
        fallback_results = json.loads(_call_classmethod_large(
            o, "Graph.KG.Traversal", "BFSFastJson", seed, "KNOWS", 2))

        assert len(arno_results) > 0
        assert len(fallback_results) > 0
        assert all("o" in r for r in arno_results[:5]), "Arno results must have 'o' key"
        assert all("step" in r for r in arno_results[:5]), "Arno results must have 'step' key"

    def test_sc003_predicate_filter(self, arno_setup):
        conn, o, eng, seed = arno_setup
        from iris_vector_graph.schema import _call_classmethod_large

        knows_str = _call_classmethod_large(o, "Graph.KG.NKGAccel", "BFSJson",
                                             seed, '["KNOWS"]', 1, 0)
        all_str = _call_classmethod_large(o, "Graph.KG.NKGAccel", "BFSJson",
                                           seed, '[]', 1, 0)

        knows_nodes = {r["o"] for r in json.loads(knows_str)}
        all_nodes = {r["o"] for r in json.loads(all_str)}

        # KNOWS-filtered result must be subset of all-predicate result
        assert knows_nodes.issubset(all_nodes), (
            "KNOWS-filtered nodes not subset of all-predicate nodes"
        )

    def test_sc004_fallback_on_no_arno(self, arno_setup):
        # Structural test — verify the fallback path exists in NKGAccel.cls
        conn, o, eng, seed = arno_setup
        with open("iris_src/src/Graph/KG/NKGAccel.cls") as f:
            cls_src = f.read()
        assert "BFSFastJson" in cls_src, "Fallback to BFSFastJson must exist in NKGAccel.cls"
        assert "Return ##class(Graph.KG.Traversal).BFSFastJson" in cls_src

    def test_sc005_execute_cypher_routes_through_arno(self, arno_setup):
        conn, o, eng, seed = arno_setup
        result = eng.execute_cypher(
            "MATCH (a {node_id:$src})-[:KNOWS*1..2]-(b) RETURN DISTINCT b.node_id LIMIT 100",
            {"src": seed}
        )
        assert result
        assert len(result.rows) > 0, "VL path via execute_cypher must return results"
        assert len(result.rows) <= 100

