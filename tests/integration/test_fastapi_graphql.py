"""
Integration tests for FastAPI + GraphQL endpoint.

Tests /graphql endpoint, health check, and GraphQL queries via HTTP.
"""

import pytest
from fastapi.testclient import TestClient


# TDD Gate: Tests will initially fail until FastAPI app is implemented
try:
    from api.main import app
    APP_EXISTS = True
except ImportError:
    APP_EXISTS = False
    app = None


@pytest.mark.requires_database
@pytest.mark.integration
@pytest.mark.skipif(not APP_EXISTS, reason="FastAPI app not implemented yet - TDD gate")
class TestFastAPIGraphQL:
    """Integration tests for FastAPI + GraphQL endpoint"""

    def test_root_endpoint(self):
        """Test root endpoint returns API information"""
        client = TestClient(app)
        response = client.get("/")

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "IRIS Vector Graph API"
        assert data["graphql_endpoint"] == "/graphql"

    def test_health_check_endpoint(self):
        """Test health check endpoint verifies database connection"""
        client = TestClient(app)
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["database"] == "connected"
        assert data["graphql"] == "available"

    def test_graphql_endpoint_query(self):
        """Test GraphQL endpoint executes simple query"""
        client = TestClient(app)

        # GraphQL query
        query = """
            query GetProtein($id: ID!) {
                protein(id: $id) {
                    id
                    name
                }
            }
        """

        # Create test protein first
        mutation = """
            mutation CreateProtein($input: CreateProteinInput!) {
                createProtein(input: $input) {
                    id
                    name
                }
            }
        """

        # Create protein
        create_response = client.post(
            "/graphql",
            json={
                "query": mutation,
                "variables": {
                    "input": {
                        "id": "PROTEIN:FASTAPI_TEST",
                        "name": "FastAPI Test Protein"
                    }
                }
            }
        )

        assert create_response.status_code == 200
        create_data = create_response.json()
        assert create_data.get("errors") is None
        assert create_data["data"]["createProtein"]["name"] == "FastAPI Test Protein"

        # Query protein
        query_response = client.post(
            "/graphql",
            json={
                "query": query,
                "variables": {"id": "PROTEIN:FASTAPI_TEST"}
            }
        )

        assert query_response.status_code == 200
        query_data = query_response.json()
        assert query_data.get("errors") is None
        assert query_data["data"]["protein"]["name"] == "FastAPI Test Protein"

        # Cleanup: Delete protein
        delete_mutation = """
            mutation DeleteProtein($id: ID!) {
                deleteProtein(id: $id)
            }
        """

        client.post(
            "/graphql",
            json={
                "query": delete_mutation,
                "variables": {"id": "PROTEIN:FASTAPI_TEST"}
            }
        )

    def test_graphql_endpoint_mutation(self):
        """Test GraphQL endpoint executes mutations"""
        client = TestClient(app)

        mutation = """
            mutation CreateProtein($input: CreateProteinInput!) {
                createProtein(input: $input) {
                    id
                    name
                    function
                }
            }
        """

        response = client.post(
            "/graphql",
            json={
                "query": mutation,
                "variables": {
                    "input": {
                        "id": "PROTEIN:MUTATION_TEST",
                        "name": "Mutation Test Protein",
                        "function": "Test function"
                    }
                }
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert data.get("errors") is None
        assert data["data"]["createProtein"]["function"] == "Test function"

        # Cleanup
        delete_mutation = """
            mutation DeleteProtein($id: ID!) {
                deleteProtein(id: $id)
            }
        """

        client.post(
            "/graphql",
            json={
                "query": delete_mutation,
                "variables": {"id": "PROTEIN:MUTATION_TEST"}
            }
        )

    def test_graphql_endpoint_error_handling(self):
        """Test GraphQL endpoint returns errors for invalid queries"""
        client = TestClient(app)

        # Invalid query (nonexistent protein)
        query = """
            query GetProtein($id: ID!) {
                protein(id: $id) {
                    id
                    name
                }
            }
        """

        response = client.post(
            "/graphql",
            json={
                "query": query,
                "variables": {"id": "PROTEIN:NONEXISTENT"}
            }
        )

        assert response.status_code == 200
        data = response.json()
        # Should return null for non-existent protein (not an error)
        assert data["data"]["protein"] is None

    def test_graphql_endpoint_syntax_error(self):
        """Test GraphQL endpoint handles syntax errors"""
        client = TestClient(app)

        # Invalid GraphQL syntax
        response = client.post(
            "/graphql",
            json={"query": "query { invalid syntax }"}
        )

        assert response.status_code == 200
        data = response.json()
        # Should return GraphQL errors
        assert "errors" in data
