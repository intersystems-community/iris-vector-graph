"""Spec 233: the per-run id prefix rule shared by the CPG smoke and the genomics E2E."""

from __future__ import annotations

import copy

from tests.e2e.fhir_conftest import prefix_resources


def _obs(rid, **extra):
    return {"resourceType": "Observation", "id": rid, **extra}


def test_ids_are_prefixed():
    out = prefix_resources([_obs("a"), {"resourceType": "Patient", "id": "p"}], "t1")
    assert [r["id"] for r in out] == ["t1-a", "t1-p"]


def test_references_rewritten_on_every_terminator():
    res = [
        {"resourceType": "Patient", "id": "p"},
        _obs(
            "a",
            subject={"reference": "Patient/p"},
            note=[{"text": "see Patient/p|v1"}, {"text": "Patient/p/_history/1"}, {"text": "Patient/p#x"}],
        ),
    ]
    out = prefix_resources(res, "t1")
    obs = out[1]
    assert obs["subject"]["reference"] == "Patient/t1-p"
    assert [n["text"] for n in obs["note"]] == [
        "see Patient/t1-p|v1",
        "Patient/t1-p/_history/1",
        "Patient/t1-p#x",
    ]


def test_id_that_prefixes_another_id():
    res = [
        _obs("a"),
        _obs("ab"),
        _obs("c", hasMember=[{"reference": "Observation/a"}, {"reference": "Observation/ab"}]),
    ]
    out = prefix_resources(res, "t1")
    assert [m["reference"] for m in out[2]["hasMember"]] == ["Observation/t1-a", "Observation/t1-ab"]


def test_unknown_key_left_alone():
    out = prefix_resources([_obs("a", subject={"reference": "Patient/elsewhere"})], "t1")
    assert out[0]["subject"]["reference"] == "Patient/elsewhere"


def test_contained_reference_unchanged():
    res = [_obs("a", contained=[{"resourceType": "Specimen", "id": "s"}], specimen={"reference": "#s"})]
    out = prefix_resources(res, "t1")
    assert out[0]["specimen"]["reference"] == "#s"
    assert out[0]["contained"][0]["id"] == "s"


def test_input_not_mutated():
    res = [{"resourceType": "Patient", "id": "p"}, _obs("a", subject={"reference": "Patient/p"})]
    before = copy.deepcopy(res)
    prefix_resources(res, "t1")
    assert res == before
