"""Spec 232 research R11: the rule methods of Graph.KG.FHIRGraph, table-driven.

ParseJsonLinks, Resolve, SplitCanonical, SplitKeep and ExtractLinks run no SQL and
change no state. They are unit tests of those methods; they need the container only
because the code is ObjectScript. The grammar rows are the case table the Python
parser runs in tests/unit/test_232_fhir_links.py.
"""

from __future__ import annotations

import json
import os

import pytest

pytestmark = [pytest.mark.e2e]

_CLS = "Graph.KG.FHIRGraph"
_CASES = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "fixtures", "fhir_json_links_cases.json"
)
CASES = json.load(open(_CASES))
CQF = "extension:http://hl7.org/fhir/StructureDefinition/cqf-library"


@pytest.fixture(scope="module")
def call(fhir_conn):
    import iris

    irisobj = iris.createIRIS(fhir_conn)

    def _call(method, *args):
        return json.loads(irisobj.classMethodValue(_CLS, method, *args))

    return _call


# --- ParseJsonLinks ---------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_parse_json_links(call, case):
    out = call("ParseJsonLinks", json.dumps(case["input"]))
    if case["ok"]:
        assert out == {"status": "ok", "links": case["input"]}
    else:
        assert out["status"] == "error"
        assert case["error_contains"] in out["error"]


def test_parse_json_links_not_json(call):
    out = call("ParseJsonLinks", "PlanDefinition.library")
    assert out["status"] == "error"
    assert "json links must be a JSON array of strings" in out["error"]


# --- Resolve ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "version,cands,outcome,target",
    [
        ("", {}, "no-definition", ""),
        ("1.0.0", {}, "no-definition", ""),
        ("1.0.0", {"Library/l1": "1.0.0", "Library/l2": "2.0.0"}, "resolved", "Library/l1"),
        ("3.0.0", {"Library/l1": "1.0.0", "Library/l2": "2.0.0"}, "version-not-found", ""),
        ("1.0.0", {"Library/a": "1.0.0", "Library/b": "1.0.0"}, "ambiguous", ""),
        ("", {"Library/m": "1.0.0"}, "resolved", "Library/m"),
        ("", {"Library/m": None}, "resolved", "Library/m"),
        ("", {"Library/l1": "1.0.0", "Library/l2": "2.0.0"}, "ambiguous", ""),
        ("", {"Library/a": None, "Library/b": None}, "ambiguous", ""),
        # A versioned reference never matches a candidate that declares no version.
        ("1.0.0", {"Library/a": None}, "version-not-found", ""),
        ("1.0.0", {"Library/a": None, "Library/b": "1.0.0"}, "resolved", "Library/b"),
        # Versions compare exactly: no prefix match, no case folding.
        ("1.0", {"Library/a": "1.0.0"}, "version-not-found", ""),
        ("1.0.0-RC", {"Library/a": "1.0.0-rc"}, "version-not-found", ""),
    ],
)
def test_resolve(call, version, cands, outcome, target):
    out = call("Resolve", "http://ex.org/ivg232/L", version, json.dumps(cands))
    assert out == {"outcome": outcome, "target": target}


# --- SplitCanonical -----------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("http://ex.org/L", {"url": "http://ex.org/L", "version": None}),
        ("http://ex.org/L|1.0.0", {"url": "http://ex.org/L", "version": "1.0.0"}),
        # The indexer splits on the first bar; the rest is the version.
        ("http://ex.org/L|1.0|beta", {"url": "http://ex.org/L", "version": "1.0|beta"}),
        # An empty version is no version, as the index stores it.
        ("http://ex.org/L|", {"url": "http://ex.org/L", "version": None}),
    ],
)
def test_split_canonical(call, value, expected):
    assert call("SplitCanonical", value) == expected


def test_split_canonical_skip_is_empty(fhir_conn):
    import iris

    # The Native API hands an empty %String back as None.
    irisobj = iris.createIRIS(fhir_conn)
    assert irisobj.classMethodValue(_CLS, "SplitCanonical", "#x") in ("", None)
    assert irisobj.classMethodValue(_CLS, "SplitCanonical", "") in ("", None)


def test_split_canonical_truncates_to_220(call):
    url = "http://ex.org/" + "u" * 300
    version = "v" * 300
    out = call("SplitCanonical", f"{url}|{version}")
    assert out == {"url": url[:220], "version": version[:220]}
    out = call("SplitCanonical", "http://ex.org/" + "u" * 207)  # 221 characters
    assert len(out["url"]) == 220


# --- SplitKeep ----------------------------------------------------------------------

A, B = "http://ex.org/ivg232/A|1.0.0", "http://ex.org/ivg232/B"


