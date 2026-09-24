"""Spec 232 FR-010, FR-011: the json_links grammar, checked before the round trip.

The case table is shared with tests/e2e/test_232_fhir_link_rules_e2e.py, which runs
the same rows against Graph.KG.FHIRGraph.ParseJsonLinks, so the two parsers cannot
drift.
"""

from __future__ import annotations

import json
import os

import pytest

from iris_vector_graph.fhir_links import DEFAULT_EXTENSIONS, link_predicate, parse_json_links

_CASES = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures", "fhir_json_links_cases.json")
CASES = json.load(open(_CASES))


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_grammar(case):
    if case["ok"]:
        assert parse_json_links(case["input"]) == case["input"]
    else:
        with pytest.raises(ValueError) as err:
            parse_json_links(case["input"])
        assert case["error_contains"] in str(err.value)


def test_returns_a_copy():
    entries = ["PlanDefinition.library"]
    out = parse_json_links(entries)
    assert out == entries and out is not entries


def test_tuple_accepted():
    assert parse_json_links(("PlanDefinition.library",)) == ["PlanDefinition.library"]


@pytest.mark.parametrize(
    "entry,predicate",
    [
        ("PlanDefinition.library", "library"),
        ("ActivityDefinition.profile", "profile"),
        ("extension:http://hl7.org/fhir/StructureDefinition/cqf-library", "cqf-library"),
        ("extension:https://ex.org/a/b/my-link", "my-link"),
        ("extension:urn:oid:1.2.3.4", "1.2.3.4"),
    ],
)
def test_link_predicate(entry, predicate):
    assert link_predicate(entry) == predicate


def test_default_extensions():
    assert DEFAULT_EXTENSIONS == ("extension:http://hl7.org/fhir/StructureDefinition/cqf-library",)
    assert parse_json_links(list(DEFAULT_EXTENSIONS)) == list(DEFAULT_EXTENSIONS)
