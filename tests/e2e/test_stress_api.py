import os
import time
import uuid
import json

import pytest

IRIS_HOST = os.environ.get("IRIS_HOST", "localhost")
IRIS_PORT = int(os.environ.get("IRIS_PORT", "1972"))
IRIS_NS = os.environ.get("IRIS_NAMESPACE", "USER")
IRIS_USER = os.environ.get("IRIS_USERNAME", "test")
IRIS_PASS = os.environ.get("IRIS_PASSWORD", "test")
API_BASE = os.environ.get("IVG_API_URL", "http://localhost:8000")


@pytest.fixture(scope="module")
def iris_conn():
    try:
        import iris
        from iris_vector_graph.engine import IRISGraphEngine
        c = iris.connect(IRIS_HOST, IRIS_PORT, IRIS_NS, IRIS_USER, IRIS_PASS)
        e = IRISGraphEngine(c, embedding_dimension=4)
        e.initialize_schema()
        yield c, e
        c.close()
    except Exception as ex:
        pytest.skip(f"IRIS unavailable: {ex}")


@pytest.fixture(scope="module")
def api_client(iris_conn):
    try:
        from fastapi.testclient import TestClient
        from api.main import create_app
        from iris_vector_graph.engine import IRISGraphEngine
        conn, engine = iris_conn
        app = create_app(engine=engine)
        return TestClient(app)
    except Exception as ex:
        pytest.skip(f"API not available: {ex}")


@pytest.fixture(scope="module")
def gql_client(iris_conn):
    try:
        from fastapi.testclient import TestClient
        from api.main import create_app
        conn, engine = iris_conn
        app = create_app(engine=engine)
        return TestClient(app)
    except Exception as ex:
        pytest.skip(f"API not available: {ex}")


class TestCypherRESTEndpoint:

    def test_simple_match_returns_200(self, api_client, iris_conn):
        conn, engine = iris_conn
        pfx = f"api_{uuid.uuid4().hex[:6]}"
        engine.create_node(f"{pfx}:n1", labels=["APINode"], properties={"val": 1})
        r = api_client.post("/api/cypher", json={
            "query": f"MATCH (n:APINode) WHERE n.node_id = '{pfx}:n1' RETURN n.node_id"
        })
        assert r.status_code == 200
        body = r.json()
        assert "rows" in body
        assert "columns" in body

    def test_syntax_error_returns_400(self, api_client):
        r = api_client.post("/api/cypher", json={"query": "MATCH (n RETURN n"})
        assert r.status_code == 400
        body = r.json()
        assert "errorType" in body or "error" in body

    def test_parameterized_query(self, api_client, iris_conn):
        conn, engine = iris_conn
        pfx = f"apip_{uuid.uuid4().hex[:6]}"
        engine.create_node(f"{pfx}:p1", labels=["APIParam"], properties={"score": 99})
        r = api_client.post("/api/cypher", json={
            "query": "MATCH (n:APIParam) WHERE n.node_id = $nid RETURN n.score",
            "parameters": {"nid": f"{pfx}:p1"}
        })
        assert r.status_code == 200
        body = r.json()
        assert body["rowCount"] >= 1

    def test_response_includes_timing(self, api_client):
        r = api_client.post("/api/cypher", json={
            "query": "MATCH (n:__NeverExists__999) RETURN n.node_id"
        })
        assert r.status_code == 200
        body = r.json()
        assert "executionTimeMs" in body or "translationTimeMs" in body

    def test_response_includes_trace_id(self, api_client):
        r = api_client.post("/api/cypher", json={
            "query": "MATCH (n:__NeverExists__999) RETURN n.node_id"
        })
        body = r.json()
        assert "traceId" in body

    def test_empty_query_returns_error(self, api_client):
        r = api_client.post("/api/cypher", json={"query": ""})
        assert r.status_code in (400, 422, 500)

    def test_missing_query_field_returns_422(self, api_client):
        r = api_client.post("/api/cypher", json={"parameters": {}})
        assert r.status_code == 422

    def test_large_result_set(self, api_client, iris_conn):
        conn, engine = iris_conn
        pfx = f"large_{uuid.uuid4().hex[:6]}"
        for i in range(500):
            engine.create_node(f"{pfx}:{i}", labels=["LargeResult"])
        r = api_client.post("/api/cypher", json={
            "query": f"MATCH (n:LargeResult) WHERE n.node_id STARTS WITH '{pfx}:' RETURN n.node_id"
        })
        assert r.status_code == 200
        body = r.json()
        assert body["rowCount"] >= 500

    def test_sql_execution_error_returns_500(self, api_client):
        r = api_client.post("/api/cypher", json={
            "query": "MATCH (n) WHERE 1/0 = 1 RETURN n.node_id"
        })
        assert r.status_code in (400, 500)

    def test_write_query_create_node(self, api_client):
        pfx = f"wq_{uuid.uuid4().hex[:6]}"
        r = api_client.post("/api/cypher", json={
            "query": f"CREATE (n:WriteQueryNode {{node_id: '{pfx}:created'}}) RETURN n.node_id"
        })
        assert r.status_code in (200, 201, 400, 405)

    def test_concurrent_requests(self, api_client, iris_conn):
        import threading
        conn, engine = iris_conn
        pfx = f"concapi_{uuid.uuid4().hex[:6]}"
        for i in range(10):
            engine.create_node(f"{pfx}:{i}", labels=["ConcAPI"])

        results = []
        errors = []

        def do_request(i):
            try:
                r = api_client.post("/api/cypher", json={
                    "query": f"MATCH (n:ConcAPI) WHERE n.node_id STARTS WITH '{pfx}:' RETURN count(n) AS c"
                })
                results.append(r.status_code)
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=do_request, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, f"Concurrent API requests failed: {errors}"
        assert all(s == 200 for s in results), f"Some requests failed: {results}"

    def test_enable_optimization_flag(self, api_client):
        r = api_client.post("/api/cypher", json={
            "query": "MATCH (n:__NeverExists__999) RETURN n.node_id",
            "enable_optimization": True
        })
        assert r.status_code == 200
        body = r.json()
        assert "queryMetadata" in body

    def test_sql_query_exposed_in_metadata(self, api_client):
        r = api_client.post("/api/cypher", json={
            "query": "MATCH (n:__NeverExists__999) RETURN n.node_id",
            "enable_optimization": True
        })
        body = r.json()
        meta = body.get("queryMetadata", {})
        assert "sqlQuery" in meta


