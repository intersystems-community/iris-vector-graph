"""Spec 235 US2: `Graph.KG.FHIRGraph.GroupByPatient`, driven directly on a scratch
graph whose compartment edges and `via` qualifiers are written by hand."""

from __future__ import annotations

import json
import os
import uuid

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

PRED = "in_patient_compartment"
# (resource, patient, via)
COMP = [
    ("Observation/o1", "Patient/p1", ["subject"]),
    ("Observation/o2", "Patient/p1", ["performer"]),
    ("Observation/o2", "Patient/p2", ["subject"]),
    ("Observation/o3", "Patient/p2", ["performer", "subject"]),
    ("Encounter/e1", "Patient/p1", ["patient"]),
    ("Observation/o4", "Patient/p1", ["performer"]),
    ("Observation/o5", "Patient/p3", ["subject"]),
    ("Observation/o6", "Patient/p4", ["subject"]),
]
SCORES = {
    "Observation/o1": 0.4,
    "Observation/o2": 0.1,
    "Observation/o3": 0.2,
    "Encounter/e1": 0.05,
    "Observation/o4": 0.01,
    "Observation/o5": 0.03,
    "Observation/o6": 0.03,
    "Patient/p1": 0.3,
    "Practitioner/d1": 0.07,
}


@pytest.fixture(scope="module")
def graph(iris_connection):
    from iris_vector_graph.engine import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    g = f"t235grp:{uuid.uuid4().hex[:8]}"
    nodes = sorted({k for k in SCORES} | {p for _, p, _ in COMP} | {s for s, _, _ in COMP})
    for n in nodes:
        engine.create_node(n, labels=[n.split("/")[0]], graph=g)
    for s, p, via in COMP:
        engine.create_edge(s, PRED, p, qualifiers={"via": via}, graph=g)
    # An ordinary reference edge: not a compartment edge, so it attributes nothing.
    engine.create_edge("Practitioner/d1", "organization", "Patient/p1", graph=g)
    try:
        yield engine, g
    finally:
        engine.erase_graph(g)


def _group(graph, scores=SCORES, via="", explain_top=5, top_k=50):
    engine, g = graph
    raw = engine._store._call_classmethod(
        "Graph.KG.FHIRGraph", "GroupByPatient", g, json.dumps(scores), via, str(explain_top), str(top_k)
    )
    out = json.loads(str(raw))
    assert out.get("status") == "ok", out
    return out


def _by_patient(out):
    return {p["patient"]: p for p in out["patients"]}


def test_sums(graph):
    out = _by_patient(_group(graph))
    assert abs(out["Patient/p1"]["score"] - (0.4 + 0.1 + 0.05 + 0.01)) < 1e-12
    assert abs(out["Patient/p2"]["score"] - (0.1 + 0.2)) < 1e-12
    assert out["Patient/p1"]["contributors_total"] == 4
    assert out["Patient/p2"]["contributors_total"] == 2


def test_order_and_ties(graph):
    out = _group(graph)
    assert [p["patient"] for p in out["patients"]] == ["Patient/p1", "Patient/p2", "Patient/p3", "Patient/p4"]


def test_contributors_sorted_with_via(graph):
    p1 = _by_patient(_group(graph))["Patient/p1"]
    assert p1["contributors"] == [
        {"key": "Observation/o1", "score": 0.4, "via": ["subject"]},
        {"key": "Observation/o2", "score": 0.1, "via": ["performer"]},
        {"key": "Encounter/e1", "score": 0.05, "via": ["patient"]},
        {"key": "Observation/o4", "score": 0.01, "via": ["performer"]},
    ]


def test_explain_top(graph):
    p1 = _by_patient(_group(graph, explain_top=2))["Patient/p1"]
    assert [c["key"] for c in p1["contributors"]] == ["Observation/o1", "Observation/o2"]
    assert p1["contributors_total"] == 4
    assert abs(p1["score"] - 0.56) < 1e-12
    assert _by_patient(_group(graph, explain_top=0))["Patient/p1"]["contributors"] == []


