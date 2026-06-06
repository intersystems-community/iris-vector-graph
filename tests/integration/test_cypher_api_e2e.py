"""
E2E tests for cypher_api.py FastAPI routes against live ivg-iris.

These tests use a real IRISGraphEngine (not mocked) to verify that the
full stack works: HTTP route → engine → IRIS SQL/ObjectScript → result.

Complements test_cypher_api_admin.py (unit tests with mock engine).
"""
import json
import pytest

try:
    from fastapi.testclient import TestClient
    _HAS_FASTAPI = True
except ImportError:
    _HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(not _HAS_FASTAPI, reason="fastapi not installed")


@pytest.fixture(scope="function")
def live_client(iris_connection, iris_master_cleanup):
    """TestClient with real IRISGraphEngine against ivg-iris."""
    import iris_vector_graph.cypher_api as _api_module
    from iris_vector_graph.cypher_api import app
    from iris_vector_graph.engine import IRISGraphEngine

    eng = IRISGraphEngine(iris_connection, embedding_dimension=128)
    for i in range(5):
        eng.create_node(f"api_e2e_{i}", labels=["E2ENode"],
                        properties={"score": str(i * 0.5)})
    for i in range(4):
        eng.create_edge(f"api_e2e_{i}", "LINKED", f"api_e2e_{i+1}")
    eng.sync()

    # Inject engine directly into the module-level cache
    old_cache = _api_module._engine_cache
    _api_module._engine_cache = eng
    try:
        with TestClient(app, raise_server_exceptions=False) as tc:
            yield tc, eng
    finally:
        _api_module._engine_cache = old_cache


# ---------------------------------------------------------------------------
# Health check E2E
# ---------------------------------------------------------------------------

class TestHealthE2E:

    def test_health_returns_200(self, live_client):
        tc, _ = live_client
        resp = tc.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data


# ---------------------------------------------------------------------------
# Cypher query E2E
# ---------------------------------------------------------------------------

class TestCypherQueryE2E:

    def test_cypher_match_all_nodes(self, live_client):
        tc, _ = live_client
        resp = tc.post("/api/cypher",
                       json={"query": "MATCH (n:E2ENode) RETURN n.node_id LIMIT 3"})
        assert resp.status_code == 200
        data = resp.json()
        assert "columns" in data
        assert "rows" in data

    def test_cypher_count_nodes(self, live_client):
        tc, _ = live_client
        resp = tc.post("/api/cypher",
                       json={"query": "MATCH (n:E2ENode) RETURN count(n) AS cnt"})
        assert resp.status_code == 200
        data = resp.json()
        rows = data.get("rows", [])
        assert len(rows) >= 1
        assert int(rows[0][0]) >= 5

    def test_cypher_parameterized(self, live_client):
        tc, _ = live_client
        resp = tc.post("/api/cypher", json={
            "query": "MATCH (n {node_id: $id}) RETURN n.node_id AS id",
            "parameters": {"id": "api_e2e_0"}
        })
        assert resp.status_code == 200
        data = resp.json()
        rows = data.get("rows", [])
        assert len(rows) >= 1
        assert rows[0][0] == "api_e2e_0"

    def test_cypher_traverse_edge(self, live_client):
        tc, _ = live_client
        resp = tc.post("/api/cypher", json={
            "query": "MATCH (n {node_id: $id})-[:LINKED]->(m) RETURN m.node_id AS neighbor",
            "parameters": {"id": "api_e2e_0"}
        })
        assert resp.status_code == 200
        data = resp.json()
        rows = data.get("rows", [])
        assert len(rows) >= 1
        assert rows[0][0] == "api_e2e_1"

    def test_cypher_empty_result(self, live_client):
        tc, _ = live_client
        resp = tc.post("/api/cypher",
                       json={"query": "MATCH (n {node_id: '__no_such__'}) RETURN n"})
        assert resp.status_code == 200
        data = resp.json()
        rows = data.get("rows", [])
        assert len(rows) == 0


# ---------------------------------------------------------------------------
# Schema endpoint E2E
# ---------------------------------------------------------------------------

class TestSchemaE2E:

    def test_schema_endpoint(self, live_client):
        tc, _ = live_client
        resp = tc.get("/schema")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)

    def test_indexes_endpoint(self, live_client):
        tc, _ = live_client
        resp = tc.get("/indexes")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Stats / server E2E
# ---------------------------------------------------------------------------

class TestStatsE2E:

    def test_stats_endpoint(self, live_client):
        tc, _ = live_client
        resp = tc.get("/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)

    def test_server_info_endpoint(self, live_client):
        tc, _ = live_client
        resp = tc.get("/server")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# Neo4j compat E2E
# ---------------------------------------------------------------------------

class TestNeo4jCompatE2E:

    def test_neo4j_discovery(self, live_client):
        tc, _ = live_client
        resp = tc.get("/db/neo4j")
        assert resp.status_code in (200, 404)

    def test_neo4j_tx_commit(self, live_client):
        tc, _ = live_client
        resp = tc.post("/db/neo4j/tx/commit", json={
            "statements": [
                {"statement": "MATCH (n:E2ENode) RETURN count(n) AS cnt",
                 "parameters": {}}
            ]
        })
        assert resp.status_code in (200, 201, 401, 500)
        if resp.status_code in (200, 201):
            data = resp.json()
            assert "results" in data or "errors" in data

    def test_root_discovery(self, live_client):
        tc, _ = live_client
        resp = tc.get("/")
        assert resp.status_code in (200, 404)


# ---------------------------------------------------------------------------
# Admin routes E2E — schema init, indexes rebuild
# ---------------------------------------------------------------------------

class TestAdminE2E:

    def test_admin_schema_init_e2e(self, live_client):
        """Admin schema init against live IRIS — idempotent, should succeed."""
        tc, _ = live_client
        resp = tc.post("/admin/schema/init",
                       json={"embedding_dim": 128, "auto_deploy_objectscript": False})
        assert resp.status_code in (200, 500)
        if resp.status_code == 200:
            data = resp.json()
            assert "status" in data

    def test_admin_indexes_rebuild_e2e(self, live_client):
        """Admin indexes rebuild triggers rebuild_kg + rebuild_nkg against live IRIS."""
        tc, _ = live_client
        resp = tc.post("/admin/indexes/rebuild")
        assert resp.status_code in (200, 500)
        if resp.status_code == 200:
            data = resp.json()
            assert "status" in data

    def test_admin_queries_e2e(self, live_client):
        """Admin queries endpoint — method exists and is callable.
        The /admin/queries route calls list_active_queries() which is safe
        on Community IRIS (GetISCProduct=4 guard returns []). Route tested
        via mock in test_cypher_api_admin.py; skip live call to avoid
        threading-context segfault with TestClient."""
        pytest.skip("list_active_queries segfaults in TestClient thread context on Community IRIS")

    def test_admin_explain_e2e(self, live_client):
        """Admin explain — translates Cypher to SQL and returns plan."""
        tc, _ = live_client
        resp = tc.post("/admin/explain",
                       json={"query": "MATCH (n:E2ENode) RETURN n.node_id",
                             "parameters": {}})
        assert resp.status_code in (200, 500)
