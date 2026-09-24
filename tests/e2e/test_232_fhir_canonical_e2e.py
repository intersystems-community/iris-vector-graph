"""Spec 232 US1: canonical search columns become edges by the resolution rule (R7).

The knowledge fixture (tests/e2e/fixtures/fhir/knowledge/) goes in through the FHIR
server, stage by stage, with one SyncOnce after each stage. The graph is registered
with json_links = [] so only index canonicals apply: pd1's `library` value is still
merged into `depends-on`, as the index merges it, and cqf-library is not read.

Tests run in file order and share the loaded fixture.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.exceptions import FHIRGraphError
from tests.e2e.fhir_conftest import GRAPH, FhirLoader

pytestmark = [pytest.mark.e2e]

DEP = "depends-on"
IC = "instantiates-canonical"
PD_DEP = "http://hl7.org/fhir/SearchParameter/PlanDefinition-depends-on|4.0.1"


def _rows(conn, sql, *args):
    cur = conn.cursor()
    try:
        cur.execute(sql, list(args))
        return [tuple(r) for r in cur.fetchall()]
    finally:
        cur.close()


def _register(engine, conn, denylist=()):
    """Register with no json links, then rebuild."""
    try:
        engine.fhir_graph_register(denylist=list(denylist), json_links=[])
    except TypeError:  # the engine grows json_links in US2
        engine.fhir_graph_register(denylist=list(denylist))
        cur = conn.cursor()
        cur.execute("UPDATE Graph_KG.fhir_graphs SET json_links = '[]' WHERE graph_id = ?", [GRAPH])
        conn.commit()
        cur.close()
    return engine.fhir_graph_rebuild(GRAPH)


@pytest.fixture(scope="module")
def kf(fhir_conn, fhir_engine):
    """Stage 1 loaded and synced, into a graph with no json links."""
    _register(fhir_engine, fhir_conn)
    ld = FhirLoader(fhir_conn)
    ld.knowledge("stage1")
    fhir_engine.fhir_graph_sync(GRAPH)
    yield ld
    # Leave the graph at its default registration.
    fhir_engine.fhir_graph_register(denylist=[])
    fhir_engine.fhir_graph_rebuild(GRAPH)


def k(ld, rtype, name):
    return f"{rtype}/{ld.id(name)}"


def _edges(conn, source):
    return set(_rows(conn, "SELECT p, o_id FROM Graph_KG.rdf_edges WHERE graph_id = ? AND s = ?", GRAPH, source))


def _edge_ids(conn):
    return {
        r[0]: r[1:]
        for r in _rows(conn, "SELECT edge_id, s, p, o_id FROM Graph_KG.rdf_edges WHERE graph_id = ?", GRAPH)
    }


def _unresolved(conn, source):
    return set(
        _rows(
            conn,
            "SELECT param, target, reason FROM Graph_KG.fhir_unresolved WHERE graph_id = ? AND source = ?",
            GRAPH,
            source,
        )
    )


def _refs(conn, source):
    return set(
        _rows(
            conn,
            "SELECT param, url, version, origin, kind FROM Graph_KG.fhir_canonical_refs"
            " WHERE graph_id = ? AND source = ?",
            GRAPH,
            source,
        )
    )


def _defs(conn, key):
    return _rows(conn, "SELECT url, version FROM Graph_KG.fhir_definitions WHERE graph_id = ? AND rsrc_key = ?", GRAPH, key)


def _state(conn):
    """Everything US1 writes, as sets, for the sync-equals-rebuild comparison."""
    q = {
        "edges": "SELECT s, p, o_id, qualifiers FROM Graph_KG.rdf_edges WHERE graph_id = ?",
        "unresolved": "SELECT source, param, target, reason FROM Graph_KG.fhir_unresolved WHERE graph_id = ?",
        "defs": "SELECT rsrc_key, url, version FROM Graph_KG.fhir_definitions WHERE graph_id = ?",
        "refs": "SELECT source, param, url, version, origin, kind FROM Graph_KG.fhir_canonical_refs WHERE graph_id = ?",
        "nodes": "SELECT node_id FROM Graph_KG.nodes WHERE graph_id = ?",
    }
    return {name: set(_rows(conn, sql, GRAPH)) for name, sql in q.items()}


# --- stage 1 ------------------------------------------------------------------------


def test_definitions_recorded(fhir_conn, kf):
    assert _defs(fhir_conn, k(kf, "Library", "l1")) == [(kf.url("Library/L"), "1.0.0")]
    assert _defs(fhir_conn, k(kf, "Library", "l2")) == [(kf.url("Library/L"), "2.0.0")]
    assert _defs(fhir_conn, k(kf, "Library", "b")) == [(kf.url("Library/B"), None)]
    assert _defs(fhir_conn, k(kf, "PlanDefinition", "pd1")) == [(kf.url("PlanDefinition/pd1"), None)]
    # A resource with no url, and a type with no url param, have no row.
    assert _defs(fhir_conn, k(kf, "PlanDefinition", "pd2")) == []
    assert _defs(fhir_conn, k(kf, "Patient", "p1")) == []


def test_stage1_pd2_outcomes(fhir_conn, kf):
    pd2 = k(kf, "PlanDefinition", "pd2")
    L = kf.url("Library/L")
    assert _edges(fhir_conn, pd2) == {(DEP, k(kf, "Library", "l2"))}
    assert _unresolved(fhir_conn, pd2) == {
        (DEP, f"{L}|3.0.0", "version-not-found"),
        (DEP, L, "ambiguous"),
        (DEP, "http://hl7.org/fhir/Library/x", "no-definition"),
    }
    assert _refs(fhir_conn, pd2) == {
        (DEP, L, "2.0.0", "index", "canonical"),
        (DEP, L, "3.0.0", "index", "canonical"),
        (DEP, L, None, "index", "canonical"),
        (DEP, "http://hl7.org/fhir/Library/x", None, "index", "canonical"),
    }


def test_stage1_merged_depends_on(fhir_conn, kf):
    """With no json links the index's union stands: library values are depends-on."""
    pd1 = k(kf, "PlanDefinition", "pd1")
    assert _edges(fhir_conn, pd1) == {(DEP, k(kf, "Library", "l1")), (DEP, k(kf, "Library", "b"))}
    assert _unresolved(fhir_conn, pd1) == set()
    pd3 = k(kf, "PlanDefinition", "pd3")
    assert _edges(fhir_conn, pd3) == {(DEP, k(kf, "Library", "b"))}


