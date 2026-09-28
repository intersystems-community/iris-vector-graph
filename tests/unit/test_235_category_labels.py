"""Spec 235 US5: the category / meta_profile oracles in tests/e2e/interp_fixture.py.

Pure functions over resource dicts; no IRIS.
"""

from __future__ import annotations

import pytest

from tests.e2e import interp_fixture as ifx

OBS = "http://terminology.hl7.org/CodeSystem/observation-category"
LOCAL = "http://example.org/local-category"
R4 = {"Observation", "Procedure", "Condition", "Patient"}


@pytest.mark.parametrize(
    "code, label",
    [
        ("vital-signs", "VitalSigns"),
        ("laboratory", "Laboratory"),
        ("encounter-diagnosis", "EncounterDiagnosis"),
        ("clinical-note", "ClinicalNote"),
        ("problem-list-item", "ProblemListItem"),
        ("social_history", "SocialHistory"),
    ],
)
def test_pascal(code, label):
    assert ifx.pascal(code) == label


def _obs(*codings, **extra):
    return {
        "resourceType": "Observation",
        "id": "o1",
        "category": [{"coding": list(codings)}] if codings else [],
        **extra,
    }


class TestExpectedLabels:
    def test_allowlisted_code_gets_label(self):
        assert ifx.expected_labels(_obs({"system": OBS, "code": "vital-signs"}), R4) == {"Observation", "VitalSigns"}

    def test_local_code_no_label(self):
        assert ifx.expected_labels(_obs({"system": LOCAL, "code": "vital-signs"}), R4) == {"Observation"}

    def test_type_name_collision_skipped(self):
        assert ifx.expected_labels(_obs({"system": OBS, "code": "procedure"}), R4) == {"Observation"}

    def test_type_label_always_present(self):
        assert ifx.expected_labels({"resourceType": "Patient", "id": "p"}, R4) == {"Patient"}

    def test_plain_code_category(self):
        """AllergyIntolerance.category is a `code`, no system: property only."""
        r = {"resourceType": "AllergyIntolerance", "id": "a", "category": ["food"]}
        assert ifx.expected_labels(r, R4) == {"AllergyIntolerance"}


class TestExpectedCategories:
    def test_sorted_deduplicated(self):
        r = {
            "resourceType": "Observation",
            "id": "o",
            "category": [
                {"coding": [{"system": OBS, "code": "vital-signs"}, {"system": OBS, "code": "laboratory"}]},
                {"coding": [{"system": OBS, "code": "vital-signs", "display": "Vital"}]},
            ],
        }
        assert ifx.expected_categories(r) == [f"{OBS}|laboratory", f"{OBS}|vital-signs"]

    def test_missing_system(self):
        assert ifx.expected_categories(_obs({"code": "x"})) == ["|x"]

    def test_plain_code(self):
        r = {"resourceType": "AllergyIntolerance", "id": "a", "category": ["food", "medication"]}
        assert ifx.expected_categories(r) == ["|food", "|medication"]

    def test_none(self):
        assert ifx.expected_categories({"resourceType": "Patient", "id": "p"}) == []

    def test_text_only_category_is_empty(self):
        assert ifx.expected_categories({"resourceType": "Observation", "id": "o", "category": [{"text": "x"}]}) == []


class TestExpectedProfiles:
    def test_sorted_version_kept(self):
        r = {"resourceType": "Patient", "id": "p", "meta": {"profile": ["http://b/p|1.0", "http://a/p"]}}
        assert ifx.expected_profiles(r) == ["http://a/p", "http://b/p|1.0"]

    def test_none(self):
        assert ifx.expected_profiles({"resourceType": "Patient", "id": "p"}) == []
