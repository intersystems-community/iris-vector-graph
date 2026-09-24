"""Spec 232 US2: links the search index cannot see come from json_links (R4-R6).

The knowledge fixture goes in stage by stage, as in test_232_fhir_canonical_e2e, but
the graph keeps its default json links: `<Type>.library` for each split column, plus
cqf-library. Tests run in file order and share the loaded fixture.
"""

from __future__ import annotations

import json

import pytest

from tests.e2e.fhir_conftest import GRAPH, FhirLoader, connect

pytestmark = [pytest.mark.e2e]

DEP = "depends-on"
LIB = "library"
CQF_URL = "http://hl7.org/fhir/StructureDefinition/cqf-library"
CQF = f"extension:{CQF_URL}"
PD_LIB = "PlanDefinition.library"
PD_DEP = "http://hl7.org/fhir/SearchParameter/PlanDefinition-depends-on|4.0.1"
USES_URL = "http://ex.org/ivg232/StructureDefinition/uses"
USES = f"extension:{USES_URL}"


def _rows(conn, sql, *args):
    cur = conn.cursor()
    try:
        cur.execute(sql, list(args))
        return [tuple(r) for r in cur.fetchall()]
    finally:
        cur.close()


def k(ld, rtype, name):
    return f"{rtype}/{ld.id(name)}"


def _edges(conn, source):
    return set(_rows(conn, "SELECT p, o_id FROM Graph_KG.rdf_edges WHERE graph_id = ? AND s = ?", GRAPH, source))


def _qualifiers(conn, source, p, target):
    return [
        json.loads(r[0])
        for r in _rows(
            conn,
            "SELECT qualifiers FROM Graph_KG.rdf_edges WHERE graph_id = ? AND s = ? AND p = ? AND o_id = ?",
            GRAPH,
            source,
            p,
            target,
        )
    ]


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


def _stored_links(conn):
    raw = _rows(conn, "SELECT json_links FROM Graph_KG.fhir_graphs WHERE graph_id = ?", GRAPH)[0][0]
    return json.loads(raw) if raw else None


@pytest.fixture(scope="module")
def jl(fhir_conn, fhir_engine):
    """Stage 1 loaded and synced, into a graph with the default json links."""
    out = fhir_engine.fhir_graph_register(denylist=[])
    if not out.get("rebuilt"):
        fhir_engine.fhir_graph_rebuild(GRAPH)
    ld = FhirLoader(fhir_conn)
    ld.knowledge("stage1")
    fhir_engine.fhir_graph_sync(GRAPH)
    yield ld
    # Leave the graph at its default registration.
    out = fhir_engine.fhir_graph_register(denylist=[])
    if not out.get("rebuilt"):
        fhir_engine.fhir_graph_rebuild(GRAPH)


# --- the default list ---------------------------------------------------------------


def test_default_list_is_data_driven(fhir_conn, fhir_engine, jl):
    links = _stored_links(fhir_conn)
    assert links[-1] == CQF
    lib_entries = links[:-1]
    assert lib_entries == sorted(lib_entries)
    assert PD_LIB in lib_entries
    assert "Measure.library" in lib_entries
    assert "ActivityDefinition.library" in lib_entries
    # Library's own depends-on has no .library branch.
    assert "Library.library" not in lib_entries
    assert all(e.endswith(".library") for e in lib_entries)


def test_register_reply_has_links(fhir_engine, jl):
    out = fhir_engine.fhir_graph_register(denylist=[])
    assert out["json_links"][-1] == CQF
    assert not out.get("rebuilt")  # unchanged list, no rebuild


# --- stage 1 ------------------------------------------------------------------------


def test_library_split(fhir_conn, jl):
    pd1 = k(jl, "PlanDefinition", "pd1")
    edges = _edges(fhir_conn, pd1)
    assert (LIB, k(jl, "Library", "l1")) in edges
    assert (DEP, k(jl, "Library", "b")) in edges
    assert (DEP, k(jl, "Library", "l1")) not in edges
    assert _qualifiers(fhir_conn, pd1, LIB, k(jl, "Library", "l1")) == [{"jsonLink": PD_LIB}]
    assert _qualifiers(fhir_conn, pd1, DEP, k(jl, "Library", "b")) == [{"searchParam": PD_DEP}]


def test_url_in_both_arrays_gets_both_edges(fhir_conn, jl):
    pd3 = k(jl, "PlanDefinition", "pd3")
    b = k(jl, "Library", "b")
    assert _edges(fhir_conn, pd3) == {(LIB, b), (DEP, b)}