@pytest.mark.parametrize(
    "index,library,related,keep",
    [
        # lib only: the url leaves depends-on (it becomes a library edge).
        ([A], [A], [], []),
        # ra only: it stays.
        ([B], [], [B], [B]),
        # both: it stays in depends-on and also gets a library edge.
        ([A], [A], [A], [A]),
        # neither (the index saw it through a branch we do not read): it stays.
        ([B], [], [], [B]),
        ([A, B], [A], [B], [B]),
        # Versions matter: A|2.0.0 in library does not remove A|1.0.0.
        ([A], ["http://ex.org/ivg232/A|2.0.0"], [], [A]),
        # A body value is normalised as the index normalises it: empty version is none.
        ([B], [B + "|"], [], []),
    ],
)
def test_split_keep(call, index, library, related, keep):
    out = call("SplitKeep", json.dumps(index), json.dumps(library), json.dumps(related))
    assert out == {"keep": keep}


def test_split_keep_truncates_body_side(call):
    """The index holds 220 characters of a long url; the body value is cut to match."""
    long_url = "http://ex.org/" + "x" * 250
    out = call("SplitKeep", json.dumps([long_url[:220]]), json.dumps([long_url]), "[]")
    assert out == {"keep": []}


# --- ExtractLinks -------------------------------------------------------------------


def _links(call, resource, entries):
    out = call("ExtractLinks", json.dumps(resource), json.dumps(entries))
    assert "links" in out, out
    return sorted((x["entry"], x["param"], x["value"], x["kind"]) for x in out["links"])


def _pd(**body):
    return {"resourceType": "PlanDefinition", "id": "pd", **body}


def test_extract_element_string_and_array(call):
    lib = "PlanDefinition.library"
    assert _links(call, _pd(library="http://ex.org/L"), [lib]) == [
        (lib, "library", "http://ex.org/L", "canonical")
    ]
    assert _links(call, _pd(library=["http://ex.org/A", 3, {"x": 1}, "http://ex.org/B|1"]), [lib]) == [
        (lib, "library", "http://ex.org/A", "canonical"),
        (lib, "library", "http://ex.org/B|1", "canonical"),
    ]


def test_extract_element_other_type_or_non_string(call):
    assert _links(call, _pd(library="http://ex.org/L"), ["Measure.library"]) == []
    assert _links(call, _pd(library={"reference": "Library/x"}), ["PlanDefinition.library"]) == []
    assert _links(call, _pd(), ["PlanDefinition.library"]) == []


def test_extract_extension_nested(call):
    ext = {"url": CQF[len("extension:") :], "valueCanonical": "http://ex.org/U1"}
    res = _pd(action=[{"action": [{"extension": [ext]}]}])
    assert _links(call, res, [CQF]) == [(CQF, "cqf-library", "http://ex.org/U1", "canonical")]


def test_extract_modifier_extension_and_reference(call):
    url = CQF[len("extension:") :]
    res = _pd(
        modifierExtension=[{"url": url, "valueCanonical": "http://ex.org/M"}],
        extension=[
            {"url": url, "valueReference": {"reference": "Library/abc"}},
            {"url": "http://other.org/x", "valueCanonical": "http://ex.org/no"},
            {"url": url, "valueString": "ignored"},
        ],
    )
    assert _links(call, res, [CQF]) == [
        (CQF, "cqf-library", "Library/abc", "reference"),
        (CQF, "cqf-library", "http://ex.org/M", "canonical"),
    ]


def test_extract_extension_url_outside_an_extension_array(call):
    """An object with a matching url that is not in an extension array is not a match."""
    url = CQF[len("extension:") :]
    res = _pd(relatedArtifact=[{"url": url, "valueCanonical": "http://ex.org/no"}])
    assert _links(call, res, [CQF]) == []


def test_extract_both_kinds_together(call):
    url = CQF[len("extension:") :]
    res = _pd(library=["http://ex.org/L|1.0.0"], extension=[{"url": url, "valueCanonical": "http://ex.org/U"}])
    assert _links(call, res, ["PlanDefinition.library", CQF]) == [
        ("PlanDefinition.library", "library", "http://ex.org/L|1.0.0", "canonical"),
        (CQF, "cqf-library", "http://ex.org/U", "canonical"),
    ]


def _nested(depth):
    node: dict = {}
    for _ in range(depth):
        node = {"a": node}
    return node


def test_extract_depth_limit(call):
    ok = call("ExtractLinks", json.dumps(_pd(deep=_nested(63))), json.dumps([CQF]))
    assert ok.get("links") == [], ok
    bad = call("ExtractLinks", json.dumps(_pd(deep=_nested(64))), json.dumps([CQF]))
    assert bad["status"] == "error"
    assert "PlanDefinition/pd" in bad["error"] and "64" in bad["error"]


def test_extract_match_limit(call):
    url = CQF[len("extension:") :]
    many = [{"url": url, "valueCanonical": f"http://ex.org/L{i}"} for i in range(10001)]
    bad = call("ExtractLinks", json.dumps(_pd(extension=many)), json.dumps([CQF]))
    assert bad["status"] == "error"
    assert "PlanDefinition/pd" in bad["error"] and "10000" in bad["error"]
    ok = call("ExtractLinks", json.dumps(_pd(extension=many[:10000])), json.dumps([CQF]))
    assert len(ok["links"]) == 10000
