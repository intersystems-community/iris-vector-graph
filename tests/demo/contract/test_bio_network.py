"""Contract tests for GET /api/bio/network/{protein_id} (FR-012, FR-013, FR-018)"""
import pytest
from httpx import AsyncClient, ASGITransport
import sys
from pathlib import Path

src_path = Path(__file__).parent.parent.parent.parent / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from iris_demo_server.app import app


@pytest.mark.asyncio
async def test_network_response_schema():
    """Validates network response structure matches contract"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/bio/network/ENSP00000269305?expand_depth=1"
        )

    assert response.status_code == 200
    data = response.json()

    # Validate top-level structure
    assert "result" in data
    assert "metrics" in data

    # Validate result structure (InteractionNetwork)
    result = data["result"]
    assert "nodes" in result
    assert "edges" in result
    assert "layout_hints" in result or result.get("layout_hints") is None

    # Validate nodes structure
    assert isinstance(result["nodes"], list)
    assert len(result["nodes"]) > 0

    # Validate first node
    first_node = result["nodes"][0]
    assert "protein_id" in first_node
    assert "name" in first_node
    assert "organism" in first_node

    # Validate edges structure
    assert isinstance(result["edges"], list)

    # Validate first edge (if exists)
    if result["edges"]:
        first_edge = result["edges"][0]
        assert "source_protein_id" in first_edge
        assert "target_protein_id" in first_edge
        assert "interaction_type" in first_edge
        assert "confidence_score" in first_edge
        assert 0.0 <= first_edge["confidence_score"] <= 1.0

    # Validate metrics
    metrics = data["metrics"]
    assert metrics["query_type"] == "network_expansion"
    assert metrics["result_count"] == len(result["nodes"])


@pytest.mark.asyncio
async def test_network_node_expansion():
    """Validates expand_depth parameter changes node count"""
    # Test depth=1
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response1 = await client.get(
            "/api/bio/network/ENSP00000269305?expand_depth=1"
        )

    assert response1.status_code == 200
    data1 = response1.json()
    nodes_depth1 = len(data1["result"]["nodes"])

    # Test depth=2 (should have more or equal nodes)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response2 = await client.get(
            "/api/bio/network/ENSP00000269305?expand_depth=2"
        )

    assert response2.status_code == 200
    data2 = response2.json()
    nodes_depth2 = len(data2["result"]["nodes"])

    # Depth 2 should have >= nodes than depth 1
    assert nodes_depth2 >= nodes_depth1


@pytest.mark.asyncio
async def test_network_size_limits():
    """Validates FR-018: max 500 nodes enforced"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/bio/network/ENSP00000269305?expand_depth=3"
        )

    assert response.status_code == 200
    data = response.json()
    node_count = len(data["result"]["nodes"])

    # FR-018: Maximum 500 nodes to prevent browser performance issues
    assert node_count <= 500, f"Network has {node_count} nodes (>500 limit)"


@pytest.mark.asyncio
async def test_network_edge_validation():
    """Validates all edge protein IDs exist in nodes"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/bio/network/ENSP00000269305?expand_depth=1"
        )

    assert response.status_code == 200
    data = response.json()
    result = data["result"]

    # Build set of node IDs
    node_ids = {node["protein_id"] for node in result["nodes"]}

    # Validate all edges reference valid nodes
    for edge in result["edges"]:
        assert edge["source_protein_id"] in node_ids, \
            f"Edge source {edge['source_protein_id']} not in nodes"
        assert edge["target_protein_id"] in node_ids, \
            f"Edge target {edge['target_protein_id']} not in nodes"


@pytest.mark.asyncio
async def test_network_performance_requirement():
    """Validates FR-002: <2 second response time"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/bio/network/ENSP00000269305?expand_depth=1"
        )

    assert response.status_code == 200
    data = response.json()
    execution_time_ms = data["metrics"]["execution_time_ms"]

    # FR-002: System MUST display results within 2 seconds
    assert execution_time_ms < 2000, f"Network expansion took {execution_time_ms}ms (>2000ms limit)"
