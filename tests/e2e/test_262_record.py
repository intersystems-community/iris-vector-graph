"""Spec 262 US4 (T045, T046): a namespace's IVG Schema is recorded once, inferred when unambiguous, and an engine on
another schema is refused (data-model.md's transitions; FR-005, FR-006, FR-009, FR-013).

All in IVG262RCD, built anew per fixture: an empty namespace; a 4.5.1 namespace whose IVG tables are all in `RAG`
(v4.5.1's own code, `schema_prefix="RAG"`); and a 4.5.1 namespace holding IVG tables in `Graph_KG` and in `RAG`.
Every dry run, and every refusal, is checked against a whole-namespace snapshot: catalog, row counts, and a digest of
each of IVG's globals.
"""

from __future__ import annotations

import pytest

from tests.e2e import ns262

pytestmark = [pytest.mark.e2e, ns262.requires_container]

NS = "IVG262RCD"


def _engine(conn, schema=None, **kw):
    from iris_vector_graph import IRISGraphEngine

    return IRISGraphEngine(conn, embedding_dimension=8, namespace=NS, schema_prefix=schema, native_cypher=False, **kw)


def _record(conn):
    return ns262.native(conn).get("^IVG.Deploy", "schema")


def _held(conn) -> dict:
    """The schemas holding IVG tables, with their counts: setup's own probe, run with the first-access check off."""
    engine = _engine(conn)
    with engine._schema_check_suspended():
        return engine._ivg_table_schemas()


def _build(**kw):
    from build_release_namespace import build

    return build("v4.5.1", NS, **kw)


# --------------------------------------------------------------------------- a new namespace

@pytest.fixture(scope="module")
def fresh():
    ns262.recreate(NS)
    conn = ns262.connect(NS)
    before = ns262.deep_snapshot(conn)
    dry = _engine(conn).initialize_schema(dry_run=True)
    after_dry = ns262.deep_snapshot(conn)
    status = _engine(conn).initialize_schema()
    _engine(conn).create_node("rcd:keep", labels=["Keep"])  # something for a wrong write to disturb
    conn.commit()
    yield {"conn": conn, "dry": dry, "status": status, "before": before, "after_dry": after_dry}
    conn.close()


def test_a_new_namespace_records_graph_kg(fresh):
    assert fresh["status"]["schema"] == "Graph_KG" and fresh["status"]["schema_recorded"] == "new"
    assert _record(fresh["conn"]) == "Graph_KG"


def test_the_new_namespace_dry_run_reports_and_writes_nothing(fresh):
    assert fresh["dry"]["dry_run"] is True
    assert fresh["dry"]["schema"] == "Graph_KG" and fresh["dry"]["schema_recorded"] == "new"
    assert fresh["after_dry"] == fresh["before"]


def test_a_second_setup_finds_the_record(fresh):
    assert _engine(fresh["conn"]).initialize_schema(dry_run=True)["schema_recorded"] == "existing"


# T046
@pytest.mark.parametrize("call", [
    pytest.param(lambda e: e.create_node("rcd:x", labels=["X"]), id="create_node"),
    pytest.param(lambda e: e.execute_cypher("CREATE (:X {name: 'x'})"), id="cypher_create"),
    pytest.param(lambda e: e.execute_cypher("MATCH (n) RETURN count(n)"), id="cypher_read"),
    pytest.param(lambda e: e.create_edge_temporal("rcd:a", "R", "rcd:b", 1700000000), id="temporal_write"),
    pytest.param(lambda e: e.erase_graph("g1"), id="erase_graph"),
    pytest.param(lambda e: e.initialize_schema(), id="initialize_schema"),
])
def test_an_engine_on_another_schema_is_refused_on_first_access_and_changes_nothing(fresh, call):
    from iris_vector_graph import IVGSchemaError

    conn = fresh["conn"]
    before = ns262.deep_snapshot(conn)
    engine = _engine(conn, "RAG")
    with pytest.raises(IVGSchemaError) as raised:
        call(engine)
    text = str(raised.value)
    for part in ("Graph_KG", "RAG", "one IVG Schema per namespace", "use a separate namespace", NS):
        assert part in text, text
    assert ns262.deep_snapshot(conn) == before


# --------------------------------------------------------------------------- 4.5.1, every IVG table in RAG

@pytest.fixture(scope="module")
def rag_only(fresh):
    _build(schema_prefix="RAG", only_prefix=True)
    conn = ns262.connect(NS)
    yield conn
    conn.close()


def test_the_rag_fixture_is_a_451_namespace_with_its_tables_in_rag(rag_only):
    held = _held(rag_only)
    assert list(held) == ["RAG"] and held["RAG"]["nodes"] > 0 and held["RAG"]["rdf_edges"] > 0
    assert _record(rag_only) is None


def test_a_default_engine_on_the_rag_namespace_is_told_to_name_rag_and_nothing_is_recorded(rag_only):
    from iris_vector_graph import IVGSchemaError

    before = ns262.deep_snapshot(rag_only)
    with pytest.raises(IVGSchemaError) as raised:
        _engine(rag_only).initialize_schema()
    text = str(raised.value)
    assert "RAG (" in text and "schema_prefix='RAG'" in text and "Nothing was recorded" in text, text
    assert ns262.deep_snapshot(rag_only) == before


def test_the_rag_namespace_is_inferred_and_the_dry_run_writes_nothing(rag_only):
    before = ns262.deep_snapshot(rag_only)
    dry = _engine(rag_only, "RAG").initialize_schema(dry_run=True)
    assert (dry["schema"], dry["schema_recorded"], dry["other_schemas"]) == ("RAG", "inferred", {})
    assert ns262.deep_snapshot(rag_only) == before


