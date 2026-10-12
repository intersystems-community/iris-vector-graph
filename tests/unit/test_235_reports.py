"""Spec 235 US4: `fhir_coverage_report` and `fhir_concept_gaps` with the class methods
mocked. The live figures are in tests/e2e/test_235_coverage_gaps_e2e.py."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.engine import IRISGraphEngine

G = "fhir:IVGFHIR:X0001"
FOUR = ["code", "value-concept", "component-code", "component-value-concept"]
LINKS = {"status": "ok", "graph_id": G, "rows": [{"source": "Library/a", "status": "Library/b"}]}
COVERAGE = {
    "status": "ok",
    "graph": G,
    "interpretation_version": 1,
    "interpretation_current": 1,
    "stale": False,
    "types": {
        "Observation": {
            "count": 3,
            "compartment_type": True,
            "with_compartment": 2,
            "compartment_share": 0.6667,
            "patientless": 1,
            "patientless_sample": ["Observation/x"],
            "meta_profile": {},
            "category": {"label": 2, "local": 0, "none": 1},
        },
        "Medication": {"count": 1, "compartment_type": False, "meta_profile": {}, "category": {"label": 0, "local": 0, "none": 1}},
    },
    "resolution": {"by_system": {"http://loinc.org": {"codes": 2, "resources": 3, "resolved_codes": 1, "resolved_resources": 1}}},
    "linked_patients": 2,
}
PATHS = {
    "status": "ok",
    "paths": {
        "Observation": {"code": "Observation.code", "value-concept": "(Observation.value as CodeableConcept)"},
        "AllergyIntolerance": {"code": "AllergyIntolerance.code|AllergyIntolerance.reaction.substance"},
        "Substance": {"code": "Substance.code|Substance.x.where(y)"},
    },
}
GAPS = {
    "status": "ok",
    "unmatched": [{"system": "http://snomed.info/sct", "code": "1", "resources": 4}],
    "unmatched_total": {"codes": 1, "resources": 4},
    "text_only": {"code": 0},
}


def _replies(indexed=None):
    def answer(cls, op, *args):
        return json.dumps(
            {
                "IndexedTokenParams": {"status": "ok", **(indexed or {"used": FOUR, "dropped": []})},
                "CoverageReport": COVERAGE,
                "LinkReport": LINKS,
                "TokenPaths": PATHS,
                "ConceptGaps": GAPS,
            }[op]
        )

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


def _call(native, op):
    return next(c.args for c in native.classMethodValue.call_args_list if c.args[1] == op)


class TestCoverage:
    def test_shape(self, eng):
        engine, _ = eng
        rep = engine.fhir_coverage_report(G)
        assert set(rep) == {
            "graph",
            "interpretation_version",
            "interpretation_current",
            "stale",
            "types",
            "resolution",
            "linked_patients",
            "link_report",
        }
        assert rep["types"] == COVERAGE["types"]
        assert rep["resolution"]["params_used"] == FOUR
        assert rep["resolution"]["dropped_params"] == []
        assert rep["resolution"]["by_system"] == COVERAGE["resolution"]["by_system"]

    def test_link_report_unchanged(self, eng):
        engine, _ = eng
        assert engine.fhir_coverage_report(G)["link_report"] == engine.fhir_link_report(G)

    def test_clinical_params_passed(self, eng):
        engine, native = eng
        native.classMethodValue.side_effect = _replies({"used": ["code"], "dropped": FOUR[1:]})
        rep = engine.fhir_coverage_report(G)
        assert json.loads(_call(native, "CoverageReport")[3]) == ["code"]
        assert rep["resolution"]["dropped_params"] == FOUR[1:]

    def test_id_prefix(self, eng):
        engine, native = eng
        engine.fhir_coverage_report(G, id_prefix="t0a1b2c3d")
        assert _call(native, "CoverageReport")[4] == "t0a1b2c3d"

    @pytest.mark.parametrize("bad", ["", "a/b", "x y", 3])
    def test_bad_prefix(self, eng, bad):
        engine, native = eng
        with pytest.raises(ValueError):
            engine.fhir_coverage_report(G, id_prefix=bad)
        native.classMethodValue.assert_not_called()

    def test_default_graph_rejected(self, eng):
        engine, _ = eng
        with pytest.raises(ValueError):
            engine.fhir_coverage_report(None)


class TestGaps:
    def test_shape(self, eng):
        engine, _ = eng
        rep = engine.fhir_concept_gaps(G)
        assert rep == {
            "graph": G,
            "params_used": ["code"],
            "dropped_params": [],
            "unmatched": GAPS["unmatched"],
            "unmatched_total": GAPS["unmatched_total"],
            "text_only": GAPS["text_only"],
            "skipped_paths": ["Substance.x.where(y)"],
        }

    def test_fields_passed(self, eng):
        engine, native = eng
        engine.fhir_concept_gaps(G, params=["code", "value-concept"], top=5)
        args = _call(native, "ConceptGaps")
        assert json.loads(args[3]) == ["code", "value-concept"]
        assert json.loads(args[4]) == {
            "Observation": {"code": [["code"]], "value-concept": [["valueCodeableConcept"]]},
            "AllergyIntolerance": {"code": [["code"], ["reaction", "substance"]]},
            "Substance": {"code": [["code"]]},
        }
        assert args[5] == 5
        assert json.loads(_call(native, "TokenPaths")[3]) == ["code", "value-concept"]

    def test_clinical(self, eng):
        engine, native = eng
        native.classMethodValue.side_effect = _replies({"used": FOUR[:2], "dropped": FOUR[2:]})
        rep = engine.fhir_concept_gaps(G, params="clinical")
        assert rep["params_used"] == FOUR[:2]
        assert rep["dropped_params"] == FOUR[2:]
        assert json.loads(_call(native, "ConceptGaps")[3]) == FOUR[:2]

    def test_clinical_nothing_indexed(self, eng):
        engine, native = eng
        native.classMethodValue.side_effect = _replies({"used": [], "dropped": FOUR})
        rep = engine.fhir_concept_gaps(G, params="clinical")
        assert rep["unmatched"] == [] and rep["text_only"] == {}
        assert "ConceptGaps" not in [c.args[1] for c in native.classMethodValue.call_args_list]

    @pytest.mark.parametrize("top", [0, -1, True, 1.5, "20"])
    def test_bad_top(self, eng, top):
        engine, native = eng
        with pytest.raises(ValueError):
            engine.fhir_concept_gaps(G, top=top)
        native.classMethodValue.assert_not_called()

    def test_bad_params(self, eng):
        engine, _ = eng
        with pytest.raises(ValueError):
            engine.fhir_concept_gaps(G, params="code")

    def test_id_prefix(self, eng):
        engine, native = eng
        engine.fhir_concept_gaps(G, id_prefix="t0a1b2c3d")
        assert _call(native, "ConceptGaps")[6] == "t0a1b2c3d"
