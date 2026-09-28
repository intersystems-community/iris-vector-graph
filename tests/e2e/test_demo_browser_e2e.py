"""The three demos in a real browser: headless Chromium against a live demo server.

The server runs in this process on a free port, pointed at IVGFHIR on
ivg-iris-enterprise. The bio and FHIR data are seeded first; fraud runs with
DEMO_MODE=true because the fraud API is a separate service. Each test clicks through
a scenario the way a presenter would and checks what the page draws. HTMX and D3
load from their CDNs, so the tests need network access.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

pytest.importorskip("fasthtml")
pw = pytest.importorskip("playwright.sync_api")
uvicorn = pytest.importorskip("uvicorn")

from iris_demo_server.services import bio_demo_data as bio  # noqa: E402
from iris_demo_server.services import fhir_demo_data as fhir_data  # noqa: E402

pytestmark = [pytest.mark.e2e]

TIMEOUT = 20_000


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(fhir_conn, fhir_engine):
    fhir_data.seed(fhir_conn, fhir_engine, log=lambda _: None)
    bio.seed(fhir_conn, fhir_engine, log=lambda _: None)

    saved = {k: os.environ.get(k) for k in ("DEMO_MODE",)}
    os.environ["DEMO_MODE"] = "true"
    from iris_demo_server import app as app_mod
    from iris_demo_server.routes import biomedical, fhir, fraud

    fraud._fraud_client = None
    biomedical.set_biomedical_client(None)
    fhir.set_fhir_client(None)

    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app_mod.app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    deadline = time.time() + 20
    while not srv.started:
        if time.time() > deadline:
            raise RuntimeError("demo server did not start")
        time.sleep(0.1)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    t.join(timeout=10)
    fraud._fraud_client = None
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def page(browser):
    ctx = browser.new_context(viewport={"width": 1400, "height": 1000})
    pg = ctx.new_page()
    pg.set_default_timeout(TIMEOUT)
    errors = []
    pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    pg.on("console", lambda m: errors.append(f"console: {m.text}") if m.type == "error" else None)
    pg.js_errors = errors
    yield pg
    ctx.close()


def _no_js_errors(page):
    assert page.js_errors == [], page.js_errors


# ------------------------------------------------------------------ landing


def test_landing_page_shows_three_live_demos(server, page):
    page.goto(server + "/")
    cards = page.locator(".card")
    assert cards.count() == 3
    page.wait_for_selector("#status-bio.ok")
    page.wait_for_selector("#status-fhir.ok")
    page.wait_for_selector("#status-fraud.warn")
    assert f"{len(bio.PROTEINS)} proteins" in page.inner_text("#status-bio")
    assert "patients" in page.inner_text("#status-fhir")
    assert "Demo heuristic" in page.inner_text("#status-fraud")
    _no_js_errors(page)


@pytest.mark.parametrize("path,title", [("/fraud", "Fraud"), ("/bio", "Biomedical"), ("/fhir", "FHIR")])
def test_each_card_opens_its_demo(server, page, path, title):
    page.goto(server + "/")
    page.click(f'a.go[href="{path}"]')
    page.wait_for_url(server + path)
    assert title.lower() in page.title().lower()
    _no_js_errors(page)


# -------------------------------------------------------------------- fraud


def test_fraud_high_risk_scenario_scores_and_draws(server, page):
    page.goto(server + "/fraud")
    assert "Demo heuristic" in page.inner_text("#fraud-mode")
    page.click('button[hx-get="/api/fraud/scenario/high_risk"]')
    page.wait_for_selector('#txn-form button:has-text("Score Transaction")')
    page.click('#txn-form button:has-text("Score Transaction")')
    badge = page.wait_for_selector(".risk-badge")
    assert badge.get_attribute("class").split()[-1] in {"risk-high", "risk-critical"}
    assert "Demo Heuristic" in page.inner_text("#results")
    page.wait_for_function("document.querySelectorAll('#graph svg circle').length >= 5")
    _no_js_errors(page)


# ---------------------------------------------------------------------- bio


def test_bio_header_counts_come_from_the_graph(server, page):
    page.goto(server + "/bio")
    stats = page.inner_text("#bio-stats")
    assert str(len(bio.PROTEINS)) in stats and str(len(bio.INTERACTIONS)) in stats
    _no_js_errors(page)


def test_bio_tp53_search_then_network(server, page):
    page.goto(server + "/bio")
    page.click('button[hx-get="/api/bio/scenario/cancer_protein"]')
    page.click('#search-form button:has-text("Search Proteins")')
    row = page.wait_for_selector('#results tr:has(code:text-is("TP53"))')
    row.click()
    neighbours = {b for a, b, _, _ in bio.INTERACTIONS if a == "TP53"} | {
        a for a, b, _, _ in bio.INTERACTIONS if b == "TP53"}
    page.wait_for_function(
        f"document.querySelectorAll('#network-graph svg circle').length >= {len(neighbours) + 1}")
    _no_js_errors(page)


def test_bio_kinase_inhibitor_search_lists_drug_targets(server, page):
    page.goto(server + "/bio")
    page.click('button[hx-get="/api/bio/scenario/drug_target"]')
    page.click('#search-form button:has-text("Search Proteins")')
    page.wait_for_selector("#results table")
    text = page.inner_text("#results")
    for sym in ("EGFR", "ABL1", "BRAF", "JAK2"):
        assert sym in text
    assert "not vector similarity" in text
    _no_js_errors(page)


def test_bio_pathway_walks_glycolysis(server, page):
    page.goto(server + "/bio")
    page.click('button[hx-get="/api/bio/scenario/metabolic_pathway"]')
    page.click('#search-form button:has-text("Find Pathway")')
    page.wait_for_selector('#results h3:has-text("Protein Pathway")')
    text = page.inner_text("#results")
    for sym in ("GAPDH", "PGK1", "PGAM1", "ENO1", "PKM", "LDHA"):
        assert sym in text
    assert "5 hops" in text
    _no_js_errors(page)


# --------------------------------------------------------------------- fhir


def test_fhir_diabetes_search_then_neighbourhood(server, page):
    page.goto(server + "/fhir")
    page.click('button[hx-get="/api/fhir/scenario/diabetes"]')
    page.click('#search-form button:has-text("Search the FHIR graph")')
    first = page.wait_for_selector("#results tr.clickable")
    first.click()
    page.wait_for_function("document.querySelectorAll('#viz svg circle').length > 1")
    assert page.inner_text("#viz-title").startswith("Patient/")
    _no_js_errors(page)


def test_fhir_live_add_then_sync(server, page):
    page.goto(server + "/fhir")
    page.click('#live-result >> xpath=.. >> button:has-text("Add condition")')
    page.wait_for_selector('#live-result:has-text("PUT")')
    assert "the graph does not yet" in page.inner_text("#live-result")
    page.click('#live-result >> xpath=.. >> button:has-text("Sync graph")')
    page.wait_for_selector('#live-result:has-text("Synced")')
    _no_js_errors(page)
