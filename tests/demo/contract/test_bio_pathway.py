"""Contract tests for POST /api/bio/pathway (FR-019, FR-020)"""
import pytest
from httpx import AsyncClient, ASGITransport
import sys
from pathlib import Path

src_path = Path(__file__).parent.parent.parent.parent / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from iris_demo_server.app import app


@pytest.mark.asyncio
async def test_pathway_response_schema():
    """Validates pathway response structure matches contract"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/bio/pathway",
            json={
                "source_protein_id": "ENSP00000011898",  # TSPAN9
                "target_protein_id": "ENSP00000324101",  # CD151 (direct neighbor)
                "max_hops": 2
            }
        )

    assert response.status_code == 200
    data = response.json()

    # Validate top-level structure
    assert "result" in data
    assert "metrics" in data

    # Validate result structure (PathwayResult)
    result = data["result"]
    assert "path" in result
    assert "intermediate_proteins" in result
    assert "path_interactions" in result
    assert "confidence" in result

    # Validate path
    assert isinstance(result["path"], list)
    assert len(result["path"]) >= 2  # At least source + target

    # Validate intermediate proteins
    assert len(result["intermediate_proteins"]) == len(result["path"])

    # Validate path interactions (should be len(path) - 1)
    assert len(result["path_interactions"]) == len(result["path"]) - 1

    # Validate confidence score
    assert 0.0 <= result["confidence"] <= 1.0

    # Validate metrics
    metrics = data["metrics"]
    assert metrics["query_type"] == "pathway_search"
    assert metrics["execution_time_ms"] >= 0
    assert "graph_traversal" in metrics["search_methods"]


@pytest.mark.asyncio
async def test_pathway_max_hops():
    """Validates max_hops parameter limits path length"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/bio/pathway",
            json={
                "source_protein_id": "ENSP00000012049",  # QPCTL
                "target_protein_id": "ENSP00000321221",  # SH2B1 (direct neighbor)
                "max_hops": 2
            }
        )

    assert response.status_code == 200
    data = response.json()
    path = data["result"]["path"]

    # Path length should be max_hops + 1 (source + intermediate + target)
    assert len(path) <= 3  # max_hops=2 means at most 3 nodes


@pytest.mark.asyncio
async def test_pathway_interaction_confidence():
    """Validates all interactions have confidence scores 0.0-1.0"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/bio/pathway",
            json={
                "source_protein_id": "ENSP00000012443",  # PPP5C
                "target_protein_id": "ENSP00000295228",  # INHBB (direct neighbor)
                "max_hops": 3
            }
        )

    assert response.status_code == 200
    data = response.json()
    interactions = data["result"]["path_interactions"]

    for interaction in interactions:
        assert "confidence_score" in interaction
        assert 0.0 <= interaction["confidence_score"] <= 1.0
        assert "interaction_type" in interaction
        assert "source_protein_id" in interaction
        assert "target_protein_id" in interaction


@pytest.mark.asyncio
async def test_pathway_performance_requirement():
    """Validates FR-002: <2 second response time"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/bio/pathway",
            json={
                "source_protein_id": "ENSP00000011898",  # TSPAN9
                "target_protein_id": "ENSP00000324101",  # CD151 (direct neighbor)
                "max_hops": 2
            }
        )

    assert response.status_code == 200
    data = response.json()
    execution_time_ms = data["metrics"]["execution_time_ms"]

    # FR-002: System MUST display results within 2 seconds
    assert execution_time_ms < 2000, f"Pathway search took {execution_time_ms}ms (>2000ms limit)"
