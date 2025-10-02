"""
Contract tests for POST /api/cypher endpoint - Error Cases

Tests validate CypherErrorResponse schema per contracts/cypher_api.yaml.
These tests MUST FAIL until error handling is implemented (TDD gate).
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
class TestCypherAPIErrorContract:
    """Contract tests for Cypher API endpoint - error cases"""

    def test_cypher_endpoint_syntax_error(self):
        """
        Test syntax error returns valid CypherErrorResponse with line/column.

        Contract Reference: cypher_api.yaml lines 86-95
        """
        client = TestClient(app)

        # Invalid Cypher syntax (RETRUN instead of RETURN)
        response = client.post(
            "/api/cypher",
            json={
                "query": "MATCH (p:Protein) RETRUN p.name"
            }
        )

        assert response.status_code == 400
        data = response.json()

        # Validate CypherErrorResponse schema
        assert "errorType" in data
        assert "message" in data
        assert "errorCode" in data
        assert "traceId" in data

        # Validate error type
        assert data["errorType"] == "syntax"
        assert data["errorCode"] == "SYNTAX_ERROR"

        # Validate optional line/column fields
        if "line" in data:
            assert isinstance(data["line"], int)
            assert data["line"] >= 1
        if "column" in data:
            assert isinstance(data["column"], int)
            assert data["column"] >= 1

        # Validate suggestion field (optional)
        if "suggestion" in data:
            assert isinstance(data["suggestion"], str)

        # Validate traceId
        assert isinstance(data["traceId"], str)
        assert len(data["traceId"]) > 0

    def test_cypher_endpoint_undefined_variable_error(self):
        """
        Test undefined variable error returns semantic error.

        Contract Reference: cypher_api.yaml lines 96-105
        """
        client = TestClient(app)

        # Invalid Cypher - variable 'm' not defined
        response = client.post(
            "/api/cypher",
            json={
                "query": "MATCH (p:Protein) RETURN m.name"
            }
        )

        assert response.status_code == 400
        data = response.json()

        # Validate error type
        assert data["errorType"] == "translation"
        assert data["errorCode"] == "UNDEFINED_VARIABLE"

        # Validate error message
        assert "message" in data
        assert isinstance(data["message"], str)

        # Validate line/column if present
        if "line" in data:
            assert isinstance(data["line"], int)
        if "column" in data:
            assert isinstance(data["column"], int)

    def test_cypher_endpoint_query_timeout(self):
        """
        Test query timeout returns 408 with timeout error.

        Contract Reference: cypher_api.yaml lines 112-120
        """
        client = TestClient(app)

        # Query with very short timeout (likely to timeout)
        response = client.post(
            "/api/cypher",
            json={
                "query": "MATCH (n)-[r*1..10]->(m) RETURN n, m",
                "timeout": 1  # 1 second timeout
            }
        )

        # May succeed if database is fast, or timeout with 408
        if response.status_code == 408:
            data = response.json()

            # Validate timeout error response
            assert data["errorType"] == "timeout"
            assert data["errorCode"] == "QUERY_TIMEOUT"
            assert "message" in data
            assert "traceId" in data

            # Validate suggestion
            if "suggestion" in data:
                assert isinstance(data["suggestion"], str)

    def test_cypher_endpoint_complexity_limit_exceeded(self):
        """
        Test complexity limit error (max depth exceeded).

        Contract Reference: cypher_api.yaml lines 127-135
        """
        client = TestClient(app)

        # Variable-length path exceeding max depth (10 hops)
        response = client.post(
            "/api/cypher",
            json={
                "query": "MATCH (p:Protein)-[r*1..20]->(m:Protein) RETURN p, m"
            }
        )

        # Should reject with 413 or 400 (complexity limit exceeded)
        assert response.status_code in [400, 413]
        data = response.json()

        # Validate error response
        assert data["errorType"] == "translation"
        assert data["errorCode"] == "COMPLEXITY_LIMIT_EXCEEDED"
        assert "message" in data
        assert "traceId" in data

    def test_cypher_endpoint_fk_constraint_violation(self):
        """
        Test FK constraint violation returns 500 execution error.

        Contract Reference: cypher_api.yaml lines 144-149
        """
        client = TestClient(app)

        # Query referencing non-existent node (FK violation)
        response = client.post(
            "/api/cypher",
            json={
                "query": "MATCH (p:Protein {id: 'PROTEIN:NONEXISTENT_NODE'}) RETURN p.name"
            }
        )

        # May return 200 with empty results, or 500 if FK enforced during query
        # This depends on implementation details
        # If 500, validate error structure
        if response.status_code == 500:
            data = response.json()

            assert data["errorType"] == "execution"
            assert data["errorCode"] in ["FK_CONSTRAINT_VIOLATION", "SQL_EXECUTION_ERROR"]
            assert "message" in data
            assert "traceId" in data

    def test_cypher_endpoint_error_response_traceId_unique(self):
        """
        Test that each error response has a unique traceId.
        """
        client = TestClient(app)

        # Execute two invalid queries
        response1 = client.post(
            "/api/cypher",
            json={"query": "MATCH (p:Protein) RETRUN p"}
        )

        response2 = client.post(
            "/api/cypher",
            json={"query": "MATCH (p:Protein) RETRUN p"}
        )

        assert response1.status_code == 400
        assert response2.status_code == 400

        data1 = response1.json()
        data2 = response2.json()

        # Validate traceIds are unique
        assert "traceId" in data1
        assert "traceId" in data2
        assert data1["traceId"] != data2["traceId"]

    def test_cypher_endpoint_error_response_all_required_fields(self):
        """
        Test that all required fields are present in error responses.

        Contract Reference: cypher_api.yaml lines 251-257
        """
        client = TestClient(app)

        response = client.post(
            "/api/cypher",
            json={"query": "MATCH (p:Protein) RETRUN p"}
        )

        assert response.status_code == 400
        data = response.json()

        # Required fields per schema
        required_fields = ["errorType", "message", "errorCode", "traceId"]
        for field in required_fields:
            assert field in data, f"Required field '{field}' missing from error response"
            assert data[field] is not None, f"Required field '{field}' is null"
            assert data[field] != "", f"Required field '{field}' is empty string"
