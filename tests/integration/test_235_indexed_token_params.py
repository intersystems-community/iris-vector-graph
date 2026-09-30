"""Spec 235 US3: Graph.KG.FHIRGraph.IndexedTokenParams, called directly over TCP in
IVGFHIR on the repository's live SearchColumn rows (research R9). The session
fixture resets the repository, so the module loads the genomics fixture for the
Observations these params need (4.1.1 gate)."""

from __future__ import annotations

import json

import pytest

from tests.e2e.fhir_conftest import (  # noqa: F401
    GRAPH,
    _fhir_session,
    fhir_conn,
    fhir_conn_required,
    genomics_loaded,
)

pytestmark = pytest.mark.usefixtures("genomics_loaded")


def _indexed(conn, params):
    import iris

    raw = iris.createIRIS(conn).classMethodValue("Graph.KG.FHIRGraph", "IndexedTokenParams", GRAPH, params)
    return json.loads(raw)


def test_used_and_dropped(fhir_conn):  # noqa: F811
    out = _indexed(fhir_conn, '["code","value-concept","nope"]')
    assert out["status"] == "ok", out
    assert out["used"] == ["code", "value-concept"]
    assert out["dropped"] == ["nope"]


def test_clinical_four_all_indexed(fhir_conn):  # noqa: F811
    """R9: every clinical param is a TOKEN param on Observation, which the graph holds."""
    four = ["code", "value-concept", "component-code", "component-value-concept"]
    out = _indexed(fhir_conn, json.dumps(four))
    assert out["used"] == four and out["dropped"] == []


def test_reference_param_is_not_a_token(fhir_conn):  # noqa: F811
    out = _indexed(fhir_conn, '["subject","code"]')
    assert out["used"] == ["code"] and out["dropped"] == ["subject"]


def test_duplicates_once_and_empty(fhir_conn):  # noqa: F811
    assert _indexed(fhir_conn, '["code","code"]')["used"] == ["code"]
    out = _indexed(fhir_conn, "[]")
    assert out["used"] == [] and out["dropped"] == []


def test_bad_json_is_an_error(fhir_conn):  # noqa: F811
    assert _indexed(fhir_conn, "not json")["status"] == "error"
