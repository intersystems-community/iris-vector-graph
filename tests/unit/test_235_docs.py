"""Spec 235 FR-024: the interpretation contract in docs/FHIR_GRAPH.md and the 4.1.0
Upgrading note in CHANGELOG.md. 4.1.0 is the first release after 4.0.0; the 4.0.1 fixes
and spec 235 ship in it, so there is no v4.0.1 or v4.2.0 section."""

from __future__ import annotations

import json
import os
import re

import pytest

from iris_vector_graph._engine.fhir_graph import FHIR_PPR_EXCLUDE

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DOC = os.path.join(ROOT, "docs", "FHIR_GRAPH.md")
CHANGELOG = os.path.join(ROOT, "CHANGELOG.md")
SPEC = os.path.join(ROOT, "specs", "235-fhir-graph-interpretation")
MEASURES = ("sync_ms", "ppr_ms", "ppr_group_ms", "coverage_ms", "gaps_ms")


def _read(path):
    with open(path) as fh:
        return fh.read()


def _section(text, heading):
    """The body of the `## heading` section, up to the next `## `."""
    m = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1) if m else None


@pytest.fixture(scope="module")
def doc():
    return _read(DOC)


@pytest.fixture(scope="module")
def contract(doc):
    body = _section(doc, "Interpretation contract")
    assert body is not None, "docs/FHIR_GRAPH.md has no '## Interpretation contract'"
    return body


def test_section_after_genomics(doc, contract):
    heads = re.findall(r"^## (.+)$", doc, re.M)
    assert heads.index("Interpretation contract") == heads.index("Genomics") + 1


@pytest.mark.parametrize(
    "concept, kind",
    [
        ("`in_patient_compartment`", "edge"),
        ("clinical tokens", "token"),
        ("`category`", "property"),
        ("`meta_profile`", "property"),
        ("category labels", "label"),
    ],
)
def test_kind_table(contract, concept, kind):
    rows = [r for r in contract.splitlines() if r.startswith("|") and concept.lower() in r.lower()]
    assert rows, f"no table row for {concept}"
    cells = [c.strip().lower() for c in rows[0].strip("|").split("|")]
    assert cells[1] == kind, (concept, cells)


@pytest.mark.parametrize(
    "phrase",
    [
        "validating profiles",
        "per-profile interpretation maps",
        "Encounter, Practitioner, RelatedPerson and Device compartments",
        "`Patient.link`",
        "category hub nodes",
        "persisted",
        "`fhir_bridge`",
    ],
)
def test_non_goals(contract, phrase):
    assert phrase.lower() in contract.lower(), phrase


def test_meta_profile_not_validated(contract):
    assert "claimed by the writer, not validated" in contract


def test_ppr_exclude_named(contract):
    assert "FHIR_PPR_EXCLUDE" in contract
    for entry in FHIR_PPR_EXCLUDE:
        assert f"`{entry}`" in contract, entry


def test_genomics_denylist_advice_replaced(doc):
    genomics = _section(doc, "Genomics")
    assert "is a denylist candidate for PPR" not in genomics
    assert "FHIR_PPR_EXCLUDE" in genomics


def _median_rows(contract):
    """{(set, measure): (baseline, current)} from the medians table; '–' is None."""
    out = {}
    for line in contract.splitlines():
        cells = [c.strip().strip("`") for c in line.strip().strip("|").split("|")]
        if len(cells) == 4 and cells[0] in ("fixture", "scale") and cells[1] in MEASURES:
            out[(cells[0], cells[1])] = tuple(None if c in ("–", "-", "") else float(c) for c in cells[2:])
    return out


def test_medians_match_bench(contract):
    base_path, cur_path = os.path.join(SPEC, "baseline.json"), os.path.join(SPEC, "current.json")
    if not (os.path.exists(base_path) and os.path.exists(cur_path)):
        pytest.skip("bench results live in the gitignored spec directory")
    base, cur = json.loads(_read(base_path)), json.loads(_read(cur_path))
    rows = _median_rows(contract)
    for set_ in ("fixture", "scale"):
        for m in MEASURES:
            want = tuple(None if r[set_].get(m) is None else float(round(r[set_][m])) for r in (base, cur))
            assert rows.get((set_, m)) == want, (set_, m, rows.get((set_, m)), want)


def test_changelog_upgrading():
    text = _read(CHANGELOG)
    m = re.search(r"^### v4\.1\.0.*?(?=^### v)", text, re.M | re.S)
    assert m, "CHANGELOG.md has no v4.1.0 section"
    section = m.group(0)
    up = re.search(r"\*\*Upgrading\*\*(.*?)(?=^\*\*|\Z)", section, re.M | re.S)
    assert up, "no **Upgrading** note in v4.1.0"
    for word in ("interp_version", "fhir_reinterpret", "first sync", "get_fhir_graph_schema_sql"):
        assert word in " ".join(up.group(1).split()), word
    assert "### v4.2.0" not in text and "### v4.0.1" not in text
    assert text.index("### v4.1.0") < text.index("### v4.0.0")
