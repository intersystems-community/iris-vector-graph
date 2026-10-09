"""FHIRGraph methods that read only ^KG: ExpandConcepts and the error paths of GroupByPatient.

Runs against USER with a throwaway named graph written through engine.create_edge, like
test_235_group_by_patient. No FHIR repository is needed.
"""

from __future__ import annotations

import json
import uuid

import pytest

from iris_vector_graph.engine import IRISGraphEngine

FG = "Graph.KG.FHIRGraph"


@pytest.fixture
def cg(iris_connection):
    engine = IRISGraphEngine(iris_connection)
    g = f"t_fg:{uuid.uuid4().hex[:8]}"
    for n in ("C1", "C2", "C3", "C4"):
        engine.create_node(n, graph=g)
    engine.create_edge("C1", "subClassOf", "C2", graph=g)
    engine.create_edge("C2", "subClassOf", "C3", graph=g)
    engine.create_edge("C2", "related", "C4", graph=g)

    class Handle:
        graph = g

        def call(self, method, *args):
            out = engine._store._iris_obj().classMethodValue(FG, method, *args)
            return json.loads(str(out))

    try:
        yield Handle()
    finally:
        engine.erase_graph(g)


def expand(cg, ids, preds="", hops=1, direction="in"):
    return cg.call("ExpandConcepts", cg.graph, json.dumps(ids), preds, hops, direction)


def test_expand_in_follows_object_to_subject(cg):
    assert expand(cg, ["C3"], hops=2) == {"status": "ok", "ids": ["C3", "C2", "C1"]}
    assert expand(cg, ["C3"], hops=1)["ids"] == ["C3", "C2"]


def test_expand_out_follows_subject_to_object(cg):
    assert expand(cg, ["C1"], hops=2, direction="out")["ids"] == ["C1", "C2", "C4", "C3"]  # predicates in subscript order: related, subClassOf


def test_expand_both_walks_each_tree(cg):
    assert expand(cg, ["C2"], hops=1, direction="both")["ids"] == ["C2", "C4", "C3", "C1"]


def test_expand_predicate_filter_and_seed_dedupe(cg):
    only = expand(cg, ["C2"], json.dumps(["related"]), 1, "out")
    assert only["ids"] == ["C2", "C4"]
    assert expand(cg, ["C2", "C2"], json.dumps(["nope"]), 3, "out")["ids"] == ["C2"]
    # an unknown seed expands to itself and then stops early
    assert expand(cg, ["ghost"], hops=5)["ids"] == ["ghost"]


def test_expand_rejects_a_bad_direction_and_bad_json(cg):
    bad = expand(cg, ["C1"], direction="sideways")
    assert bad["status"] == "error" and "sideways" in bad["error"]
    assert cg.call("ExpandConcepts", cg.graph, "{not json")["status"] == "error"


def test_group_by_patient_rejects_scores_that_are_not_an_object(cg):
    out = cg.call("GroupByPatient", cg.graph, "[1, 2]")
    assert out == {"status": "error", "error": "scores must be a JSON object"}


def test_group_by_patient_reports_unparseable_scores(cg):
    assert cg.call("GroupByPatient", cg.graph, "{broken")["status"] == "error"
