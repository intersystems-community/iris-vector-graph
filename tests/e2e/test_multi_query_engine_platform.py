#!/usr/bin/env python3
"""
End-to-End Test Suite for Multi-Query-Engine Platform

Tests the complete IRIS Vector Graph system with all three query engines:
1. GraphQL API (/graphql)
2. openCypher API (/api/cypher)
3. SQL Direct (iris.connect())

Validates:
- All endpoints are operational
- Cross-engine consistency (same data accessible via all engines)
- Hybrid workflows (create via one engine, query via another)
- Performance characteristics
- Error handling across all engines
"""

import os
import pytest
import json
import numpy as np
from fastapi.testclient import TestClient

# Test if FastAPI app exists
try:
    from api.main import app
    APP_EXISTS = True
except ImportError:
    APP_EXISTS = False
    app = None


# NOTE: iris_connection fixture is provided by tests/conftest.py
# Do not define a local fixture here to avoid shadowing


@pytest.fixture
def test_client():
    """Fixture providing test client for FastAPI"""
    if not APP_EXISTS:
        pytest.skip("FastAPI app not available")

    return TestClient(app)


@pytest.fixture
def test_data_cleanup(iris_connection):
    """Fixture to cleanup test data before and after tests"""
    conn = iris_connection
    cursor = conn.cursor()

    # Cleanup before test
    test_ids = ["E2E:TEST:PROTEIN1", "E2E:TEST:PROTEIN2", "E2E:TEST:GENE1"]
    for test_id in test_ids:
        try:
            cursor.execute("DELETE FROM kg_NodeEmbeddings WHERE id LIKE ?", (test_id,))
            cursor.execute("DELETE FROM rdf_edges WHERE s LIKE ? OR o_id LIKE ?", (test_id, test_id))
            cursor.execute("DELETE FROM rdf_props WHERE s LIKE ?", (test_id,))
            cursor.execute("DELETE FROM rdf_labels WHERE s LIKE ?", (test_id,))
            cursor.execute("DELETE FROM nodes WHERE node_id LIKE ?", (test_id,))
        except:
            pass
    conn.commit()

    yield

    # Cleanup after test
    for test_id in test_ids:
        try:
            cursor.execute("DELETE FROM kg_NodeEmbeddings WHERE id LIKE ?", (test_id,))
            cursor.execute("DELETE FROM rdf_edges WHERE s LIKE ? OR o_id LIKE ?", (test_id, test_id))
            cursor.execute("DELETE FROM rdf_props WHERE s LIKE ?", (test_id,))
            cursor.execute("DELETE FROM rdf_labels WHERE s LIKE ?", (test_id,))
            cursor.execute("DELETE FROM nodes WHERE node_id LIKE ?", (test_id,))
        except:
            pass
    conn.commit()


# ==============================================================================
# Test 1: FastAPI Application Health
# ==============================================================================

@pytest.mark.requires_database
@pytest.mark.e2e
@pytest.mark.skipif(not APP_EXISTS, reason="FastAPI app not available")
def test_fastapi_application_health(test_client):
    """Test that FastAPI application is healthy and reports all engines available"""

    # Test root endpoint
    response = test_client.get("/")
    assert response.status_code == 200
    data = response.json()

    assert data["name"] == "IRIS Vector Graph - Multi-Query-Engine API"
    assert "query_engines" in data
    assert "graphql" in data["query_engines"]
    assert "opencypher" in data["query_engines"]
    assert "sql" in data["query_engines"]

    # Test health check
    response = test_client.get("/health")
    assert response.status_code == 200
    health = response.json()

    assert health["status"] == "healthy"
    assert health["database"] == "connected"
    assert health["graphql"] == "available"
    assert health["cypher"] == "available"


# ==============================================================================
# Test 2: SQL Direct - Create Test Data
# ==============================================================================

