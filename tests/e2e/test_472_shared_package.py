"""4.7.2: `initialize_schema()` under a `schema_prefix` whose class package another tool already uses.

4.7.1 refused any package that held a class IVG did not create, though 4.4.2 allowed it and ADR-0016 names the layout
as supported: a tool that keeps its own tables in the schema (here `RAG.KGEntities` and `RAG.SourceDocuments`). Now only
a class IVG would create a class of the same name over is refused, before any DDL.

Three scratch namespaces, each dropped and made anew (`IVG_NS_IVG470S1`, `IVG_NS_IVG470S2`, `IVG_NS_IVG470S3`; set by
the gate, unset outside it so the file skips):

- S1: foreign tables and a foreign function are in `RAG` first. Setup deploys beside them, a 3-node cycle gives
  personalized PageRank its closed-form scores, the foreign classes and rows are untouched afterwards, a second setup
  is accepted, and erasing the graph leaves the foreign tables standing.
- S2: a foreign table `RAG.nodes` is there first. Setup is refused naming it, and nothing was created.
- S3: a foreign function `RAG.JSON_VALUE` (class `RAG.funcJSONVALUE`), the class IVG's own function compiles to, is
  there first. Refused the same way, and the function still answers as before.
"""

from __future__ import annotations

import os

import pytest

from tests.e2e import ns262, scratch_vars

pytestmark = [
    pytest.mark.e2e,
    ns262.requires_container,
    scratch_vars.requires("IVG_NS_IVG470S1", "IVG_NS_IVG470S2", "IVG_NS_IVG470S3"),
]

NS1 = os.environ.get("IVG_NS_IVG470S1", "")
NS2 = os.environ.get("IVG_NS_IVG470S2", "")
NS3 = os.environ.get("IVG_NS_IVG470S3", "")
SCHEMA = "RAG"
ALPHA = 0.85


def _count(conn, sql, params=None):
    return ns262.rows(conn, sql, params)[0][0]


def _classes(conn, package="RAG"):
    return {r[0] for r in ns262.rows(
        conn, "SELECT Name FROM %Dictionary.ClassDefinition WHERE UPPER(Name) %STARTSWITH ?", [package + "."])}


def _tables(conn, schema="RAG"):
    return {r[0] for r in ns262.rows(
        conn, "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE UPPER(TABLE_SCHEMA) = ?", [schema])}


def _engine(conn, ns):
    from iris_vector_graph import IRISGraphEngine

    return IRISGraphEngine(conn, embedding_dimension=4, namespace=ns, schema_prefix=SCHEMA, native_cypher=False)


def _time_changed(conn, name):
    return _count(conn, "SELECT TimeChanged FROM %Dictionary.ClassDefinition WHERE Name = ?", [name])


@pytest.fixture(scope="module")
def shared():
    ns262.recreate(NS1)
    conn = ns262.connect(NS1)
    cur = conn.cursor()
    # the other tool's own tables, with a row each, and a function of its own whose name merely starts with `func`
    cur.execute("CREATE TABLE RAG.KGEntities (id INTEGER)")
    cur.execute("CREATE TABLE RAG.SourceDocuments (id INTEGER)")
    cur.execute("INSERT INTO RAG.KGEntities (id) VALUES (7)")
    cur.execute("INSERT INTO RAG.SourceDocuments (id) VALUES (11)")
    cur.execute("CREATE FUNCTION RAG.Foo(a INTEGER) RETURNS INTEGER LANGUAGE OBJECTSCRIPT { Quit a + 1 }")
    conn.commit()
    foreign = ("RAG.KGEntities", "RAG.SourceDocuments", "RAG.funcFoo")
    before = {"classes": _classes(conn), "changed": {c: _time_changed(conn, c) for c in foreign}}
    engine = _engine(conn, NS1)
    status = engine.initialize_schema()
    try:
        yield {"conn": conn, "engine": engine, "status": status, "before": before, "foreign": foreign}
    finally:
        cur.close()
        conn.close()


def test_the_other_tools_classes_were_there_and_setup_deployed_beside_them(shared):
    conn = shared["conn"]
    assert {"RAG.KGEntities", "RAG.SourceDocuments", "RAG.funcFoo"} <= shared["before"]["classes"]
    assert shared["status"]["schema"] == SCHEMA and shared["status"]["dry_run"] is False
    assert {"nodes", "rdf_edges", "rdf_props", "rdf_labels"} <= _tables(conn)
    assert {"RAG.nodes", "RAG.SqlProcs", "RAG.funcCYCMP"} <= _classes(conn)
    assert not _tables(conn, "GRAPH_KG"), "IVG made tables in the default schema as well"


def test_personalized_pagerank_over_a_cycle_gives_the_closed_form_scores(shared):
    """Seed A on A -> B -> C -> A, damping a: x_a = (1 - a) / (1 - a^3), x_b = a x_a, x_c = a^2 x_a (nothing is
    dangling, the walk restarts at A with probability 1 - a)."""
    engine = shared["engine"]
    for node in ("A", "B", "C"):
        assert engine.create_node(node) is True
    for s, o in (("A", "B"), ("B", "C"), ("C", "A")):
        assert engine.create_edge(s, "next", o) is True
    x_a = (1 - ALPHA) / (1 - ALPHA ** 3)
    want = {"A": x_a, "B": ALPHA * x_a, "C": ALPHA ** 2 * x_a}
    got = engine.kg_PERSONALIZED_PAGERANK(["A"], damping_factor=ALPHA)
    assert set(got) - {"A"} == {"B", "C"}, ("nodes beyond the seed", got)
    for node, score in want.items():
        assert got[node] == pytest.approx(score, abs=1e-4), (node, got)


