"""Spec 235 US4: `fhir_coverage_report` and `fhir_concept_gaps` on the live IVGFHIR
repository, against the fixture-side oracles `interp_fixture.expected_coverage` and
`expected_gaps`. Both reports are scoped to this run's id prefix, because other
modules leave resources in the shared graph."""

from __future__ import annotations

import json
import os
import statistics
import time

import pytest

from iris_vector_graph._engine.fhir_graph import _token_fields
from tests.e2e import interp_fixture as ifx
from tests.e2e.fhir_conftest import GRAPH, FhirLoader, _sync

pytestmark = [pytest.mark.e2e]
os.environ.setdefault("SKIP_IRIS_TESTS", "false")

SEARCHCOLUMN = "HS_FHIRServer_Storage_Json.SearchColumn"


def _rows(conn, sql, *args):
    cur = conn.cursor()
    try:
        cur.execute(sql, list(args))
        return [tuple(r) for r in cur.fetchall()]
    finally:
        cur.close()


def _params(conn, rtype):
    import iris

    out = json.loads(iris.createIRIS(conn).classMethodValue("Graph.KG.FHIRGraph", "CompartmentParams", GRAPH, rtype))
    return set(out["params"])


def _fields(conn, params):
    """type -> param -> element paths, from the TOKEN SearchColumn FHIRPaths."""
    out: dict = {}
    for rtype, param, fhirpath in _rows(
        conn, f"SELECT ResourceType, ParamName, FHIRPath FROM {SEARCHCOLUMN} WHERE %UPPER(Type) = 'TOKEN'"
    ):
        if param in params:
            fields, _ = _token_fields(fhirpath, rtype)
            if fields:
                out.setdefault(rtype, {})[param] = fields
    return out


@pytest.fixture(scope="module")
def run(fhir_conn_required, fhir_engine, synthea_loaded):
    prefix, resources = synthea_loaded
    id_prefix = f"{prefix}-"
    rep = fhir_engine.fhir_coverage_report(GRAPH, id_prefix=id_prefix)
    used = rep["resolution"]["params_used"]
    types = sorted({r["resourceType"] for r in resources})
    comp = {t: p for t in types if (p := _params(fhir_conn_required, t))}
    rows = _rows(fhir_conn_required, f"SELECT ResourceType, ParamName, FHIRPath FROM {SEARCHCOLUMN} WHERE Type = 'reference'")
    resolved = {(s, c) for s, c in _rows(fhir_conn_required, "SELECT code_system_uri, code FROM Graph_KG.code_crosswalk")}
    return {
        "id_prefix": id_prefix,
        "resources": resources,
        "report": rep,
        "used": used,
        "comp": comp,
        "paths": ifx.compartment_paths(rows, comp),
        "fields": _fields(fhir_conn_required, set(used)),
        "resolved": resolved,
    }


@pytest.fixture(scope="module")
def want(run):
    return ifx.expected_coverage(run["resources"], run["comp"], run["paths"], run["fields"], run["resolved"])


def test_types(run, want):
    """SC-005 / FR-020: per type count, compartment share, patient-less sample,
    meta_profile histogram and category distribution."""
    got = run["report"]["types"]
    assert set(got) == set(want["types"])
    for t, w in want["types"].items():
        g = dict(got[t])
        if w["compartment_type"]:
            assert g.pop("compartment_share") == round(w["with_compartment"] / w["count"], 4), t
        assert g == w, t


def test_resolution(run, want):
    res = run["report"]["resolution"]
    assert res["params_used"] and set(res["params_used"]) <= {"code", "value-concept", "component-code", "component-value-concept"}
    assert set(res["params_used"]) | set(res["dropped_params"]) == {"code", "value-concept", "component-code", "component-value-concept"}
    assert res["by_system"] == want["resolution"]["by_system"]


def test_linked_patients(run, want):
    """The fixture's one synthetic seealso pair."""
    assert run["report"]["linked_patients"] == want["linked_patients"] == 2


def test_link_report_embedded(fhir_engine, run):
    assert run["report"]["link_report"] == fhir_engine.fhir_link_report(GRAPH)


def test_gaps(fhir_engine, run):
    """FR-021: unmatched pairs by resource count, their totals and text-only counts."""
    got = fhir_engine.fhir_concept_gaps(GRAPH, params="clinical", id_prefix=run["id_prefix"])
    want = ifx.expected_gaps(run["resources"], run["used"], run["fields"], run["resolved"])
    assert got["params_used"] == run["used"]
    assert got["unmatched_total"] == want["unmatched_total"]
    assert got["unmatched"] == want["unmatched"]
    assert got["text_only"] == want["text_only"]


def test_gaps_repeatable(fhir_engine, run):
    a = fhir_engine.fhir_concept_gaps(GRAPH, id_prefix=run["id_prefix"])
    b = fhir_engine.fhir_concept_gaps(GRAPH, id_prefix=run["id_prefix"])
    assert a == b
    assert fhir_engine.fhir_coverage_report(GRAPH, id_prefix=run["id_prefix"]) == run["report"]


def test_text_only_put(fhir_conn_required, fhir_engine):
    """A code with text and no coding counts as text-only; one with a coding does not."""
    loader = FhirLoader(fhir_conn_required)
    rs = [
        {"resourceType": "Observation", "id": loader.id("text"), "status": "final", "code": {"text": "pain score"}},
        {
            "resourceType": "Observation",
            "id": loader.id("coded"),
            "status": "final",
            "code": {"text": "heart rate", "coding": [{"system": "http://loinc.org", "code": "8867-4"}]},
        },
    ]
    try:
        for r in rs:
            loader.put(r)
        _sync(fhir_conn_required)
        got = fhir_engine.fhir_concept_gaps(GRAPH, id_prefix=f"{loader.prefix}-")
        assert got["text_only"] == {"code": 1}
    finally:
        for r in rs:
            loader.dispatch("DELETE", f"/Observation/{r['id']}")
        _sync(fhir_conn_required)


def _median_s(fn, n=5):
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t0)
    return statistics.median(times)


def test_budget(fhir_engine, run):
    """SC-008: coverage report and gaps under 2 s (median of 5) on the fixture."""
    cov = _median_s(lambda: fhir_engine.fhir_coverage_report(GRAPH, id_prefix=run["id_prefix"]))
    gaps = _median_s(lambda: fhir_engine.fhir_concept_gaps(GRAPH, params="clinical", id_prefix=run["id_prefix"]))
    print(f"\nUS4 medians: coverage {cov * 1000:.0f} ms, gaps(clinical) {gaps * 1000:.0f} ms")
    assert cov < 2.0 and gaps < 2.0
