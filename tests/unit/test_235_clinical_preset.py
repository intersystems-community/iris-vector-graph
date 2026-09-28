"""Spec 235 US3: `fhir_resolve_concepts(params="clinical", detail=...)`.

`Graph.KG.FHIRGraph.IndexedTokenParams` and `ResolveConcepts` are mocked; the live
behaviour is in tests/e2e/test_235_clinical_resolve_e2e.py.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph import CLINICAL_PARAMS as EXPORTED
from iris_vector_graph._engine.fhir_graph import CLINICAL_PARAMS
from iris_vector_graph.engine import IRISGraphEngine

G = "fhir:IVGFHIR:X0001"
CG = "umls"
FOUR = ["code", "value-concept", "component-code", "component-value-concept"]
RESOLVED = {
    "status": "ok",
    "keys": ["Observation/o1", "MedicationRequest/m7"],
    "via": {"MedicationRequest/m7": "medication"},
    "medication_hop": {"medications": 1, "added": 1},
}


def _replies(indexed=None, resolved=RESOLVED):
    """classMethodValue answering by operation name, recording each call."""

    def answer(cls, op, *args):
        if op == "IndexedTokenParams":
            return json.dumps({"status": "ok", **(indexed or {"used": FOUR, "dropped": []})})
        if op == "ResolveConcepts":
            return json.dumps(resolved)
        raise AssertionError(op)

    return answer


@pytest.fixture
def eng():
    conn = MagicMock()
    conn.cursor.return_value.fetchall.return_value = []
    engine = IRISGraphEngine(conn, embedding_dimension=4)
    native = MagicMock()
    native.classMethodValue.side_effect = _replies()
    with patch.object(engine, "_iris_obj", return_value=native):
        yield engine, native


def _ops(native):
    return [c.args[1] for c in native.classMethodValue.call_args_list]


def _call(native, op):
    return next(c.args for c in native.classMethodValue.call_args_list if c.args[1] == op)


def test_constant():
    assert CLINICAL_PARAMS == tuple(FOUR)
    assert EXPORTED is CLINICAL_PARAMS


class TestDefault:
    def test_none_is_code_without_index_probe(self, eng):
        """`""` is `["code"]` in ResolveConcepts, as before spec 235."""
        engine, native = eng
        out = engine.fhir_resolve_concepts(G, CG, ["C1"])
        assert out == RESOLVED["keys"]
        assert _ops(native) == ["ResolveConcepts"]
        assert _call(native, "ResolveConcepts")[5] == ""

    def test_list_is_sent_as_given_without_index_probe(self, eng):
        engine, native = eng
        engine.fhir_resolve_concepts(G, CG, ["C1"], params=["code", "category"])
        assert _ops(native) == ["ResolveConcepts"]
        assert json.loads(_call(native, "ResolveConcepts")[5]) == ["code", "category"]

    def test_none_detail(self, eng):
        engine, _ = eng
        out = engine.fhir_resolve_concepts(G, CG, ["C1"], detail=True)
        assert out == {
            "keys": RESOLVED["keys"],
            "params_used": ["code"],
            "dropped_params": [],
            "via": RESOLVED["via"],
            "medication_hop": RESOLVED["medication_hop"],
        }


class TestClinical:
    def test_expands_probes_and_sends_used(self, eng):
        engine, native = eng
        native.classMethodValue.side_effect = _replies(
            {"used": ["code", "value-concept"], "dropped": ["component-code", "component-value-concept"]}
        )
        out = engine.fhir_resolve_concepts(G, CG, ["C1"], params="clinical")
        assert out == RESOLVED["keys"]
        assert _ops(native) == ["IndexedTokenParams", "ResolveConcepts"]
        probe = _call(native, "IndexedTokenParams")
        assert probe[2] == G and json.loads(probe[3]) == FOUR
        assert json.loads(_call(native, "ResolveConcepts")[5]) == ["code", "value-concept"]

    def test_detail(self, eng):
        engine, native = eng
        native.classMethodValue.side_effect = _replies(
            {"used": ["code", "value-concept"], "dropped": ["component-code", "component-value-concept"]}
        )
        out = engine.fhir_resolve_concepts(G, CG, ["C1"], params="clinical", detail=True)
        assert out == {
            "keys": ["Observation/o1", "MedicationRequest/m7"],
            "params_used": ["code", "value-concept"],
            "dropped_params": ["component-code", "component-value-concept"],
            "via": {"MedicationRequest/m7": "medication"},
            "medication_hop": {"medications": 1, "added": 1},
        }

    def test_all_dropped_is_empty_without_resolve(self, eng):
        engine, native = eng
        native.classMethodValue.side_effect = _replies({"used": [], "dropped": FOUR})
        assert engine.fhir_resolve_concepts(G, CG, ["C1"], params="clinical") == []
        out = engine.fhir_resolve_concepts(G, CG, ["C1"], params="clinical", detail=True)
        assert out["keys"] == [] and out["params_used"] == [] and out["dropped_params"] == FOUR
        assert out["medication_hop"] == {"medications": 0, "added": 0}
        assert "ResolveConcepts" not in _ops(native)

    def test_no_ids_no_io(self, eng):
        engine, native = eng
        assert engine.fhir_resolve_concepts(G, CG, [], params="clinical") == []
        assert engine.fhir_resolve_concepts(G, CG, [], params="clinical", detail=True)["keys"] == []
        native.classMethodValue.assert_not_called()

    def test_reply_without_hop_fields(self, eng):
        """A pre-235-shaped reply (no via, no medication_hop) still gives the dict."""
        engine, native = eng
        native.classMethodValue.side_effect = _replies(resolved={"status": "ok", "keys": ["Condition/c1"]})
        out = engine.fhir_resolve_concepts(G, CG, ["C1"], detail=True)
        assert out["via"] == {} and out["medication_hop"] == {"medications": 0, "added": 0}


class TestErrors:
    @pytest.mark.parametrize("bad", ["Clinical", "code", "", "all"])
    def test_unknown_preset_raises_before_io(self, eng, bad):
        engine, native = eng
        with pytest.raises(ValueError):
            engine.fhir_resolve_concepts(G, CG, ["C1"], params=bad)
        native.classMethodValue.assert_not_called()


class TestConceptPPR:
    def test_clinical_reaches_resolve_and_seeds_are_keys(self, eng):
        engine, native = eng
        with (
            patch.object(engine, "fhir_expand_concepts", return_value=["C1"]),
            patch.object(engine, "kg_PERSONALIZED_PAGERANK", return_value={"Observation/o1": 1.0}) as ppr,
        ):
            engine.fhir_concept_ppr(G, CG, ["C1"], params="clinical")
        assert ppr.call_args.args[0] == RESOLVED["keys"]

    def test_unknown_preset_raises_before_io(self, eng):
        engine, native = eng
        with patch.object(engine, "fhir_expand_concepts") as expand:
            with pytest.raises(ValueError):
                engine.fhir_concept_ppr(G, CG, ["C1"], params="nope")
            expand.assert_not_called()
        native.classMethodValue.assert_not_called()
