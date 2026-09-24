"""Spec 231 US3, US4 / SC-004, SC-005: from concept nodes to ranked FHIR resources.

A concept graph holds ten concepts, each with one narrower child. `code_crosswalk`
maps an ICD-10-CM code to every concept. The FHIR repository holds 1,000 Patients and
1,000 Conditions coded with those codes, so the latency budget is measured on
2,000+ resources. The bulk fixture uses fixed ids: PUT is an upsert, and a rerun finds
it already loaded instead of growing the shared repository.
"""

from __future__ import annotations

import statistics
import time
import warnings

import pytest

from tests.e2e.fhir_conftest import GRAPH, FhirLoader

pytestmark = [pytest.mark.e2e]

ICD = "http://hl7.org/fhir/sid/icd-10-cm"
CG = "concepts:ivg231"
N = 1000
PARENTS = [f"C{i}" for i in range(10)]
CHILDREN = [f"C{i}.1" for i in range(10)]


def _code(i):
    """Condition i carries one of twenty codes: 0-9 map to parents, 10-19 to children."""
    return f"Z{i % 20:02d}.1"


def _rows(conn, sql, *args):
    cur = conn.cursor()
    try:
        cur.execute(sql, list(args))
        return [tuple(r) for r in cur.fetchall()]
    finally:
        cur.close()


def _condition(cid, pid, code):
    return {
        "resourceType": "Condition",
        "id": cid,
        "code": {"coding": [{"system": ICD, "code": code}]},
        "subject": {"reference": f"Patient/{pid}"},
    }


@pytest.fixture(scope="module")
def pipeline(fhir_conn, fhir_engine):
    ld = FhirLoader(fhir_conn)
    loaded = _rows(
        fhir_conn,
        "SELECT COUNT(*) FROM \"HSFHIR_X0001_R\".Rsrc WHERE Key %STARTSWITH 'Condition/sc005-' AND Deleted = 0",
    )[0][0]
    if loaded < N:
        for i in range(N):
            ld.put({"resourceType": "Patient", "id": f"sc005-p{i}"})
            ld.put(_condition(f"sc005-c{i}", f"sc005-p{i}", _code(i)))

    for c in PARENTS + CHILDREN:
        fhir_engine.create_node(c, labels=["Concept"], graph=CG)
    for p, c in zip(PARENTS, CHILDREN):
        fhir_engine.create_edge(p, "narrower", c, graph=CG)
    for i, c in enumerate(PARENTS + CHILDREN):
        fhir_engine.code_crosswalk_add(ICD, f"Z{i:02d}.1", c, target_graph=CG, relation="exact", source="test231")

    fhir_engine.fhir_graph_register(denylist=[])
    fhir_engine.fhir_graph_sync(GRAPH)
    return ld


def _expected(codes):
    return {f"Condition/sc005-c{i}" for i in range(N) if _code(i) in codes}


def test_resolve(fhir_engine, pipeline):
    """US3 scenario 1: live resources whose code token matches a crosswalked code."""
    keys = set(fhir_engine.fhir_resolve_concepts(GRAPH, CG, PARENTS[:2]))
    assert _expected({"Z00.1", "Z01.1"}) <= keys
    assert all(k.startswith("Condition/") for k in keys)


def test_expand(fhir_engine, pipeline):
    got = fhir_engine.fhir_expand_concepts(CG, ["C3"], hops=1)
    assert got[0] == "C3" and set(got) == {"C3", "C3.1"}
    assert fhir_engine.fhir_expand_concepts(CG, ["C3"], hops=0) == ["C3"]
    assert fhir_engine.fhir_expand_concepts(CG, ["C3"], predicates=["broader"]) == ["C3"]


def test_relation_filter(fhir_engine, pipeline):
    """Every fixture mapping is `exact`, so asking only for `broader` resolves nothing."""
    assert fhir_engine.fhir_resolve_concepts(GRAPH, CG, ["C0"], relations=["broader"]) == []


def test_deleted_is_not_returned(fhir_conn, fhir_engine, pipeline):
    """US3 scenario 2."""
    ld = pipeline
    pid, cid = ld.id("pdel"), ld.id("cdel")
    ld.put({"resourceType": "Patient", "id": pid})
    ld.put(_condition(cid, pid, "Z05.1"))
    assert f"Condition/{cid}" in fhir_engine.fhir_resolve_concepts(GRAPH, CG, ["C5"])
    ld.delete("Condition", cid)
    assert f"Condition/{cid}" not in fhir_engine.fhir_resolve_concepts(GRAPH, CG, ["C5"])


