"""Spec 235 US4: the coverage and gaps oracles in tests/e2e/interp_fixture.py, on a
synthetic resource list. Pure functions; no IRIS."""

from __future__ import annotations

from tests.e2e import interp_fixture as ifx

OBS_CAT = "http://terminology.hl7.org/CodeSystem/observation-category"
LOINC = "http://loinc.org"
SCT = "http://snomed.info/sct"
COMP = {"Observation": ["subject", "performer"], "Encounter": ["patient"]}
PATHS = {("Observation", "subject"): ["subject"], ("Encounter", "patient"): ["subject"]}
FIELDS = {
    "Observation": {"code": [("code",)], "value-concept": [("valueCodeableConcept",)]},
    "Condition": {"code": [("code",)]},
}


def _cc(*codes, text=None):
    out = {"coding": [{"system": s, "code": c} for s, c in codes]}
    if text:
        out["text"] = text
    return out


def _obs(i, patient=None, **extra):
    r = {"resourceType": "Observation", "id": f"o{i}", **extra}
    if patient:
        r["subject"] = {"reference": patient}
    return r


def _resources():
    return [
        {"resourceType": "Patient", "id": "a", "link": [{"other": {"reference": "Patient/b"}, "type": "seealso"}]},
        {"resourceType": "Patient", "id": "b"},
        {"resourceType": "Patient", "id": "c"},
        _obs(1, "Patient/a", code=_cc((LOINC, "1"), (SCT, "9")), category=[_cc((OBS_CAT, "vital-signs"))],
             meta={"profile": ["http://p/vs"]}),
        _obs(2, "Patient/b", code=_cc((LOINC, "1")), category=[_cc(("http://local", "x"))]),
        _obs(3, code=_cc((LOINC, "2")), valueCodeableConcept=_cc((SCT, "9")), meta={"profile": ["http://p/vs"]}),
        _obs(4, "Patient/a", code={"text": "free text"}),
        _obs(5, "Patient/a"),
        {"resourceType": "Condition", "id": "c1", "code": _cc((SCT, "7"))},
        {"resourceType": "Medication", "id": "m1", "code": _cc(("rx", "5"))},
    ]


class TestCoverage:
    def test_types(self):
        rep = ifx.expected_coverage(_resources(), COMP, PATHS, FIELDS, set())
        obs = rep["types"]["Observation"]
        assert obs["count"] == 5
        assert obs["compartment_type"] is True
        assert obs["with_compartment"] == 4
        assert obs["patientless"] == 1
        assert obs["patientless_sample"] == ["Observation/o3"]
        assert obs["meta_profile"] == {"http://p/vs": 2}
        assert obs["category"] == {"label": 1, "local": 1, "none": 3}
        # Types with no compartment params, and Patient, carry no compartment figures.
        for t in ("Medication", "Condition", "Patient"):
            assert rep["types"][t]["compartment_type"] is False
            assert "with_compartment" not in rep["types"][t]
        assert rep["types"]["Patient"]["count"] == 3

    def test_patientless_sample_sorted_capped(self):
        rs = [_obs(i) for i in range(30, 0, -1)]
        obs = ifx.expected_coverage(rs, COMP, PATHS, FIELDS, set())["types"]["Observation"]
        assert obs["patientless"] == 30
        assert obs["patientless_sample"] == sorted(f"Observation/o{i}" for i in range(1, 31))[:20]

    def test_linked_patients(self):
        assert ifx.expected_coverage(_resources(), COMP, PATHS, FIELDS, set())["linked_patients"] == 2

    def test_resolution(self):
        rep = ifx.expected_coverage(_resources(), COMP, PATHS, FIELDS, {(SCT, "9"), (LOINC, "2")})
        assert rep["resolution"]["by_system"] == {
            LOINC: {"codes": 2, "resources": 3, "resolved_codes": 1, "resolved_resources": 1},
            SCT: {"codes": 2, "resources": 3, "resolved_codes": 1, "resolved_resources": 2},
        }


class TestGaps:
    def test_unmatched_sorted(self):
        rep = ifx.expected_gaps(_resources(), ["code", "value-concept"], FIELDS, {(LOINC, "2")})
        assert rep["unmatched"] == [
            {"system": LOINC, "code": "1", "resources": 2},
            {"system": SCT, "code": "9", "resources": 2},
            {"system": SCT, "code": "7", "resources": 1},
        ]
        assert rep["unmatched_total"] == {"codes": 3, "resources": 4}

    def test_top(self):
        rep = ifx.expected_gaps(_resources(), ["code"], FIELDS, set(), top=1)
        assert rep["unmatched"] == [{"system": LOINC, "code": "1", "resources": 2}]
        assert rep["unmatched_total"] == {"codes": 4, "resources": 4}

    def test_text_only(self):
        """A `code` with text and no coding counts; an absent `code` does not."""
        rep = ifx.expected_gaps(_resources(), ["code", "value-concept"], FIELDS, set())
        assert rep["text_only"] == {"code": 1, "value-concept": 0}

    def test_text_with_coding_not_text_only(self):
        rs = [_obs(1, code=_cc((LOINC, "1"), text="t"))]
        assert ifx.expected_gaps(rs, ["code"], FIELDS, set())["text_only"] == {"code": 0}

    def test_type_without_param_ignored(self):
        rs = [{"resourceType": "Medication", "id": "m", "code": {"text": "x"}}]
        rep = ifx.expected_gaps(rs, ["code"], FIELDS, set())
        assert rep["text_only"] == {"code": 0} and rep["unmatched"] == []
