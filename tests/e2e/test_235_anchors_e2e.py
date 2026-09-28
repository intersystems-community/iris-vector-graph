"""Spec 235 US6: patient anchors from the synced graph, and `/api/cypher` falling back
to the external bridge only for a patient no graph holds (research R15).

The oracle is the vendored file: a patient's compartment resources come from
`interp_fixture.expected_compartments`, their clinical tokens from
`resource_tokens`, and the concepts from the crosswalk table as read back.
"""

from __future__ import annotations

import collections
import json
import os
import uuid
from unittest.mock import patch

import pytest

from tests.e2e import interp_fixture as ifx
from tests.e2e.fhir_conftest import GRAPH
from tests.e2e.test_235_coverage_gaps_e2e import SEARCHCOLUMN, _fields, _params, _rows

pytestmark = [pytest.mark.e2e]
os.environ.setdefault("SKIP_IRIS_TESTS", "false")

RUN = uuid.uuid4().hex[:8]
XW_SOURCE = "ivg235anchors"
CONCEPT = f"t235anc:{RUN}"
N_CODES = 40


@pytest.fixture(scope="module")
def run(fhir_conn_required, fhir_engine, synthea_loaded):
    """The run's patients, the oracle's per-patient token pairs, and a crosswalk of
    the N_CODES commonest run pairs, dropped afterwards."""
    prefix, resources = synthea_loaded
    used = fhir_engine._indexed_clinical(GRAPH)[0]
    types = sorted({r["resourceType"] for r in resources})
    comp = {t: p for t in types if (p := _params(fhir_conn_required, t))}
    rows = _rows(fhir_conn_required, f"SELECT ResourceType, ParamName, FHIRPath FROM {SEARCHCOLUMN} WHERE Type = 'reference'")
    reached = ifx.expected_compartments(resources, comp, ifx.compartment_paths(rows, comp))
    fields = _fields(fhir_conn_required, set(used))
    by_key = {f"{r['resourceType']}/{r['id']}": r for r in resources}
    pairs: dict = collections.defaultdict(set)
    for src, patients in reached.items():
        toks = set().union(*ifx.resource_tokens(by_key[src], used, fields).values())
        for patient in patients:
            pairs[patient] |= toks
    common = collections.Counter(p for toks in pairs.values() for p in toks).most_common(N_CODES)
    try:
        for i, ((system, code), _) in enumerate(common):
            fhir_engine.code_crosswalk_add(system, code, f"{CONCEPT}:c{i}", relation="exact", source=XW_SOURCE)
        xw = collections.defaultdict(set)
        for s, c, t in _rows(fhir_conn_required, "SELECT code_system_uri, code, target_node_id FROM Graph_KG.code_crosswalk"):
            xw[(s, c)].add(t)
        yield {"prefix": prefix, "pairs": pairs, "xw": xw, "resources": resources}
    finally:
        cur = fhir_conn_required.cursor()
        try:
            cur.execute("DELETE FROM Graph_KG.code_crosswalk WHERE source = ?", [XW_SOURCE])
            fhir_conn_required.commit()
        finally:
            cur.close()


def _want(run, patient_key):
    return sorted({t for p in run["pairs"].get(patient_key, ()) for t in run["xw"].get(p, ())})


def _patients(run):
    return sorted(f"Patient/{r['id']}" for r in run["resources"] if r["resourceType"] == "Patient")


def test_graph_anchors(fhir_engine, run):
    """Scenario 1: every run patient's anchors are its compartment's crosswalked codes."""
    nonempty = 0
    for key in _patients(run):
        got = fhir_engine.fhir_patient_anchors(key.split("/", 1)[1], graph=GRAPH)
        want = _want(run, key)
        assert got["graphs"] == [GRAPH], key
        assert got["anchors"] == [{"id": i, "graph": GRAPH} for i in want], key
        nonempty += bool(want)
    assert nonempty >= 2, "the crosswalk should reach several patients"


def test_absent_patient(fhir_engine):
    assert fhir_engine.fhir_patient_anchors(f"t235absent-{RUN}", graph=GRAPH) == {"graphs": [], "anchors": []}


