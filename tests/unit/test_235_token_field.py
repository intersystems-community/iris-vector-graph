"""Spec 235 US4: `_token_field` and `_token_fields`, the SearchColumn FHIRPath to
element path helpers used for text-only gap detection (research R11)."""

from __future__ import annotations

import pytest

from iris_vector_graph._engine.fhir_graph import _token_field, _token_fields


@pytest.mark.parametrize(
    "fhirpath, want",
    [
        ("Observation.code", ("Observation", ("code",))),
        ("(Observation.value as CodeableConcept)", ("Observation", ("valueCodeableConcept",))),
        ("Observation.value as CodeableConcept", ("Observation", ("valueCodeableConcept",))),
        ("Observation.component.code", ("Observation", ("component", "code"))),
        (
            "(Observation.component.value as CodeableConcept)",
            ("Observation", ("component", "valueCodeableConcept")),
        ),
        (
            "Observation.component.value as CodeableConcept",
            ("Observation", ("component", "valueCodeableConcept")),
        ),
        (
            "MedicationRequest.medication as CodeableConcept",
            ("MedicationRequest", ("medicationCodeableConcept",)),
        ),
        ("FamilyMemberHistory.condition.code", ("FamilyMemberHistory", ("condition", "code"))),
        ("  Condition.code  ", ("Condition", ("code",))),
    ],
)
def test_token_field(fhirpath, want):
    assert _token_field(fhirpath) == want


@pytest.mark.parametrize(
    "fhirpath",
    [
        "",
        "code",
        "Observation",
        "Observation.code.where(system='x')",
        "Observation.value.ofType(CodeableConcept)",
        "Observation.value as Quantity",
        "Observation.code | Condition.code",
        "observation.code",
    ],
)
def test_token_field_rejects(fhirpath):
    with pytest.raises(ValueError):
        _token_field(fhirpath)


class TestTokenFields:
    def test_union_same_type(self):
        fp = "AllergyIntolerance.code|AllergyIntolerance.reaction.substance"
        assert _token_fields(fp, "AllergyIntolerance") == ([("code",), ("reaction", "substance")], [])

    def test_union_other_type_dropped(self):
        fp = "Condition.code | Observation.code | Procedure.code"
        assert _token_fields(fp, "Observation") == ([("code",)], [])

    def test_unsupported_part_reported(self):
        fp = "Substance.code|Substance.ingredient.substance as CodeableConcept|Substance.x.where(y)"
        fields, skipped = _token_fields(fp, "Substance")
        assert fields == [("code",), ("ingredient", "substanceCodeableConcept")]
        assert skipped == ["Substance.x.where(y)"]

    def test_deduplicated(self):
        assert _token_fields("Observation.code|(Observation.code)", "Observation") == ([("code",)], [])

    def test_empty(self):
        assert _token_fields("", "Observation") == ([], [])
