"""FHIRGraph's canonical-link helpers: json_links validation, canonical splitting, resolution, extraction.

They take and return JSON strings and read no repository, so they run in USER. Expected values follow
the rules written on each method (research R4-R7 of spec 232), not what the code returned.
"""

from __future__ import annotations

import json

import pytest

from iris_vector_graph.engine import IRISGraphEngine

FG = "Graph.KG.FHIRGraph"


@pytest.fixture
def fg(iris_connection):
    obj = IRISGraphEngine(iris_connection, embedding_dimension=768)._store._iris_obj()

    def call(method, *args):
        out = obj.classMethodValue(FG, method, *args)
        return "" if out is None else str(out)

    return call


# ----------------------------------------------------------------------- ParseJsonLinks


@pytest.mark.parametrize(
    "links",
    [
        ["Measure.library"],
        ["Measure.library", "PlanDefinition.library"],  # elements on different types may share a predicate
        ["extension:http://example.org/fhir/ext/cqf-library"],
        ["extension:urn:example:thing", "Measure.relatedArtifact"],
    ],
)
def test_valid_json_links_are_echoed_back(fg, links):
    assert json.loads(fg("ParseJsonLinks", json.dumps(links))) == {"status": "ok", "links": links}


@pytest.mark.parametrize(
    "raw,message",
    [
        ('{"a": 1}', "json links must be a JSON array of strings"),
        ("not json", "json links must be a JSON array of strings"),
        ('["Measure.library", 3]', "json links must be a JSON array of strings"),
        ('["lowercase.library"]', "json link 'lowercase.library' is not 'Type.element' or 'extension:<url>'"),
        ('["Measure"]', "json link 'Measure' is not 'Type.element' or 'extension:<url>'"),
        ('["extension:not-a-url"]', "json link 'extension:not-a-url' is not 'Type.element' or 'extension:<url>'"),
        ('["Measure.library", "Measure.library"]', "json link 'Measure.library' is listed twice"),
        (
            '["Measure.library", "extension:http://x.org/library"]',
            "json links 'Measure.library' and 'extension:http://x.org/library' both give predicate 'library'",
        ),
        (
            '["extension:http://x.org/library", "Measure.library"]',
            "json links 'extension:http://x.org/library' and 'Measure.library' both give predicate 'library'",
        ),
    ],
)
def test_invalid_json_links_are_refused_with_the_reason(fg, raw, message):
    assert json.loads(fg("ParseJsonLinks", raw)) == {"status": "error", "error": message}


# --------------------------------------------------------------------------- Resolve


@pytest.mark.parametrize(
    "version,candidates,expected",
    [
        ("", {}, {"outcome": "no-definition", "target": ""}),
        ("", {"Library/a": "1.0"}, {"outcome": "resolved", "target": "Library/a"}),
        ("", {"Library/a": None}, {"outcome": "resolved", "target": "Library/a"}),
        ("", {"Library/a": "1.0", "Library/b": "2.0"}, {"outcome": "ambiguous", "target": ""}),
        ("2.0", {"Library/a": "1.0", "Library/b": "2.0"}, {"outcome": "resolved", "target": "Library/b"}),
        ("3.0", {"Library/a": "1.0", "Library/b": "2.0"}, {"outcome": "version-not-found", "target": ""}),
        ("1.0", {"Library/a": "1.0", "Library/b": "1.0"}, {"outcome": "ambiguous", "target": ""}),
    ],
)
def test_the_resolution_rule(fg, version, candidates, expected):
    assert json.loads(fg("Resolve", "http://x/lib", version, json.dumps(candidates))) == expected


def test_resolve_with_unparseable_candidates_is_an_error_object(fg):
    assert json.loads(fg("Resolve", "u", "", "{broken"))["status"] == "error"


# -------------------------------------------------------------------- SplitCanonical


