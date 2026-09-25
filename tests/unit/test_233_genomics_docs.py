"""Spec 233 US4: the Genomics section of docs/FHIR_GRAPH.md. No IRIS.

The concept counts come from the vendored ontology and fixture, so the prose cannot
drift from the files the E2E loads. The per-param edge table is checked against the
live sync by tests/e2e/test_233_genomics_topology_e2e.py::test_docs_edge_table_matches.
"""

from __future__ import annotations

import os
import re
from collections import Counter

import pytest

from tests.e2e import genomics_fixture as gf

DOC = os.path.join(os.path.dirname(__file__), "..", "..", "docs", "FHIR_GRAPH.md")


@pytest.fixture(scope="module")
def text():
    with open(DOC) as f:
        return f.read()


@pytest.fixture(scope="module")
def section(text):
    m = re.search(r"^## Genomics\n(.*?)(?=^## )", text, re.S | re.M)
    assert m, "docs/FHIR_GRAPH.md has no `## Genomics` section"
    return m.group(1)


def _table(section, header):
    m = re.search(rf"^\| {header} +\| edges +\|\n\|[-| ]+\|\n((?:\|.*\|\n)+)", section, re.M)
    assert m, f"no `| {header} | edges |` table"
    rows = {}
    for line in m.group(1).splitlines():
        name, count = (c.strip() for c in line.strip("|").split("|")[:2])
        rows[name.strip("`")] = int(count.replace(",", ""))
    return rows


def test_placed_between_concepts_and_security(text):
    headings = re.findall(r"^## (.+)$", text, re.M)
    i = headings.index("Genomics")
    assert headings.index("From a concept to ranked resources") < i < headings.index("Security model")


@pytest.mark.parametrize(
    "phrase",
    [
        r"embeddings",
        r"publication graph",
        r"VCF",
        r"co-resid",
        r"ValidatedBy",
        r"new operator",
        r"cross-graph quer",
    ],
)
def test_names_each_non_goal(section, phrase):
    assert re.search(phrase, section, re.I), f"non-goal {phrase!r} not named"


def test_concept_counts_match_the_vendored_files(section):
    triples = gf.load_ontology()
    edges = [t for t in triples if t[1] != gf.RDFS_LABEL]
    nodes = {s for s, _, _ in triples} | {o for _, _, o in edges}
    labels = sum(1 for t in triples if t[1] == gf.RDFS_LABEL)
    for n, what in ((len(nodes), "nodes"), (len(edges), "edges"), (labels, "labels")):
        assert f"{n:,} {what}" in section, f"`{n:,} {what}` not stated"
    by_pred = Counter(p.rsplit("/", 1)[-1].split("#")[-1] for _, p, _ in edges)
    table = {k.split(":")[-1]: v for k, v in _table(section, "predicate").items()}
    assert table == dict(by_pred)
    _, counts = gf.crosswalk_rows(gf.load_fixture())
    for what in ("clean", "normalized", "unmapped"):
        assert f"{counts[what]:,} {what}" in section, f"crosswalk `{counts[what]} {what}` not stated"


def test_provenance_target_is_a_denylist_candidate(section):
    para = [p for p in section.split("\n\n") if "Provenance.target" in p]
    assert any("denylist" in p for p in para)


def test_arrow_direction(section):
    assert "event → patient" in section


def test_per_param_edge_table(section):
    assert _table(section, "param")
