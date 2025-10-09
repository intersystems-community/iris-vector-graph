"""Contract tests for GET /api/bio/scenario/{scenario_name} (FR-029)"""
import pytest
from httpx import AsyncClient, ASGITransport
import sys
from pathlib import Path

src_path = Path(__file__).parent.parent.parent.parent / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from iris_demo_server.app import app


@pytest.mark.asyncio
async def test_scenario_cancer_protein():
    """Validates cancer_protein scenario loads correctly"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/bio/scenario/cancer_protein")

    assert response.status_code == 200
    # Response should be HTML form with HTMX attributes
    html = response.text
    assert 'name="query_text"' in html
    assert 'value="TP53"' in html
    assert 'name="query_type"' in html
    assert 'name="top_k"' in html


@pytest.mark.asyncio
async def test_scenario_metabolic_pathway():
    """Validates metabolic_pathway scenario loads correctly"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/bio/scenario/metabolic_pathway")

    assert response.status_code == 200
    html = response.text
    assert 'name="source_protein_id"' in html
    assert 'name="target_protein_id"' in html
    assert 'name="max_hops"' in html
    assert "ENSP00000306407" in html  # GAPDH
    assert "ENSP00000316649" in html  # LDHA


@pytest.mark.asyncio
async def test_scenario_drug_target():
    """Validates drug_target scenario loads correctly"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/bio/scenario/drug_target")

    assert response.status_code == 200
    html = response.text
    assert 'name="query_text"' in html
    assert "kinase inhibitor" in html or "drug_target" in html.lower()


@pytest.mark.asyncio
async def test_scenario_invalid_name():
    """Validates error handling for invalid scenario"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/bio/scenario/invalid_name")

    # Should return 200 with error message (or 404)
    assert response.status_code in [200, 404]
    if response.status_code == 200:
        content = response.text if response.headers.get("content-type", "").startswith("text") \
                  else response.json()
        # Should mention available scenarios
        assert "cancer_protein" in str(content).lower() or "not found" in str(content).lower()


@pytest.mark.asyncio
async def test_scenario_htmx_attributes():
    """Validates HTMX attributes present for reactive updates"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/bio/scenario/cancer_protein")

    assert response.status_code == 200
    html = response.text
    # Should have HTMX post target
    assert "hx-post" in html or "hx_post" in html
    assert "/api/bio/search" in html or "search" in html.lower()
