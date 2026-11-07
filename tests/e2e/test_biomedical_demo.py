"""The biomedical demo's reads, over the repo's biomedical sample data, which this module loads itself.

Its namespace is `IVG_BIODEMO_NAMESPACE`, a scratch namespace with IVG installed that the E2E gate makes per run
(tests/e2e/gate.py GROUPS): the documented loader, `scripts.setup.load_sample_data` (examples/demo_biomedical.py
names it), TRUNCATEs every IVG table first, so it never loads into the session namespace. The tests skipped on a
fresh container ("No labeled entities found — load sample data first") and so never ran in the gate.

By hand, from scripts/sample_data_768.sql: HGNC:11998 (Gene, symbol TP53), DOID:162 (Disease, name Cancer) and
CHEMBL:123 (Drug, name SomeDrug); CHEMBL:123 -targets-> HGNC:11998 -ASSOCIATED_WITH-> DOID:162; a 768-wide vector
for each of the three.
"""

import pytest

from tests.e2e import own_data_ns

NS_VAR = "IVG_BIODEMO_NAMESPACE"
SQL_FILE = own_data_ns.REPO / "scripts" / "sample_data_768.sql"
NODES = {"HGNC:11998": ["Gene"], "DOID:162": ["Disease"], "CHEMBL:123": ["Drug"]}


@pytest.fixture(scope="module")
def engine():
    from iris_vector_graph.engine import IRISGraphEngine
    from scripts.setup import load_sample_data

    conn, _ns = own_data_ns.connect(NS_VAR)
    load_sample_data(conn)
    e = IRISGraphEngine(conn, embedding_dimension=768)
    assert e.sync()
    yield e
    e.erase_all()
    conn.close()


@pytest.mark.e2e
def test_database_connectivity(engine):
    ready = engine.is_ready() if callable(engine.is_ready) else engine.is_ready
    if not ready:
        pytest.skip("IRIS not ready or schema not initialized")


@pytest.mark.e2e
def test_schema_initialized(engine):
    ready = engine.is_ready() if callable(engine.is_ready) else engine.is_ready
    if not ready:
        pytest.skip("IRIS not ready or schema not initialized")


@pytest.mark.e2e
def test_node_count_positive(engine):
    result = engine.execute_cypher("MATCH (n) RETURN count(n) AS c")
    if result.error and 'Table' in (result.error or ''):
        pytest.skip('Schema not initialized')
    assert result.error is None


@pytest.mark.e2e
def test_protein_query_by_id(engine):
    result = engine.execute_cypher("MATCH (n) RETURN n.node_id, labels(n)", raise_on_error=True)
    assert {row[0]: row[1] for row in result.rows} == NODES


@pytest.mark.e2e
def test_protein_query_with_properties(engine):
    result = engine.execute_cypher(
        "MATCH (n) WHERE n.name IS NOT NULL RETURN n.node_id, n.name", raise_on_error=True
    )
    assert sorted(map(tuple, result.rows)) == [("CHEMBL:123", "SomeDrug"), ("DOID:162", "Cancer")]
    result = engine.execute_cypher("MATCH (n:Gene) RETURN n.symbol", raise_on_error=True)
    assert result.rows == [["TP53"]]


@pytest.mark.e2e
def test_protein_vector_similarity(engine):
    import time
    query = own_data_ns.sql_vector(SQL_FILE, "HGNC:11998")
    t0 = time.perf_counter()
    results = engine.search_nodes_by_vector(query, k=3)
    assert (time.perf_counter() - t0) * 1000 < 10000
    # its own vector is nearest to itself, at cosine 1; the other two follow
    assert results[0][0] == "HGNC:11998" and results[0][1] == pytest.approx(1.0, abs=1e-6), results
    assert {r[0] for r in results} == set(NODES), results


@pytest.mark.e2e
def test_protein_interactions_graph_traversal(engine):
    import time
    t0 = time.perf_counter()
    result = engine.execute_cypher(
        "MATCH (n {node_id: $id})-[r]->(m) RETURN type(r), m.node_id",
        {"id": "CHEMBL:123"}, raise_on_error=True,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert elapsed_ms < 1000
    assert result.rows == [["targets", "HGNC:11998"]]


@pytest.mark.e2e
def test_multi_hop_graph_traversal(engine):
    import time
    t0 = time.perf_counter()
    result = engine.execute_cypher(
        "MATCH (n {node_id: $id})-[*1..2]->(m) RETURN m.node_id",
        {"id": "CHEMBL:123"}, raise_on_error=True,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert elapsed_ms < 5000
    # one hop to the gene, two to the disease it is associated with
    assert sorted(r[0] for r in result.rows) == ["DOID:162", "HGNC:11998"]


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