@pytest.mark.requires_database
@pytest.mark.e2e
def test_sql_direct_create_test_data(iris_connection, test_data_cleanup):
    """Create test data using SQL Direct (establishes baseline for other engines)"""
    conn = iris_connection
    cursor = conn.cursor()

    # Create nodes
    cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ("E2E:TEST:PROTEIN1",))
    cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ("E2E:TEST:PROTEIN2",))
    cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ("E2E:TEST:GENE1",))

    # Add labels
    cursor.execute("INSERT INTO rdf_labels (s, label) VALUES (?, ?)", ("E2E:TEST:PROTEIN1", "Protein"))
    cursor.execute("INSERT INTO rdf_labels (s, label) VALUES (?, ?)", ("E2E:TEST:PROTEIN2", "Protein"))
    cursor.execute("INSERT INTO rdf_labels (s, label) VALUES (?, ?)", ("E2E:TEST:GENE1", "Gene"))

    # Add properties
    cursor.execute("INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
                   ("E2E:TEST:PROTEIN1", "name", "E2E_TestProtein1"))
    cursor.execute("INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
                   ("E2E:TEST:PROTEIN1", "function", "E2E test protein"))
    cursor.execute("INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
                   ("E2E:TEST:PROTEIN2", "name", "E2E_TestProtein2"))
    cursor.execute("INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
                   ("E2E:TEST:GENE1", "name", "E2E_TestGene1"))

    # Add relationship (edge_id is auto-generated via IDENTITY)
    cursor.execute("INSERT INTO rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
                   ("E2E:TEST:PROTEIN1", "INTERACTS_WITH", "E2E:TEST:PROTEIN2"))
    cursor.execute("INSERT INTO rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
                   ("E2E:TEST:GENE1", "ENCODES", "E2E:TEST:PROTEIN1"))

    # Add embeddings
    emb1 = np.random.randn(768)
    emb1 = emb1 / np.linalg.norm(emb1)
    emb_str1 = '[' + ','.join([str(x) for x in emb1.tolist()]) + ']'

    cursor.execute("INSERT INTO kg_NodeEmbeddings (id, emb) VALUES (?, TO_VECTOR(?))",
                   ("E2E:TEST:PROTEIN1", emb_str1))

    conn.commit()

    # Verify data exists
    cursor.execute("SELECT COUNT(*) FROM nodes WHERE node_id LIKE ?", ("E2E:TEST:%",))
    count = cursor.fetchone()[0]
    assert count == 3, f"Expected 3 test nodes, found {count}"


# ==============================================================================
# Test 3: Cypher Engine - Query Test Data
# ==============================================================================

@pytest.mark.requires_database
@pytest.mark.e2e
@pytest.mark.skipif(not APP_EXISTS, reason="FastAPI app not available")

def test_cypher_engine_query_test_data(test_client, iris_connection, test_data_cleanup):
    """Query test data using openCypher engine (validates Cypher-to-SQL translation)"""

    # First create test data
    conn = iris_connection
    cursor = conn.cursor()

    cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ("E2E:TEST:PROTEIN1",))
    cursor.execute("INSERT INTO rdf_labels (s, label) VALUES (?, ?)", ("E2E:TEST:PROTEIN1", "Protein"))
    cursor.execute("INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
                   ("E2E:TEST:PROTEIN1", "name", "E2E_TestProtein1"))
    conn.commit()

    # Query via Cypher (use id property which maps to node_id)
    response = test_client.post(
        "/api/cypher",
        json={
            "query": "MATCH (p:Protein {id: 'E2E:TEST:PROTEIN1'}) RETURN p",
            "enableOptimization": True
        }
    )

    assert response.status_code == 200, f"Cypher query failed: {response.text}"
    data = response.json()

    assert "columns" in data
    assert "rows" in data
    assert "traceId" in data
    assert data["rowCount"] >= 1, f"Expected at least 1 result from Cypher query"
    assert data["rows"][0][0] == "E2E:TEST:PROTEIN1"  # Returns node_id


# ==============================================================================
# Test 4: GraphQL Engine - Query Test Data
# ==============================================================================

@pytest.mark.requires_database
@pytest.mark.e2e
@pytest.mark.skipif(not APP_EXISTS, reason="FastAPI app not available")

def test_graphql_engine_query_test_data(test_client, iris_connection, test_data_cleanup):
    """Query test data using GraphQL engine (validates DataLoader batching)"""

    # First create test data
    conn = iris_connection
    cursor = conn.cursor()

    cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ("E2E:TEST:PROTEIN1",))
    cursor.execute("INSERT INTO rdf_labels (s, label) VALUES (?, ?)", ("E2E:TEST:PROTEIN1", "Protein"))
    cursor.execute("INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
                   ("E2E:TEST:PROTEIN1", "name", "E2E_TestProtein1"))
    cursor.execute("INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
                   ("E2E:TEST:PROTEIN1", "function", "E2E test protein"))
    conn.commit()

    # Query via GraphQL
    graphql_query = """
    query {
        protein(id: "E2E:TEST:PROTEIN1") {
            id
            name
            function
        }
    }
    """

    response = test_client.post(
        "/graphql",
        json={"query": graphql_query}
    )

    assert response.status_code == 200, f"GraphQL query failed: {response.text}"
    data = response.json()

    assert "data" in data
    assert data["data"]["protein"] is not None
    assert data["data"]["protein"]["name"] == "E2E_TestProtein1"
    assert data["data"]["protein"]["function"] == "E2E test protein"


