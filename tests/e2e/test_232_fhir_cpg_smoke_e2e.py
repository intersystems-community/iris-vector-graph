"""Spec 232 SC-005 smoke: the vendored CPG CHF examples (tests/e2e/fixtures/fhir/cpg)
go in through DispatchRequest, the graph syncs, and every canonical row in the link
report has a status. Slow and non-gating: it prints the per-param totals rather than
pinning them.

Ids take the loader's per-run prefix. Every `Type/id` substring is rewritten the same
way, so literal references and canonical urls (which end in `Type/id`) stay
consistent and a run never resolves against another run's resources.
"""

from __future__ import annotations

import json
import os
import re

import pytest

from tests.e2e.fhir_conftest import _FIXTURES, GRAPH, FhirLoader

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

BUNDLE = os.path.join(_FIXTURES, "cpg", "cpg-chf-2.0.0.json")


def _prefixed(prefix):
    with open(BUNDLE) as fh:
        raw = fh.read()
    keys = {(e["resource"]["resourceType"], e["resource"]["id"]) for e in json.loads(raw)["entry"]}
    # Longest ids first, so no id rewrites inside a longer one.
    alt = "|".join(re.escape(f"{t}/{i}") for t, i in sorted(keys, key=lambda k: -len(k[1])))
    raw = re.sub(rf"({alt})(?=[\"|/#])", lambda m: m.group(1).replace("/", f"/{prefix}-", 1), raw)
    resources = [e["resource"] for e in json.loads(raw)["entry"]]
    for r in resources:
        r["id"] = f"{prefix}-{r['id']}"
    return resources


@pytest.fixture(scope="module")
def cpg(fhir_conn, fhir_engine):
    out = fhir_engine.fhir_graph_register(denylist=[])
    if not out.get("rebuilt"):
        fhir_engine.fhir_graph_rebuild(GRAPH)
    ld = FhirLoader(fhir_conn)
    resources = _prefixed(ld.prefix)
    loaded = []
    try:
        for r in resources:
            ld.put(r)
            loaded.append((r["resourceType"], r["id"]))
        fhir_engine.fhir_graph_sync(GRAPH)
        yield {f"{t}/{i}" for t, i in loaded}
    finally:
        for t, i in reversed(loaded):
            ld.delete(t, i)
        fhir_engine.fhir_graph_sync(GRAPH)


def test_every_canonical_row_has_a_status(fhir_engine, cpg):
    rows = [r for r in fhir_engine.fhir_link_report(GRAPH)["rows"] if r["source"] in cpg]
    canon = [r for r in rows if r["kind"] == "canonical"]
    assert canon, "the CPG bundle carries canonical links"
    assert all(r["status"] for r in canon)

    totals: dict = {}
    for r in rows:
        outcome = "resolved" if r["status"] in cpg else r["status"]
        totals.setdefault(r["param"], {}).setdefault(outcome, 0)
        totals[r["param"]][outcome] += 1
    print(f"\nCPG CHF link report ({len(cpg)} resources, {len(rows)} rows):")
    for param in sorted(totals):
        print(f"  {param:28} " + ", ".join(f"{k}={v}" for k, v in sorted(totals[param].items())))
    # The PlanDefinitions name Library/CHF, which the bundle carries.
    assert totals.get("library", {}).get("resolved", 0) >= 1
