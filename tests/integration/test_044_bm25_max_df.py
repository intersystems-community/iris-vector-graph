"""Spec 044: Search can skip query terms that occur in most documents (max_df_fraction).

Decisions (user, 2026-10-09): the parameter is a fraction of N; if every query term is over the
cutoff the single lowest-df term is kept; the default is off; the skipped terms are reported.
Expected scores come from the Python reference in test_044_bm25_incremental, computed over a query
with the skipped terms removed, never from the index under test.
"""

from __future__ import annotations

import json

import pytest

from tests.integration.test_044_bm25_incremental import (  # noqa: F401  (ix is a fixture)
    brute_df,
    insert_all,
    ix,
    ref_scores,
    search,
    write_schema1,
)

CLS = "Graph.KG.BM25Index"


def docs_with_a_universal_term(n=200):
    """Every document holds `common`; every fifth holds `rare`; every tenth `mid` too."""
    docs = {}
    for i in range(n):
        toks = ["common", f"u{i}"]
        if i % 5 == 0:
            toks.append("rare")
        if i % 2 == 0:
            toks.append("half")
        docs[f"d{i:04d}"] = " ".join(toks)
    return docs


def report(ix, name, q, k=10_000, frac=0.0):
    return json.loads(str(ix.call("SearchReport", name, q, k, frac)))


def test_the_default_is_off_and_scores_are_unchanged(ix):
    name = ix.name("mdf_default")
    docs = docs_with_a_universal_term()
    insert_all(ix, name, docs)
    q = "common rare"
    got = search(ix, name, q)
    want = ref_scores(docs, q)
    assert set(got) == set(want)
    for d, s in want.items():
        assert got[d] == pytest.approx(s, rel=1e-9)
    explicit_off = {r["id"]: r["score"] for r in json.loads(str(ix.call("Search", name, q, 10_000, 0)))}
    assert explicit_off == got


def test_terms_over_the_fraction_are_skipped_and_scores_equal_the_query_without_them(ix):
    name = ix.name("mdf_skip")
    docs = docs_with_a_universal_term()
    insert_all(ix, name, docs)
    out = report(ix, name, "common rare", frac=0.5)
    assert out["skipped"] == ["common"] and out["fallback"] == 0
    got = {r["id"]: r["score"] for r in out["results"]}
    want = ref_scores(docs, "rare")  # the reference runs the query with the term removed
    assert set(got) == set(want)
    for d, s in want.items():
        assert got[d] == pytest.approx(s, rel=1e-9)
    assert len(got) == 40  # only documents holding `rare`; `common` no longer drags in the other 160


def test_the_cutoff_is_a_fraction_of_n_and_the_boundary_is_inclusive_of_the_term(ix):
    name = ix.name("mdf_bound")
    docs = docs_with_a_universal_term()
    insert_all(ix, name, docs)
    df = brute_df(docs)
    n = len(docs)
    # `half` sits in exactly half of the documents: at fraction 0.5 it stays, just below it goes
    assert df["half"] / n == 0.5
    assert report(ix, name, "half rare", frac=0.5)["skipped"] == []
    assert report(ix, name, "half rare", frac=0.49)["skipped"] == ["half"]


def test_when_every_term_is_over_the_cutoff_the_lowest_df_term_is_kept(ix):
    name = ix.name("mdf_all")
    docs = docs_with_a_universal_term()
    insert_all(ix, name, docs)
    out = report(ix, name, "common half", frac=0.1)
    assert out["fallback"] == 1
    assert out["skipped"] == ["common"]  # `half` has the lower df and is the one kept
    got = {r["id"]: r["score"] for r in out["results"]}
    want = ref_scores(docs, "half")
    assert set(got) == set(want)
    for d, s in want.items():
        assert got[d] == pytest.approx(s, rel=1e-9)


def test_a_single_over_the_cutoff_term_is_kept_not_returned_empty(ix):
    name = ix.name("mdf_single")
    docs = docs_with_a_universal_term()
    insert_all(ix, name, docs)
    out = report(ix, name, "common", frac=0.5)
    assert out["fallback"] == 1 and out["skipped"] == []
    assert len(out["results"]) == len(docs)


def test_unknown_terms_are_neither_skipped_nor_counted_as_the_fallback(ix):
    name = ix.name("mdf_unknown")
    docs = docs_with_a_universal_term()
    insert_all(ix, name, docs)
    out = report(ix, name, "nosuchterm common rare", frac=0.5)
    assert out["skipped"] == ["common"]
    assert {r["id"] for r in out["results"]} == set(ref_scores(docs, "rare"))


def test_a_repeated_query_term_is_reported_once(ix):
    name = ix.name("mdf_dup")
    docs = docs_with_a_universal_term()
    insert_all(ix, name, docs)
    assert report(ix, name, "common common rare", frac=0.5)["skipped"] == ["common"]


def test_report_without_a_cutoff_skips_nothing_and_matches_search(ix):
    name = ix.name("mdf_nocut")
    docs = docs_with_a_universal_term()
    insert_all(ix, name, docs)
    out = report(ix, name, "common rare", frac=0)
    assert out["skipped"] == [] and out["fallback"] == 0
    assert {r["id"]: r["score"] for r in out["results"]} == search(ix, name, "common rare")


def test_an_empty_index_or_query_reports_empty(ix):
    name = ix.name("mdf_empty")
    out = report(ix, name, "common", frac=0.5)
    assert out == {"results": [], "skipped": [], "fallback": 0}
    docs = docs_with_a_universal_term(20)
    insert_all(ix, name, docs)
    assert report(ix, name, "", frac=0.5) == {"results": [], "skipped": [], "fallback": 0}


def test_a_schema_1_index_ignores_the_cutoff(ix):
    # schema 1 has no df to read; Upgrade first. The cutoff must not change what such an index answers.
    name = ix.name("mdf_s1")
    docs = docs_with_a_universal_term(60)
    write_schema1(ix, name, docs)
    with_cutoff = report(ix, name, "common rare", frac=0.5)
    assert with_cutoff["skipped"] == []
    assert {r["id"]: r["score"] for r in with_cutoff["results"]} == search(ix, name, "common rare")
