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

from tests.docs.consistency import BASELINE, build_context, check_doc, docs_to_check, load_baseline

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
