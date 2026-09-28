"""Spec 235 US1: the compartment oracles in tests/e2e/interp_fixture.py. No IRIS."""

from __future__ import annotations

import pytest

from tests.e2e import interp_fixture as ifx

COMP = {
    "Observation": {"subject", "performer"},
    "Encounter": {"patient", "subject"},
    "Provenance": {"patient"},
    "Patient": {"link"},
    "MedicationRequest": {"subject"},
}
PATHS = {
    ("Observation", "subject"): ["subject"],
    ("Observation", "performer"): ["performer"],
    ("Encounter", "patient"): ["subject"],
    ("Encounter", "subject"): ["subject"],
    ("Provenance", "patient"): ["target"],
    ("Patient", "link"): ["link.other"],
    ("MedicationRequest", "subject"): ["subject"],
}


def _ref(key):
    return {"reference": key}


def _patients(*ids):
    return [{"resourceType": "Patient", "id": i} for i in ids]


def _expected(resources):
    return ifx.expected_compartments(resources, COMP, PATHS)


class TestExpectedCompartments:
    def test_two_params_same_patient_one_entry(self):
        obs = {"resourceType": "Observation", "id": "o1", "subject": _ref("Patient/p1"), "performer": [_ref("Patient/p1")]}
        assert _expected(_patients("p1") + [obs]) == {"Observation/o1": {"Patient/p1": ["performer", "subject"]}}

    def test_different_patients_two_entries(self):
        obs = {"resourceType": "Observation", "id": "o1", "subject": _ref("Patient/p1"), "performer": [_ref("Patient/p2")]}
        assert _expected(_patients("p1", "p2") + [obs]) == {
            "Observation/o1": {"Patient/p1": ["subject"], "Patient/p2": ["performer"]}
        }

    def test_group_subject_gives_none(self):
        obs = {"resourceType": "Observation", "id": "o1", "subject": _ref("Group/g1")}
        group = {"resourceType": "Group", "id": "g1"}
        assert _expected([group, obs]) == {}

    def test_non_patient_performer_ignored(self):
        doc = {"resourceType": "Practitioner", "id": "d1"}
        obs = {"resourceType": "Observation", "id": "o1", "subject": _ref("Patient/p1"), "performer": [_ref("Practitioner/d1")]}
        assert _expected(_patients("p1") + [doc, obs]) == {"Observation/o1": {"Patient/p1": ["subject"]}}

    def test_patient_resource_skipped(self):
        pa, pb = _patients("pa", "pb")
        pa["link"] = [{"other": _ref("Patient/pb"), "type": "seealso"}]
        assert _expected([pa, pb]) == {}

    def test_patient_outside_set_gives_none(self):
        obs = {"resourceType": "Observation", "id": "o1", "subject": _ref("Patient/elsewhere")}
        assert _expected(_patients("p1") + [obs]) == {}

    def test_provenance_target(self):
        prov = {
            "resourceType": "Provenance",
            "id": "v1",
            "target": [_ref("Patient/p1"), _ref("Observation/o1")],
        }
        obs = {"resourceType": "Observation", "id": "o1", "subject": _ref("Patient/p1")}
        got = _expected(_patients("p1") + [obs, prov])
        assert got["Provenance/v1"] == {"Patient/p1": ["patient"]}

    def test_encounter_patient_and_subject(self):
        enc = {"resourceType": "Encounter", "id": "e1", "subject": _ref("Patient/p1")}
        assert _expected(_patients("p1") + [enc]) == {"Encounter/e1": {"Patient/p1": ["patient", "subject"]}}

    def test_type_without_compartment_params(self):
        org = {"resourceType": "Organization", "id": "x1", "partOf": _ref("Patient/p1")}
        assert _expected(_patients("p1") + [org]) == {}

    def test_nested_path_through_lists(self):
        paths = {("CareTeam", "participant"): ["participant.member"]}
        team = {
            "resourceType": "CareTeam",
            "id": "t1",
            "participant": [{"member": _ref("Practitioner/d1")}, {"member": _ref("Patient/p1")}],
        }
        got = ifx.expected_compartments(_patients("p1") + [team], {"CareTeam": {"participant"}}, paths)
        assert got == {"CareTeam/t1": {"Patient/p1": ["participant"]}}


class TestReferencePaths:
    @pytest.mark.parametrize(
        "fhirpath, want",
        [
            ("Observation.subject", [("Observation", "subject", None)]),
            ("CarePlan.activity.detail.performer", [("CarePlan", "activity.detail.performer", None)]),
            ("MedicationRequest.medication as Reference", [("MedicationRequest", "medicationReference", None)]),
            ("(MedicationRequest.medication as Reference)", [("MedicationRequest", "medicationReference", None)]),
            ("Provenance.target.where(resolve() is Patient)", [("Provenance", "target", "Patient")]),
            (
                "Composition.relatesTo.target as Reference",
                [("Composition", "relatesTo.targetReference", None)],
            ),
            (
                "AllergyIntolerance.patient | CarePlan.subject.where(resolve() is Patient) | (MedicationRequest.medication as Reference)",
                [
                    ("AllergyIntolerance", "patient", None),
                    ("CarePlan", "subject", "Patient"),
                    ("MedicationRequest", "medicationReference", None),
                ],
            ),
        ],
    )
    def test_forms(self, fhirpath, want):
        assert ifx.reference_paths(fhirpath) == want

    def test_unknown_form_raises(self):
        with pytest.raises(ValueError, match="ofType"):
            ifx.reference_paths("Observation.value.ofType(Reference)")


def test_compartment_paths_filters_type_and_target():
    rows = [
        ("Observation", "subject", "Observation.subject"),
        ("Encounter", "patient", "Encounter.subject.where(resolve() is Patient)"),
        ("Appointment", "actor", "Appointment.participant.actor"),
        ("Appointment", "location", "Appointment.participant.actor.where(resolve() is Location)"),
        ("Observation", "encounter", "Observation.encounter"),
    ]
    comp = {"Observation": {"subject"}, "Encounter": {"patient"}, "Appointment": {"actor", "location"}}
    assert ifx.compartment_paths(rows, comp) == {
        ("Observation", "subject"): ["subject"],
        ("Encounter", "patient"): ["subject"],
        ("Appointment", "actor"): ["participant.actor"],
    }