def test_ppr_stays_in_graph(fhir_conn, fhir_engine, pipeline):
    """US3 scenario 3 / SC-004: every ranked node is a node of the FHIR graph."""
    # Unbounded: the 300 seeds hold the restart mass and would fill any small top-k.
    scores = fhir_engine.fhir_concept_ppr(GRAPH, CG, PARENTS[:3], hops=1, top_k=None)
    assert scores
    nodes = {
        r[0] for r in _rows(fhir_conn, "SELECT node_id FROM Graph_KG.nodes WHERE graph_id = ?", GRAPH)
    }
    assert set(scores) <= nodes
    assert not set(scores) & set(PARENTS + CHILDREN)
    assert any(k.startswith("Patient/sc005-") for k in scores)


def test_seed_in_another_graph_scores_nothing(fhir_engine, pipeline):
    """A concept node is not in the FHIR graph, so a walk seeded there reaches nothing."""
    scores = fhir_engine.kg_PERSONALIZED_PAGERANK(["C0"], graph=GRAPH, return_top_k=10)
    assert not {k: v for k, v in scores.items() if k != "C0" and v > 0}


def _median_ms(fn, runs=7):
    fn()  # warm the statement cache
    samples = []
    for _ in range(runs):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000)
    return statistics.median(samples)


def test_latency_budget(fhir_conn, fhir_engine, pipeline):
    """SC-005 on 2,000+ resources: resolve 10 concepts <= 250 ms, full pipeline <= 1,000 ms."""
    live = _rows(fhir_conn, 'SELECT COUNT(*) FROM "HSFHIR_X0001_R".Rsrc WHERE Deleted = 0')[0][0]
    assert live >= 2000
    resolve = _median_ms(lambda: fhir_engine.fhir_resolve_concepts(GRAPH, CG, PARENTS))
    full = _median_ms(
        lambda: fhir_engine.fhir_concept_ppr(GRAPH, CG, PARENTS, hops=1, max_iterations=20)
    )
    print(f"\nSC-005: resolve 10 concepts {resolve:.1f} ms; pipeline {full:.1f} ms (median of 7)")
    assert resolve <= 250, f"ResolveConcepts median {resolve:.1f} ms"
    assert full <= 1000, f"pipeline median {full:.1f} ms"


class TestBridgeMigration:
    """US4: fhir_bridges rows become code_crosswalk rows; the Python writer writes both."""

    def test_write_through_and_rerun(self, fhir_conn, fhir_engine, pipeline):
        node = "MESH:D231"
        with pytest.warns(DeprecationWarning):
            fhir_engine.fhir_bridge_add("Z07.1", node, fhir_code_system="ICD10CM", bridge_type="icd10_to_mesh")
        row = _rows(
            fhir_conn,
            "SELECT relation, source, source_version FROM Graph_KG.code_crosswalk "
            "WHERE code_system_uri = ? AND code = ? AND target_graph = '' AND target_node_id = ?",
            ICD,
            "Z07.1",
            node,
        )
        assert row == [("related", "fhir_bridges", "icd10_to_mesh")]

        first = fhir_engine.migrate_fhir_bridges_to_crosswalk()
        count = _rows(fhir_conn, "SELECT COUNT(*) FROM Graph_KG.code_crosswalk")[0][0]
        second = fhir_engine.migrate_fhir_bridges_to_crosswalk()
        assert first == second
        assert _rows(fhir_conn, "SELECT COUNT(*) FROM Graph_KG.code_crosswalk")[0][0] == count

        # A migrated bridge resolves like any crosswalk row: default-graph concept.
        keys = set(fhir_engine.fhir_resolve_concepts(GRAPH, None, [node]))
        assert _expected({"Z07.1"}) <= keys

    def test_get_kg_anchors_warns(self, fhir_engine, pipeline):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            fhir_engine.get_kg_anchors(icd_codes=["Z07.1"])
        assert any(issubclass(w.category, DeprecationWarning) for w in caught)
