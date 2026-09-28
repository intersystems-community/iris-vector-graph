"""Spec 235 US1: `in_patient_compartment` edges on the live IVGFHIR repository.

Membership is checked against the server's own `HSFHIR_X0001_S.<Type>Compartments`
tables (contracts/fixture.md, research R5); `via` against the fixture-side oracle
`interp_fixture.expected_compartments`, whose paths come from `SearchColumn`.
"""

from __future__ import annotations

import json
import os
import zipfile

import pytest

from tests.e2e import interp_fixture as ifx
from tests.e2e.fhir_conftest import GRAPH, FhirLoader, _sync

pytestmark = [pytest.mark.e2e]
os.environ.setdefault("SKIP_IRIS_TESTS", "false")

PRED = "in_patient_compartment"
SEARCHCOLUMN = "HS_FHIRServer_Storage_Json.SearchColumn"
# Documented oracle exclusions, as (type, key) pairs. None are expected (research R4).
EXCLUDED: set = set()


def _rows(conn, sql, *args):
    cur = conn.cursor()
    try:
        cur.execute(sql, list(args))
        return [tuple(r) for r in cur.fetchall()]
    finally:
        cur.close()


def _key(r):
    return f"{r['resourceType']}/{r['id']}"


def _params(conn, rtype):
    import iris

    out = json.loads(iris.createIRIS(conn).classMethodValue("Graph.KG.FHIRGraph", "CompartmentParams", GRAPH, rtype))
    return set(out["params"])


def _compartment_edges(conn, sources=None):
    """{s: {o_id: via}} over in_patient_compartment edges, optionally for `sources` only."""
    out = {}
    for s, o, q in _rows(conn, "SELECT s, o_id, qualifiers FROM Graph_KG.rdf_edges WHERE graph_id = ? AND p = ?", GRAPH, PRED):
        if sources is None or s in sources:
            out.setdefault(s, {})[o] = (json.loads(q) if q else {}).get("via")
    return out


def _gk(conn):
    import iris

    return str(iris.createIRIS(conn).classMethodValue("Graph.KG.GraphKey", "ForIndex", GRAPH))


def _adjacent(conn, s, p, o):
    import iris

    irisobj = iris.createIRIS(conn)
    gk = _gk(conn)
    return bool(irisobj.isDefined("^KG", "out", gk, s, p, o)), bool(irisobj.isDefined("^KG", "in", gk, o, p, s))


@pytest.fixture(scope="module")
def run(fhir_conn_required, synthea_loaded, genomics_loaded):
    """Both fixtures' resources, with the type -> compartment params and
    (type, param) -> element paths the oracles need."""
    resources = synthea_loaded[1] + genomics_loaded[1]
    types = sorted({r["resourceType"] for r in resources})
    comp = {t: p for t in types if (p := _params(fhir_conn_required, t))}
    rows = _rows(
        fhir_conn_required,
        f"SELECT ResourceType, ParamName, FHIRPath FROM {SEARCHCOLUMN} WHERE Type = 'reference'",
    )
    paths = ifx.compartment_paths(rows, comp)
    return {
        "resources": resources,
        "keys": {_key(r) for r in resources},
        "comp": comp,
        "paths": paths,
        "synthea_prefix": synthea_loaded[0],
    }


@pytest.fixture(scope="module")
def edges(fhir_conn_required, run):
    return _compartment_edges(fhir_conn_required, run["keys"])


def test_membership_matches_server(fhir_conn_required, run, edges):
    """SC-001: per compartment type, the server's Compartments rows (self rows,
    dangling targets and other runs dropped) equal the graph's compartment edges."""
    live_patients = {
        s
        for (s,) in _rows(
            fhir_conn_required,
            "SELECT s FROM Graph_KG.rdf_labels WHERE graph_id = ? AND label = 'Patient'",
            GRAPH,
        )
    }
    # The server puts `link`ed Patients in each other's compartment; the graph skips
    # the Patient type by design (FR-004), so Patient rows are not compared.
    types = sorted(({r["resourceType"] for r in run["resources"]} & set(run["comp"])) - {"Patient"})
    mismatches, counts = {}, {}
    for t in types:
        want = {}
        for k, v in _rows(
            fhir_conn_required,
            f"SELECT Key, value FROM HSFHIR_X0001_S.{t}Compartments WHERE value %STARTSWITH 'Patient/'",
        ):
            if k == v or v not in live_patients or k not in run["keys"] or (t, k) in EXCLUDED:
                continue
            want.setdefault(k, set()).add(v)
        got = {s: set(o) for s, o in edges.items() if s.startswith(f"{t}/")}
        counts[t] = sum(len(v) for v in got.values())
        if want != got:
            mismatches[t] = {
                "missing": sorted((k, v) for k in want for v in want[k] - got.get(k, set()))[:10],
                "extra": sorted((k, v) for k in got for v in got[k] - want.get(k, set()))[:10],
            }
    print("\ncompartment edges per type:", json.dumps(counts, sort_keys=True))
    assert not mismatches, json.dumps(mismatches, indent=1)
    assert sum(counts.values()) > 0