@pytest.mark.parametrize(
    "value,expected",
    [
        ("http://x/lib", {"url": "http://x/lib", "version": None}),
        ("http://x/lib|1.0", {"url": "http://x/lib", "version": "1.0"}),
        ("http://x/lib|1.0|extra", {"url": "http://x/lib", "version": "1.0|extra"}),
        ("", ""),
        ("#contained", ""),
        ("|1.0", ""),
    ],
)
def test_split_canonical(fg, value, expected):
    raw = fg("SplitCanonical", value)
    assert (json.loads(raw) if raw else "") == expected


def test_split_canonical_cuts_each_part_to_the_index_width(fg):
    out = json.loads(fg("SplitCanonical", "u" * 300 + "|" + "v" * 300))
    assert len(out["url"]) == 220 and len(out["version"]) == 220


# ----------------------------------------------------------------------------- SplitKeep


def test_split_keep_drops_library_only_values(fg):
    index = ["http://a", "http://b|1", "http://c", "http://d"]
    library = ["http://a", "http://b|1", "http://d"]
    related = ["http://a"]
    out = json.loads(fg("SplitKeep", json.dumps(index), json.dumps(library), json.dumps(related)))
    # a is in both (keeps both edges), b|1 and d are library only (dropped), c is in neither (not taken away)
    assert out == {"keep": ["http://a", "http://c"]}


def test_split_keep_skips_values_the_index_skips(fg):
    out = json.loads(fg("SplitKeep", '["#contained", "http://a"]', '["#contained"]', "[]"))
    assert out == {"keep": ["#contained", "http://a"]}


def test_split_keep_with_bad_json_is_an_error_object(fg):
    assert json.loads(fg("SplitKeep", "{", "[]", "[]"))["status"] == "error"


# -------------------------------------------------------------------------- ExtractLinks

MEASURE = {
    "resourceType": "Measure",
    "id": "m1",
    "library": ["http://l1", "http://l2"],
    "extension": [
        {"url": "http://x.org/ext", "valueCanonical": "http://c1"},
        {"url": "http://x.org/ext", "valueReference": {"reference": "Library/L9"}},
        {"url": "http://other", "valueCanonical": "http://no"},
    ],
}


def links_of(fg, resource, links):
    out = json.loads(fg("ExtractLinks", json.dumps(resource), json.dumps(links)))
    return out


def test_elements_and_extensions_are_extracted_in_order(fg):
    out = links_of(fg, MEASURE, ["Measure.library", "extension:http://x.org/ext"])
    got = [(x["entry"], x["param"], x["value"], x["kind"]) for x in out["links"]]
    assert got == [
        ("Measure.library", "library", "http://l1", "canonical"),
        ("Measure.library", "library", "http://l2", "canonical"),
        ("extension:http://x.org/ext", "ext", "http://c1", "canonical"),
        ("extension:http://x.org/ext", "ext", "Library/L9", "reference"),
    ]


def test_a_string_valued_element_and_a_type_mismatch(fg):
    single = {"resourceType": "Measure", "id": "m2", "library": "http://only"}
    assert [x["value"] for x in links_of(fg, single, ["Measure.library"])["links"]] == ["http://only"]
    assert links_of(fg, single, ["PlanDefinition.library"])["links"] == []  # another type's entry
    assert links_of(fg, {"resourceType": "Measure", "id": "m3", "library": [1, "http://ok"]}, ["Measure.library"])[
        "links"
    ][0]["value"] == "http://ok"


def test_extract_links_refuses_bad_links_and_unparseable_resources(fg):
    out = json.loads(fg("ExtractLinks", json.dumps(MEASURE), '["nope"]'))
    assert out["status"] == "error" and "json link 'nope'" in out["error"]
    assert json.loads(fg("ExtractLinks", "{broken", '["Measure.library"]'))["status"] == "error"


def test_a_resource_nested_past_the_depth_bound_is_refused(fg):
    node: dict = {"url": "http://x.org/ext", "valueCanonical": "http://deep"}
    for _ in range(70):
        node = {"extension": [node]}
    resource = {"resourceType": "Measure", "id": "deep", **node}
    out = json.loads(fg("ExtractLinks", json.dumps(resource), '["extension:http://x.org/ext"]'))
    assert out["status"] == "error" and "deeper than 64 levels" in out["error"]
