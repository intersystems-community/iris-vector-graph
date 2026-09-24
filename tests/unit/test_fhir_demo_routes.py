"""The FHIR graph demo's routes, against a fake client.

The page must show what the engine returned and nothing else: counts from
`fhir_graph_status`, stage timings as measured, and an honest message when the
namespace is not there, rather than numbers made up for the screenshot.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

pytest.importorskip("fasthtml")

from fasthtml.common import FastHTML  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from iris_demo_server.routes import fhir as routes  # noqa: E402
from iris_demo_server.services import fhir_demo_data as data  # noqa: E402


class FakeClient:
    graph = "fhir:IVGFHIR:X0001"
    concept_graph = data.CONCEPT_GRAPH

    def __init__(self):
        self.calls = []

    def stats(self):
        return {
            "graph": self.graph,
            "nodes": 4321,
            "edges": 8765,
            "by_type": {"Patient": 200, "Condition": 300},
            "unresolved": 7,
            "pending": 2,
            "last_sync": "2026-09-23 20:00:00",
        }

    def search(self, concepts, hops=1, relations=None, top_k=10):
        self.calls.append(("search", list(concepts), hops, relations, top_k))
        return {
            "concepts": list(concepts),
            "expanded": [{"id": c, "name": data.CONCEPTS.get(c, c)} for c in concepts],
            "seeds": 3,
            "patients": [
                {
                    "key": "Patient/demo-p007",
                    "name": "Ada Quinn",
                    "score": 0.0123,
                    "evidence": [{"key": "Condition/demo-c1", "code": "E11.9", "display": "T2DM"}],
                }
            ],
            "practitioners": [
                {"key": "Practitioner/demo-dr01", "name": "Dr Ito", "score": 0.004, "specialty": "Endocrinology"}
            ],
            "timings": {"expand_ms": 1.5, "resolve_ms": 6.25, "ppr_ms": 40.0},
        }

    def neighborhood(self, key, limit=60):
        self.calls.append(("neighborhood", key))
        return {
            "nodes": [{"id": key, "type": "Patient", "label": "Ada Quinn"}],
            "links": [],
        }

    def add_condition(self, patient, code):
        self.calls.append(("add", patient, code))
        return {"key": "Condition/demo-live-1", "status": 201}

    def sync(self):
        self.calls.append(("sync",))
        return {"status": "ok", "keys": 1}


@pytest.fixture
def fake():
    client = FakeClient()
    routes.set_fhir_client(client)
    yield client
    routes.set_fhir_client(None)


@pytest.fixture
def http(fake):
    app = FastHTML()
    routes.register_fhir_routes(app)
    return TestClient(app)


def test_page_renders_real_stats(http):
    resp = http.get("/fhir")
    assert resp.status_code == 200
    body = resp.text
    assert "FHIR" in body and "4,321" in body and "8,765" in body
    assert "fhir:IVGFHIR:X0001" in body
    for scenario in routes.SCENARIOS:
        assert f"/api/fhir/scenario/{scenario}" in body


def test_page_says_so_when_iris_is_unreachable(monkeypatch):
    routes.set_fhir_client(None)

    def boom():
        raise RuntimeError("connection refused")

    monkeypatch.setattr(routes, "_build_client", boom)
    app = FastHTML()
    routes.register_fhir_routes(app)
    resp = TestClient(app).get("/fhir")
    assert resp.status_code == 200
    assert "connection refused" in resp.text
    assert "fhir_demo_data" in resp.text  # how to seed it


def test_search_form_runs_the_pipeline(http, fake):
    resp = http.post(
        "/api/fhir/search",
        data={"concepts": [data.DIABETES], "hops": "2", "evidence": "diagnoses"},
    )
    assert resp.status_code == 200
    assert fake.calls[-1] == ("search", [data.DIABETES], 2, ["exact", "narrower"], 10)
    assert "Ada Quinn" in resp.text and "E11.9" in resp.text
    assert "6.2" in resp.text  # the resolve timing as measured
    assert "Dr Ito" in resp.text


def test_search_with_labs_passes_no_relation_filter(http, fake):
    http.post("/api/fhir/search", data={"concepts": [data.CKD], "hops": "0", "evidence": "all"})
    assert fake.calls[-1] == ("search", [data.CKD], 0, None, 10)


def test_search_json(http, fake):
    resp = http.post("/api/fhir/search", json={"concepts": [data.CHF], "hops": 1})
    assert resp.status_code == 200
    out = resp.json()
    assert out["patients"][0]["key"] == "Patient/demo-p007"
    assert out["timings"]["ppr_ms"] == 40.0


@pytest.mark.parametrize("bad", [{"concepts": []}, {"concepts": ["C999"]}, {"concepts": [data.CHF], "hops": 9}])
def test_search_rejects_bad_input(http, fake, bad):
    resp = http.post("/api/fhir/search", json=bad)
    assert resp.status_code == 400
    assert not [c for c in fake.calls if c[0] == "search"]


def test_scenario_fills_the_form(http):
    for name, scenario in routes.SCENARIOS.items():
        resp = http.get(f"/api/fhir/scenario/{name}")
        assert resp.status_code == 200
        for concept in scenario["concepts"]:
            assert f'value="{concept}"' in resp.text
    assert http.get("/api/fhir/scenario/nope").status_code == 404


def test_neighborhood_json_keeps_the_slash(http, fake):
    resp = http.get("/api/fhir/neighborhood/Patient/demo-p007")
    assert resp.status_code == 200
    assert resp.json()["nodes"][0]["id"] == "Patient/demo-p007"
    assert fake.calls[-1] == ("neighborhood", "Patient/demo-p007")


def test_neighborhood_rejects_non_keys(http, fake):
    assert http.get("/api/fhir/neighborhood/not-a-key").status_code == 400


def test_live_add_then_sync(http, fake):
    resp = http.post("/api/fhir/live/add", data={"patient": "Patient/demo-p003", "code": "I50.9"})
    assert resp.status_code == 200
    assert ("add", "Patient/demo-p003", "I50.9") in fake.calls
    assert "Condition/demo-live-1" in resp.text
    assert "sync" in resp.text.lower()  # tells the user the graph has not seen it yet

    resp = http.post("/api/fhir/live/sync")
    assert resp.status_code == 200
    assert fake.calls[-1] == ("sync",)


@pytest.mark.parametrize(
    "form",
    [
        {"patient": "Patient/demo-p003", "code": "Z99.9"},  # not a demo code
        {"patient": "Observation/demo-o1", "code": "I10"},  # not a patient
        {"patient": "Patient/x' OR 1=1", "code": "I10"},
    ],
)
def test_live_add_rejects_bad_input(http, fake, form):
    assert http.post("/api/fhir/live/add", data=form).status_code == 400
    assert not [c for c in fake.calls if c[0] == "add"]


def test_stats_fragment(http):
    resp = http.get("/api/fhir/stats")
    assert resp.status_code == 200
    assert "4,321" in resp.text


def test_arch_page():
    from iris_demo_server.app import app

    resp = TestClient(app).get("/arch/fhir")
    assert resp.status_code == 200
    assert "fhir_resolve_concepts" in resp.text


def test_homepage_links_the_demo():
    from iris_demo_server.app import app

    assert 'href="/fhir"' in TestClient(app).get("/").text
