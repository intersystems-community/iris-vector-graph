"""Spec 233 US3: a model result with provenance, written back and synced.

Two model versions each write a Device, a prediction Observation and a Provenance
whose `entity` is two of somaticPatient's variant Observations from the loaded
fixture. The inputs and the Patient are already prefixed, so `prefix_resources`
rewrites only the trio's references to itself.
"""

from __future__ import annotations

import pytest

from tests.e2e.fhir_conftest import (
    GRAPH,
    FhirLoader,
    _sync,
    graph_edges,
    load_run,
    neighbourhood,
    prefix_resources,
    teardown_run,
)
from tests.e2e.genomics_fixture import model_result

pytestmark = [pytest.mark.e2e]

INPUTS = ["EGFR-L858R-var", "ROS1-Fusion-var"]


@pytest.fixture(scope="module")
def models(fhir_conn_required, genomics_loaded):
    prefix, _ = genomics_loaded
    loader = FhirLoader(fhir_conn_required)
    loader.prefix = prefix
    patient = f"Patient/{prefix}-somaticPatient"
    inputs = [f"Observation/{prefix}-{i}" for i in INPUTS]
    trios = {v: prefix_resources(model_result(v, patient, inputs), prefix) for v in (7, 8)}
    stored = load_run(fhir_conn_required, loader, [r for v in (7, 8) for r in trios[v]])
    try:
        yield loader, prefix, patient, inputs, {v: {r["resourceType"]: r for r in t} for v, t in trios.items()}
    finally:
        teardown_run(fhir_conn_required, loader, stored)


def _key(r):
    return f"{r['resourceType']}/{r['id']}"


def test_provenance_edges(fhir_conn_required, models):
    _, _, _, inputs, trios = models
    t = trios[7]
    prov = _key(t["Provenance"])
    edges = set(graph_edges(fhir_conn_required, [prov]))
    print(f"\n{prov} edges: {sorted(edges)}")
    assert (prov, "target", _key(t["Observation"])) in edges
    assert (prov, "agent", _key(t["Device"])) in edges
    for i in inputs:
        assert (prov, "entity", i) in edges


def test_neighbourhood_two_hops(fhir_conn_required, fhir_engine, models):
    _, _, _, inputs, trios = models
    t = trios[7]
    pred, dev = _key(t["Observation"]), _key(t["Device"])
    near = neighbourhood(fhir_conn_required, pred, 2)
    assert dev in near
    assert set(inputs) <= near
    scores = fhir_engine.kg_PERSONALIZED_PAGERANK([pred], bidirectional=True, graph=GRAPH, return_top_k=None)
    print(f"\nPPR from {pred}: device {scores.get(dev, 0):.5f}, inputs {[round(scores.get(i, 0), 5) for i in inputs]}")
    assert scores.get(dev, 0) > 0
    assert all(scores.get(i, 0) > 0 for i in inputs)


def test_two_versions_isolated(fhir_conn_required, models):
    _, _, _, _, trios = models
    for v, other in ((7, 8), (8, 7)):
        t, o = trios[v], trios[other]
        assert _key(t["Observation"]) != _key(o["Observation"])
        agents = {x for _, p, x in graph_edges(fhir_conn_required, [_key(t["Provenance"])]) if p == "agent"}
        assert agents == {_key(t["Device"])}


def test_delete_provenance_removes_edges(fhir_conn_required, models):
    loader, _, _, _, trios = models
    t = trios[7]
    prov, pred, dev = _key(t["Provenance"]), _key(t["Observation"]), _key(t["Device"])
    out = loader.dispatch("DELETE", f"/{prov}")
    assert str(out["status"]).startswith("20"), out
    _sync(fhir_conn_required)
    cur = fhir_conn_required.cursor()
    try:
        cur.execute(
            "SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE graph_id = ? AND (s = ? OR o_id = ?)", [GRAPH, prov, prov]
        )
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT node_id FROM Graph_KG.nodes WHERE graph_id = ? AND node_id IN (?, ?)", [GRAPH, pred, dev])
        assert {r[0] for r in cur.fetchall()} == {pred, dev}
    finally:
        cur.close()
