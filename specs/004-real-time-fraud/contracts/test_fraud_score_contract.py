"""
Contract Tests for Fraud Scoring API

These tests validate the POST /fraud/score endpoint contract against the OpenAPI specification.

**Test-First Requirement**: These tests MUST fail before implementation begins.
**Constitutional Compliance**: @pytest.mark.requires_database for live IRIS validation

Run with:
    pytest specs/004-real-time-fraud/contracts/test_fraud_score_contract.py -v
"""

import pytest
from fastapi.testclient import TestClient
import iris

# Import will fail initially (implementation doesn't exist yet)
try:
    from api.main import app
    from api.routers import fraud
except ImportError:
    app = None
    fraud = None


@pytest.fixture
def test_client():
    """FastAPI test client fixture"""
    if app is None:
        pytest.skip("FastAPI app not yet implemented")
    return TestClient(app)


@pytest.fixture
def iris_connection():
    """Live IRIS database connection for contract tests"""
    conn = iris.connect(
        host="localhost",
        port=1972,
        namespace="USER",
        username="_SYSTEM",
        password="SYS"
    )
    yield conn
    conn.close()


@pytest.fixture
def setup_test_entities(iris_connection):
    """
    Setup test entities in nodes table for contract testing.

    Creates:
    - acct:test_payer1 (payer account)
    - dev:test_device1 (device)
    - ip:192.168.1.100 (IP address)
    - mer:test_merchant1 (merchant)
    """
    cursor = iris_connection.cursor()

    # Insert test nodes
    test_nodes = [
        "acct:test_payer1",
        "dev:test_device1",
        "ip:192.168.1.100",
        "mer:test_merchant1",
    ]

    for node_id in test_nodes:
        cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", (node_id,))

    iris_connection.commit()

    yield test_nodes

    # Cleanup
    for node_id in test_nodes:
        cursor.execute("DELETE FROM nodes WHERE node_id = ?", (node_id,))
    iris_connection.commit()


# ==============================================================================
# Contract Tests (MUST FAIL initially - no implementation yet)
# ==============================================================================

@pytest.mark.requires_database
def test_fraud_score_mlp_mode_success(test_client, iris_connection, setup_test_entities):
    """
    FR-001: System MUST provide fraud scoring endpoint accepting entity IDs
    FR-002: System MUST return fraud probability (0-1) and min 3 reason codes

    Expected behavior:
    - POST /fraud/score with valid MLP request
    - Returns 200 OK with prob in [0.0, 1.0] and reasons array (len >= 3)
    - Response time <20ms p95 (validated in performance tests)
    """
    request_payload = {
        "mode": "MLP",
        "payer": "acct:test_payer1",
        "device": "dev:test_device1",
        "ip": "ip:192.168.1.100",
        "merchant": "mer:test_merchant1",
        "amount": 129.99,
        "country": "US"
    }

    response = test_client.post("/fraud/score", json=request_payload)

    # MUST return 200 OK
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

    # Validate response schema
    data = response.json()
    assert "prob" in data, "Response missing 'prob' field"
    assert "reasons" in data, "Response missing 'reasons' field"

    # FR-002: Fraud probability in [0.0, 1.0]
    assert isinstance(data["prob"], (int, float)), f"prob must be numeric, got {type(data['prob'])}"
    assert 0.0 <= data["prob"] <= 1.0, f"prob must be in [0.0, 1.0], got {data['prob']}"

    # FR-002: Minimum 3 reason codes
    assert isinstance(data["reasons"], list), "reasons must be array"
    assert len(data["reasons"]) >= 3, f"Expected >= 3 reasons, got {len(data['reasons'])}"

    # Validate reason code schema
    for reason in data["reasons"]:
        assert "kind" in reason, "Reason missing 'kind' field"
        assert reason["kind"] in ["feature", "vector"], f"Invalid kind: {reason['kind']}"
        assert "detail" in reason, "Reason missing 'detail' field"
        assert "weight" in reason, "Reason missing 'weight' field"
        assert isinstance(reason["weight"], (int, float)), "weight must be numeric"


@pytest.mark.requires_database
def test_fraud_score_ego_mode_success(test_client, iris_connection, setup_test_entities):
    """
    FR-003: System MUST support EGO mode (bounded ego-graph + GraphSAGE)

    Expected behavior:
    - POST /fraud/score with mode="EGO"
    - Returns 200 OK with valid fraud score
    - Response time <50ms p95 (validated in performance tests)
    """
    request_payload = {
        "mode": "EGO",
        "payer": "acct:test_payer1",
        "device": "dev:test_device1",
        "ip": "ip:192.168.1.100",
        "merchant": "mer:test_merchant1",
        "amount": 1500.00
    }

    response = test_client.post("/fraud/score", json=request_payload)

    # MUST return 200 OK
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

    # Validate response schema (same as MLP mode)
    data = response.json()
    assert "prob" in data
    assert "reasons" in data
    assert 0.0 <= data["prob"] <= 1.0
    assert len(data["reasons"]) >= 3


