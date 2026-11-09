"""R043: the docs agree with the code (doc-test layer 2). No IRIS needed; runs in CI and ci-parity.

One case per document (README.md, CONTRIBUTING.md, docs/**/*.md that git ships). What is checked is listed in
tests/docs/consistency.py.

The docs had failures when this test was written (4.6.0, before workstream C rewrote them). They are listed, one
per line, in tests/docs/consistency_known_failures.txt, and the test is a ratchet:

* a document whose findings are all on that list is XFAIL, and the reason lists them, so they show in every run;
* a finding not on the list FAILS: a new document, or a new line in an old one, must be right;
* a listed finding that no longer occurs FAILS until its line is deleted, so the list only shrinks.

R045 empties the list. Regenerate nothing: delete lines as docs are fixed.
"""

from __future__ import annotations

import pytest

from tests.docs.consistency import (
    BASELINE,
    anchors_of,
    build_context,
    check_doc,
    check_links,
    docs_to_check,
    github_slug,
    load_baseline,
)

CTX = build_context()
DOCS = docs_to_check(CTX.repo, CTX)
KNOWN = load_baseline()


@pytest.mark.parametrize("doc", DOCS)
def test_doc_names_only_what_exists(doc):
    findings = check_doc(doc, (CTX.repo / doc).read_text(), CTX, reader=doc in CTX.reader_docs)
    keys = {f.key() for f in findings}
    known = {k for k in KNOWN if k[0] == doc}

    new = sorted((f for f in findings if f.key() not in known), key=lambda f: f.line)
    gone = sorted(known - keys)
    message = []
    if new:
        message.append(f"{doc}: {len(new)} new finding(s), not in {BASELINE.name}:")
        message += [f"  line {f.line}: {f.kind}: {f.item}" for f in new]
    if gone:
        message.append(f"{doc}: fixed, so delete these lines from {BASELINE.name}:")
        message += [f"  {d} | {k} | {i}" for d, k, i in gone]
    assert not message, "\n".join(message)

    if findings:
        listed = "; ".join(f"line {f.line} {f.kind} {f.item}" for f in sorted(findings, key=lambda f: f.line))
        pytest.xfail(f"{len(findings)} known finding(s) (R045 fixes them): {listed}")


def test_baseline_names_only_documents_that_are_checked():
    stale = sorted({doc for doc, _, _ in KNOWN} - set(DOCS))
    assert not stale, (f"{BASELINE.name} lists documents that are gone or no longer checked; delete their "
                       f"lines (a moved document starts clean): {stale}")


def test_baseline_has_no_duplicate_lines():
    lines = [ln.strip() for ln in BASELINE.read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    assert len(lines) == len(set(lines))


# ── links and their anchors (4.6.5) ────────────────────────────────────────────
# CHANGELOG.md is not in DOCS (its history names things that are gone), but a reader follows its links into the
# current docs. 4.6.4 shipped two that went nowhere: a KNOWN_ISSUES heading that had been renamed, and a file that
# had moved. test_doc_names_only_what_exists checks only that a link's file exists; this checks its #anchor too.


@pytest.mark.parametrize(
    "heading, slug",
    [
        # worked by hand from GitHub's rules: lower-case; drop what is not a letter, digit, `_` or `-`; space -> `-`
        ("A date or time read by position from a collected list compares as its text",
         "a-date-or-time-read-by-position-from-a-collected-list-compares-as-its-text"),
        ("On IRIS 2026.3, grouping over an UNWIND", "on-iris-20263-grouping-over-an-unwind"),
        ("`create_node` and `store_node`", "create_node-and-store_node"),
        ("Rollback: what survives (and why)", "rollback-what-survives-and-why"),
        ("Leiden: `engine.leiden_communities()`", "leiden-engineleiden_communities"),
        ("A — B", "a--b"),
        ("See [the guide](x.md) for **more**", "see-the-guide-for-more"),
        ("1. Setup", "1-setup"),
        ("Über uns", "über-uns"),
    ],
)
def test_github_slug(heading, slug):
    assert github_slug(heading) == slug


def test_repeated_headings_get_a_numeric_suffix_and_fences_hold_none():
    text = "# Top\n\n## Same\n\ntext\n\n## Same\n\n```\n# not a heading\n```\n\n### Same\n\n<a id=\"Named\"></a>\n"
    assert anchors_of(text) == {"top", "same", "same-1", "same-2", "named"}


def test_check_links_reports_a_missing_file_and_a_missing_anchor(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "G.md").write_text("# Guide\n\n## First part\n")
    doc = (
        "[ok](docs/G.md#first-part) [file gone](docs/NOPE.md) [anchor gone](docs/G.md#second-part) "
        "[here](#intro) [nowhere](#outro) [web](https://example.com/x#y)\n\n# Intro\n"
    )
    (tmp_path / "C.md").write_text(doc)
    got = sorted((f.kind, f.item) for f in check_links("C.md", doc, tmp_path))
    assert got == [("anchor", "#outro"), ("anchor", "docs/G.md#second-part"), ("link", "docs/NOPE.md")]


@pytest.mark.parametrize("doc", ["CHANGELOG.md", *DOCS])
def test_links_reach_a_file_and_a_heading(doc):
    text = (CTX.repo / doc).read_text()
    findings = sorted(check_links(doc, text, CTX.repo), key=lambda f: f.line)
    assert not findings, f"{doc} has links that go nowhere:\n" + "\n".join(
        f"  line {f.line}: {f.kind}: {f.item}" for f in findings
    )
