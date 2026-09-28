"""Spec 235 US2: `fhir_concept_ppr` exclusions and `group_by="patient"`.

Expansion, resolution, PPR and `Graph.KG.FHIRGraph.GroupPPR` are mocked; the live
behaviour is in tests/e2e/test_235_patient_ranking_e2e.py.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph import FHIR_PPR_EXCLUDE as EXPORTED
from iris_vector_graph._engine.fhir_graph import FHIR_PPR_EXCLUDE
from iris_vector_graph.engine import IRISGraphEngine

G = "fhir:IVGFHIR:X0001"
CG = "umls"
SCORES = {"Observation/o1": 0.3, "Patient/p1": 0.2}
GROUPED = {
    "patients": [
        {
            "patient": "Patient/p1",
            "score": 0.3,
            "contributors_total": 1,
            "contributors": [{"key": "Observation/o1", "score": 0.3, "via": ["subject"]}],
        }
    ],
    "unattributed": {"count": 1, "score": 0.2},
}


@pytest.fixture
def eng():
    conn = MagicMock()
    conn.cursor.return_value.fetchall.return_value = []
    engine = IRISGraphEngine(conn, embedding_dimension=4)
    native = MagicMock()
    native.classMethodValue.return_value = json.dumps({"status": "ok", **GROUPED})
    with (
        patch.object(engine, "_iris_obj", return_value=native),
        patch.object(engine, "fhir_expand_concepts", return_value=["C1"]),
        patch.object(engine, "fhir_resolve_concepts", return_value=["Condition/c1"]) as resolve,
        patch.object(engine, "kg_PERSONALIZED_PAGERANK", return_value=dict(SCORES)) as ppr,
    ):
        yield engine, native, ppr, resolve


def test_constant():
    assert FHIR_PPR_EXCLUDE == ("in_patient_compartment", "Provenance.target", "Provenance.entity")
    assert EXPORTED is FHIR_PPR_EXCLUDE


class TestDefault:
    def test_passes_default_exclusions_and_returns_scores(self, eng):
        engine, native, ppr, _ = eng
        out = engine.fhir_concept_ppr(G, CG, ["C1"], top_k=10)
        assert out == SCORES
        kw = ppr.call_args.kwargs
        assert kw["exclude_predicates"] == list(FHIR_PPR_EXCLUDE)
        assert kw["return_top_k"] == 10
        native.classMethodValue.assert_not_called()

    def test_empty_list_walks_every_edge(self, eng):
        engine, _, ppr, _ = eng
        engine.fhir_concept_ppr(G, CG, ["C1"], exclude_predicates=[])
        assert ppr.call_args.kwargs["exclude_predicates"] == []

    def test_custom_list(self, eng):
        engine, _, ppr, _ = eng
        engine.fhir_concept_ppr(G, CG, ["C1"], exclude_predicates=("subject", "Observation.encounter"))
        assert ppr.call_args.kwargs["exclude_predicates"] == ["subject", "Observation.encounter"]

    def test_malformed_exclusion_raises_before_io(self, eng):
        engine, _, ppr, resolve = eng
        with pytest.raises(ValueError):
            engine.fhir_concept_ppr(G, CG, ["C1"], exclude_predicates=["Type."])
        resolve.assert_not_called()
        ppr.assert_not_called()

    def test_clinical_params_forwarded(self, eng):
        engine, _, _, resolve = eng
        engine.fhir_concept_ppr(G, CG, ["C1"], params="clinical")
        assert resolve.call_args.kwargs["params"] == "clinical"


class TestGroupBy:
    def test_walks_and_groups_inside_iris(self, eng):
        # One GroupPPR call walks and groups, so no score list crosses the wire: every
        # score as JSON passed IRIS's string limit at ~100 patients (spec 235, R17).
        engine, native, ppr, _ = eng
        out = engine.fhir_concept_ppr(G, CG, ["C1"], group_by="patient", top_k=7)
        assert out == GROUPED
        ppr.assert_not_called()
        args = native.classMethodValue.call_args.args
        assert args[:3] == ("Graph.KG.FHIRGraph", "GroupPPR", G)
        assert json.loads(args[3]) == ["Condition/c1"]
        assert args[4:6] == (0.85, 20)
        assert json.loads(args[6]) == list(FHIR_PPR_EXCLUDE)
        assert args[7:] == ("", 5, 7)

    def test_via_explain_top_and_all(self, eng):
        engine, native, _, _ = eng
        engine.fhir_concept_ppr(
            G,
            CG,
            ["C1"],
            group_by="patient",
            via=["subject", "performer"],
            explain_top=2,
            top_k=None,
            damping_factor=0.5,
            max_iterations=3,
            exclude_predicates=[],
        )
        args = native.classMethodValue.call_args.args
        assert args[4:6] == (0.5, 3)
        assert json.loads(args[6]) == []
        assert json.loads(args[7]) == ["subject", "performer"]
        assert args[8:] == (2, 0)

    def test_no_exclusion_is_empty_string(self, eng):
        engine, native, _, _ = eng
        engine.fhir_concept_ppr(G, CG, ["C1"], group_by="patient", exclude_predicates=None)
        assert native.classMethodValue.call_args.args[6] == ""

    def test_nothing_resolves_is_empty_shape(self, eng):
        engine, native, ppr, resolve = eng
        resolve.return_value = []
        out = engine.fhir_concept_ppr(G, CG, ["C1"], group_by="patient")
        assert out == {"patients": [], "unattributed": {"count": 0, "score": 0.0}}
        ppr.assert_not_called()
        native.classMethodValue.assert_not_called()

    def test_status_key_not_returned(self, eng):
        engine, _, _, _ = eng
        assert "status" not in engine.fhir_concept_ppr(G, CG, ["C1"], group_by="patient")


class TestErrors:
    @pytest.mark.parametrize(
        "kw",
        [
            {"via": ["subject"]},
            {"explain_top": 3},
            {"group_by": "encounter"},
            {"group_by": "patient", "explain_top": -1},
            {"group_by": "patient", "explain_top": True},
            {"group_by": "patient", "via": ["subject;drop"]},
            {"group_by": "patient", "via": ["Subject"]},
            {"group_by": "patient", "via": [""]},
            {"group_by": "patient", "via": "subject"},
        ],
    )
    def test_raises_before_io(self, eng, kw):
        engine, native, ppr, resolve = eng
        with pytest.raises(ValueError):
            engine.fhir_concept_ppr(G, CG, ["C1"], **kw)
        resolve.assert_not_called()
        ppr.assert_not_called()
        native.classMethodValue.assert_not_called()

    def test_explain_top_zero_ok(self, eng):
        engine, native, _, _ = eng
        engine.fhir_concept_ppr(G, CG, ["C1"], group_by="patient", explain_top=0)
        assert native.classMethodValue.call_args.args[8] == 0