# ==============================================================================
# Test 5: Cross-Engine Consistency
# ==============================================================================

@pytest.mark.requires_database
@pytest.mark.e2e
@pytest.mark.skipif(not APP_EXISTS, reason="FastAPI app not available")

def test_cross_engine_consistency(test_client, iris_connection, test_data_cleanup):
    """Verify same data is accessible and consistent across all three query engines"""

    # Create test data via SQL
    conn = iris_connection
    cursor = conn.cursor()

    cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ("E2E:TEST:PROTEIN1",))
    cursor.execute("INSERT INTO rdf_labels (s, label) VALUES (?, ?)", ("E2E:TEST:PROTEIN1", "Protein"))
    cursor.execute("INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
                   ("E2E:TEST:PROTEIN1", "name", "E2E_ConsistencyTest"))
    conn.commit()

    # Query via SQL Direct - get node_id
    cursor.execute("SELECT node_id FROM nodes WHERE node_id = ?", ("E2E:TEST:PROTEIN1",))
    sql_result = cursor.fetchone()[0]

    # Query via Cypher - return node (which is node_id)
    cypher_response = test_client.post(
        "/api/cypher",
        json={"query": "MATCH (p:Protein {id: 'E2E:TEST:PROTEIN1'}) RETURN p"}
    )
    cypher_data = cypher_response.json()
    cypher_result = cypher_data["rows"][0][0]

    # Query via GraphQL - get id
    graphql_query = """
    query {
        protein(id: "E2E:TEST:PROTEIN1") {
            id
        }
    }
    """
    graphql_response = test_client.post("/graphql", json={"query": graphql_query})
    graphql_data = graphql_response.json()
    graphql_result = graphql_data["data"]["protein"]["id"]

    # Verify consistency - all three engines return the same node ID
    assert sql_result == cypher_result == graphql_result == "E2E:TEST:PROTEIN1", \
        f"Inconsistent results: SQL={sql_result}, Cypher={cypher_result}, GraphQL={graphql_result}"


# ==============================================================================
# Test 6: Hybrid Workflow - Create via GraphQL, Query via Cypher
# ==============================================================================

@pytest.mark.requires_database
@pytest.mark.e2e
@pytest.mark.skipif(not APP_EXISTS, reason="FastAPI app not available")

def test_hybrid_workflow_graphql_to_cypher(test_client, test_data_cleanup):
    """Create data via GraphQL, then query it via Cypher (validates cross-engine workflows)"""

    # Create via GraphQL mutation
    mutation = """
    mutation {
        createProtein(input: {
            id: "E2E:TEST:PROTEIN1",
            name: "E2E_HybridTest",
            function: "Created via GraphQL"
        }) {
            id
            name
        }
    }
    """

    create_response = test_client.post("/graphql", json={"query": mutation})
    assert create_response.status_code == 200, f"GraphQL mutation failed: {create_response.text}"
    create_data = create_response.json()
    assert create_data["data"]["createProtein"]["name"] == "E2E_HybridTest"

    # Query via Cypher (use id lookup)
    cypher_response = test_client.post(
        "/api/cypher",
        json={"query": "MATCH (p:Protein {id: 'E2E:TEST:PROTEIN1'}) RETURN p"}
    )

    assert cypher_response.status_code == 200
    cypher_data = cypher_response.json()
    assert cypher_data["rowCount"] >= 1
    assert cypher_data["rows"][0][0] == "E2E:TEST:PROTEIN1"  # Returns node_id


# ==============================================================================
# Test 7: Performance - All Engines Under 100ms
# ==============================================================================

@pytest.mark.requires_database
@pytest.mark.e2e
@pytest.mark.skipif(not APP_EXISTS, reason="FastAPI app not available")