def test_stage1_ad1_and_cp1(fhir_conn, kf):
    ad1 = k(kf, "ActivityDefinition", "ad1")
    assert _edges(fhir_conn, ad1) == set()
    assert _unresolved(fhir_conn, ad1) == {(DEP, kf.url("Library/U2"), "no-definition")}
    cp1 = k(kf, "CarePlan", "cp1")
    assert _edges(fhir_conn, cp1) == {
        ("subject", k(kf, "Patient", "p1")),
        ("patient", k(kf, "Patient", "p1")),
        (IC, k(kf, "PlanDefinition", "pd1")),
    }
    assert _unresolved(fhir_conn, cp1) == set()


def test_canonical_edge_qualifier(fhir_conn, kf):
    rows = _rows(
        fhir_conn,
        "SELECT qualifiers FROM Graph_KG.rdf_edges WHERE graph_id = ? AND s = ? AND o_id = ?",
        GRAPH,
        k(kf, "PlanDefinition", "pd2"),
        k(kf, "Library", "l2"),
    )
    assert rows == [('{"searchParam":"' + PD_DEP + '"}',)]


def test_status_counts_new_reasons(fhir_engine, kf):
    st = fhir_engine.fhir_graph_status(GRAPH)
    for reason in ("version-not-found", "ambiguous", "no-definition"):
        assert st["unresolved"][reason] >= 1, reason
    assert st["canonical"] >= 9


# --- stages 2-4 ---------------------------------------------------------------------


def test_stage2_new_carrier(fhir_conn, fhir_engine, kf):
    kf.knowledge("stage2")
    fhir_engine.fhir_graph_sync(GRAPH)
    assert _defs(fhir_conn, k(kf, "Library", "m2")) == [(kf.url("Library/U1"), None)]


def test_stage3_url_edit_resolves_in_one_sync(fhir_conn, fhir_engine, kf):
    ad1 = k(kf, "ActivityDefinition", "ad1")
    kf.knowledge("stage3")
    fhir_engine.fhir_graph_sync(GRAPH)
    assert _defs(fhir_conn, k(kf, "Library", "m")) == [(kf.url("Library/U2"), None)]
    assert _edges(fhir_conn, ad1) == {(DEP, k(kf, "Library", "m"))}
    assert _unresolved(fhir_conn, ad1) == set()