@pytest.mark.requires_database
def test_fraud_score_returns_min_3_reasons(test_client, iris_connection, setup_test_entities):
    """
    FR-002: System MUST return at least 3 reason codes
    FR-014: System MUST return reason codes in 95% of requests

    Expected behavior:
    - Every valid request returns >= 3 reason codes
    - Reason codes sorted by weight (descending)
    """
    request_payload = {
        "mode": "MLP",
        "payer": "acct:test_payer1",
        "device": "dev:test_device1",
        "ip": "ip:192.168.1.100",
        "merchant": "mer:test_merchant1",
        "amount": 50.00
    }

    response = test_client.post("/fraud/score", json=request_payload)
    assert response.status_code == 200

    data = response.json()
    reasons = data["reasons"]

    # FR-002: Minimum 3 reasons
    assert len(reasons) >= 3, f"Expected >= 3 reasons, got {len(reasons)}"

    # Reasons should be sorted by weight (descending)
    weights = [r["weight"] for r in reasons]
    assert weights == sorted(weights, reverse=True), "Reasons not sorted by weight desc"

    # FR-011: Feature reasons show concrete values
    feature_reasons = [r for r in reasons if r["kind"] == "feature"]
    if feature_reasons:
        example_reason = feature_reasons[0]
        assert "=" in example_reason["detail"], f"Feature reason missing '=' format: {example_reason['detail']}"

    # FR-012: Vector proximity reason exists (if embedding available)
    vector_reasons = [r for r in reasons if r["kind"] == "vector"]
    if vector_reasons:
        example_reason = vector_reasons[0]
        assert "sim_to_fraud" in example_reason["detail"] or "cold_start" in example_reason["detail"], \
            f"Vector reason invalid format: {example_reason['detail']}"


@pytest.mark.requires_database
def test_fraud_score_invalid_entity_id_400(test_client):
    """
    Input Validation: Invalid entity_id format should return 400 Bad Request

    Expected behavior:
    - POST with malformed entity_id (missing namespace, invalid chars)
    - Returns 400 with clear error message
    """
    request_payload = {
        "mode": "MLP",
        "payer": "invalid_id_no_namespace",  # Invalid format (missing namespace prefix)
        "device": "dev:test_device1",
        "ip": "ip:192.168.1.100",
        "merchant": "mer:test_merchant1"
    }

    response = test_client.post("/fraud/score", json=request_payload)

    # MUST return 400 Bad Request
    assert response.status_code == 400, f"Expected 400, got {response.status_code}: {response.text}"

    # Validate error response schema
    data = response.json()
    assert "error" in data, "Error response missing 'error' field"
    assert "detail" in data, "Error response missing 'detail' field"
    assert "trace_id" in data, "Error response missing 'trace_id' field"

    # Error detail should mention entity_id validation failure
    assert "entity_id" in data["detail"].lower() or "payer" in data["detail"].lower(), \
        f"Error detail should mention entity_id validation: {data['detail']}"


@pytest.mark.requires_database
def test_fraud_score_entity_not_found_404(test_client, iris_connection):
    """
    NFR-008: System MUST handle missing data gracefully
    Error Handling: Non-existent entity_id should return 404 Not Found

    Expected behavior:
    - POST with valid-format but non-existent entity_id
    - Returns 404 with clear error message (entity not found in nodes table)
    """
    request_payload = {
        "mode": "MLP",
        "payer": "acct:nonexistent_user999",  # Valid format, but doesn't exist
        "device": "dev:test_device1",
        "ip": "ip:192.168.1.100",
        "merchant": "mer:test_merchant1"
    }

    response = test_client.post("/fraud/score", json=request_payload)

    # MUST return 404 Not Found
    assert response.status_code == 404, f"Expected 404, got {response.status_code}: {response.text}"

    # Validate error response schema
    data = response.json()
    assert "error" in data
    assert "detail" in data
    assert "trace_id" in data

    # Error detail should mention entity not found
    assert "not found" in data["error"].lower() or "not exist" in data["detail"].lower(), \
        f"Error should indicate entity not found: {data['error']} - {data['detail']}"