def test_cqf_library_on_nested_action(fhir_conn, jl):
    pd1 = k(jl, "PlanDefinition", "pd1")
    m = k(jl, "Library", "m")
    assert (CQF_URL.rsplit("/", 1)[1], m) in _edges(fhir_conn, pd1)
    assert _qualifiers(fhir_conn, pd1, "cqf-library", m) == [{"jsonLink": CQF}]
    assert _unresolved(fhir_conn, pd1) == set()


def test_refs_record_origin(fhir_conn, jl):
    pd1 = k(jl, "PlanDefinition", "pd1")
    assert _refs(fhir_conn, pd1) == {
        (LIB, jl.url("Library/L"), "1.0.0", PD_LIB, "canonical"),
        (DEP, jl.url("Library/B"), None, "index", "canonical"),
        ("cqf-library", jl.url("Library/U1"), None, CQF, "canonical"),
    }


def test_status_json_links_counts(fhir_engine, jl):
    counts = fhir_engine.fhir_graph_status(GRAPH)["json_links"]
    assert counts[PD_LIB] >= 2  # pd1 and pd3
    assert counts[CQF] >= 1
    assert "Measure.library" in counts  # every entry, 0 included
    assert all(isinstance(v, int) for v in counts.values())


# --- stages 2 and 3 ------------------------------------------------------------------


def test_stage2_cqf_becomes_ambiguous(fhir_conn, fhir_engine, jl):
    pd1 = k(jl, "PlanDefinition", "pd1")
    jl.knowledge("stage2")
    fhir_engine.fhir_graph_sync(GRAPH)
    edges = _edges(fhir_conn, pd1)
    assert not any(p == "cqf-library" for p, _ in edges)
    assert _unresolved(fhir_conn, pd1) == {("cqf-library", jl.url("Library/U1"), "ambiguous")}


def test_stage3_cqf_resolves_to_the_remaining_carrier(fhir_conn, fhir_engine, jl):
    pd1 = k(jl, "PlanDefinition", "pd1")
    jl.knowledge("stage3")
    fhir_engine.fhir_graph_sync(GRAPH)
    assert ("cqf-library", k(jl, "Library", "m2")) in _edges(fhir_conn, pd1)
    assert _unresolved(fhir_conn, pd1) == set()


# --- owner links --------------------------------------------------------------------


def test_owner_extension_link(fhir_conn, fhir_engine, jl):
    """An extra extension entry: a valueCanonical resolves by the US1 rule, a
    valueReference by the 231 rule."""
    pdx = {
        "resourceType": "PlanDefinition",
        "id": jl.id("pdx"),
        "status": "active",
        "extension": [
            {"url": USES_URL, "valueCanonical": jl.url("Library/L") + "|1.0.0"},
            {"url": USES_URL, "valueReference": {"reference": f"Patient/{jl.id('p1')}"}},
            {"url": USES_URL, "valueCanonical": jl.url("Library/L") + "|9.9.9"},
        ],
    }
    jl.put(pdx)
    links = _stored_links(fhir_conn) + [USES]
    out = fhir_engine.fhir_graph_register(denylist=[], json_links=links)
    assert out["rebuilt"] is True, out
    assert out["json_links"] == links
    src = k(jl, "PlanDefinition", "pdx")
    assert _edges(fhir_conn, src) == {("uses", k(jl, "Library", "l1")), ("uses", k(jl, "Patient", "p1"))}
    assert _qualifiers(fhir_conn, src, "uses", k(jl, "Patient", "p1")) == [{"jsonLink": USES}]
    assert _unresolved(fhir_conn, src) == {("uses", jl.url("Library/L") + "|9.9.9", "version-not-found")}
    assert ("uses", f"Patient/{jl.id('p1')}", None, USES, "reference") in _refs(fhir_conn, src)
    assert len([r for r in _refs(fhir_conn, src) if r[3] == USES]) == 3
    # Graph-wide: other runs' pdx resources carry the same extension.
    assert fhir_engine.fhir_graph_status(GRAPH)["json_links"][USES] >= 3


def test_owner_link_reference_to_missing_key(fhir_conn, fhir_engine, jl):
    src = k(jl, "PlanDefinition", "pdx")
    jl.delete("Patient", jl.id("p1"))
    fhir_engine.fhir_graph_sync(GRAPH)
    assert ("uses", k(jl, "Patient", "p1"), "deleted") in _unresolved(fhir_conn, src)


# --- registration errors -------------------------------------------------------------