def test_via_matches_fixture(run, edges):
    """FR-002: every edge's `via` is the sorted list of params that reach that Patient."""
    want = ifx.expected_compartments(run["resources"], run["comp"], run["paths"])
    got = {s: dict(o) for s, o in edges.items()}
    diffs = {s: (want.get(s), got.get(s)) for s in set(want) | set(got) if want.get(s) != got.get(s)}
    assert not diffs, json.dumps(dict(sorted(diffs.items())[:10]), indent=1)


# ------------------------------------------------------------------ scenarios


@pytest.fixture
def scratch(fhir_conn_required):
    """A fresh prefix with three Patients and a Group; everything PUT through it is
    deleted afterwards."""
    loader = FhirLoader(fhir_conn_required)
    put = []

    def add(resource):
        loader.put(resource)
        put.append(resource)
        return _key(resource)

    for name in ("p1", "p2", "p3"):
        add({"resourceType": "Patient", "id": loader.id(name)})
    add({"resourceType": "Group", "id": loader.id("g1"), "type": "person", "actual": True})
    try:
        yield loader, add
    finally:
        for r in reversed(put):
            loader.dispatch("DELETE", f"/{r['resourceType']}/{r['id']}")
        _sync(fhir_conn_required)


def _obs(loader, name, **refs):
    body = {
        "resourceType": "Observation",
        "id": loader.id(name),
        "status": "final",
        "code": {"coding": [{"system": "http://loinc.org", "code": "8867-4"}]},
    }
    for k, v in refs.items():
        body[k] = [{"reference": v}] if k == "performer" else {"reference": v}
    return body


def test_scenario_1_two_params_one_edge(fhir_conn_required, scratch):
    loader, add = scratch
    p1 = f"Patient/{loader.id('p1')}"
    key = add(_obs(loader, "o1", subject=p1, performer=p1))
    _sync(fhir_conn_required)
    assert _compartment_edges(fhir_conn_required, {key}) == {key: {p1: ["performer", "subject"]}}


def test_scenario_2_encounter(fhir_conn_required, scratch):
    loader, add = scratch
    p1 = f"Patient/{loader.id('p1')}"
    key = add(
        {
            "resourceType": "Encounter",
            "id": loader.id("e1"),
            "status": "finished",
            "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "AMB"},
            "subject": {"reference": p1},
        }
    )
    _sync(fhir_conn_required)
    # R4 lists only `patient` for Encounter; `subject` indexes the same element but
    # is not a compartment param there (research R3).
    assert _compartment_edges(fhir_conn_required, {key}) == {key: {p1: ["patient"]}}


def test_scenario_3_patient_skipped(fhir_conn_required, run):
    assert not [s for s in _compartment_edges(fhir_conn_required) if s.startswith("Patient/")]
    patients = [r for r in run["resources"] if r["resourceType"] == "Patient" and r.get("link")]
    assert len(patients) == 2
    for r in patients:
        other = r["link"][0]["other"]["reference"]
        assert _rows(
            fhir_conn_required,
            "SELECT o_id FROM Graph_KG.rdf_edges WHERE graph_id = ? AND s = ? AND p = 'link'",
            GRAPH,
            _key(r),
        ) == [(other,)]


def test_scenario_4_group_subject(fhir_conn_required, scratch):
    loader, add = scratch
    key = add(_obs(loader, "o4", subject=f"Group/{loader.id('g1')}"))
    _sync(fhir_conn_required)
    assert _compartment_edges(fhir_conn_required, {key}) == {}


def test_scenario_5_subject_change(fhir_conn_required, scratch):
    loader, add = scratch
    p1, p3 = f"Patient/{loader.id('p1')}", f"Patient/{loader.id('p3')}"
    key = add(_obs(loader, "o5", subject=p1))
    _sync(fhir_conn_required)
    assert _compartment_edges(fhir_conn_required, {key}) == {key: {p1: ["subject"]}}
    loader.put(_obs(loader, "o5", subject=p3))
    _sync(fhir_conn_required)
    assert _compartment_edges(fhir_conn_required, {key}) == {key: {p3: ["subject"]}}
    assert _adjacent(fhir_conn_required, key, PRED, p3) == (True, True)
    assert _adjacent(fhir_conn_required, key, PRED, p1) == (False, False)