@pytest.mark.requires_database
def test_fraud_score_invalid_mode_400(test_client, iris_connection, setup_test_entities):
    """
    Input Validation: Invalid mode parameter should return 400 Bad Request

    Expected behavior:
    - POST with mode not in ["MLP", "EGO"]
    - Returns 400 with clear error message
    """
    request_payload = {
        "mode": "INVALID_MODE",  # Not in allowed enum values
        "payer": "acct:test_payer1",
        "device": "dev:test_device1",
        "ip": "ip:192.168.1.100",
        "merchant": "mer:test_merchant1"
    }

    response = test_client.post("/fraud/score", json=request_payload)

    # MUST return 400 Bad Request (or 422 Unprocessable Entity for validation error)
    assert response.status_code in [400, 422], f"Expected 400/422, got {response.status_code}: {response.text}"

    # Validate error response mentions mode validation failure
    data = response.json()
    assert "mode" in str(data).lower() or "invalid" in str(data).lower(), \
        f"Error should mention invalid mode: {data}"


@pytest.mark.requires_database
def test_fraud_score_missing_required_field_400(test_client):
    """
    Input Validation: Missing required field should return 400/422

    Expected behavior:
    - POST without required field (e.g., payer)
    - Returns 400/422 with validation error
    """
    request_payload = {
        "mode": "MLP",
        # Missing "payer" (required field)
        "device": "dev:test_device1",
        "ip": "ip:192.168.1.100",
        "merchant": "mer:test_merchant1"
    }

    response = test_client.post("/fraud/score", json=request_payload)

    # MUST return 400 or 422
    assert response.status_code in [400, 422], f"Expected 400/422, got {response.status_code}: {response.text}"

    # Error should mention missing required field
    data = response.json()
    assert "payer" in str(data).lower() or "required" in str(data).lower(), \
        f"Error should mention missing required field: {data}"


# ==============================================================================
# Additional Edge Case Tests
# ==============================================================================

@pytest.mark.requires_database
def test_fraud_score_optional_fields_null(test_client, iris_connection, setup_test_entities):
    """
    NFR-008: System MUST handle missing data gracefully

    Expected behavior:
    - POST without optional fields (amount, country)
    - Returns 200 OK (optional fields have defaults or NULL handling)
    """
    request_payload = {
        "mode": "MLP",
        "payer": "acct:test_payer1",
        "device": "dev:test_device1",
        "ip": "ip:192.168.1.100",
        "merchant": "mer:test_merchant1"
        # No amount or country
    }

    response = test_client.post("/fraud/score", json=request_payload)

    # MUST still return 200 OK (optional fields)
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

    data = response.json()
    assert 0.0 <= data["prob"] <= 1.0
    assert len(data["reasons"]) >= 3


@pytest.mark.requires_database
def test_fraud_score_zero_amount(test_client, iris_connection, setup_test_entities):
    """
    Edge Case: Zero amount transaction

    Expected behavior:
    - POST with amount=0.0
    - Returns 200 OK (valid transaction, handled gracefully)
    """
    request_payload = {
        "mode": "MLP",
        "payer": "acct:test_payer1",
        "device": "dev:test_device1",
        "ip": "ip:192.168.1.100",
        "merchant": "mer:test_merchant1",
        "amount": 0.0
    }

    response = test_client.post("/fraud/score", json=request_payload)

    # MUST return 200 OK
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"


@pytest.mark.requires_database
def test_fraud_score_large_amount(test_client, iris_connection, setup_test_entities):
    """
    Edge Case: Very large transaction amount

    Expected behavior:
    - POST with amount=1000000.00 (1 million)
    - Returns 200 OK (numeric precision handled)
    """
    request_payload = {
        "mode": "MLP",
        "payer": "acct:test_payer1",
        "device": "dev:test_device1",
        "ip": "ip:192.168.1.100",
        "merchant": "mer:test_merchant1",
        "amount": 1000000.00
    }

    response = test_client.post("/fraud/score", json=request_payload)

    # MUST return 200 OK
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"


# ==============================================================================
# Test Execution Summary
# ==============================================================================

def test_contract_suite_summary():
    """
    Meta-test: Ensure all contract tests are marked correctly

    Contract test count: 11 tests total
    - 3 success path tests (MLP, EGO, min 3 reasons)
    - 4 error handling tests (400, 404, invalid mode, missing field)
    - 4 edge case tests (optional fields, zero amount, large amount)

    All tests marked @pytest.mark.requires_database
    """
    import inspect
    import sys

    current_module = sys.modules[__name__]
    test_functions = [
        obj for name, obj in inspect.getmembers(current_module)
        if inspect.isfunction(obj) and name.startswith("test_") and name != "test_contract_suite_summary"
    ]

    assert len(test_functions) == 11, f"Expected 11 contract tests, found {len(test_functions)}"

    # All tests except this meta-test should have @pytest.mark.requires_database
    for func in test_functions:
        markers = [m.name for m in getattr(func, "pytestmark", [])]
        assert "requires_database" in markers, f"{func.__name__} missing @pytest.mark.requires_database"
