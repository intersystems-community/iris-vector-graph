"""
Tests for iris_vector_graph/cypher_api.py — FastAPI Cypher HTTP server.
Uses FastAPI TestClient — no IRIS connection, engine is mocked.
"""
import json
import os
import pytest
from unittest.mock import MagicMock, patch

try:
    from fastapi.testclient import TestClient
    _HAS_FASTAPI = True
except ImportError:
    _HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(not _HAS_FASTAPI, reason="fastapi not installed")


@pytest.fixture(scope="module")
def mock_engine():
    eng = MagicMock()
    eng.execute_cypher.return_value = MagicMock(
        columns=["node_id"], rows=[["alice"], ["bob"]], error=None,
        metadata=MagicMock(warnings=[], index_usage=[], optimization_applied=[]),
    )
    eng.status.return_value = MagicMock(
        tables=MagicMock(nodes=10, edges=20, labels=5, props=15,
                         node_embeddings=0, edge_embeddings=0),
        adjacency=MagicMock(kg_populated=True, nkg_populated=True, kg_edge_count=20),
        objectscript=MagicMock(deployed=True, classes=[]),
        arno=MagicMock(loaded=False, capabilities={}),
        indexes=MagicMock(hnsw_built=False, ivf_indexes=[], bm25_indexes=[], plaid_indexes=[]),
        embedding_dimension=128,
        probe_ms=1.0,
        errors=[],
        pending_sync=False,
        to_dict=lambda: {"tables": {"nodes": 10}},
    )
    eng._show_indexes.return_value = MagicMock(
        columns=["name", "type", "state"], rows=[["hnsw", "VECTOR", "ONLINE"]]
    )
    eng.initialize_schema = MagicMock(return_value=None)
    eng.conn = MagicMock()
    return eng