@pytest.fixture
def second_graph(fhir_conn_required, fhir_engine, run):
    """A scratch FHIR graph over the same repository. Its watermarks start just
    below this run's first row, so one SyncOnce projects only the run (a Rebuild
    would re-project the whole shared repository)."""
    scratch = f"fhir:IVGFHIR:t235{RUN}"
    cols = [
        r[0]
        for r in _rows(
            fhir_conn_required,
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = 'Graph_KG' "
            "AND TABLE_NAME = 'fhir_graphs' ORDER BY ORDINAL_POSITION",
        )
    ]
    copy = [c for c in cols if c not in ("graph_id", "wm_rsrc", "wm_ver", "task_id", "last_sync", "last_rebuild", "last_error", "last_counts")]
    rschema, vschema = _rows(fhir_conn_required, "SELECT rsrc_schema, ver_schema FROM Graph_KG.fhir_graphs WHERE graph_id = ?", GRAPH)[0]
    (lo,) = _rows(fhir_conn_required, f"SELECT MIN(ID) FROM {rschema}.Rsrc WHERE Key LIKE ?", f"%/{run['prefix']}-%")[0]
    (maxv,) = _rows(fhir_conn_required, f"SELECT MAX(ID) FROM {vschema}.RsrcVer")[0]
    # INSERT ... SELECT takes no `?` in its select list, so copy the row through Python.
    (vals,) = _rows(fhir_conn_required, f"SELECT {', '.join(copy)} FROM Graph_KG.fhir_graphs WHERE graph_id = ?", GRAPH)
    cur = fhir_conn_required.cursor()
    try:
        cur.execute(
            f"INSERT INTO Graph_KG.fhir_graphs (graph_id, wm_rsrc, wm_ver, {', '.join(copy)}) "
            f"VALUES ({', '.join(['?'] * (3 + len(copy)))})",
            [scratch, lo - 1, maxv or 0, *vals],
        )
        fhir_conn_required.commit()
    finally:
        cur.close()
    try:
        fhir_engine.fhir_graph_sync(scratch)
        yield scratch
    finally:
        fhir_engine.erase_graph(scratch)
        cur = fhir_conn_required.cursor()
        try:
            cur.execute("DELETE FROM Graph_KG.fhir_graphs WHERE graph_id = ?", [scratch])
            fhir_conn_required.commit()
        finally:
            cur.close()


def test_two_graphs(fhir_engine, run, second_graph):
    """Scenario 2: without `graph=` the anchors name both graphs; with it, one."""
    key = max(_patients(run), key=lambda k: len(_want(run, k)))
    pid = key.split("/", 1)[1]
    want = _want(run, key)
    assert want
    both = fhir_engine.fhir_patient_anchors(pid)
    assert both["graphs"] == sorted([GRAPH, second_graph])
    assert both["anchors"] == sorted(
        ({"id": i, "graph": g} for i in want for g in (GRAPH, second_graph)), key=lambda a: (a["id"], a["graph"])
    )
    one = fhir_engine.fhir_patient_anchors(pid, graph=second_graph)
    assert one == {"graphs": [second_graph], "anchors": [{"id": i, "graph": second_graph} for i in want]}


def _post(fhir_engine, body):
    from fastapi.testclient import TestClient

    from iris_vector_graph import cypher_api

    with patch.object(cypher_api, "_get_engine", return_value=fhir_engine):
        return TestClient(cypher_api.app).post("/api/cypher", json=body)


def test_graph_source_through_api(fhir_engine, run, monkeypatch):
    pytest.importorskip("fastapi")
    monkeypatch.delenv("IVG_API_KEY", raising=False)
    key = max(_patients(run), key=lambda k: len(_want(run, k)))
    with patch("iris_vector_graph.fhir_bridge.fhir_search_conditions") as search:
        resp = _post(
            fhir_engine,
            {"query": "RETURN $patient_anchors AS a", "fhir_patient_id": key.split("/", 1)[1], "fhir_graph": GRAPH},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    search.assert_not_called()
    assert body["anchor_source"] == "graph"
    assert body["anchors"] == [{"id": i, "graph": GRAPH} for i in _want(run, key)]
    assert json.loads(body["rows"][0][0]) == _want(run, key)  # list values come back JSON-encoded


def test_absent_patient_uses_bridge(fhir_engine, monkeypatch):
    """Scenario 3: a patient no graph holds gets the pre-235 bridge anchors."""
    pytest.importorskip("fastapi")
    monkeypatch.delenv("IVG_API_KEY", raising=False)
    monkeypatch.delenv("IVG_FHIR_ALLOWED_BASES", raising=False)
    monkeypatch.setenv("FHIR_BASE_URL", "http://fhir.test.invalid/r4")
    ok = {"error": None, "conditions": [{"code": "J45"}, {"code": "E11.9"}]}
    with patch("iris_vector_graph.fhir_bridge.fhir_search_conditions", return_value=ok) as search, patch(
        "iris_vector_graph.fhir_bridge.get_kg_anchors", return_value=["DOID:2841", "DOID:9352"]
    ) as kg:
        resp = _post(fhir_engine, {"query": "RETURN $patient_anchors AS a", "fhir_patient_id": f"t235absent-{RUN}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert search.call_args.kwargs["patient_id"] == f"t235absent-{RUN}"
    assert kg.call_args.args[1] == ["J45", "E11.9"]
    assert body["anchor_source"] == "fhir_bridge"
    assert body["anchors"] == [{"id": "DOID:2841", "graph": None}, {"id": "DOID:9352", "graph": None}]
    assert json.loads(body["rows"][0][0]) == ["DOID:2841", "DOID:9352"]


def test_anchors_json_roundtrip(fhir_engine, run):
    key = _patients(run)[0]
    got = fhir_engine.fhir_patient_anchors(key.split("/", 1)[1])
    assert json.loads(json.dumps(got)) == got