def test_the_rag_namespace_records_rag_and_the_objectscript_features_read_rag(rag_only):
    engine = _engine(rag_only, "RAG")
    status = engine.initialize_schema()
    assert (status["schema"], status["schema_recorded"]) == ("RAG", "inferred")
    assert _record(rag_only) == "RAG"
    one = lambda sql: ns262.rows(rag_only, sql)[0][0]  # noqa: E731

    # erase (Graph.KG.Eraser): a node and its edge go from RAG's tables
    engine.create_node("rcd:e1", labels=["Erase"])
    engine.create_node("rcd:e2", labels=["Erase"])
    engine.create_edge("rcd:e1", "GONE", "rcd:e2")
    assert one("SELECT COUNT(*) FROM RAG.rdf_edges WHERE p = 'GONE'") == 1
    engine.delete_node("rcd:e1")
    assert one("SELECT COUNT(*) FROM RAG.nodes WHERE node_id = 'rcd:e1'") == 0
    assert one("SELECT COUNT(*) FROM RAG.rdf_edges WHERE p = 'GONE'") == 0

    # temporal write (Graph.KG.TemporalIndex) and its window read
    engine.create_edge_temporal("rcd:t1", "AT", "rcd:t2", 1_700_000_000, weight=2.5)
    window = engine.get_edges_in_window("rcd:t1", "AT", 1_699_999_000, 1_700_001_000)
    assert [(w["s"], w["p"], w["o"], float(w["w"])) for w in window] == [("rcd:t1", "AT", "rcd:t2", 2.5)]
    # the Graph_KG.nodes an ObjectScript class naming Graph_KG would have needed is not there
    assert one("SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'Graph_KG' "
               "AND TABLE_NAME = 'nodes'") == 0


def test_a_record_compares_without_case(rag_only):
    """Recorded as `rag`: an engine on `RAG` is the same schema, one on `RAG2` is not."""
    from iris_vector_graph import IVGSchemaError

    nat = ns262.native(rag_only)
    nat.set("rag", "^IVG.Deploy", "schema")
    try:
        assert _engine(rag_only, "RAG").get_node_count() > 0
        with pytest.raises(IVGSchemaError) as raised:
            _engine(rag_only, "RAG2").get_node_count()
        assert raised.value.recorded == "rag" and raised.value.requested == "RAG2"
    finally:
        nat.set("RAG", "^IVG.Deploy", "schema")


# --------------------------------------------------------------------------- 4.5.1, IVG tables in Graph_KG and RAG

@pytest.fixture(scope="module")
def two_schemas(rag_only):
    _build()
    _build(keep=True, seed=False, schema_prefix="RAG")  # the RAG tables, with the Graph_KG rows copied in
    conn = ns262.connect(NS)
    yield conn
    conn.close()


def test_the_two_schema_fixture_holds_ivg_tables_in_both(two_schemas):
    held = _held(two_schemas)
    assert sorted(held) == ["Graph_KG", "RAG"]
    assert all(c["nodes"] > 0 and c["rdf_edges"] > 0 for c in held.values()), held
    assert _record(two_schemas) is None


def test_setup_with_no_name_stops_listing_both_schemas_and_their_counts_and_records_nothing(two_schemas):
    from iris_vector_graph import IVGSchemaError

    held = _held(two_schemas)
    before = ns262.deep_snapshot(two_schemas)
    for dry_run in (True, False):
        with pytest.raises(IVGSchemaError) as raised:
            _engine(two_schemas).initialize_schema(dry_run=dry_run)
        text = str(raised.value)
        for s, c in held.items():
            assert f"{s} ({c['nodes']} nodes, {c['rdf_edges']} edges)" in text, text
        assert "Nothing was recorded" in text
    assert ns262.deep_snapshot(two_schemas) == before


def test_naming_rag_dry_run_reports_rag_explicit_and_the_graph_kg_tables_and_writes_nothing(two_schemas):
    held = _held(two_schemas)
    before = ns262.deep_snapshot(two_schemas)
    dry = _engine(two_schemas, "RAG").initialize_schema(dry_run=True)
    assert (dry["schema"], dry["schema_recorded"]) == ("RAG", "explicit")
    assert dry["other_schemas"] == {"Graph_KG": held["Graph_KG"]}
    assert ns262.deep_snapshot(two_schemas) == before


@pytest.mark.xfail(strict=True, raises=Exception, reason=(
    "OPEN, R084 (phase 1-5 rewrite): Graph.KG.Meta, PageRank, Traversal, Centrality, Communities and TraversalPaths "
    "declare SqlSchemaName = Graph_KG, rewritten to RAG while they stay in package Graph.KG, so IRIS maps schema RAG "
    "to package Graph.KG ($$GetPackage^%apiDDL) and setup's DDL collides with the Graph_KG table classes "
    "(-400 compiling Graph.KG.prockgKNNVEC, -324 'Graph.KG.rdfedges already has index idxedgesadj')"))
def test_naming_rag_records_rag_and_reports_the_graph_kg_tables_untouched(two_schemas):
    before = ns262.deep_snapshot(two_schemas)
    graph_kg_rows = {t: n for (s, t), n in before["rows"].items() if s == "Graph_KG"}
    status = _engine(two_schemas, "RAG").initialize_schema()
    assert (status["schema"], status["schema_recorded"]) == ("RAG", "explicit")
    assert _record(two_schemas) == "RAG"
    assert any("Graph_KG" in w and "left untouched" in w for w in status["warnings"]), status["warnings"]
    after = ns262.deep_snapshot(two_schemas)
    assert {t: n for (s, t), n in after["rows"].items() if s == "Graph_KG"} == graph_kg_rows