@pytest.mark.parametrize(
    "links,message",
    [
        (["plandefinition.library"], "json link 'plandefinition.library' is not 'Type.element' or 'extension:<url>'"),
        ([PD_LIB, "extension:http://ex.org/a/library"], "both give predicate 'library'"),
        ([CQF, "extension:http://ex.org/other/cqf-library"], "both give predicate 'cqf-library'"),
    ],
)
def test_bad_list_fails_and_changes_nothing(fhir_conn, fhir_engine, jl, links, message):
    import iris

    before = _stored_links(fhir_conn)
    raw = iris.createIRIS(fhir_conn).classMethodValue(
        "Graph.KG.FHIRGraph", "Register", "", "[]", 60, json.dumps(links)
    )
    out = json.loads(raw)
    assert out["status"] == "error", out
    assert message in out["error"]
    assert _stored_links(fhir_conn) == before


def test_python_rejects_before_the_round_trip(fhir_engine, jl):
    with pytest.raises(ValueError, match="plandefinition.library"):
        fhir_engine.fhir_graph_register(json_links=["plandefinition.library"])


# --- change and rebuild ---------------------------------------------------------------


def test_busy_rebuild_stores_the_list(fhir_conn, fhir_engine, jl):
    import iris

    links = [e for e in _stored_links(fhir_conn) if e != USES]
    other = connect()
    try:
        iris.createIRIS(other).classMethodVoid("IVGTest.FHIRLoad", "HoldLock", GRAPH)
        out = fhir_engine.fhir_graph_register(denylist=[], json_links=links)
    finally:
        iris.createIRIS(other).classMethodVoid("IVGTest.FHIRLoad", "ReleaseLock", GRAPH)
        other.close()
    assert out["rebuilt"] is False, out
    assert out["rebuild"]["status"] == "busy"
    assert _stored_links(fhir_conn) == links
    assert fhir_engine.fhir_graph_status(GRAPH)["last_error"] == "json_links changed; rebuild pending"
    fhir_engine.fhir_graph_rebuild(GRAPH)
    st = fhir_engine.fhir_graph_status(GRAPH)
    assert not st["last_error"]
    assert USES not in st["json_links"]


def test_removing_library_entry_restores_merged_depends_on(fhir_conn, fhir_engine, jl):
    pd1 = k(jl, "PlanDefinition", "pd1")
    links = [e for e in _stored_links(fhir_conn) if e != PD_LIB]
    out = fhir_engine.fhir_graph_register(denylist=[], json_links=links)
    assert out["rebuilt"] is True, out
    edges = _edges(fhir_conn, pd1)
    assert (DEP, k(jl, "Library", "l1")) in edges
    assert (DEP, k(jl, "Library", "b")) in edges
    assert not any(p == LIB for p, _ in edges)
    assert PD_LIB not in fhir_engine.fhir_graph_status(GRAPH)["json_links"]


def test_no_links(fhir_conn, fhir_engine, jl):
    pd1 = k(jl, "PlanDefinition", "pd1")
    out = fhir_engine.fhir_graph_register(denylist=[], json_links=[])
    assert out["rebuilt"] is True and out["json_links"] == []
    assert not any(p == "cqf-library" for p, _ in _edges(fhir_conn, pd1))
    assert fhir_engine.fhir_graph_status(GRAPH)["json_links"] == {}


def test_denied_json_predicate(fhir_conn, fhir_engine, jl):
    pd1 = k(jl, "PlanDefinition", "pd1")
    try:
        fhir_engine.fhir_graph_register(denylist=["PlanDefinition.cqf-library"])
        fhir_engine.fhir_graph_rebuild(GRAPH)
        edges = _edges(fhir_conn, pd1)
        assert not any(p == "cqf-library" for p, _ in edges)
        assert (LIB, k(jl, "Library", "l1")) in edges
        assert not any(r[0] == "cqf-library" for r in _refs(fhir_conn, pd1))
    finally:
        fhir_engine.fhir_graph_register(denylist=[])
        fhir_engine.fhir_graph_rebuild(GRAPH)


def test_sync_equals_rebuild(fhir_conn, fhir_engine, jl):
    q = {
        "edges": "SELECT s, p, o_id, qualifiers FROM Graph_KG.rdf_edges WHERE graph_id = ?",
        "unresolved": "SELECT source, param, target, reason FROM Graph_KG.fhir_unresolved WHERE graph_id = ?",
        "refs": "SELECT source, param, url, version, origin, kind FROM Graph_KG.fhir_canonical_refs WHERE graph_id = ?",
    }
    fhir_engine.fhir_graph_sync(GRAPH)
    synced = {n: set(_rows(fhir_conn, s, GRAPH)) for n, s in q.items()}
    fhir_engine.fhir_graph_rebuild(GRAPH)
    rebuilt = {n: set(_rows(fhir_conn, s, GRAPH)) for n, s in q.items()}
    for part in synced:
        assert synced[part] == rebuilt[part], (part, sorted(synced[part] ^ rebuilt[part])[:10])
