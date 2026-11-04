"""4.6.0 R039: the glossary (`CONTEXT.md`) defines the terms the rewritten reader docs use, and only defines them.

The terms are read off README, USER_GUIDE and ADMIN_GUIDE by hand (each with where a reader meets it); a term is
defined when it heads a `**Term**` entry. The glossary holds no implementation detail: no global, class, method,
file path or ObjectScript, which belong in specs, ADRs and code (its own closing section says so). ADMIN_GUIDE's
"Further reading" points at the ADRs and at the glossary.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GLOSSARY = (REPO / "CONTEXT.md").read_text()

#: term -> a phrase of a reader doc that uses it (lower case), and that doc
TERMS = {
    "IVG Schema": ("ivg schema", "README.md"),
    "Graph": ("default graph", "docs/USER_GUIDE.md"),
    "Named Graph": ("named graph", "docs/USER_GUIDE.md"),
    "Graph Key": ("graph key", "docs/USER_GUIDE.md"),
    "All-graphs read": ("use graph", "docs/USER_GUIDE.md"),
    "Namespace": ("namespace", "README.md"),
    "Native Reader": ("arno", "docs/ADMIN_GUIDE.md"),
    "Owner": ("objectscript", "docs/ADMIN_GUIDE.md"),
    "Arno": ("arno", "README.md"),
    "Adjacency Index": ("adjacency index", "docs/ADMIN_GUIDE.md"),
    "Interned snapshot": ("^nkg", "docs/USER_GUIDE.md"),
    "Rebuilt store": ("rebuild", "docs/ADMIN_GUIDE.md"),
    "Structural Edge": ("edge", "docs/USER_GUIDE.md"),
    "Temporal Edge": ("temporal edge", "docs/USER_GUIDE.md"),
    "Temporal-only predicate": ("temporal-only", "docs/ADMIN_GUIDE.md"),
    "Multigraph mode": ("multigraph", "docs/USER_GUIDE.md"),
    "Ledger": ("ledger", "README.md"),
    "Revision": ("revision", "docs/USER_GUIDE.md"),
    "Changeset": ("changeset", "docs/USER_GUIDE.md"),
    "Erasure": ("erase", "docs/USER_GUIDE.md"),
    "Redaction": ("redact", "docs/USER_GUIDE.md"),
    "Collision": ("collision", "docs/USER_GUIDE.md"),
    "BM25 Index": ("bm25", "docs/USER_GUIDE.md"),
    "Embedding Registry": ("registry", "docs/ADMIN_GUIDE.md"),
    "Quarantine": ("quarantine", "docs/ADMIN_GUIDE.md"),
    "Legacy objects": ("legacy_objects", "docs/ADMIN_GUIDE.md"),
    "Native executor": ("native", "README.md"),
    "SQL path": ("sql path", "docs/ADMIN_GUIDE.md"),
    "Compiled plan": ("compiled plan", "docs/ADMIN_GUIDE.md"),
    "Degree": ("degree", "docs/USER_GUIDE.md"),
}


def _defined() -> set:
    return {m.group(1).strip() for m in re.finditer(r"^\*\*([^*]+)\*\*", GLOSSARY, re.M)}


@pytest.mark.parametrize("term", sorted(TERMS))
def test_a_term_the_docs_use_is_defined(term):
    phrase, doc = TERMS[term]
    assert phrase in (REPO / doc).read_text().lower(), f"{doc} no longer uses {phrase!r}: drop or move {term}"
    assert term in _defined(), f"CONTEXT.md does not define {term}"


#: what implementation detail looks like: a global, a class or method reference, a file, ObjectScript
DETAIL = [
    (r"\^[A-Za-z%]", "a global"),
    (r"##class|Graph\.KG\.|\$[A-Z]", "ObjectScript or a class"),
    (r"\.(py|cls|json|md)\b", "a file"),
    (r"\b\w+\(\)", "a method"),
    (r"`[a-z_]+\.[a-z_]+`", "a qualified name"),
]


def _entries():
    body = GLOSSARY.split("## What is NOT in this glossary")[0]
    return {m.group(1): m.group(2) for m in re.finditer(r"^\*\*([^*]+)\*\*\n(.*?)(?=^\*\*|\Z|^---)", body, re.M | re.S)}


@pytest.mark.parametrize("pattern, what", DETAIL, ids=[d[1] for d in DETAIL])
def test_the_glossary_holds_no_implementation_detail(pattern, what):
    # `^NKG` is the name the guides give the Interned snapshot, so a reader can find it here; no other name is
    found = {
        term: m.group(0)
        for term, text in _entries().items()
        for m in [re.search(pattern, text.replace("`^NKG`", ""))]
        if m
    }
    assert found == {}, f"{what} in the glossary: {found}"


def test_further_reading_points_at_the_adrs_and_the_glossary():
    admin = (REPO / "docs" / "ADMIN_GUIDE.md").read_text()
    further = admin.split("## Further reading", 1)[1]
    assert "](adr/)" in further and "](../CONTEXT.md)" in further, further
