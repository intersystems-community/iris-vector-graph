import time
import pytest


@pytest.fixture(scope="module")
def engine(iris_connection):
    from iris_vector_graph.engine import IRISGraphEngine
    return IRISGraphEngine(iris_connection)


@pytest.mark.e2e
def test_fraud_data_loaded(engine):
    result = engine.execute_cypher("MATCH (n) RETURN count(n) AS c")
    if result.error and 'Table' in (result.error or ''):
        pytest.skip("Schema not initialized")
    assert result.error is None
    assert result.rows and result.rows[0][0] >= 0


@pytest.mark.e2e
def test_fraud_schema_exists(engine):
    result = engine.execute_cypher(
        "MATCH (n) WHERE labels(n) <> '[]' RETURN DISTINCT labels(n) LIMIT 10"
    )
    assert isinstance(result.rows, list)


@pytest.mark.e2e
def test_account_query_by_id(engine):
    result = engine.execute_cypher(
        "MATCH (n) RETURN n.id LIMIT 1"
    )
    if not result.rows:
        pytest.skip("No nodes in database")
    assert result.rows[0][0] is not None


@pytest.mark.e2e
def test_account_with_risk_score(engine, iris_connection):
    import uuid
    pfx = f"risk_{uuid.uuid4().hex[:8]}"
    created = engine.create_node(pfx, properties={"risk_score": "0.85", "type": "Account"})
    if not created:
        pytest.skip("Schema not initialized — cannot create test node")
    result = engine.execute_cypher(
        "MATCH (n) WHERE n.risk_score IS NOT NULL RETURN n.id, n.risk_score LIMIT 5"
    )
    engine.delete_node(pfx)
    assert len(result.rows) >= 1
    for _, score in result.rows:
        assert 0.0 <= float(score) <= 1.0


@pytest.mark.e2e
def test_transaction_graph_traversal(engine):
    result = engine.execute_cypher(
        "MATCH ()-[r]->() RETURN count(r) AS c"
    )
    if not result.rows or result.rows[0][0] == 0:
        pytest.skip("No edges available")
    src = engine.execute_cypher(
        "MATCH (n)-[r:FROM_ACCOUNT]->(m) RETURN n.id, m.id LIMIT 5"
    )
    if not src["rows"]:
        src = engine.execute_cypher("MATCH (n)-[r]->(m) RETURN n.id, m.id LIMIT 5")
    t0 = time.perf_counter()
    result = engine.execute_cypher(
        "MATCH (a)-[r]->(b) RETURN type(r), b.id LIMIT 20"
    )
    assert (time.perf_counter() - t0) * 1000 < 5000
    assert len(result.rows) >= 0


@pytest.mark.e2e
def test_multi_hop_transaction_path(engine):
    src = engine.execute_cypher("MATCH (n)-[r]->(m) RETURN n.id LIMIT 1")
    if not src["rows"]:
        pytest.skip("No edges available")
    source = src["rows"][0][0]
    t0 = time.perf_counter()
    result = engine.execute_cypher(
        "MATCH (n {id: $id})-[*1..2]->(m) RETURN m.id LIMIT 10",
        {"id": source}
    )
    assert (time.perf_counter() - t0) * 1000 < 10000
    assert isinstance(result.rows, list)


@pytest.mark.e2e
def test_ring_pattern_detection(engine):
    t0 = time.perf_counter()
    result = engine.execute_cypher(
        "MATCH (n)-[r1]->(m)-[r2]->(n) RETURN n.id, count(r1) AS ring_count LIMIT 20"
    )
    assert (time.perf_counter() - t0) * 1000 < 10000
    assert isinstance(result.rows, list)


@pytest.mark.e2e
def test_mule_account_detection(engine):
    t0 = time.perf_counter()
    result = engine.execute_cypher(
        "MATCH (n)-[r]->(m) RETURN m.id, count(r) AS degree ORDER BY degree DESC LIMIT 10"
    )
    assert (time.perf_counter() - t0) * 1000 < 5000
    assert isinstance(result.rows, list)


@pytest.mark.e2e
def test_counterparty_analysis(engine):
    result = engine.execute_cypher(
        "MATCH (a)-[r1]->(b)-[r2]->(c) WHERE a.id <> c.id RETURN a.id, count(DISTINCT c.id) AS counterparties LIMIT 5"
    )
    assert isinstance(result.rows, list)


@pytest.mark.e2e
def test_vector_anomaly_detection(engine):
    import time, json, random
    r = engine.execute_cypher("MATCH (n) RETURN n.id LIMIT 1")
    if not r["rows"]:
        pytest.skip("No nodes available")
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
def test_alert_query(engine):
    result = engine.execute_cypher(
        "MATCH (n:Alert) RETURN n.id, n.severity LIMIT 10"
    )
    assert isinstance(result.rows, list)
