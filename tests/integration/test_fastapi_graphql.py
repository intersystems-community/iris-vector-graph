"""
Integration tests for FastAPI + GraphQL endpoint.

Tests /graphql endpoint, health check, and GraphQL queries via HTTP.
"""

import pytest
from fastapi.testclient import TestClient


# TDD Gate: Tests will initially fail until FastAPI app with biomedical schema is implemented
try:
    from api.main import create_app
    from api.gql.schema import schema as _bio_schema
    _mutation_type = _bio_schema.graphql_schema.mutation_type
    APP_EXISTS = _mutation_type is not None and any(
        "protein" in f.lower() for f in (_mutation_type.fields.keys() if _mutation_type else [])
    )
except (ImportError, AttributeError, Exception):
    APP_EXISTS = False
    create_app = None


@pytest.mark.requires_database
@pytest.mark.integration
@pytest.mark.skipif(not APP_EXISTS, reason="FastAPI app not implemented yet - TDD gate")
class TestFastAPIGraphQL:
    """Integration tests for FastAPI + GraphQL endpoint"""

    @pytest.fixture
    def test_app(self, engine):
        """Create FastAPI app using the test engine (connects to test container)."""
        return create_app(engine=engine)

    def test_root_endpoint(self, test_app):
        """Test root endpoint returns API information"""
        client = TestClient(test_app)
        response = client.get("/")

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "IRIS Vector Graph API"
        assert data["graphql_endpoint"] == "/graphql"

    def test_health_check_endpoint(self, test_app):
        """Test health check endpoint verifies database connection"""
        client = TestClient(test_app)
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["database"] == "connected"
        assert data["graphql"] == "available"

    def test_graphql_endpoint_query(self, test_app, iris_connection):
        """Test GraphQL endpoint executes simple query"""
        client = TestClient(test_app)

        # Pre-cleanup
        client.post("/graphql", json={"query": "mutation { deleteProtein(id: \"PROTEIN:FASTAPI_TEST\") }"})

        mutation = """
            mutation CreateProtein($input: CreateProteinInput!) {
                createProtein(input: $input) {
                    id
                    name
                }
            }
        """
        query = """
            query GetProtein($id: ID!) {
                protein(id: $id) {
                    id
                    name
                }
            }
        """

        create_response = client.post(
            "/graphql",
            json={
                "query": mutation,
                "variables": {"input": {"id": "PROTEIN:FASTAPI_TEST", "name": "FastAPI Test Protein"}},
            },
        )

        assert create_response.status_code == 200
        create_data = create_response.json()
        assert create_data.get("errors") is None, f"Create errors: {create_data.get('errors')}"
        assert create_data["data"]["createProtein"]["name"] == "FastAPI Test Protein"

        query_response = client.post(
            "/graphql",
            json={"query": query, "variables": {"id": "PROTEIN:FASTAPI_TEST"}},
        )

        assert query_response.status_code == 200
        query_data = query_response.json()
        assert query_data.get("errors") is None
        assert query_data["data"]["protein"]["name"] == "FastAPI Test Protein"

        # Cleanup
        client.post(
            "/graphql",
            json={"query": "mutation { deleteProtein(id: \"PROTEIN:FASTAPI_TEST\") }"},
        )

    def test_graphql_endpoint_mutation(self, test_app, iris_connection):
        """Test GraphQL endpoint executes mutations"""
        client = TestClient(test_app)

        # Pre-cleanup
        client.post("/graphql", json={"query": "mutation { deleteProtein(id: \"PROTEIN:MUTATION_TEST\") }"})

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
                        "function": "Test function",
                    }
                },
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data.get("errors") is None, f"Mutation errors: {data.get('errors')}"
        assert data["data"]["createProtein"]["function"] == "Test function"

        # Cleanup
        client.post(
            "/graphql",
            json={"query": "mutation { deleteProtein(id: \"PROTEIN:MUTATION_TEST\") }"},
        )

    def test_graphql_endpoint_error_handling(self, test_app):
        """Test GraphQL endpoint returns errors for invalid queries"""
        client = TestClient(test_app)

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
            json={"query": query, "variables": {"id": "PROTEIN:NONEXISTENT"}},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["data"]["protein"] is None

    def test_graphql_endpoint_syntax_error(self, test_app):
        """Test GraphQL endpoint handles syntax errors"""
        client = TestClient(test_app)

        response = client.post("/graphql", json={"query": "query { invalid syntax }"})

        assert response.status_code == 200
        data = response.json()
        assert "errors" in data