def test_top_k(graph):
    assert len(_group(graph, top_k=0)["patients"]) == 4
    assert [p["patient"] for p in _group(graph, top_k=1)["patients"]] == ["Patient/p1"]


def test_via_filter(graph):
    out = _group(graph, via=json.dumps(["subject"]))
    pats = _by_patient(out)
    assert abs(pats["Patient/p1"]["score"] - 0.4) < 1e-12
    assert pats["Patient/p1"]["contributors_total"] == 1
    assert abs(pats["Patient/p2"]["score"] - 0.3) < 1e-12
    # o4 and e1 reach p1 through performer / patient only: nothing kept, unattributed.
    assert out["unattributed"]["count"] == 4
    assert abs(out["unattributed"]["score"] - (0.05 + 0.01 + 0.3 + 0.07)) < 1e-12


def test_unattributed(graph):
    out = _group(graph)
    # Patient/p1 is scored but is not its own contributor; Practitioner/d1 has no
    # compartment edge.
    assert out["unattributed"]["count"] == 2
    assert abs(out["unattributed"]["score"] - 0.37) < 1e-12


def test_unknown_key_is_unattributed(graph):
    out = _group(graph, scores={"Observation/nosuch": 0.5, "Observation/o1": 0.1})
    assert out["unattributed"] == {"count": 1, "score": 0.5}
    assert [p["patient"] for p in out["patients"]] == ["Patient/p1"]


def test_empty_scores(graph):
    out = _group(graph, scores={})
    assert out["patients"] == [] and out["unattributed"] == {"count": 0, "score": 0}


def test_bad_json_is_an_error(graph):
    engine, g = graph
    raw = engine._store._call_classmethod("Graph.KG.FHIRGraph", "GroupByPatient", g, "not json", "", "5", "50")
    assert json.loads(str(raw))["status"] == "error"


# GroupPPR walks and groups in one call so the scores never become a string: at ~100
# Synthea patients the uncapped scores JSON passed IRIS's string limit (spec 235, R17).
EXCL = json.dumps(["in_patient_compartment"])


def _call(graph, method, *args):
    engine, _ = graph
    return engine._store._call_classmethod("Graph.KG.PageRank" if method == "RunJson" else "Graph.KG.FHIRGraph", method, *args)


@pytest.mark.parametrize("via", ["", json.dumps(["subject"])])
def test_group_ppr_equals_run_json_then_group(graph, via):
    _, g = graph
    seeds = json.dumps(["Practitioner/d1", "Observation/o2"])
    ranked = json.loads(str(_call(graph, "RunJson", seeds, "0.85", "20", "1", "1.0", g, EXCL, "0")))
    scores = {r["id"]: r["score"] for r in ranked if r["score"] > 0}
    assert scores
    want = json.loads(str(_call(graph, "GroupByPatient", g, json.dumps(scores), via, "3", "0")))
    got = json.loads(str(_call(graph, "GroupPPR", g, seeds, "0.85", "20", EXCL, via, "3", "0")))
    assert got["status"] == "ok", got
    assert [p["patient"] for p in got["patients"]] == [p["patient"] for p in want["patients"]]
    for a, b in zip(got["patients"], want["patients"]):
        assert abs(a["score"] - b["score"]) < 1e-12
        assert a["contributors_total"] == b["contributors_total"]
        assert [c["key"] for c in a["contributors"]] == [c["key"] for c in b["contributors"]]
    assert got["unattributed"]["count"] == want["unattributed"]["count"]
    assert abs(got["unattributed"]["score"] - want["unattributed"]["score"]) < 1e-12


def test_group_ppr_no_seeds(graph):
    _, g = graph
    got = json.loads(str(_call(graph, "GroupPPR", g, "[]", "0.85", "20", EXCL, "", "5", "50")))
    assert got == {"status": "ok", "patients": [], "unattributed": {"count": 0, "score": 0}}


def test_group_ppr_bad_exclusion_is_an_error(graph):
    _, g = graph
    got = json.loads(str(_call(graph, "GroupPPR", g, '["Observation/o1"]', "0.85", "20", '["Type."]', "", "5", "50")))
    assert got["status"] == "error"
