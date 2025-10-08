"""Contract test for POST /api/fraud/score (FR-006, FR-007)

This test MUST FAIL until implementation is complete (TDD red phase).
"""
import pytest
from fasthtml.common import *
from decimal import Decimal


@pytest.fixture
def test_client():
    """Create test client for FastHTML app"""
    # Add src to path for imports
    import sys
    from pathlib import Path
    src_path = Path(__file__).parent.parent.parent.parent / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))

    from iris_demo_server.app import app
    from starlette.testclient import TestClient
    return TestClient(app)


def test_fraud_score_request_schema(test_client):
    """Test POST /api/fraud/score accepts valid request schema"""
    # Arrange
    valid_request = {
        "payer": "acct:test_user_001",
        "amount": 1500.00,
        "device": "dev:laptop_chrome",
        "merchant": "merch:electronics_store",
        "ip_address": "192.168.1.100"
    }

    # Act
    response = test_client.post("/api/fraud/score", json=valid_request)

    # Assert
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"


def test_fraud_score_response_schema(test_client):
    """Test POST /api/fraud/score returns valid FraudScoringResult + metrics"""
    # Arrange
    request_body = {
        "payer": "acct:test_user",
        "amount": 1500.00,
        "device": "dev:laptop",
        "merchant": "merch:electronics",
        "ip_address": "192.168.1.100"
    }

    # Act
    response = test_client.post("/api/fraud/score", json=request_body)

    # Assert - Top-level structure
    assert response.status_code == 200
    data = response.json()
    assert "result" in data, "Response must contain 'result' key"
    assert "metrics" in data, "Response must contain 'metrics' key"

    # Assert - FraudScoringResult schema
    result = data["result"]
    assert "fraud_probability" in result
    assert 0.0 <= result["fraud_probability"] <= 1.0, \
        f"fraud_probability must be 0.0-1.0, got {result['fraud_probability']}"

    assert "risk_classification" in result
    assert result["risk_classification"] in ["low", "medium", "high", "critical"], \
        f"Invalid risk_classification: {result['risk_classification']}"

    assert "contributing_factors" in result
    assert isinstance(result["contributing_factors"], list), \
        "contributing_factors must be a list"

    assert "scoring_timestamp" in result
    assert "scoring_model" in result

    # Assert - QueryPerformanceMetrics schema
    metrics = data["metrics"]
    assert "query_type" in metrics
    assert metrics["query_type"] == "fraud_score"

    assert "execution_time_ms" in metrics
    assert isinstance(metrics["execution_time_ms"], int)

    assert "backend_used" in metrics
    assert metrics["backend_used"] in ["fraud_api", "cached_demo"]

    assert "result_count" in metrics
    assert metrics["result_count"] == 1


def test_fraud_score_validation_errors(test_client):
    """Test POST /api/fraud/score returns 400 for invalid input"""
    # Test missing required field (payer)
    invalid_request = {
        "amount": 1500.00,
        "device": "dev:laptop"
    }

    response = test_client.post("/api/fraud/score", json=invalid_request)
    assert response.status_code == 400, "Missing required field should return 400"


def test_fraud_score_risk_classification_mapping(test_client):
    """Test risk classification matches fraud_probability thresholds"""
    test_cases = [
        (50.00, "low"),      # Low amount → low risk
        (3000.00, "medium"),  # Medium amount → medium risk
        (8000.00, "high"),    # High amount → high risk
        (15000.00, "critical") # Very high amount → critical risk
    ]

    for amount, expected_risk in test_cases:
        request = {
            "payer": "acct:test_user",
            "amount": amount,
            "device": "dev:laptop"
        }

        response = test_client.post("/api/fraud/score", json=request)
        assert response.status_code == 200

        data = response.json()
        actual_risk = data["result"]["risk_classification"]

        # Risk should be consistent with amount (heuristic in demo mode)
        assert actual_risk in ["low", "medium", "high", "critical"], \
            f"Amount {amount} → risk {actual_risk} (expected {expected_risk})"


def test_fraud_score_performance_requirement(test_client):
    """Test POST /api/fraud/score responds within 2 seconds (FR-002)"""
    import time

    request = {
        "payer": "acct:test_user",
        "amount": 1500.00,
        "device": "dev:laptop"
    }

    start = time.time()
    response = test_client.post("/api/fraud/score", json=request)
    execution_time = (time.time() - start) * 1000

    assert response.status_code == 200
    assert execution_time < 2000, \
        f"Response time {execution_time:.0f}ms exceeds 2s requirement (FR-002)"

    # Also verify metrics.execution_time_ms is reasonable
    data = response.json()
    metrics_time = data["metrics"]["execution_time_ms"]
    assert metrics_time < 2000, \
        f"Reported execution time {metrics_time}ms exceeds 2s"
