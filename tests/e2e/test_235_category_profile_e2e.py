"""Spec 235 US5: `category` and `meta_profile` node properties and category labels on
the live IVGFHIR repository. The oracles (`interp_fixture.expected_*`) read the
vendored Synthea file only."""

from __future__ import annotations

import json
import os

import pytest

from tests.e2e import interp_fixture as ifx
from tests.e2e.fhir_conftest import GRAPH, FhirLoader, _sync

pytestmark = [pytest.mark.e2e]
os.environ.setdefault("SKIP_IRIS_TESTS", "false")

SEARCHCOLUMN = "HS_FHIRServer_Storage_Json.SearchColumn"
OBS = "http://terminology.hl7.org/CodeSystem/observation-category"
LOCAL = "http://example.org/ivg235/local-category"


def _rows(conn, sql, *args):
    cur = conn.cursor()
    try:
        cur.execute(sql, list(args))
        return [tuple(r) for r in cur.fetchall()]
    finally:
        cur.close()


def _key(r):
    return f"{r['resourceType']}/{r['id']}"


def _props(conn, key_name, prefix=None):
    out = {}
    for s, v in _rows(conn, 'SELECT s, val FROM Graph_KG.rdf_props WHERE graph_id = ? AND "key" = ?', GRAPH, key_name):
        if prefix is None or s.split("/", 1)[1].startswith(prefix):
            out[s] = json.loads(v)
    return out


def _labels(conn, keys):
    out = {k: set() for k in keys}
    for s, lab in _rows(conn, "SELECT s, label FROM Graph_KG.rdf_labels WHERE graph_id = ?", GRAPH):
        if s in out:
            out[s].add(lab)
    return out


def _gk(conn):
    import iris

    return str(iris.createIRIS(conn).classMethodValue("Graph.KG.GraphKey", "ForIndex", GRAPH))


def _label_global(conn, label, key):
    import iris

    return bool(iris.createIRIS(conn).isDefined("^KG", "label", _gk(conn), label, key))


@pytest.fixture(scope="module")
def r4types(fhir_conn_required):
    return {t for (t,) in _rows(fhir_conn_required, f"SELECT DISTINCT %EXACT(ResourceType) FROM {SEARCHCOLUMN}") if t != "Resource"}


@pytest.fixture(scope="module")
def run(synthea_loaded):
    prefix, resources = synthea_loaded
    return prefix, resources, {_key(r): r for r in resources}


def test_category_property(fhir_conn_required, run):
    """Scenario 1, FR-016."""
    prefix, resources, _ = run
    got = _props(fhir_conn_required, "category", prefix)
    want = {_key(r): c for r in resources if (c := ifx.expected_categories(r))}
    assert want
    assert got == want, (sorted(set(got) ^ set(want))[:5])


def test_labels(fhir_conn_required, fhir_engine, run, r4types):
    """FR-017."""
    prefix, resources, by_key = run
    got = _labels(fhir_conn_required, by_key)
    want = {k: ifx.expected_labels(r, r4types) for k, r in by_key.items()}
    diff = {k: (got[k], want[k]) for k in by_key if got[k] != want[k]}
    assert not diff, list(diff.items())[:5]
    counts = {}
    for r in resources:
        for lab in ifx.expected_labels(r, r4types) - {r["resourceType"]}:
            counts[lab] = counts.get(lab, 0) + 1
    print("\ncategory labels:", dict(sorted(counts.items())))
    for lab in ("VitalSigns", "Laboratory"):
        n = sum(1 for r in resources if r["resourceType"] == "Observation" and lab in ifx.expected_labels(r, r4types))
        assert n
        res = fhir_engine.execute_cypher(
            f"USE GRAPH '{GRAPH}' MATCH (o:Observation:{lab}) WHERE o.id STARTS WITH $pfx RETURN count(o)",
            {"pfx": f"Observation/{prefix}-"},
        )
        rows = res.rows if hasattr(res, "rows") else res.get("rows", [])
        assert rows[0][0] == n, (lab, rows, n)


def test_meta_profile(fhir_conn_required, run, r4types):
    """Scenario 3, FR-018. No label comes from a profile."""
    prefix, resources, by_key = run
    got = _props(fhir_conn_required, "meta_profile", prefix)
    want = {_key(r): p for r in resources if (p := ifx.expected_profiles(r))}
    assert want
    assert got == want, (sorted(set(got) ^ set(want))[:5])
    hist = {}
    for p in want.values():
        for u in p:
            hist[u] = hist.get(u, 0) + 1
    print("\nmeta_profile histogram:", sorted(hist.items(), key=lambda kv: -kv[1])[:10])
    labels = set().union(*_labels(fhir_conn_required, by_key).values())
    assert not any("StructureDefinition" in lab or lab.startswith("UsCore") for lab in labels)


def test_no_procedure_collision(fhir_engine, run):
    prefix = run[0]
    res = fhir_engine.execute_cypher(
        f"USE GRAPH '{GRAPH}' MATCH (p:Procedure) WHERE p.id CONTAINS $pfx RETURN p.id",
        {"pfx": f"/{prefix}-"},
    )
    rows = res.rows if hasattr(res, "rows") else res.get("rows", [])
    assert rows and all(r[0].startswith("Procedure/") for r in rows)


# ------------------------------------------------------------------ scratch PUTs


@pytest.fixture
def scratch(fhir_conn_required):
    loader = FhirLoader(fhir_conn_required)
    put = []

    def add(resource):
        loader.put(resource)
        if resource not in put:
            put.append(resource)
        return _key(resource)

    try:
        yield loader, add
    finally:
        for r in reversed(put):
            loader.dispatch("DELETE", f"/{r['resourceType']}/{r['id']}")
        _sync(fhir_conn_required)


def _obs(loader, name, *codings):
    return {
        "resourceType": "Observation",
        "id": loader.id(name),
        "status": "final",
        "category": [{"coding": list(codings)}],
        "code": {"coding": [{"system": "http://loinc.org", "code": "8867-4"}]},
    }


def test_local_code_no_label(fhir_conn_required, scratch):
    """Scenario 2."""
    loader, add = scratch
    key = add(_obs(loader, "local", {"system": LOCAL, "code": "vital-signs"}))
    _sync(fhir_conn_required)
    assert _props(fhir_conn_required, "category")[key] == [f"{LOCAL}|vital-signs"]
    assert _labels(fhir_conn_required, [key])[key] == {"Observation"}


def test_category_change_removes_label(fhir_conn_required, scratch):
    loader, add = scratch
    key = add(_obs(loader, "chg", {"system": OBS, "code": "vital-signs"}))
    _sync(fhir_conn_required)
    assert _labels(fhir_conn_required, [key])[key] == {"Observation", "VitalSigns"}
    assert _label_global(fhir_conn_required, "VitalSigns", key)
    add(_obs(loader, "chg", {"system": OBS, "code": "laboratory"}))
    _sync(fhir_conn_required)
    assert _labels(fhir_conn_required, [key])[key] == {"Observation", "Laboratory"}
    assert not _label_global(fhir_conn_required, "VitalSigns", key)
    assert _label_global(fhir_conn_required, "Laboratory", key)
    assert _label_global(fhir_conn_required, "Observation", key)
    assert _props(fhir_conn_required, "category")[key] == [f"{OBS}|laboratory"]
    # No category at all: the row goes, the type label stays.
    body = _obs(loader, "chg")
    del body["category"]
    add(body)
    _sync(fhir_conn_required)
    assert key not in _props(fhir_conn_required, "category")
    assert _labels(fhir_conn_required, [key])[key] == {"Observation"}
