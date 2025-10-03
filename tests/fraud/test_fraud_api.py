"""
API Tests for Fraud Scoring Endpoints

Tests FastAPI endpoints with live IRIS database.
"""

import pytest
import requests
import time

# API base URL (configured for embedded fraud server)
API_BASE_URL = "http://localhost:8100"


@pytest.mark.integration
@pytest.mark.api
class TestFraudAPIEndpoints:
    """Test fraud API endpoints"""

    def test_health_endpoint(self):
        """Test /fraud/health endpoint"""
        response = requests.get(f"{API_BASE_URL}/fraud/health")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "healthy"
        assert "model_loaded" in data
        assert "database_connected" in data

    def test_score_endpoint_mlp_mode(self):
        """Test /fraud/score endpoint with MLP mode"""
        payload = {
            "mode": "MLP",
            "payer": "acct:test_user_001",
            "device": "dev:laptop",
            "ip": "ip:192.168.1.1",
            "merchant": "merch:test_store",
            "amount": 100.0
        }

        response = requests.post(f"{API_BASE_URL}/fraud/score", json=payload)
        assert response.status_code == 200

        data = response.json()
        assert "prob" in data
        assert "reasons" in data
        assert "trace_id" in data
        assert "mode" in data
        assert "timestamp" in data

        # Probability should be between 0 and 1
        assert 0.0 <= data["prob"] <= 1.0

        # Mode should match request
        assert data["mode"] == "MLP"

    def test_score_endpoint_minimal_fields(self):
        """Test /fraud/score with minimal required fields"""
        payload = {
            "mode": "MLP",
            "payer": "acct:minimal_test",
            "amount": 50.0
        }

        response = requests.post(f"{API_BASE_URL}/fraud/score", json=payload)
        assert response.status_code == 200

        data = response.json()
        assert "prob" in data
        assert 0.0 <= data["prob"] <= 1.0

    def test_score_endpoint_performance(self):
        """Test fraud scoring API latency"""
        payload = {
            "mode": "MLP",
            "payer": "acct:perf_test",
            "device": "dev:laptop",
            "ip": "ip:192.168.1.1",
            "merchant": "merch:store",
            "amount": 100.0
        }

        # Measure 10 requests to get average latency
        latencies = []
        for _ in range(10):
            start = time.perf_counter()
            response = requests.post(f"{API_BASE_URL}/fraud/score", json=payload)
            elapsed_ms = (time.perf_counter() - start) * 1000
            latencies.append(elapsed_ms)
            assert response.status_code == 200

        avg_latency = sum(latencies) / len(latencies)
        print(f"\nAverage API latency: {avg_latency:.2f}ms")

        # Should complete in <100ms on average (accounting for network + processing)
        assert avg_latency < 100, f"Average latency {avg_latency:.2f}ms exceeds 100ms threshold"

    def test_score_endpoint_invalid_mode(self):
        """Test /fraud/score with invalid mode"""
        payload = {
            "mode": "INVALID_MODE",
            "payer": "acct:test",
            "amount": 100.0
        }

        response = requests.post(f"{API_BASE_URL}/fraud/score", json=payload)
        # Should return 422 for validation error
        assert response.status_code == 422

    def test_score_endpoint_missing_required_fields(self):
        """Test /fraud/score with missing required fields"""
        payload = {
            "mode": "MLP"
            # Missing payer and amount
        }

        response = requests.post(f"{API_BASE_URL}/fraud/score", json=payload)
        assert response.status_code == 422

    def test_score_endpoint_large_amount(self):
        """Test fraud scoring with large transaction amount"""
        payload = {
            "mode": "MLP",
            "payer": "acct:large_tx_test",
            "amount": 999999.99
        }

        response = requests.post(f"{API_BASE_URL}/fraud/score", json=payload)
        assert response.status_code == 200

        data = response.json()
        # Large amounts might trigger higher fraud scores
        assert "prob" in data

    def test_score_endpoint_concurrent_requests(self):
        """Test fraud scoring handles concurrent requests"""
        import concurrent.futures

        def make_request(i):
            payload = {
                "mode": "MLP",
                "payer": f"acct:concurrent_test_{i}",
                "amount": 100.0
            }
            response = requests.post(f"{API_BASE_URL}/fraud/score", json=payload)
            return response.status_code

        # Make 10 concurrent requests
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(make_request, i) for i in range(10)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        # All should succeed
        assert all(status == 200 for status in results)
