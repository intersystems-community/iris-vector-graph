"""Spec 231 FR-019: `/fhir-event` is deprecated in 4.1.0 and accepts an optional `graph`.

The registered FHIR graph (`ivg fhir register`) replaces the sidecar: it reads the
repository itself, so nothing has to call in. Until 5.0 the endpoint keeps working,
says it is deprecated in the response and a `Deprecation` header, and can write into a
named graph instead of the default one.
"""

from __future__ import annotations

import warnings
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

G = "fhir:IVGFHIR:X0001"


def _client(engine):
    from api.routers.fhir_event import router

    app = FastAPI()
    app.state.engine = engine
    app.include_router(router, prefix="/fhir-event")
    return TestClient(app, raise_server_exceptions=False)


def _engine():
    eng = MagicMock()
    eng.get_embedding.return_value = None
    return eng


BODY = {
    "resourceType": "Condition",
    "id": "c1",
    "fhirUrl": "Condition/c1",
    "patientRef": "Patient/p1",
    "date": "2026-01-02",
}


def test_response_says_deprecated():
    resp = _client(_engine()).post("/fhir-event/", json=BODY)
    assert resp.status_code == 200
    assert resp.headers.get("Deprecation") == "true"
    assert any("deprecated" in w and "ivg fhir register" in w for w in resp.json()["warnings"])


def test_handler_emits_deprecation_warning():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _client(_engine()).post("/fhir-event/", json=BODY)
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)


def test_graph_is_forwarded_to_every_write():
    eng = _engine()
    resp = _client(eng).post("/fhir-event/", json={**BODY, "graph": G})
    assert resp.status_code == 200
    for c in eng.create_node.call_args_list:
        assert c.kwargs.get("graph") == G
    assert eng.create_edge_temporal.call_args.kwargs.get("graph") == G


def test_no_graph_means_default_graph():
    """Omitted, the calls are exactly the 4.0 ones: no graph argument at all."""
    eng = _engine()
    _client(eng).post("/fhir-event/", json=BODY)
    for c in eng.create_node.call_args_list:
        assert "graph" not in c.kwargs
    assert "graph" not in eng.create_edge_temporal.call_args.kwargs


@pytest.mark.parametrize("bad", ["", "a" * 300])
def test_bad_graph_is_422(bad):
    resp = _client(_engine()).post("/fhir-event/", json={**BODY, "graph": bad})
    assert resp.status_code == 422


def test_embedding_goes_to_the_graph():
    from api.routers import fhir_event

    eng = _engine()
    assert fhir_event.already_embedded(eng, "Condition/c1", graph=G) is False
    assert eng.get_embedding.call_args.kwargs.get("graph") == G