def test_scenario_6_delete(fhir_conn_required, scratch):
    loader, add = scratch
    p1 = f"Patient/{loader.id('p1')}"
    key = add(_obs(loader, "o6", subject=p1))
    _sync(fhir_conn_required)
    assert key in _compartment_edges(fhir_conn_required, {key})
    loader.delete("Observation", loader.id("o6"))
    _sync(fhir_conn_required)
    assert _compartment_edges(fhir_conn_required, {key}) == {}
    assert _adjacent(fhir_conn_required, key, PRED, p1) == (False, False)


def _state(conn, keys):
    edges = {(s, p, o, q) for s, p, o, q in _rows(conn, "SELECT s, p, o_id, qualifiers FROM Graph_KG.rdf_edges WHERE graph_id = ?", GRAPH) if s in keys}
    props = {(s, k, v) for s, k, v in _rows(conn, 'SELECT s, "key", val FROM Graph_KG.rdf_props WHERE graph_id = ?', GRAPH) if s in keys}
    labels = {(s, lb) for s, lb in _rows(conn, "SELECT s, label FROM Graph_KG.rdf_labels WHERE graph_id = ?", GRAPH) if s in keys}
    return edges, props, labels


def test_scenario_7_full_equals_incremental(fhir_conn_required, fhir_engine, run):
    """FR-008: a rebuild leaves the run's edges, props and labels as the incremental
    syncs built them."""
    before = _state(fhir_conn_required, run["keys"])
    assert any(p == PRED for _, p, _, _ in before[0])
    fhir_engine.fhir_graph_rebuild(GRAPH)
    after = _state(fhir_conn_required, run["keys"])
    for name, b, a in zip(("edges", "props", "labels"), before, after):
        assert b == a, (name, sorted(b - a)[:5], sorted(a - b)[:5])


def test_scenario_8_ordinary_edges(fhir_conn_required, fhir_engine, edges, tmp_path):
    """FR-007: verify_graph is clean, and a snapshot carries the compartment edges
    in both its sql and globals layers. The snapshot is not restored: a restore
    replaces the whole IVGFHIR namespace (research, deviation noted in tasks.md)."""
    report = fhir_engine.verify_graph(GRAPH)
    assert report.get("ok"), report
    path = str(tmp_path / "snap.zip")
    fhir_engine.save_snapshot(path, layers=["sql", "globals"])
    s, targets = next(iter(sorted(edges.items())))
    o, via = next(iter(sorted(targets.items())))
    with zipfile.ZipFile(path) as zf:
        rows = [json.loads(line) for line in zf.read("sql/Graph_KG_rdf_edges.ndjson").decode().splitlines() if line]
        kg = zf.read("globals/KG.ndjson").decode()
    hit = [r for r in rows if r.get("graph_id") == GRAPH and r["s"] == s and r["p"] == PRED and r["o_id"] == o]
    assert len(hit) == 1 and json.loads(hit[0]["qualifiers"])["via"] == via, hit
    assert any(s in line and PRED in line and o in line for line in kg.splitlines())


def test_cypher_match(fhir_engine, run, edges):
    prefix = f"Observation/{run['synthea_prefix']}-"
    r = fhir_engine.execute_cypher(
        f"USE GRAPH '{GRAPH}' MATCH (o:Observation)-[:{PRED}]->(p:Patient) "
        "WHERE o.id STARTS WITH $pfx RETURN o.id, p.id",
        {"pfx": prefix},
    )
    rows = r.rows if hasattr(r, "rows") else r.get("rows", [])
    want = {(s, o) for s, t in edges.items() if s.startswith(prefix) for o in t}
    assert want
    assert {tuple(x) for x in rows} == want


def test_existing_counts_exclude(fhir_conn_required, fhir_engine, edges):
    """FR-026: `edges` counts only reference edges; compartment edges have their own count."""
    status = fhir_engine.fhir_graph_status(GRAPH)
    (total,) = _rows(fhir_conn_required, "SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE graph_id = ? AND p = ?", GRAPH, PRED)[0]
    (others,) = _rows(fhir_conn_required, "SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE graph_id = ? AND p <> ?", GRAPH, PRED)[0]
    assert status["compartment_edges"] == total >= sum(len(t) for t in edges.values())
    assert status["edges"] == others
    report = fhir_engine.fhir_link_report(GRAPH)
    assert not [r for r in report.get("rows", []) if r.get("param") == PRED]
    assert PRED not in json.dumps(report.get("totals", {}))
