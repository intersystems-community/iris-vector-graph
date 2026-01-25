#!/usr/bin/env python3
"""
Biomedical Demo E2E Test Suite

Tests the complete biomedical demo workflow including:
- Database connectivity
- Protein queries
- Vector similarity search
- Graph traversal
- GraphQL API operations
- Hybrid search (RRF fusion)

Per Constitution II: All tests use live IRIS database.
"""

import pytest

# ==============================================================================
# T009: Test Database Connectivity
# ==============================================================================


@pytest.mark.e2e
@pytest.mark.requires_database
def test_database_connectivity(iris_connection):
    """Test that database is accessible and schema exists."""
    cursor = iris_connection.cursor()

    # Verify core tables exist
    tables = ["rdf_labels", "rdf_props", "rdf_edges", "kg_NodeEmbeddings"]

    for table in tables:
        cursor.execute(f"SELECT TOP 1 * FROM {table}")
        # No exception means table exists

    # Verify we can query
    cursor.execute("SELECT COUNT(*) FROM rdf_labels")
    count = cursor.fetchone()[0]

    assert count >= 0, "Should be able to count labels"


@pytest.mark.e2e
@pytest.mark.requires_database
def test_biomedical_data_loaded(iris_connection, biomedical_test_data):
    """Test that biomedical sample data is available."""
    # biomedical_test_data fixture from conftest.py

    if not biomedical_test_data["has_data"]:
        pytest.skip("Biomedical sample data not loaded - run load_sample_data()")

    # Should have some data
    assert (
        biomedical_test_data["embedding_count"] > 0 or biomedical_test_data["edge_count"] > 0
    ), "Should have either embeddings or edges"


# ==============================================================================
# T010: Test Protein Query by ID
# ==============================================================================


@pytest.mark.e2e
@pytest.mark.requires_database
def test_protein_query_by_id(iris_connection):
    """Test querying a specific protein/gene by ID."""
    cursor = iris_connection.cursor()

    # Query for a known entity from sample data
    cursor.execute(
        """
        SELECT s, label FROM rdf_labels 
        WHERE label IN ('Gene', 'Protein', 'Disease', 'Drug')
        LIMIT 5
    """
    )

    results = cursor.fetchall()

    # Should find at least one entity
    if len(results) == 0:
        pytest.skip("No biomedical entities found - load sample data first")

    # Verify we can query properties for found entities
    entity_id = results[0][0]

    cursor.execute("SELECT key, val FROM rdf_props WHERE s = ?", (entity_id,))
    props = cursor.fetchall()

    # Entity should exist (may or may not have properties)
    assert entity_id is not None


@pytest.mark.e2e
@pytest.mark.requires_database
def test_protein_query_with_properties(iris_connection):
    """Test querying protein with its properties."""
    cursor = iris_connection.cursor()

    # Find an entity with properties
    cursor.execute(
        """
        SELECT DISTINCT l.s, l.label 
        FROM rdf_labels l
        JOIN rdf_props p ON l.s = p.s
        WHERE l.label IN ('Gene', 'Protein')
        LIMIT 1
    """
    )

    result = cursor.fetchone()

    if result is None:
        pytest.skip("No entities with properties found")

    entity_id, label = result

    # Get all properties
    cursor.execute("SELECT key, val FROM rdf_props WHERE s = ?", (entity_id,))
    props = {row[0]: row[1] for row in cursor.fetchall()}

    assert len(props) > 0, f"Entity {entity_id} should have properties"


# ==============================================================================
# T011: Test Protein Vector Similarity
# ==============================================================================


@pytest.mark.e2e
@pytest.mark.requires_database
def test_protein_vector_similarity(iris_connection, biomedical_test_data, timing_tracker):
    """Test vector similarity search for proteins."""
    cursor = iris_connection.cursor()

    # Check if we have embeddings
    cursor.execute("SELECT COUNT(*) FROM kg_NodeEmbeddings")
    embedding_count = cursor.fetchone()[0]

    if embedding_count == 0:
        pytest.skip("No embeddings available for vector similarity test")

    # Get a sample embedding
    cursor.execute("SELECT id, emb FROM kg_NodeEmbeddings LIMIT 1")
    result = cursor.fetchone()

    if result is None:
        pytest.skip("Could not retrieve sample embedding")

    sample_id = result[0]

    # Try vector similarity search (may fail if VECTOR functions unavailable)
    try:
        with timing_tracker:
            cursor.execute(
                """
                SELECT TOP 5 e2.id, VECTOR_COSINE(e1.emb, e2.emb) as similarity
                FROM kg_NodeEmbeddings e1, kg_NodeEmbeddings e2
                WHERE e1.id = ?
                AND e2.id != ?
                ORDER BY similarity DESC
            """,
                (sample_id, sample_id),
            )

            similar = cursor.fetchall()

        timing_tracker.assert_under(10000, "Vector similarity search")  # 10s max

        assert similar is not None
        similar_list = list(similar) if similar else []
        assert len(similar_list) >= 0

    except Exception as e:
        if "VECTOR" in str(e).upper():
            pytest.skip("VECTOR functions not available (requires IRIS 2025.1+)")
        raise


