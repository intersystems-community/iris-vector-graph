"""Spec 233 US1: the reference edges the Genomics Reporting IG shape yields.

The vendored IG examples are PUT under a per-run prefix and synced with the 231/232
registration. No engine code is involved beyond that; `expected_edges` is the oracle.
"""

from __future__ import annotations

import collections
import os
import re

import pytest

from tests.e2e.fhir_conftest import (
    GRAPH,
    FhirLoader,
    graph_edges,
    load_run,
    prefix_resources,
    teardown_run,
)
from tests.e2e.genomics_fixture import expected_edges, load_fixture, unindexed_refs

pytestmark = [pytest.mark.e2e]

_DOCS = os.path.join(os.path.dirname(__file__), "..", "..", "docs", "FHIR_GRAPH.md")


def _rows(conn, sql, *args):
    cur = conn.cursor()
    try:
        cur.execute(sql, list(args))
        return [tuple(r) for r in cur.fetchall()]
    finally:
        cur.close()


def _keys(resources):
    return {f"{r['resourceType']}/{r['id']}" for r in resources}


def _refs(node):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "reference" and isinstance(v, str):
                yield v
            else:
                yield from _refs(v)
    elif isinstance(node, list):
        for v in node:
            yield from _refs(v)


def _counts(edges):
    return collections.Counter(p for _, p, _ in edges)


@pytest.fixture(scope="module")
def live(fhir_conn_required, genomics_loaded):
    _, resources = genomics_loaded
    # Reference edges only: spec 235's derived compartment edges are checked in
    # test_235_compartment_e2e.py.
    return {e for e in graph_edges(fhir_conn_required, _keys(resources)) if e[1] != "in_patient_compartment"}


def test_edges_match_fixture(genomics_loaded, live):
    _, resources = genomics_loaded
    want = expected_edges(resources)
    print("\n| param | edges |\n| --- | --- |")
    for param, n in sorted(_counts(live).items()):
        print(f"| {param} | {n} |")
    print(f"total {len(live)} over {len(resources)} resources")
    assert sorted(live - want) == [], "edges the oracle does not predict"
    assert sorted(want - live) == [], "predicted edges missing from the graph"


def test_every_reference_accounted(fhir_conn_required, fhir_engine, genomics_loaded, live):
    _, resources = genomics_loaded
    keys = _keys(resources)
    unresolved = {
        (s, t): reason
        for s, _, t, reason in _rows(
            fhir_conn_required,
            "SELECT source, param, target, reason FROM Graph_KG.fhir_unresolved WHERE graph_id = ?",
            GRAPH,
        )
        if s in keys
    }
    linked = {(r["source"], r["url"]) for r in fhir_engine.fhir_link_report(GRAPH)["rows"] if r["source"] in keys}
    by_pair = {(s, o) for s, _, o in live}
    unindexed = unindexed_refs(resources)
    unindexed_pairs = {(s, o) for s, _, o in unindexed}
    missing, contained = [], 0
    for r in resources:
        src = f"{r['resourceType']}/{r['id']}"
        for ref in _refs(r):
            pair = (src, ref)
            if ref.startswith("#"):
                contained += 1
            elif not ({pair} & (by_pair | unindexed_pairs | set(unresolved) | linked)):
                missing.append(pair)
    print(f"\ncontained {contained}; unresolved by reason {dict(collections.Counter(unresolved.values()))}")
    print(f"unindexed by path {dict(collections.Counter(p for _, p, _ in unindexed))}")
    assert missing == []


def test_result_and_derived_from_present(live):
    counts = _counts(live)
    assert counts["result"] > 0
    assert counts["derived-from"] > 0


def test_docs_edge_table_matches(live):
    with open(_DOCS) as fh:
        text = fh.read()
    section = re.search(r"^## Genomics\n(.*?)(?=^## )", text, re.S | re.M).group(1)
    table = re.search(r"^\| param +\| edges +\|\n\|[-| ]+\|\n((?:\|.*\|\n)+)", section, re.M).group(1)
    documented = {}
    for line in table.strip().splitlines():
        param, n = (c.strip().strip("`") for c in line.strip("|").split("|"))
        documented[param] = int(n)
    assert documented == dict(_counts(live))


def test_teardown_leaves_no_run_keys(fhir_conn_required, genomics_loaded):
    """One DiagnosticReport and everything it reaches, under its own prefix."""
    fixture = {f"{r['resourceType']}/{r['id']}": r for r in load_fixture()}
    report = next(k for k, r in sorted(fixture.items()) if k.startswith("DiagnosticReport/") and r.get("result"))
    closure, todo = set(), [report]
    while todo:
        key = todo.pop()
        if key not in closure:
            closure.add(key)
            todo.extend(ref for ref in _refs(fixture[key]) if ref in fixture)
    loader = FhirLoader(fhir_conn_required)
    stored = load_run(fhir_conn_required, loader, prefix_resources([fixture[k] for k in sorted(closure)], loader.prefix))
    assert graph_edges(fhir_conn_required, _keys(stored)), "the slice made no edges"
    teardown_run(fhir_conn_required, loader, stored)
    like = f"%/{loader.prefix}-%"
    assert _rows(
        fhir_conn_required,
        "SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE graph_id = ? AND (s LIKE ? OR o_id LIKE ?)",
        GRAPH,
        like,
        like,
    ) == [(0,)]
    assert _rows(
        fhir_conn_required, 'SELECT COUNT(*) FROM "HSFHIR_X0001_R".Rsrc WHERE Key LIKE ? AND Deleted = 0', like
    ) == [(0,)]
