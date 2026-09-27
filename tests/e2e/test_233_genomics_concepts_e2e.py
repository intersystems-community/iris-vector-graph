"""Spec 233 US2: ontology concept to ranked patients.

The vendored SO + MONDO slice goes into `concepts:ivg233`, the fixture's codings
are crosswalked to it, and resolve and concept-PPR run over the loaded genomics
set. `expected` and `expand` in genomics_fixture are the oracle.
"""

from __future__ import annotations

import statistics
import time

import pytest

from tests.e2e.fhir_conftest import GRAPH
from tests.e2e.genomics_fixture import (
    CONCEPT_GRAPH,
    ONTOLOGY,
    SEEDS,
    crosswalk_rows,
    expand,
    expected,
    load_fixture,
    load_ontology,
    patients_of,
)

pytestmark = [pytest.mark.e2e]

PARAMS = ["component-value-concept", "value-concept"]
LABEL = "http://www.w3.org/2000/01/rdf-schema#label"


def _rows(conn, sql, *args):
    cur = conn.cursor()
    try:
        cur.execute(sql, list(args))
        return [tuple(r) for r in cur.fetchall()]
    finally:
        cur.close()


def _drop_crosswalk(conn):
    cur = conn.cursor()
    try:
        cur.execute(
            "DELETE FROM Graph_KG.code_crosswalk WHERE target_graph = ? AND source = ?", [CONCEPT_GRAPH, "ivg233"]
        )
        conn.commit()
    finally:
        cur.close()


@pytest.fixture(scope="module")
def concepts(fhir_conn_required, fhir_engine, genomics_loaded):
    eng = fhir_engine
    eng.erase_graph(CONCEPT_GRAPH)
    _drop_crosswalk(fhir_conn_required)
    rows, counts = crosswalk_rows(load_fixture())
    try:
        imported = eng.import_rdf(ONTOLOGY, graph=CONCEPT_GRAPH)
        for system, code, iri in rows:
            eng.code_crosswalk_add(
                system, code, iri, target_graph=CONCEPT_GRAPH, relation="exact", source="ivg233"
            )
        yield imported, counts
    finally:
        eng.erase_graph(CONCEPT_GRAPH)
        _drop_crosswalk(fhir_conn_required)


@pytest.fixture(scope="module")
def edges():
    return [t for t in load_ontology() if t[1] != LABEL]


@pytest.fixture(scope="module")
def run(genomics_loaded):
    prefix, resources = genomics_loaded
    keys = {f"{r['resourceType']}/{r['id']}" for r in resources}
    return prefix, resources, keys


def _concepts(eng, iri, hops, direction):
    if not hops:
        return [iri]
    return eng.fhir_expand_concepts(CONCEPT_GRAPH, [iri], hops=hops, direction=direction)


def test_crosswalk_counts(concepts):
    imported, counts = concepts
    print(f"\nimport_rdf {imported}; crosswalk {counts}")
    assert counts["clean"] + counts["normalized"] > 0


@pytest.mark.parametrize("iri, hops, direction", SEEDS, ids=[s[0].rsplit("/", 1)[-1] for s in SEEDS])
def test_seed_resolves_to_oracle(fhir_engine, concepts, edges, run, iri, hops, direction):
    _, resources, keys = run
    local = expand(edges, [iri], hops, direction)
    live = _concepts(fhir_engine, iri, hops, direction)
    assert set(live) == local, "live expansion differs from the oracle's"
    got = set(fhir_engine.fhir_resolve_concepts(GRAPH, CONCEPT_GRAPH, live, params=PARAMS)) & keys
    want = expected(resources, local)
    print(f"\n{iri}: {len(local)} concepts, {len(got)} resources (oracle {len(want)})")
    assert got == want


def test_default_params_miss_variants(fhir_engine, concepts, run):
    _, resources, keys = run
    gene = "http://identifiers.org/hgnc/2621"
    with_params = expected(resources, {gene})
    assert with_params, "the CYP2C19 oracle is empty"
    got = set(fhir_engine.fhir_resolve_concepts(GRAPH, CONCEPT_GRAPH, [gene])) & keys
    assert not got & with_params


def _non_empty(resources, edges):
    out = []
    for iri, hops, direction in SEEDS:
        obs = expected(resources, expand(edges, [iri], hops, direction))
        if obs:
            out.append((iri, hops, direction, obs))
    return out


def test_ppr_separation(fhir_engine, concepts, edges, run):
    prefix, resources, keys = run
    run_patients = {k for k in keys if k.startswith("Patient/")}
    for iri, hops, direction, obs in _non_empty(resources, edges):
        scores = fhir_engine.fhir_concept_ppr(
            GRAPH, CONCEPT_GRAPH, [iri], hops=hops, direction=direction, params=PARAMS, top_k=None
        )
        ours = patients_of(resources, obs)
        others = run_patients - ours
        print(f"\n{iri}: top 20")
        for k, v in sorted(((k, v) for k, v in scores.items() if k in keys), key=lambda kv: -kv[1])[:20]:
            print(f"  {v:.5f} {k.split('/')[0]:20} {k}{'  *' if k in ours else ''}")
        assert ours, f"{iri}: the oracle names no Patient"
        low = min(scores.get(p, 0.0) for p in ours)
        high = max((scores.get(p, 0.0) for p in others), default=0.0)
        assert low > 0, f"{iri}: an oracle Patient scores 0"
        assert low > high, f"{iri}: an oracle Patient scores {low} <= another Patient's {high}"


def test_timings(fhir_engine, concepts, edges, run):
    iri, hops, direction = SEEDS[0]
    live = _concepts(fhir_engine, iri, hops, direction)
    resolve, ppr = [], []
    for _ in range(5):
        t = time.perf_counter()
        fhir_engine.fhir_resolve_concepts(GRAPH, CONCEPT_GRAPH, live, params=PARAMS)
        resolve.append(time.perf_counter() - t)
        t = time.perf_counter()
        fhir_engine.fhir_concept_ppr(GRAPH, CONCEPT_GRAPH, [iri], hops=hops, direction=direction, params=PARAMS)
        ppr.append(time.perf_counter() - t)
    print(
        f"\n{iri}: resolve median {statistics.median(resolve) * 1000:.0f} ms, "
        f"concept-PPR median {statistics.median(ppr) * 1000:.0f} ms"
    )


def test_teardown_drops_concepts(fhir_conn_required, fhir_engine, concepts):
    fhir_engine.erase_graph(CONCEPT_GRAPH)
    assert _rows(fhir_conn_required, "SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE graph_id = ?", CONCEPT_GRAPH) == [
        (0,)
    ]
    assert _rows(fhir_conn_required, "SELECT COUNT(*) FROM Graph_KG.nodes WHERE graph_id = ?", CONCEPT_GRAPH) == [(0,)]