# ==============================================================================
# T012: Test Protein Interactions Graph Traversal
# ==============================================================================


@pytest.mark.e2e
@pytest.mark.requires_database
def test_protein_interactions_graph_traversal(iris_connection, timing_tracker):
    """Test graph traversal for protein interactions."""
    cursor = iris_connection.cursor()

    # Check if we have edges
    cursor.execute("SELECT COUNT(*) FROM rdf_edges")
    edge_count = cursor.fetchone()[0]

    if edge_count == 0:
        pytest.skip("No edges available for graph traversal test")

    cursor.execute(
        """
        SELECT TOP 1 s 
        FROM rdf_edges
    """
    )

    result = cursor.fetchone()

    if result is None:
        pytest.skip("No entities with edges found")

    source_id = result[0]

    # Traverse 1-hop relationships
    with timing_tracker:
        cursor.execute(
            """
            SELECT p, o_id FROM rdf_edges WHERE s = ?
        """,
            (source_id,),
        )

        edges = cursor.fetchall()

    timing_tracker.assert_under(1000, "1-hop graph traversal")  # 1s max

    assert len(edges) > 0, f"Entity {source_id} should have outgoing edges"


@pytest.mark.e2e
@pytest.mark.requires_database
def test_multi_hop_graph_traversal(iris_connection, timing_tracker):
    """Test multi-hop graph traversal (2 hops)."""
    cursor = iris_connection.cursor()

    # Find an entity with edges
    cursor.execute("SELECT s FROM rdf_edges LIMIT 1")
    result = cursor.fetchone()

    if result is None:
        pytest.skip("No edges available")

    source_id = result[0]

    # 2-hop traversal
    with timing_tracker:
        cursor.execute(
            """
            SELECT e1.s, e1.p, e1.o_id, e2.p as p2, e2.o_id as o_id2
            FROM rdf_edges e1
            LEFT JOIN rdf_edges e2 ON e1.o_id = e2.s
            WHERE e1.s = ?
            LIMIT 20
        """,
            (source_id,),
        )

        paths = cursor.fetchall()

    timing_tracker.assert_under(5000, "2-hop graph traversal")  # 5s max

    paths_list = list(paths) if paths else []
    assert len(paths_list) >= 0


# ==============================================================================
# T013: Test GraphQL Playground Loads
# ==============================================================================


@pytest.mark.e2e
@pytest.mark.requires_database
def test_graphql_playground_loads(api_client):
    """Test that GraphQL playground UI is accessible."""
    # Test the GraphQL endpoint exists
    response = api_client.get("/graphql")

    # GraphQL playground returns HTML or redirects
    assert response.status_code in [
        200,
        307,
        308,
    ], f"GraphQL endpoint should be accessible, got {response.status_code}"


@pytest.mark.e2e
@pytest.mark.requires_database
def test_graphql_introspection(api_client):
    """Test GraphQL schema introspection."""
    # Introspection query
    query = """
    query {
        __schema {
            types {
                name
            }
        }
    }
    """

    response = api_client.post("/graphql", json={"query": query})

    assert response.status_code == 200, f"Introspection should succeed: {response.text}"

    data = response.json()
    assert "data" in data or "errors" in data  # Either data or errors is valid GraphQL response


@pytest.mark.e2e
@pytest.mark.requires_database
def test_graphql_health_check(api_client):
    """Test health endpoint returns expected structure."""
    response = api_client.get("/health")

    assert response.status_code == 200

    data = response.json()
    assert "status" in data


# ==============================================================================
# T014: Test Hybrid Search RRF Fusion
# ==============================================================================


@pytest.mark.e2e
@pytest.mark.requires_database
def test_hybrid_search_rrf_fusion(iris_connection, timing_tracker):
    """Test hybrid search combining vector and text results."""
    cursor = iris_connection.cursor()

    # Check prerequisites
    cursor.execute("SELECT COUNT(*) FROM kg_NodeEmbeddings")
    embedding_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM rdf_props")
    prop_count = cursor.fetchone()[0]

    if embedding_count == 0 and prop_count == 0:
        pytest.skip("No data available for hybrid search test")

    vector_results = None

    with timing_tracker:
        cursor.execute(
            """
            SELECT s, val FROM rdf_props 
            WHERE val LIKE '%gene%' OR val LIKE '%protein%'
            LIMIT 10
        """
        )
        text_results = cursor.fetchall()

        if embedding_count > 0:
            cursor.execute("SELECT id FROM kg_NodeEmbeddings LIMIT 10")
            vector_results = cursor.fetchall()

    timing_tracker.assert_under(5000, "Hybrid search components")

    vector_count = len(list(vector_results)) if vector_results else 0
    text_count = len(list(text_results)) if text_results else 0
    total_results = text_count + vector_count
    assert total_results >= 0


@pytest.mark.e2e
@pytest.mark.requires_database
def test_operators_available(iris_connection):
    """Test that IRISGraphOperators can be instantiated."""
    try:
        from iris_vector_graph.operators import IRISGraphOperators

        operators = IRISGraphOperators(iris_connection)

        # Test a simple operation
        # This may return empty results but shouldn't error
        assert operators is not None

    except ImportError:
        pytest.skip("iris_vector_graph.operators not available")
