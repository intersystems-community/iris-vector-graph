#!/usr/bin/env python3
"""
Test suite for IRIS native REST API endpoints
Tests the Python-first Graph.KG.Service REST endpoints
"""

import pytest
import requests
import json
import time
from typing import Dict, List, Any


class TestIRISRestAPI:
    """Test IRIS native REST API endpoints"""

    BASE_URL = "http://localhost:52773/kg"

    # 768-dimensional test vectors (truncated for readability)
    TEST_VECTORS = {
        "tp53": [0.1] * 768,  # Simplified for testing
        "cancer": [0.2] * 768,
        "drug": [0.3] * 768
    }

    @classmethod
    def setup_class(cls):
        """Setup test class - verify IRIS is accessible"""
        try:
            response = requests.get(f"{cls.BASE_URL}/health", timeout=10)
            if response.status_code != 200:
                pytest.skip("IRIS REST API not accessible")
        except requests.RequestException:
            pytest.skip("IRIS REST API not accessible")

    def test_health_endpoint(self):
        """Test health check endpoint"""
        response = requests.get(f"{self.BASE_URL}/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "Graph.KG.Service"
        assert "timestamp" in data
        assert "database" in data

    def test_vector_search_basic(self):
        """Test basic vector search functionality"""
        payload = {
            "vector": self.TEST_VECTORS["tp53"],
            "k": 10
        }

        response = requests.post(
            f"{self.BASE_URL}/vectorSearch",
            json=payload,
            headers={"Content-Type": "application/json"}
        )

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) <= 10

        if data:  # If there are results
            for result in data:
                assert "id" in result
                assert "score" in result
                assert isinstance(result["score"], (int, float))

    def test_vector_search_with_label(self):
        """Test vector search with label filtering"""
        payload = {
            "vector": self.TEST_VECTORS["cancer"],
            "k": 5,
            "label": "gene"
        }

        response = requests.post(
            f"{self.BASE_URL}/vectorSearch",
            json=payload,
            headers={"Content-Type": "application/json"}
        )

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) <= 5

    def test_vector_search_invalid_dimensions(self):
        """Test vector search with invalid vector dimensions"""
        payload = {
            "vector": [0.1, 0.2, 0.3],  # Only 3 dimensions instead of 768
            "k": 10
        }

        response = requests.post(
            f"{self.BASE_URL}/vectorSearch",
            json=payload,
            headers={"Content-Type": "application/json"}
        )

        assert response.status_code == 400
        data = response.json()
        assert "error" in data
        assert "768-dimensional" in data["message"]

    def test_hybrid_search_basic(self):
        """Test basic hybrid search functionality"""
        payload = {
            "vector": self.TEST_VECTORS["drug"],
            "text": "cancer tumor oncology treatment",
            "k": 10,
            "c": 60
        }

        response = requests.post(
            f"{self.BASE_URL}/hybridSearch",
            json=payload,
            headers={"Content-Type": "application/json"}
        )

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) <= 10

        if data:  # If there are results
            for result in data:
                assert "id" in result
                assert "score" in result
                assert "vectorScore" in result
                assert "textScore" in result
                assert isinstance(result["score"], (int, float))

    def test_hybrid_search_missing_text(self):
        """Test hybrid search with missing text parameter"""
        payload = {
            "vector": self.TEST_VECTORS["tp53"],
            "k": 10
            # Missing "text" parameter
        }

        response = requests.post(
            f"{self.BASE_URL}/hybridSearch",
            json=payload,
            headers={"Content-Type": "application/json"}
        )

        assert response.status_code == 400
        data = response.json()
        assert "error" in data
        assert "Text query required" in data["message"]

    def test_meta_path_basic(self):
        """Test basic meta path search"""
        payload = {
            "srcId": "gene:TP53",
            "pred1": "associated_with",
            "pred2": "targets",
            "maxHops": 3
        }

        response = requests.post(
            f"{self.BASE_URL}/metaPath",
            json=payload,
            headers={"Content-Type": "application/json"}
        )

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

        if data:  # If there are paths
            for path in data:
                assert "id" in path
                assert "steps" in path
                assert isinstance(path["steps"], list)

                for step in path["steps"]:
                    assert "step" in step
                    assert "subject" in step
                    assert "predicate" in step
                    assert "object" in step

    def test_meta_path_any_predicate(self):
        """Test meta path search with empty predicates (any predicate)"""
        payload = {
            "srcId": "gene:BRCA1",
            "pred1": "",
            "pred2": "",
            "maxHops": 2
        }

        response = requests.post(
            f"{self.BASE_URL}/metaPath",
            json=payload,
            headers={"Content-Type": "application/json"}
        )

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_meta_path_missing_source(self):
        """Test meta path search with missing source ID"""
        payload = {
            "pred1": "associated_with",
            "pred2": "targets",
            "maxHops": 2
            # Missing "srcId"
        }

        response = requests.post(
            f"{self.BASE_URL}/metaPath",
            json=payload,
            headers={"Content-Type": "application/json"}
        )

        assert response.status_code == 400
        data = response.json()
        assert "error" in data
        assert "Source ID required" in data["message"]

    def test_invalid_json_request(self):
        """Test endpoints with invalid JSON"""
        invalid_json = '{"vector": [0.1, 0.2, incomplete'

        response = requests.post(
            f"{self.BASE_URL}/vectorSearch",
            data=invalid_json,
            headers={"Content-Type": "application/json"}
        )

        assert response.status_code == 400

    def test_performance_vector_search(self):
        """Test vector search performance"""
        payload = {
            "vector": self.TEST_VECTORS["tp53"],
            "k": 50
        }

        start_time = time.time()
        response = requests.post(
            f"{self.BASE_URL}/vectorSearch",
            json=payload,
            headers={"Content-Type": "application/json"}
        )
        elapsed_time = time.time() - start_time

        assert response.status_code == 200
        assert elapsed_time < 5.0  # Should complete within 5 seconds

        data = response.json()
        print(f"Vector search (k=50) completed in {elapsed_time:.3f}s, returned {len(data)} results")

    def test_performance_hybrid_search(self):
        """Test hybrid search performance"""
        payload = {
            "vector": self.TEST_VECTORS["cancer"],
            "text": "cancer tumor disease treatment therapy",
            "k": 30,
            "c": 60
        }

        start_time = time.time()
        response = requests.post(
            f"{self.BASE_URL}/hybridSearch",
            json=payload,
            headers={"Content-Type": "application/json"}
        )
        elapsed_time = time.time() - start_time

        assert response.status_code == 200
        assert elapsed_time < 8.0  # Should complete within 8 seconds

        data = response.json()
        print(f"Hybrid search (k=30) completed in {elapsed_time:.3f}s, returned {len(data)} results")

    def test_performance_graph_traversal(self):
        """Test graph traversal performance"""
        payload = {
            "srcId": "gene:TP53",
            "pred1": "",
            "pred2": "",
            "maxHops": 3
        }

        start_time = time.time()
        response = requests.post(
            f"{self.BASE_URL}/metaPath",
            json=payload,
            headers={"Content-Type": "application/json"}
        )
        elapsed_time = time.time() - start_time

        assert response.status_code == 200
        assert elapsed_time < 3.0  # Should complete within 3 seconds

        data = response.json()
        print(f"Graph traversal (maxHops=3) completed in {elapsed_time:.3f}s, found {len(data)} paths")

    def test_concurrent_requests(self):
        """Test handling of concurrent requests"""
        import concurrent.futures
        import threading

        def make_request(vector_key: str) -> Dict[str, Any]:
            payload = {
                "vector": self.TEST_VECTORS[vector_key],
                "k": 20
            }
            response = requests.post(
                f"{self.BASE_URL}/vectorSearch",
                json=payload,
                headers={"Content-Type": "application/json"}
            )
            return {
                "status_code": response.status_code,
                "result_count": len(response.json()) if response.status_code == 200 else 0,
                "vector_key": vector_key
            }

        # Submit concurrent requests
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [
                executor.submit(make_request, "tp53"),
                executor.submit(make_request, "cancer"),
                executor.submit(make_request, "drug"),
                executor.submit(make_request, "tp53"),
                executor.submit(make_request, "cancer")
            ]

            results = [future.result() for future in concurrent.futures.as_completed(futures)]

        # All requests should succeed
        for result in results:
            assert result["status_code"] == 200
            print(f"Concurrent request ({result['vector_key']}) returned {result['result_count']} results")

    def test_edge_cases(self):
        """Test various edge cases"""

        # Test with k=0
        payload = {
            "vector": self.TEST_VECTORS["tp53"],
            "k": 0
        }
        response = requests.post(f"{self.BASE_URL}/vectorSearch", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 0

        # Test with very large k
        payload = {
            "vector": self.TEST_VECTORS["tp53"],
            "k": 10000
        }
        response = requests.post(f"{self.BASE_URL}/vectorSearch", json=payload)
        assert response.status_code == 200

        # Test with empty text query
        payload = {
            "vector": self.TEST_VECTORS["drug"],
            "text": "",
            "k": 10
        }
        response = requests.post(f"{self.BASE_URL}/hybridSearch", json=payload)
        assert response.status_code == 400

        # Test with maxHops=0
        payload = {
            "srcId": "gene:TP53",
            "pred1": "",
            "pred2": "",
            "maxHops": 0
        }
        response = requests.post(f"{self.BASE_URL}/metaPath", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 0

    def test_response_structure_consistency(self):
        """Test that response structures are consistent"""

        # Vector search response structure
        payload = {"vector": self.TEST_VECTORS["tp53"], "k": 5}
        response = requests.post(f"{self.BASE_URL}/vectorSearch", json=payload)
        if response.status_code == 200:
            data = response.json()
            for result in data:
                assert set(result.keys()) >= {"id", "score"}

        # Hybrid search response structure
        payload = {
            "vector": self.TEST_VECTORS["cancer"],
            "text": "test query",
            "k": 5
        }
        response = requests.post(f"{self.BASE_URL}/hybridSearch", json=payload)
        if response.status_code == 200:
            data = response.json()
            for result in data:
                assert set(result.keys()) >= {"id", "score", "vectorScore", "textScore"}

        # Meta path response structure
        payload = {
            "srcId": "test:node",
            "pred1": "",
            "pred2": "",
            "maxHops": 2
        }
        response = requests.post(f"{self.BASE_URL}/metaPath", json=payload)
        if response.status_code == 200:
            data = response.json()
            for path in data:
                assert set(path.keys()) >= {"id", "steps"}
                for step in path["steps"]:
                    assert set(step.keys()) >= {"step", "subject", "predicate", "object"}


if __name__ == "__main__":
    # Run specific tests for quick validation
    test_instance = TestIRISRestAPI()
    test_instance.setup_class()

    print("Running IRIS REST API tests...")

    try:
        test_instance.test_health_endpoint()
        print("✅ Health endpoint test passed")
    except Exception as e:
        print(f"❌ Health endpoint test failed: {e}")

    try:
        test_instance.test_vector_search_basic()
        print("✅ Vector search test passed")
    except Exception as e:
        print(f"❌ Vector search test failed: {e}")

    try:
        test_instance.test_performance_vector_search()
        print("✅ Vector search performance test passed")
    except Exception as e:
        print(f"❌ Vector search performance test failed: {e}")

    print("Test suite completed")