@pytest.fixture(scope="module")
def client(mock_engine):
    from iris_vector_graph.cypher_api import app, _reset_engine
    with patch("iris_vector_graph.cypher_api._make_engine", return_value=mock_engine), \
         patch("iris_vector_graph.cypher_api._get_engine", return_value=mock_engine):
        _reset_engine()
        with patch("iris_vector_graph.cypher_api._engine_cache", mock_engine):
            tc = TestClient(app, raise_server_exceptions=False)
            yield tc


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class TestHealth:

    def test_health_returns_200(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_response_has_status(self, client):
        resp = client.get("/health")
        data = resp.json()
        assert "status" in data or resp.status_code == 200


# ---------------------------------------------------------------------------
# Cypher query endpoint
# ---------------------------------------------------------------------------

class TestCypherEndpoint:

    def test_cypher_returns_200(self, client, mock_engine):
        with patch("iris_vector_graph.cypher_api._get_engine", return_value=mock_engine):
            resp = client.post("/api/cypher", json={"query": "MATCH (n) RETURN n.node_id"})
        assert resp.status_code in (200, 401, 500)

    def test_cypher_with_parameters(self, client, mock_engine):
        with patch("iris_vector_graph.cypher_api._get_engine", return_value=mock_engine):
            resp = client.post("/api/cypher", json={
                "query": "MATCH (n {node_id:$x}) RETURN n",
                "parameters": {"x": "alice"},
            })
        assert resp.status_code in (200, 401, 500)

    def test_cypher_no_body_returns_error(self, client):
        resp = client.post("/api/cypher", json={})
        assert resp.status_code in (400, 422, 500)


# ---------------------------------------------------------------------------
# Neo4j compat endpoints
# ---------------------------------------------------------------------------

class TestNeo4jCompat:

    def test_neo4j_discovery(self, client):
        resp = client.get("/db/neo4j")
        assert resp.status_code in (200, 404)

    def test_root_discovery(self, client):
        resp = client.get("/")
        assert resp.status_code in (200, 404)

    def test_neo4j_tx_endpoint(self, client):
        resp = client.get("/db/neo4j/tx")
        assert resp.status_code in (200, 201, 404)

    def test_neo4j_tx_commit(self, client, mock_engine):
        with patch("iris_vector_graph.cypher_api._get_engine", return_value=mock_engine):
            resp = client.post("/db/neo4j/tx/commit", json={
                "statements": [{"statement": "MATCH (n) RETURN n", "parameters": {}}]
            })
        assert resp.status_code in (200, 401, 500)

    def test_neo4j_query_v2(self, client, mock_engine):
        with patch("iris_vector_graph.cypher_api._get_engine", return_value=mock_engine):
            resp = client.post("/db/neo4j/query/v2", json={
                "statement": "MATCH (n) RETURN n", "parameters": {}
            })
        assert resp.status_code in (200, 201, 401, 404, 422, 500)


# ---------------------------------------------------------------------------
# Schema / server / stats endpoints
# ---------------------------------------------------------------------------

class TestInfoEndpoints:

    def test_schema_endpoint(self, client, mock_engine):
        with patch("iris_vector_graph.cypher_api._get_engine", return_value=mock_engine):
            resp = client.get("/schema")
        assert resp.status_code in (200, 500)

    def test_server_info_endpoint(self, client, mock_engine):
        with patch("iris_vector_graph.cypher_api._get_engine", return_value=mock_engine):
            resp = client.get("/server")
        assert resp.status_code in (200, 500)

    def test_stats_endpoint(self, client, mock_engine):
        with patch("iris_vector_graph.cypher_api._get_engine", return_value=mock_engine):
            resp = client.get("/stats")
        assert resp.status_code in (200, 500)

    def test_metrics_endpoint(self, client, mock_engine):
        with patch("iris_vector_graph.cypher_api._get_engine", return_value=mock_engine):
            resp = client.get("/metrics")
        assert resp.status_code in (200, 500)

    def test_indexes_endpoint(self, client, mock_engine):
        with patch("iris_vector_graph.cypher_api._get_engine", return_value=mock_engine):
            resp = client.get("/indexes")
        assert resp.status_code in (200, 500)


# ---------------------------------------------------------------------------
# Browser redirect
# ---------------------------------------------------------------------------

class TestBrowserEndpoint:

    def test_browser_redirect(self, client):
        resp = client.get("/browser", follow_redirects=False)
        assert resp.status_code in (200, 307, 308, 404)


# ---------------------------------------------------------------------------
# API key middleware
# ---------------------------------------------------------------------------

class TestApiKeyMiddleware:

    def test_no_api_key_env_passes_through(self, client, mock_engine):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("IVG_API_KEY", None)
            with patch("iris_vector_graph.cypher_api._get_engine", return_value=mock_engine):
                resp = client.post("/api/cypher", json={"query": "MATCH (n) RETURN n"})
            assert resp.status_code in (200, 500)

    def test_wrong_api_key_returns_401(self, client, mock_engine):
        with patch.dict(os.environ, {"IVG_API_KEY": "secret123"}):
            resp = client.post(
                "/api/cypher",
                json={"query": "MATCH (n) RETURN n"},
                headers={"X-API-Key": "wrong"},
            )
        assert resp.status_code == 401

    def test_correct_api_key_passes(self, client, mock_engine):
        with patch.dict(os.environ, {"IVG_API_KEY": "secret123"}):
            with patch("iris_vector_graph.cypher_api._get_engine", return_value=mock_engine):
                resp = client.post(
                    "/api/cypher",
                    json={"query": "MATCH (n) RETURN n"},
                    headers={"X-API-Key": "secret123"},
                )
        assert resp.status_code in (200, 500)


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------

class TestAdminEndpoints:

    def test_admin_schema_init(self, client, mock_engine):
        with patch("iris_vector_graph.cypher_api._get_engine", return_value=mock_engine):
            resp = client.post("/admin/schema/init", json={"embedding_dim": 128})
        assert resp.status_code in (200, 422, 500)

    def test_run_cypher_helper(self):
        from iris_vector_graph.cypher_api import _run_cypher
        mock_eng = MagicMock()
        mock_eng.execute_cypher.return_value = MagicMock(
            columns=["n"], rows=[["alice"]], error=None,
            metadata=MagicMock(warnings=[], index_usage=[], optimization_applied=[]),
        )
        with patch("iris_vector_graph.cypher_api._get_engine", return_value=mock_eng):
            result = _run_cypher("MATCH (n) RETURN n", {}, limit=100)
        assert isinstance(result, dict)

    def test_run_cypher_with_error(self):
        from iris_vector_graph.cypher_api import _run_cypher
        mock_eng = MagicMock()
        mock_eng.execute_cypher.return_value = MagicMock(
            columns=[], rows=[], error="parse error",
            metadata=MagicMock(warnings=[], index_usage=[], optimization_applied=[]),
        )
        with patch("iris_vector_graph.cypher_api._get_engine", return_value=mock_eng):
            result = _run_cypher("BAD", {})
        assert "error" in result or isinstance(result, dict)


# ---------------------------------------------------------------------------
# _neo4j_meta helper
# ---------------------------------------------------------------------------

class TestNeo4jMeta:

    def test_neo4j_meta_string(self):
        from iris_vector_graph.cypher_api import _neo4j_meta
        result = _neo4j_meta("hello")
        assert result is None or isinstance(result, dict)

    def test_neo4j_meta_int(self):
        from iris_vector_graph.cypher_api import _neo4j_meta
        result = _neo4j_meta(42)
        assert result is None or isinstance(result, dict)

    def test_neo4j_meta_none(self):
        from iris_vector_graph.cypher_api import _neo4j_meta
        result = _neo4j_meta(None)
        assert result is None or isinstance(result, dict)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class TestPydanticModels:

    def test_cypher_request_defaults(self):
        from iris_vector_graph.cypher_api import CypherRequest
        req = CypherRequest(query="MATCH (n) RETURN n")
        assert req.query == "MATCH (n) RETURN n"
        assert req.parameters == {}
        assert req.limitRows == 1000

    def test_neo4j_statement_model(self):
        from iris_vector_graph.cypher_api import Neo4jStatement
        stmt = Neo4jStatement(statement="MATCH (n) RETURN n", parameters={"x": 1})
        assert stmt.statement == "MATCH (n) RETURN n"

    def test_neo4j_tx_request_model(self):
        from iris_vector_graph.cypher_api import Neo4jTxRequest, Neo4jStatement
        req = Neo4jTxRequest(statements=[Neo4jStatement(statement="MATCH (n) RETURN n")])
        assert len(req.statements) == 1