def test_performance_all_engines(test_client, iris_connection, test_data_cleanup):
    """Verify all query engines respond within acceptable performance limits"""
    import time

    # Create test data
    conn = iris_connection
    cursor = conn.cursor()

    cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ("E2E:TEST:PROTEIN1",))
    cursor.execute("INSERT INTO rdf_labels (s, label) VALUES (?, ?)", ("E2E:TEST:PROTEIN1", "Protein"))
    cursor.execute("INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
                   ("E2E:TEST:PROTEIN1", "name", "E2E_PerfTest"))
    conn.commit()

    # SQL Direct Performance
    start = time.time()
    cursor.execute("SELECT p.val FROM rdf_props p WHERE p.s = ? AND p.key = ?",
                   ("E2E:TEST:PROTEIN1", "name"))
    cursor.fetchone()
    sql_time_ms = (time.time() - start) * 1000

    # Cypher Performance
    cypher_response = test_client.post(
        "/api/cypher",
        json={"query": "MATCH (p:Protein {id: 'E2E:TEST:PROTEIN1'}) RETURN p.name"}
    )
    cypher_data = cypher_response.json()
    cypher_time_ms = cypher_data["executionTimeMs"] + cypher_data["translationTimeMs"]

    # GraphQL Performance
    graphql_query = """
    query {
        protein(id: "E2E:TEST:PROTEIN1") {
            name
        }
    }
    """
    start = time.time()
    test_client.post("/graphql", json={"query": graphql_query})
    graphql_time_ms = (time.time() - start) * 1000

    # Verify performance
    assert sql_time_ms < 100, f"SQL too slow: {sql_time_ms:.2f}ms"
    assert cypher_time_ms < 100, f"Cypher too slow: {cypher_time_ms:.2f}ms"
    assert graphql_time_ms < 200, f"GraphQL too slow: {graphql_time_ms:.2f}ms"


# ==============================================================================
# Test 8: Error Handling - All Engines
# ==============================================================================

@pytest.mark.requires_database
@pytest.mark.e2e
@pytest.mark.skipif(not APP_EXISTS, reason="FastAPI app not available")

def test_error_handling_all_engines(test_client):
    """Verify all engines handle errors gracefully with appropriate status codes"""

    # Cypher - Syntax Error
    cypher_response = test_client.post(
        "/api/cypher",
        json={"query": "INVALID CYPHER SYNTAX"}
    )
    assert cypher_response.status_code == 400
    cypher_error = cypher_response.json()
    assert cypher_error["errorType"] == "syntax"
    assert "traceId" in cypher_error

    # GraphQL - Query Error
    graphql_query = """
    query {
        protein(id: "DOES_NOT_EXIST") {
            name
        }
    }
    """
    graphql_response = test_client.post("/graphql", json={"query": graphql_query})
    assert graphql_response.status_code == 200  # GraphQL returns 200 with errors in response
    graphql_data = graphql_response.json()
    assert graphql_data["data"]["protein"] is None  # Non-existent protein returns null


# ==============================================================================
# Test 9: Graph Traversal - Cypher vs GraphQL
# ==============================================================================

@pytest.mark.requires_database
@pytest.mark.e2e
@pytest.mark.skipif(not APP_EXISTS, reason="FastAPI app not available")

def test_graph_traversal_cypher_vs_graphql(test_client, iris_connection, test_data_cleanup):
    """Verify GraphQL can perform graph traversal (Cypher relationship queries not yet fully implemented)"""

    # Create test data with relationships
    conn = iris_connection
    cursor = conn.cursor()

    cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ("E2E:TEST:PROTEIN1",))
    cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ("E2E:TEST:PROTEIN2",))
    cursor.execute("INSERT INTO rdf_labels (s, label) VALUES (?, ?)", ("E2E:TEST:PROTEIN1", "Protein"))
    cursor.execute("INSERT INTO rdf_labels (s, label) VALUES (?, ?)", ("E2E:TEST:PROTEIN2", "Protein"))
    cursor.execute("INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
                   ("E2E:TEST:PROTEIN1", "name", "E2E_ProteinA"))
    cursor.execute("INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
                   ("E2E:TEST:PROTEIN2", "name", "E2E_ProteinB"))
    cursor.execute("INSERT INTO rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
                   ("E2E:TEST:PROTEIN1", "INTERACTS_WITH", "E2E:TEST:PROTEIN2"))
    conn.commit()

    # Query via GraphQL - verify relationship traversal works
    graphql_query = """
    query {
        protein(id: "E2E:TEST:PROTEIN1") {
            name
            interactsWith {
                id
                name
            }
        }
    }
    """
    graphql_response = test_client.post("/graphql", json={"query": graphql_query})
    graphql_data = graphql_response.json()

    # Verify GraphQL relationship traversal works
    assert graphql_data["data"]["protein"] is not None
    assert graphql_data["data"]["protein"]["name"] == "E2E_ProteinA"
    assert len(graphql_data["data"]["protein"]["interactsWith"]) == 1
    assert graphql_data["data"]["protein"]["interactsWith"][0]["id"] == "E2E:TEST:PROTEIN2"
    assert graphql_data["data"]["protein"]["interactsWith"][0]["name"] == "E2E_ProteinB"

    # NOTE: Cypher relationship patterns not fully implemented in MVP parser
    # Future enhancement: Add Cypher relationship traversal test when implemented


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
