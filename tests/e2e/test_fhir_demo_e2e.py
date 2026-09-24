"""The FHIR graph demo against the live repository in IVGFHIR (ivg-iris-enterprise).

Seeds the demo cohort through the FHIR service, then drives the demo client and the
page the way a presenter would: a concept search that only expansion can answer, a
relation filter that changes who is ranked, a condition added live that the graph
picks up at the next sync, and the neighbourhood the page draws.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

pytest.importorskip("fasthtml")

from iris_demo_server.services import fhir_demo_data as data  # noqa: E402

pytestmark = [pytest.mark.e2e]


@pytest.fixture(scope="module")
def client(fhir_conn, fhir_engine):
    from iris_demo_server.services.fhir_graph_client import FHIRGraphDemoClient

    out = data.seed(fhir_conn, fhir_engine, log=lambda _: None)
    return FHIRGraphDemoClient(fhir_engine, graph=out["graph"])


def _cohort_codes():
    """patient key -> set of ICD-10 codes, from the generator."""
    codes = {}
    for r in data.build_cohort():
        if r["resourceType"] == "Condition":
            codes.setdefault(r["subject"]["reference"], set()).add(r["code"]["coding"][0]["code"])
    return codes


def test_stats_count_the_cohort(client):
    stats = client.stats()
    assert stats["by_type"]["Patient"] >= data.COHORT_SIZE
    assert stats["nodes"] >= len(data.build_cohort())
    assert stats["edges"] > 0
    assert stats["pending"] == 0


def test_a_grouping_concept_needs_expansion(client):
    """`Diabetes mellitus` has no codes of its own: zero hops finds nobody, two hops
    reaches Type 2 DM and diabetic nephropathy."""
    assert client.search([data.DIABETES], hops=0)["patients"] == []

    out = client.search([data.DIABETES], hops=2, relations=["exact", "narrower"])
    expanded = {c["id"] for c in out["expanded"]}
    assert {data.TYPE2_DM, data.DIABETIC_NEPHROPATHY} <= expanded
    assert out["seeds"] > 0 and out["patients"]

    truth = _cohort_codes()
    for p in out["patients"]:
        assert any(c.startswith("E11") for c in truth[p["key"]]), p
        assert p["evidence"] and all(e["code"].startswith("E11") for e in p["evidence"])
        assert p["name"]
    for stage in ("expand_ms", "resolve_ms", "ppr_ms"):
        assert out["timings"][stage] >= 0


def test_the_specialist_ranks_first(client):
    out = client.search([data.TYPE2_DM], hops=0, relations=["exact"])
    assert out["practitioners"][0]["key"] == "Practitioner/demo-dr01"
    assert out["practitioners"][0]["specialty"] == "Endocrinology"


def test_labs_widen_the_seed_set(client):
    exact = client.search([data.CKD], hops=0, relations=["exact"])
    everything = client.search([data.CKD], hops=0, relations=None)
    assert everything["seeds"] > exact["seeds"]
    assert any(e["code"] == "33914-3" for p in everything["patients"] for e in p["evidence"])
    assert not any(e["code"] == "33914-3" for p in exact["patients"] for e in p["evidence"])


def test_a_live_condition_appears_after_sync(client, fhir_conn):
    truth = _cohort_codes()
    patient = next(
        key for key, _ in data.patients() if not any(c.startswith("J45") for c in truth.get(key, ()))
    )

    def ranked():
        out = client.search([data.ASTHMA], hops=0, relations=["exact"], top_k=500)
        return {p["key"] for p in out["patients"]}

    added = client.add_condition(patient, "J45.40")
    try:
        assert patient not in ranked(), "the graph moved before it was synced"
        assert client.status()["pending"] > 0
        client.sync()
        assert patient in ranked()
    finally:
        client.dispatch("DELETE", f"/{added['key']}")
        client.sync()
    assert patient not in ranked()


def test_neighborhood_of_a_patient(client):
    hood = client.neighborhood("Patient/demo-p001")
    ids = {n["id"] for n in hood["nodes"]}
    assert "Patient/demo-p001" in ids
    assert any(i.startswith("Practitioner/demo-dr") for i in ids)
    assert any(i.startswith("Encounter/demo-p001") for i in ids)
    for link in hood["links"]:
        assert link["source"] in ids and link["target"] in ids and link["p"]
    center = next(n for n in hood["nodes"] if n["id"] == "Patient/demo-p001")
    assert center["label"] and center["type"] == "Patient"


def test_the_page_renders_against_iris(client):
    from fasthtml.common import FastHTML
    from starlette.testclient import TestClient

    from iris_demo_server.routes import fhir as routes

    routes.set_fhir_client(client)
    try:
        app = FastHTML()
        routes.register_fhir_routes(app)
        http = TestClient(app)
        page = http.get("/fhir")
        assert page.status_code == 200
        assert f"{client.stats()['nodes']:,}" in page.text

        resp = http.post("/api/fhir/search", data={"concepts": [data.CHF], "hops": "1", "evidence": "diagnoses"})
        assert resp.status_code == 200
        assert "Patient/demo-p" in resp.text
    finally:
        routes.set_fhir_client(None)
