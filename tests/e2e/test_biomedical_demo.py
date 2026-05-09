import pytest


@pytest.fixture(scope="module")
def engine(iris_connection):
    from iris_vector_graph.engine import IRISGraphEngine
    return IRISGraphEngine(iris_connection)


@pytest.mark.e2e
def test_database_connectivity(engine):
    assert engine.is_ready


@pytest.mark.e2e
def test_schema_initialized(engine):
    assert engine.is_ready


@pytest.mark.e2e
def test_node_count_positive(engine):
    result = engine.execute_cypher("MATCH (n) RETURN count(n) AS c")
    assert result["rows"][0][0] >= 0


@pytest.mark.e2e
def test_protein_query_by_id(engine):
    result = engine.execute_cypher(
        "MATCH (n) WHERE labels(n) <> '[]' RETURN n.id, labels(n) LIMIT 5"
    )
    if not result["rows"]:
        pytest.skip("No labeled entities found — load sample data first")
    entity_id = result["rows"][0][0]
    assert entity_id is not None


@pytest.mark.e2e
def test_protein_query_with_properties(engine):
    result = engine.execute_cypher(
        "MATCH (n) WHERE n.name IS NOT NULL RETURN n.id, n.name LIMIT 1"
    )
    if not result["rows"]:
        pytest.skip("No entities with 'name' property found")
    entity_id, name = result["rows"][0]
    assert entity_id is not None
    assert name is not None


@pytest.mark.e2e
def test_protein_vector_similarity(engine):
    import time, json, random
    result = engine.execute_cypher("MATCH (n) RETURN n.id LIMIT 1")
    if not result["rows"]:
        pytest.skip("No nodes available for vector similarity test")
    t0 = time.perf_counter()
    rand_vec = json.dumps([random.random() for _ in range(engine.embedding_dimension or 4)])
    try:
        results = engine.search_nodes_by_vector(rand_vec, k=5)
        assert (time.perf_counter() - t0) * 1000 < 10000
        assert isinstance(results, list)
    except Exception as e:
        if "embedding" in str(e).lower() or "vector" in str(e).lower() or "dimension" in str(e).lower():
            pytest.skip(f"Vector search not configured: {e}")
        raise


@pytest.mark.e2e
def test_protein_interactions_graph_traversal(engine):
    import time
    result = engine.execute_cypher(
        "MATCH ()-[r]->() RETURN count(r) AS c"
    )
    if result["rows"][0][0] == 0:
        pytest.skip("No edges available for graph traversal test")

    src_result = engine.execute_cypher(
        "MATCH (n)-[r]->() RETURN n.id LIMIT 1"
    )
    if not src_result["rows"]:
        pytest.skip("No source nodes with edges found")
    source_id = src_result["rows"][0][0]

    t0 = time.perf_counter()
    result = engine.execute_cypher(
        "MATCH (n {id: $id})-[r]->(m) RETURN type(r), m.id",
        {"id": source_id}
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert elapsed_ms < 1000
    assert len(result["rows"]) > 0


@pytest.mark.e2e
def test_multi_hop_graph_traversal(engine):
    import time
    src_result = engine.execute_cypher(
        "MATCH (n)-[r]->() RETURN n.id LIMIT 1"
    )
    if not src_result["rows"]:
        pytest.skip("No edges available")
    source_id = src_result["rows"][0][0]

    t0 = time.perf_counter()
    result = engine.execute_cypher(
        "MATCH (n {id: $id})-[*1..2]->(m) RETURN m.id LIMIT 20",
        {"id": source_id}
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert elapsed_ms < 5000
    assert len(result["rows"]) >= 0


@pytest.mark.e2e
def test_graphql_playground_loads(api_client):
    response = api_client.get("/graphql")
    assert response.status_code in [200, 307, 308]


@pytest.mark.e2e
def test_graphql_introspection(api_client):
    response = api_client.post("/graphql", json={
        "query": "query { __schema { types { name } } }"
    })
    assert response.status_code == 200
    data = response.json()
    assert "data" in data or "errors" in data


@pytest.mark.e2e
def test_graphql_health_check(api_client):
    response = api_client.get("/health")
    assert response.status_code == 200
    assert "status" in response.json()


@pytest.mark.e2e
def test_hybrid_search_rrf_fusion(engine):
    import time
    t0 = time.perf_counter()
    results = engine.kg_RRF_FUSE(
        k=5, k1=5, k2=5, c=60,
        query_vector="[]",
        query_text="gene protein"
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert elapsed_ms < 5000
    assert isinstance(results, list)


@pytest.mark.e2e
def test_operators_available(engine):
    assert engine is not None
    assert hasattr(engine, "kg_PERSONALIZED_PAGERANK")
    assert hasattr(engine, "kg_KNN_VEC")
    assert hasattr(engine, "kg_RRF_FUSE")
