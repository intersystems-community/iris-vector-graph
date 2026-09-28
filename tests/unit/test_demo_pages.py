"""The demo server's landing page and the claims the fraud and bio pages make.

The landing page lists the three demos, says what each needs to run, and checks
each backend after the page loads, so a dead database never stalls it. The fraud
and bio pages show what they are running against, not numbers made up for a slide.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

pytest.importorskip("fasthtml")

from starlette.testclient import TestClient  # noqa: E402

from iris_demo_server import app as app_mod  # noqa: E402
from iris_demo_server.routes import biomedical, fhir  # noqa: E402
from iris_demo_server.routes import fraud as fraud_routes  # noqa: E402

DEMOS = ("fraud", "bio", "fhir")
UNVERIFIED = ("130M", "94.2%", "0.8%", "50K+")


class FakeBio:
    def stats(self):
        return {"proteins": 33, "interactions": 38}


class DeadBio:
    def stats(self):
        raise RuntimeError("connection refused")


class FakeFHIR:
    def stats(self):
        return {"graph": "fhir:IVGFHIR:X0001", "nodes": 1200, "edges": 3400,
                "by_type": {"Patient": 200}, "unresolved": 0, "pending": 0, "last_sync": "x"}


class DeadFHIR:
    def stats(self):
        raise RuntimeError("namespace IVGFHIR unreachable")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setattr(fraud_routes, "_fraud_client", None)
    biomedical.set_biomedical_client(FakeBio())
    fhir.set_fhir_client(FakeFHIR())
    yield TestClient(app_mod.app)
    biomedical.set_biomedical_client(None)
    fhir.set_fhir_client(None)
    fraud_routes._fraud_client = None


def test_landing_page_links_every_demo(client):
    html = client.get("/").text
    for d in DEMOS:
        assert f'href="/{d}"' in html
        assert f'id="status-{d}"' in html
        assert f'hx-get="/api/status/{d}"' in html


def test_landing_page_says_what_each_demo_needs(client):
    html = client.get("/").text
    assert "IVGFHIR" in html
    assert "bio:demo" in html
    assert "DEMO_MODE" in html


def test_landing_page_makes_no_unverified_claims(client):
    html = client.get("/").text
    for claim in UNVERIFIED:
        assert claim not in html


def test_status_reports_live_counts(client):
    assert "33 proteins" in client.get("/api/status/bio").text
    assert "200 patients" in client.get("/api/status/fhir").text
    assert "demo heuristic" in client.get("/api/status/fraud").text.lower()


def test_status_reports_a_dead_backend_as_offline(client):
    biomedical.set_biomedical_client(DeadBio())
    fhir.set_fhir_client(DeadFHIR())
    for d in ("bio", "fhir"):
        r = client.get(f"/api/status/{d}")
        assert r.status_code == 200
        assert "offline" in r.text.lower()


def test_unknown_status_is_404(client):
    assert client.get("/api/status/nope").status_code == 404


def test_fraud_page_makes_no_unverified_claims(client):
    html = client.get("/fraud").text
    for claim in UNVERIFIED:
        assert claim not in html
    assert "demo heuristic" in html.lower()


def test_fraud_score_in_demo_mode_is_labelled_as_such(client):
    r = client.post("/api/fraud/score", data={
        "payer": "acct:x", "amount": "15000", "device": "dev:x",
        "merchant": "merch:x", "ip_address": "1.2.3.4"})
    assert r.status_code == 200
    assert "Demo Heuristic" in r.text
    assert "Fraud Api" not in r.text


def test_bio_page_shows_counts_from_the_graph(client):
    html = client.get("/bio").text
    assert ">33<" in html and ">38<" in html
    for claim in UNVERIFIED:
        assert claim not in html


def test_bio_page_survives_a_dead_backend(client):
    biomedical.set_biomedical_client(DeadBio())
    r = client.get("/bio")
    assert r.status_code == 200
    assert "offline" in r.text


def test_fraud_graph_is_labelled_illustrative(client):
    r = client.post("/api/fraud/score", data={
        "payer": "acct:x", "amount": "100", "device": "dev:x",
        "merchant": "merch:x", "ip_address": "1.2.3.4"})
    assert "illustrative" in r.text and "not read from IRIS" in r.text