def test_stage4_delete_resolves_in_one_sync_and_never_repoints(fhir_conn, fhir_engine, kf):
    pd2 = k(kf, "PlanDefinition", "pd2")
    L = kf.url("Library/L")
    before = _edge_ids(fhir_conn)
    kf.knowledge("stage4")
    fhir_engine.fhir_graph_sync(GRAPH)
    assert _edges(fhir_conn, pd2) == {(DEP, k(kf, "Library", "l1"))}
    assert _unresolved(fhir_conn, pd2) == {
        (DEP, f"{L}|2.0.0", "version-not-found"),
        (DEP, f"{L}|3.0.0", "version-not-found"),
        (DEP, "http://hl7.org/fhir/Library/x", "no-definition"),
    }
    assert _defs(fhir_conn, k(kf, "Library", "l2")) == []
    after = _edge_ids(fhir_conn)
    for eid in before.keys() & after.keys():
        assert before[eid] == after[eid], eid
    # The edge to l1 is a new row, not the l2 edge moved.
    new = [eid for eid, (s, p, o) in after.items() if s == pd2 and o == k(kf, "Library", "l1")]
    assert new and new[0] not in before


# --- invariants ---------------------------------------------------------------------


def test_fault_rolls_back_definitions_and_refs(fhir_conn, fhir_engine, kf):
    import iris

    lib = {
        "resourceType": "Library",
        "id": kf.id("lf"),
        "url": kf.url("Library/F"),
        "status": "active",
        "type": {"coding": [{"code": "logic-library"}]},
    }
    pd = {
        "resourceType": "PlanDefinition",
        "id": kf.id("pdf"),
        "status": "active",
        "relatedArtifact": [{"type": "depends-on", "resource": kf.url("Library/F")}],
    }
    fhir_engine.fhir_graph_sync(GRAPH)
    before = _state(fhir_conn)
    kf.put(lib)
    kf.put(pd)
    irisobj = iris.createIRIS(fhir_conn)
    irisobj.set("beforecommit", "^IVG.FHIRGraphFault", GRAPH)
    try:
        with pytest.raises(FHIRGraphError):
            fhir_engine.fhir_graph_sync(GRAPH)
    finally:
        irisobj.kill("^IVG.FHIRGraphFault", GRAPH)
    assert _state(fhir_conn) == before
    fhir_engine.fhir_graph_sync(GRAPH)
    assert _edges(fhir_conn, f"PlanDefinition/{kf.id('pdf')}") == {(DEP, f"Library/{kf.id('lf')}")}


def test_delete_source_removes_refs_edges_unresolved(fhir_conn, fhir_engine, kf):
    pd2 = k(kf, "PlanDefinition", "pd2")
    kf.delete("PlanDefinition", kf.id("pd2"))
    fhir_engine.fhir_graph_sync(GRAPH)
    assert _edges(fhir_conn, pd2) == set()
    assert _unresolved(fhir_conn, pd2) == set()
    assert _refs(fhir_conn, pd2) == set()


def test_deleting_a_definition_does_not_leave_deleted_rows(fhir_conn, fhir_engine, kf):
    """A canonical edge to a deleted definition becomes a canonical reason, never
    `deleted`: the 231 reason is for references to keys."""
    pd3 = k(kf, "PlanDefinition", "pd3")
    kf.delete("Library", kf.id("b"))
    fhir_engine.fhir_graph_sync(GRAPH)
    assert _edges(fhir_conn, pd3) == set()
    assert _unresolved(fhir_conn, pd3) == {(DEP, kf.url("Library/B"), "no-definition")}


def test_sync_equals_rebuild(fhir_conn, fhir_engine, kf):
    fhir_engine.fhir_graph_sync(GRAPH)
    synced = _state(fhir_conn)
    fhir_engine.fhir_graph_rebuild(GRAPH)
    rebuilt = _state(fhir_conn)
    for part in synced:
        assert synced[part] == rebuilt[part], (part, sorted(synced[part] ^ rebuilt[part])[:10])


def test_denylist_variant(fhir_conn, fhir_engine, kf):
    cp1 = k(kf, "CarePlan", "cp1")
    try:
        _register(fhir_engine, fhir_conn, denylist=[f"CarePlan.{IC}"])
        assert _edges(fhir_conn, cp1) == {("subject", k(kf, "Patient", "p1")), ("patient", k(kf, "Patient", "p1"))}
        assert _refs(fhir_conn, cp1) == set()
        assert _unresolved(fhir_conn, cp1) == set()
        assert fhir_engine.fhir_graph_status(GRAPH)["last_counts"]["params"][f"CarePlan.{IC}"] == 0
    finally:
        _register(fhir_engine, fhir_conn)
    assert (IC, k(kf, "PlanDefinition", "pd1")) in _edges(fhir_conn, cp1)
