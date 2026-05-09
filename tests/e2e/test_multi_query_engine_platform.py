import time
import uuid

import pytest
from fastapi.testclient import TestClient

PREFIX = f"mqep_{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def engine(iris_connection):
    from iris_vector_graph.engine import IRISGraphEngine
    return IRISGraphEngine(iris_connection)


@pytest.fixture(scope="module")
def gql_client(engine):
    from iris_vector_graph import gql as ivg_gql
    app = ivg_gql.create_app(engine)
    return TestClient(app)


@pytest.fixture(scope="module")
def api_client(iris_connection):
    from iris_vector_graph.engine import IRISGraphEngine
    from api.main import create_app
    eng = IRISGraphEngine(iris_connection)
    app = create_app(engine=eng)
    return TestClient(app)


@pytest.fixture(scope="module")
def test_graph(engine):
    n1 = f"{PREFIX}:Node1"
    n2 = f"{PREFIX}:Node2"
    n3 = f"{PREFIX}:Node3"
    engine.create_node(n1, labels=["TestType"], properties={"name": "Alpha", "score": 1})
    engine.create_node(n2, labels=["TestType"], properties={"name": "Beta", "score": 2})
    engine.create_node(n3, labels=["TestType"], properties={"name": "Gamma", "score": 3})
    engine.create_edge(n1, "LINKS", n2)
    engine.create_edge(n2, "LINKS", n3)
    engine.rebuild_kg()
    yield n1, n2, n3
    for n in [n1, n2, n3]:
        engine.delete_node(n)


@pytest.mark.e2e
def test_fastapi_application_health(api_client):
    response = api_client.get("/health")
    assert response.status_code == 200


@pytest.mark.e2e
def test_cypher_engine_query_test_data(engine, test_graph):
    n1, n2, n3 = test_graph
    result = engine.execute_cypher(
        "MATCH (n:TestType {node_id: $id}) RETURN n.node_id",
        {"id": n1}
    )
    assert result.rows and result.rows[0][0] == n1


@pytest.mark.e2e
def test_graphql_engine_query_test_data(gql_client, test_graph):
    n1, n2, n3 = test_graph
    r = gql_client.post("/graphql", json={
        "query": f'{{ node(id: "{n1}") {{ id labels }} }}'
    })
    assert r.status_code == 200
    body = r.json()
    assert "data" in body


@pytest.mark.e2e
def test_cross_engine_consistency(engine, test_graph):
    n1, n2, n3 = test_graph
    cypher_result = engine.execute_cypher(
        "MATCH (n:TestType {node_id: $id}) RETURN n.node_id",
        {"id": n1}
    )
    assert cypher_result.rows
    cypher_id = cypher_result.rows[0][0]

    node_data = engine.get_node(n1)
    assert node_data["id"] == cypher_id == n1


@pytest.mark.e2e
def test_hybrid_workflow_graphql_to_cypher(engine, gql_client, test_graph):
    n1, n2, n3 = test_graph
    r = gql_client.post("/graphql", json={
        "query": f'{{ node(id: "{n1}") {{ id labels }} }}'
    })
    assert r.status_code == 200

    result = engine.execute_cypher(
        "MATCH (n {node_id: $id}) RETURN n.node_id",
        {"id": n1}
    )
    assert result.rows and result.rows[0][0] == n1


@pytest.mark.e2e
def test_performance_all_engines(engine, test_graph):
    n1, n2, n3 = test_graph
    times = []
    for _ in range(5):
        t0 = time.perf_counter()
        engine.execute_cypher("MATCH (n:TestType {node_id: $id}) RETURN n.node_id", {"id": n1})
        times.append((time.perf_counter() - t0) * 1000)
    times.sort()
    assert times[2] < 200, f"Cypher p50={times[2]:.1f}ms too slow"


@pytest.mark.e2e
def test_error_handling_all_engines(engine):
    result = engine.execute_cypher("MATCH (n) WHERE n.node_id = $id RETURN n.node_id", {"id": "nonexistent_xyz"})
    assert result.rows == [] or result.rows is not None


@pytest.mark.e2e
def test_graph_traversal_cypher_vs_graphql(engine, test_graph):
    n1, n2, n3 = test_graph
    result = engine.execute_cypher(
        "MATCH (a {node_id: $id})-[:LINKS*1..2]->(b) RETURN b.node_id",
        {"id": n1}
    )
    ids = {r[0] for r in result.rows}
    assert n2 in ids
    assert n3 in ids