def test_the_other_tools_classes_and_rows_are_untouched(shared):
    conn, before = shared["conn"], shared["before"]
    assert set(shared["foreign"]) <= _classes(conn)
    assert {r[0] for r in ns262.rows(conn, "SELECT id FROM RAG.KGEntities")} == {7}
    assert {r[0] for r in ns262.rows(conn, "SELECT id FROM RAG.SourceDocuments")} == {11}
    assert _count(conn, "SELECT RAG.Foo(41)") == 42
    # not recompiled either: the class's time stamp is what it was before IVG ran
    assert {c: _time_changed(conn, c) for c in shared["foreign"]} == before["changed"]


def test_a_second_setup_is_accepted_and_still_leaves_them(shared):
    conn = shared["conn"]
    again = _engine(conn, NS1).initialize_schema()
    assert again["schema"] == SCHEMA and again["schema_recorded"] == "existing"
    assert set(shared["foreign"]) <= _classes(conn)
    assert {r[0] for r in ns262.rows(conn, "SELECT id FROM RAG.KGEntities")} == {7}


def test_erasing_everything_leaves_the_other_tools_tables(shared):
    engine, conn = shared["engine"], shared["conn"]
    assert engine.erase_all() >= 3
    assert _count(conn, "SELECT COUNT(*) FROM RAG.nodes") == 0
    assert {"KGEntities", "SourceDocuments"} <= _tables(conn)
    assert {r[0] for r in ns262.rows(conn, "SELECT id FROM RAG.SourceDocuments")} == {11}


def test_a_foreign_table_named_like_an_ivg_table_is_refused_and_nothing_is_created():
    from iris_vector_graph.schema_name import IVGSchemaError

    ns262.recreate(NS2)
    conn = ns262.connect(NS2)
    try:
        cur = conn.cursor()
        cur.execute("CREATE TABLE RAG.nodes (id INTEGER)")
        cur.execute("CREATE TABLE RAG.KGEntities (id INTEGER)")
        conn.commit()
        before = (_classes(conn), _tables(conn), _tables(conn, "GRAPH_KG"))
        with pytest.raises(IVGSchemaError) as raised:
            _engine(conn, NS2).initialize_schema()
        text = str(raised.value)
        assert "RAG.nodes" in text and "Nothing was created" in text and "KGEntities" not in text
        # assert the database, not the message: no class, no table, no record
        assert (_classes(conn), _tables(conn), _tables(conn, "GRAPH_KG")) == before
        assert _tables(conn) == {"nodes", "KGEntities"}
        native = ns262.native(ns262.connect(NS2))
        assert native.get("^IVG.Deploy", "schema") in (None, "")
        assert not int(native.isDefined("^oddDEF", "RAG.SqlProcs") or 0)
        cur.close()
    finally:
        conn.close()


def test_a_foreign_function_class_with_the_name_ivgs_function_compiles_to_is_refused():
    from iris_vector_graph.schema_name import IVGSchemaError

    ns262.recreate(NS3)
    conn = ns262.connect(NS3)
    try:
        cur = conn.cursor()
        cur.execute("CREATE FUNCTION RAG.JSON_VALUE(a INTEGER) RETURNS INTEGER LANGUAGE OBJECTSCRIPT { Quit a * 2 }")
        conn.commit()
        assert "RAG.funcJSONVALUE" in _classes(conn)
        before = (_classes(conn), _tables(conn))
        with pytest.raises(IVGSchemaError) as raised:
            _engine(conn, NS3).initialize_schema()
        assert "RAG.funcJSONVALUE" in str(raised.value) and "Nothing was created" in str(raised.value)
        assert (_classes(conn), _tables(conn)) == before
        assert _count(conn, "SELECT RAG.JSON_VALUE(21)") == 42, "the other tool's function was replaced"
        cur.close()
    finally:
        conn.close()


def test_a_namespace_in_an_interrupted_rdf_edges_rescue_passes_the_clash_check():
    """The rescue drops `rdf_edges` and keeps its rows in the staging table; the retry that resumes it runs setup's
    clash check with `nodes` and the staging table in the schema. IVG's own classes are not clashes then (4.7.1 took
    them for IVG's too); `nodes` alone, the other tool's, still is."""
    from iris_vector_graph.schema import RESCUE_STAGING_TABLE
    from iris_vector_graph.schema_name import IVGSchemaError, colliding_classes, refuse_package_clash

    ns262.recreate(NS3)
    conn = ns262.connect(NS3)
    try:
        cur = conn.cursor()
        cur.execute("CREATE TABLE RAG.nodes (node_id VARCHAR(64))")
        cur.execute("CREATE TABLE RAG.KGEntities (id INTEGER)")
        conn.commit()
        assert colliding_classes(conn, SCHEMA) == ["RAG.nodes"]
        with pytest.raises(IVGSchemaError, match="RAG.nodes"):
            refuse_package_clash(conn, SCHEMA)
        cur.execute(f"CREATE TABLE RAG.{RESCUE_STAGING_TABLE} (s VARCHAR(64))")
        conn.commit()
        assert colliding_classes(conn, SCHEMA) == []
        refuse_package_clash(conn, SCHEMA)
        cur.close()
    finally:
        conn.close()
