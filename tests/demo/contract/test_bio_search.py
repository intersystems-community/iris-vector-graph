"""Contract tests for POST /api/bio/search (FR-006, FR-007)"""
import pytest
from httpx import AsyncClient, ASGITransport
import sys
from pathlib import Path

# Add src to path
src_path = Path(__file__).parent.parent.parent.parent / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from iris_demo_server.app import app


@pytest.mark.asyncio
async def test_search_response_schema():
    """Validates search response structure matches contract"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/bio/search",
            json={
                "query_text": "TP53",
                "query_type": "name",
                "top_k": 10
            }
        )

    assert response.status_code == 200
    data = response.json()

    # Validate top-level structure
    assert "result" in data
    assert "metrics" in data

    # Validate result structure (SimilaritySearchResult)
    result = data["result"]
    assert "proteins" in result
    assert "similarity_scores" in result
    assert "search_method" in result

    # Validate proteins list
    assert isinstance(result["proteins"], list)
    assert len(result["proteins"]) > 0
    assert len(result["proteins"]) <= 10  # top_k limit

    # Validate first protein structure
    first_protein = result["proteins"][0]
    assert "protein_id" in first_protein
    assert "name" in first_protein
    assert "organism" in first_protein

    # Validate similarity scores
    assert isinstance(result["similarity_scores"], list)
    assert len(result["similarity_scores"]) == len(result["proteins"])
    for score in result["similarity_scores"]:
        assert 0.0 <= score <= 1.0

    # Validate metrics
    metrics = data["metrics"]
    assert metrics["query_type"] == "protein_search"
    assert metrics["execution_time_ms"] >= 0
    assert metrics["result_count"] == len(result["proteins"])
    assert metrics["backend_used"] == "iris_direct"  # Direct IRIS integration (no API layer)


@pytest.mark.asyncio
async def test_search_top_k_limit():
    """Validates top_k parameter limits results"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/bio/search",
            json={
                "query_text": "kinase",
                "query_type": "function",
                "top_k": 5
            }
        )

    assert response.status_code == 200
    data = response.json()
    proteins = data["result"]["proteins"]
    assert len(proteins) <= 5


@pytest.mark.asyncio
async def test_search_query_types():
    """Validates all query types accepted"""
    query_types = ["name", "sequence", "function"]

    for qtype in query_types:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/bio/search",
                json={
                    "query_text": "test",
                    "query_type": qtype,
                    "top_k": 10
                }
            )

        assert response.status_code == 200, f"Failed for query_type={qtype}"
        data = response.json()
        assert "result" in data


@pytest.mark.asyncio
async def test_search_performance_requirement():
    """Validates FR-002: <2 second response time"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/bio/search",
            json={
                "query_text": "TP53",
                "query_type": "name",
                "top_k": 10
            }
        )

    assert response.status_code == 200
    data = response.json()
    execution_time_ms = data["metrics"]["execution_time_ms"]

    # FR-002: System MUST display results within 2 seconds
    assert execution_time_ms < 2000, f"Search took {execution_time_ms}ms (>2000ms limit)"


@pytest.mark.asyncio
async def test_search_validation_errors():
    """Validates Pydantic validation for invalid inputs"""
    # Missing required field
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/bio/search",
            json={
                "query_type": "name",
                "top_k": 10
            }
        )

    # Should return validation error (422 or error in response)
    assert response.status_code in [200, 422]  # May return error object in 200
    if response.status_code == 200:
        data = response.json()
        assert "error" in data


@pytest.mark.asyncio
async def test_search_scores_descending():
    """Validates similarity scores are in descending order"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/bio/search",
            json={
                "query_text": "TP53",
                "query_type": "name",
                "top_k": 10
            }
        )

    assert response.status_code == 200
    data = response.json()
    scores = data["result"]["similarity_scores"]

    # Scores should be in descending order (most similar first)
    for i in range(len(scores) - 1):
        assert scores[i] >= scores[i + 1], "Scores not in descending order"