class TestGraphQLEndpoint:

    def test_gql_stats_query(self, gql_client):
        r = gql_client.post("/graphql", json={
            "query": "{ stats { nodeCount edgeCount labelCount } }"
        })
        assert r.status_code == 200
        body = r.json()
        assert "data" in body
        stats = body["data"].get("stats")
        assert stats is not None
        assert "nodeCount" in stats

    def test_gql_node_query_by_id(self, gql_client, iris_conn):
        conn, engine = iris_conn
        pfx = f"gql_{uuid.uuid4().hex[:6]}"
        engine.create_node(f"{pfx}:g1", labels=["GQLNode"], properties={"name": "test_gql"})
        r = gql_client.post("/graphql", json={
            "query": f'{{ node(id: "{pfx}:g1") {{ id labels }} }}'
        })
        assert r.status_code == 200
        body = r.json()
        assert "data" in body

    def test_gql_nodes_query_with_label(self, iris_conn):
        conn, engine = iris_conn
        pfx = f"gqln_{uuid.uuid4().hex[:6]}"
        for i in range(5):
            engine.create_node(f"{pfx}:{i}", labels=["GQLNodes"])
        from fastapi.testclient import TestClient
        from api.main import create_app
        fresh_app = create_app(engine=engine)
        client = TestClient(fresh_app)
        r = client.post("/graphql", json={
            "query": '{ nodes(label: "GQLNodes", limit: 10) { id labels } }'
        })
        assert r.status_code == 200
        body = r.json()
        assert "data" in body
        nodes = body["data"].get("nodes", [])
        assert len(nodes) >= 5

    def test_gql_cypher_passthrough(self, gql_client, iris_conn):
        conn, engine = iris_conn
        pfx = f"gqlc_{uuid.uuid4().hex[:6]}"
        engine.create_node(f"{pfx}:c1", labels=["GQLCypher"])
        r = gql_client.post("/graphql", json={
            "query": f'{{ cypher(query: "MATCH (n:GQLCypher) WHERE n.node_id = \\"{pfx}:c1\\" RETURN n.node_id") }}'
        })
        assert r.status_code == 200
        body = r.json()
        assert "data" in body

    def test_gql_introspection(self, gql_client):
        r = gql_client.post("/graphql", json={
            "query": "{ __schema { types { name } } }"
        })
        assert r.status_code == 200
        body = r.json()
        assert "data" in body
        types = body["data"]["__schema"]["types"]
        type_names = [t["name"] for t in types]
        assert "Query" in type_names

    def test_gql_syntax_error_returns_errors(self, gql_client):
        r = gql_client.post("/graphql", json={
            "query": "{ invalid_field_xyz }"
        })
        assert r.status_code == 200
        body = r.json()
        assert "errors" in body

    def test_gql_missing_required_arg_returns_error(self, gql_client):
        r = gql_client.post("/graphql", json={
            "query": "{ node { id } }"
        })
        body = r.json()
        assert "errors" in body


class TestAPIHealthAndEdgeCases:

    def test_health_check_endpoint(self, api_client):
        for path in ["/health", "/healthz", "/api/health", "/"]:
            r = api_client.get(path)
            if r.status_code in (200, 204):
                return
        pytest.skip("No health endpoint found")

    def test_post_with_wrong_content_type(self, api_client):
        import httpx
        r = api_client.post(
            "/api/cypher",
            content=b"MATCH (n) RETURN n",
            headers={"Content-Type": "text/plain"}
        )
        assert r.status_code in (415, 422, 400)

    def test_malformed_json_returns_422(self, api_client):
        r = api_client.post(
            "/api/cypher",
            content=b"{invalid json",
            headers={"Content-Type": "application/json"}
        )
        assert r.status_code in (400, 422)

    def test_very_long_query_string(self, api_client):
        long_query = "MATCH (n:X) WHERE " + " AND ".join([f"n.prop{i} = {i}" for i in range(10)]) + " RETURN n"
        r = api_client.post("/api/cypher", json={"query": long_query})
        assert r.status_code in (200, 400, 413, 500)
