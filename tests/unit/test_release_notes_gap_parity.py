"""The release notes may not describe a gap the shipped schema has closed.

`CHANGELOG.md`'s "Known gaps" paragraph and `docs/KNOWN_ISSUES.md`'s companion list
were written while spec 227 was in flight, before the spec 230 sweep landed. Both
went on claiming that `kg_EdgeEmbeddings` carries no `graph_id`, that the BM25 leg
reads a graph-blind corpus, and that a per-graph erase leaves `^KG("prop")` and
`^KG("label")` behind — after 230 had fixed all three.

A stale gap note is worse than a missing one: a reader who believes edge vectors are
shared between graphs will hand-partition triples that are already partitioned, and a
reader who believes the eraser leaks will write a cleanup pass that kills another
graph's subscripts. So the claim is pinned against the DDL and the eraser rather than
left to whoever next edits the prose.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHANGELOG = ROOT / "CHANGELOG.md"
KNOWN_ISSUES = ROOT / "docs" / "KNOWN_ISSUES.md"
SCHEMA = ROOT / "iris_vector_graph" / "schema.py"
ERASER = ROOT / "iris_src" / "src" / "Graph" / "KG" / "Eraser.cls"


def _latest_release_section() -> str:
    """The text of the topmost `### vX.Y.Z` section in the changelog."""
    text = CHANGELOG.read_text(encoding="utf-8")
    headings = list(re.finditer(r"^### v\d+\.\d+\.\d+", text, re.MULTILINE))
    assert headings, "no versioned section found in CHANGELOG.md"
    start = headings[0].end()
    end = headings[1].start() if len(headings) > 1 else len(text)
    return text[start:end]


# (subject, the stale claim about it that spec 230 falsified)
#
# Matched per *paragraph*, not per document: "no `graph_id`" appears in a dozen
# past-tense entries describing issues that were fixed, and those must not trip this.
LIFTED_CLAIMS = [
    ("kg_EdgeEmbeddings", "keyed `(s, p, o_id)` with no `graph_id`"),
    ("kg_EdgeEmbeddings", "remain namespace-wide"),
    ("BM25", "read index structures that carry no graph"),
    ('^KG("prop")', "are not partitioned by graph"),
]


def _paragraphs(text: str) -> list:
    return re.split(r"\n\s*\n", text)


def _stale_paragraphs(text: str, subject: str, claim: str) -> list:
    """Paragraphs that name `subject` and assert `claim` about it in the present tense."""
    return [
        para
        for para in _paragraphs(text)
        if subject in para and claim in para and "fixed in" not in para
    ]


def test_edge_embeddings_declares_a_graph_id():
    """The premise of the first stale claim: the DDL does carry the graph."""
    schema = SCHEMA.read_text(encoding="utf-8")
    ddl = schema.split("CREATE TABLE IF NOT EXISTS Graph_KG.kg_EdgeEmbeddings")[1]
    ddl = ddl.split(");")[0]
    assert "graph_id" in ddl
    assert "uq_edge_emb_graph_spo UNIQUE (graph_id, s, p, o_id)" in ddl


def test_docs_corpus_is_keyed_by_graph():
    """The premise of the BM25 claim: the corpus carries the graph."""
    schema = SCHEMA.read_text(encoding="utf-8")
    ddl = schema.split("CREATE TABLE Graph_KG.docs(")[1].split(");")[0]
    assert "graph_id" in ddl
    assert "pk_docs PRIMARY KEY (graph_id, id)" in ddl


def test_eraser_kills_prop_and_label_within_one_graph():
    """The premise of the `^KG` claim: the eraser is graph-scoped on those trees."""
    eraser = ERASER.read_text(encoding="utf-8")
    for tree in ('^KG("prop"', '^KG("label"'):
        assert tree in eraser, f"{tree} is not touched by the eraser at all"


@pytest.mark.parametrize("subject,claim", LIFTED_CLAIMS)
def test_changelog_does_not_repeat_a_lifted_gap(subject, claim):
    stale = _stale_paragraphs(_latest_release_section(), subject, claim)
    assert not stale, (
        f"the latest release section still claims {subject} {claim!r}, which the "
        f"shipped schema contradicts:\n\n{stale[0]}"
    )


@pytest.mark.parametrize("subject,claim", LIFTED_CLAIMS)
def test_known_issues_does_not_repeat_a_lifted_gap(subject, claim):
    text = KNOWN_ISSUES.read_text(encoding="utf-8")
    stale = _stale_paragraphs(text, subject, claim)
    assert not stale, (
        f"docs/KNOWN_ISSUES.md still claims {subject} {claim!r}, which the shipped "
        f"schema contradicts:\n\n{stale[0]}"
    )
