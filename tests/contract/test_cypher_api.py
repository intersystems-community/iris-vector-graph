"""
Contract tests for POST /api/cypher endpoint - Success Cases

Tests validate CypherQueryResponse schema per contracts/cypher_api.yaml.
These tests MUST FAIL until the endpoint is implemented (TDD gate).
"""

import pytest
from fastapi.testclient import TestClient


# TDD Gate: Tests will initially fail until Cypher router is implemented
try:
    from api.main import app
    APP_EXISTS = True
except (ImportError, AttributeError):
    APP_EXISTS = False
    app = None


@pytest.mark.requires_database
@pytest.mark.integration
@pytest.mark.skipif(not APP_EXISTS, reason="FastAPI app not implemented yet - TDD gate")
class TestCypherAPIContract:
    """Contract tests for Cypher API endpoint - success cases"""

    def test_cypher_endpoint_simple_match_query(self):
        """
        Test simple MATCH query returns valid CypherQueryResponse.

        Contract Reference: cypher_api.yaml lines 60-78
        """
        client = TestClient(app)

        # Create test protein first
        mutation = """
            mutation {
                createProtein(input: {id: "PROTEIN:CYPHER_TEST", name: "Cypher Test Protein"}) {
                    id
                }
            }
        """
        client.post("/graphql", json={"query": mutation})

        # Execute Cypher query
        response = client.post(
            "/api/cypher",
            json={
                "query": "MATCH (p:Protein {id: 'PROTEIN:CYPHER_TEST'}) RETURN p.name, p.id"
            }
        )

        assert response.status_code == 200
        data = response.json()

        # Validate CypherQueryResponse schema
        assert "columns" in data
        assert "rows" in data
        assert "rowCount" in data
        assert "executionTimeMs" in data
        assert "translationTimeMs" in data
        assert "traceId" in data

        # Validate data types
        assert isinstance(data["columns"], list)
        assert all(isinstance(col, str) for col in data["columns"])
        assert isinstance(data["rows"], list)
        assert isinstance(data["rowCount"], int)
        assert isinstance(data["executionTimeMs"], (int, float))
        assert isinstance(data["translationTimeMs"], (int, float))
        assert isinstance(data["traceId"], str)

        # Validate results
        assert data["columns"] == ["p.name", "p.id"]
        assert data["rowCount"] == 1
        assert data["rows"][0][0] == "Cypher Test Protein"
        assert data["rows"][0][1] == "PROTEIN:CYPHER_TEST"

        # Cleanup
        delete_mutation = """
            mutation {
                deleteProtein(id: "PROTEIN:CYPHER_TEST")
            }
        """
        client.post("/graphql", json={"query": delete_mutation})

    def test_cypher_endpoint_parameterized_query(self):
        """
        Test parameterized query with $parameters.

        Contract Reference: cypher_api.yaml lines 48-53
        """
        client = TestClient(app)

        # Create test protein
        mutation = """
            mutation {
                createProtein(input: {id: "PROTEIN:PARAM_TEST", name: "Param Test"}) {
                    id
                }
            }
        """
        client.post("/graphql", json={"query": mutation})

        # Execute parameterized Cypher query
        response = client.post(
            "/api/cypher",
            json={
                "query": "MATCH (p:Protein) WHERE p.id = $proteinId RETURN p.name",
                "parameters": {
                    "proteinId": "PROTEIN:PARAM_TEST"
                }
            }
        )

        assert response.status_code == 200
        data = response.json()

        # Validate schema
        assert "columns" in data
        assert "rows" in data
        assert "rowCount" in data

        # Validate results
        assert data["columns"] == ["p.name"]
        assert data["rowCount"] == 1
        assert data["rows"][0][0] == "Param Test"

        # Cleanup
        delete_mutation = """
            mutation {
                deleteProtein(id: "PROTEIN:PARAM_TEST")
            }
        """
        client.post("/graphql", json={"query": delete_mutation})

    def test_cypher_endpoint_query_metadata_optional(self):
        """
        Test queryMetadata field is optional in response.

        Contract Reference: cypher_api.yaml lines 227-244
        """
        client = TestClient(app)

        response = client.post(
            "/api/cypher",
            json={
                "query": "MATCH (p:Protein) RETURN p.name LIMIT 1"
            }
        )

        assert response.status_code == 200
        data = response.json()

        # queryMetadata is optional - if present, validate structure
        if "queryMetadata" in data:
            metadata = data["queryMetadata"]
            assert isinstance(metadata, dict)

            # Optional fields in metadata
            if "sqlQuery" in metadata:
                assert isinstance(metadata["sqlQuery"], str)
            if "indexesUsed" in metadata:
                assert isinstance(metadata["indexesUsed"], list)
            if "optimizationsApplied" in metadata:
                assert isinstance(metadata["optimizationsApplied"], list)

    def test_cypher_endpoint_custom_timeout(self):
        """
        Test custom timeout parameter is accepted.

        Contract Reference: cypher_api.yaml lines 169-175
        """
        client = TestClient(app)

        response = client.post(
            "/api/cypher",
            json={
                "query": "MATCH (p:Protein) RETURN p.name LIMIT 1",
                "timeout": 60
            }
        )

        # Should succeed (query simple enough to execute within timeout)
        assert response.status_code == 200
        data = response.json()
        assert "columns" in data
        assert "rows" in data

    def test_cypher_endpoint_optimization_flags(self):
        """
        Test enableOptimization and enableCache parameters.

        Contract Reference: cypher_api.yaml lines 176-183
        """
        client = TestClient(app)

        response = client.post(
            "/api/cypher",
            json={
                "query": "MATCH (p:Protein) RETURN p.name LIMIT 1",
                "enableOptimization": True,
                "enableCache": True
            }
        )

        assert response.status_code == 200
        data = response.json()

        # If optimization enabled, may have metadata
        if "queryMetadata" in data and "optimizationsApplied" in data["queryMetadata"]:
            assert isinstance(data["queryMetadata"]["optimizationsApplied"], list)
