"""Spec 262 US2 (T033-T035, T037): a 4.5.1 namespace upgrades without breaking anyone's SQL.

IVG262UPG is built by v4.5.1's own code (`scripts/fixtures/build_release_namespace.py`, seeded), then gains what an
application would own: `SQLUser.CY_MINE` (a name that matches a 4.5.x pattern but is on no manifest) and a role
granted EXECUTE on the 4.5.1 `SQLUser.CY_CMP`. Then 4.6.0's `initialize_schema()` (dry run first), and
`remove_legacy_objects()`.
"""

from __future__ import annotations

import pytest

from tests.e2e import ns262

pytestmark = [pytest.mark.e2e, ns262.requires_container]

NS = "IVG262UPG"
ROLE = "ivg262_app"


def _snapshot(conn) -> dict:
    return {
        "sqluser": sorted(ns262.routines(conn, "SQLUser")),
        "views": sorted(ns262.views(conn, "SQLUser")),
        "graph_kg": sorted(ns262.routines(conn, "Graph_KG")),
        "classes": sorted(ns262.classes(conn, "User.")),
        "edges": ns262.rows(conn, "SELECT COUNT(*) FROM Graph_KG.rdf_edges")[0][0],
        "record": ns262.native(conn).get("^IVG.Deploy", "schema"),
    }


@pytest.fixture(scope="module")
def upg():
    from build_release_namespace import build

    built = build("v4.5.1", NS)
    conn = ns262.connect(NS)
    cur = conn.cursor()
    cur.execute("CREATE FUNCTION SQLUser.CY_MINE() RETURNS INTEGER LANGUAGE OBJECTSCRIPT { Quit 7 }")
    try:
        cur.execute(f"CREATE ROLE {ROLE}")
    except Exception:  # the role is container-wide; an earlier run may have made it
        pass
    cur.execute(f"GRANT EXECUTE ON SQLUser.CY_CMP TO {ROLE}")
    before = _snapshot(conn)
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(conn, embedding_dimension=8, namespace=NS, native_cypher=False)
    dry = engine.initialize_schema(dry_run=True)
    after_dry = _snapshot(conn)
    status = engine.initialize_schema()
    yield {"conn": conn, "engine": engine, "built": built, "before": before, "dry": dry, "after_dry": after_dry,
           "status": status}
    conn.close()


def test_the_fixture_is_a_451_namespace(upg):
    assert "CY_CMP" in upg["before"]["sqluser"] and upg["before"]["views"] == ["nodes", "rdf_edges", "rdf_labels",
                                                                               "rdf_props"]
    assert upg["before"]["record"] is None


def test_the_dry_run_writes_nothing_and_lists_the_grant(upg):
    assert upg["after_dry"] == upg["before"]
    dry = upg["dry"]
    assert dry["dry_run"] is True and dry["schema"] == "Graph_KG" and dry["schema_recorded"] == "inferred"
    assert {(g["object"], g["replacement"], g["subject"], g["privilege"]) for g in dry["grants_copied"]} == {
        ("SQLUser.CY_CMP", "Graph_KG.CY_CMP", ROLE, "EXECUTE")}


def test_cypher_answers_from_the_ivg_schema(upg):
    r = upg["engine"].execute_cypher("MATCH (a {name: 'Ada'})-[:knows]->(b) RETURN b.name", raise_on_error=True)
    assert [list(x) for x in r.rows] == [["Bes"]]
    assert ns262.names_outside(r.sql or "", "Graph_KG") == []


def test_legacy_sql_still_answers_on_the_same_connection(upg):
    conn = upg["conn"]
    assert ns262.rows(conn, "SELECT SQLUser.STR_SPLIT('a,b', ',')")[0][0] == '["a","b"]'
    assert int(ns262.rows(conn, "SELECT SQLUser.CY_MINE()")[0][0]) == 7
    assert ns262.rows(conn, "SELECT COUNT(*) FROM SQLUser.rdf_edges")[0][0] == upg["before"]["edges"]


def test_the_upgrade_records_the_schema_and_copies_the_grant(upg):
    status = upg["status"]
    assert status["schema"] == "Graph_KG" and status["schema_recorded"] == "inferred"
    assert status["legacy_objects"] > 0
    assert ns262.native(upg["conn"]).get("^IVG.Deploy", "schema") == "Graph_KG"
    from iris_vector_graph._engine.legacy import direct_grants

    grants = direct_grants(ns262.native(upg["conn"]), "Graph_KG.CY_CMP")
    assert [(g["subject"], g["privilege"]) for g in grants] == [(ROLE, "EXECUTE")]


def test_a_native_plan_class_from_451_is_gone(upg):
    """FR-014: the deploy fingerprint changed, so IVGC.P<n> plans compiled against 4.5.x names are dropped."""
    plans = ns262.rows(upg["conn"], "SELECT Name FROM %Dictionary.ClassDefinition WHERE Name %STARTSWITH 'IVGC.P'")
    assert plans == []


def test_remove_legacy_objects_lists_exactly_the_manifest_objects_present(upg):
    from iris_vector_graph.legacy_manifest import LEGACY_MANIFEST

    listed = upg["engine"].remove_legacy_objects()
    names = {i["sql_name"] or i["class_name"] for i in listed}
    present_451 = {f"SQLUser.{n}" for n in upg["before"]["sqluser"]} | {f"SQLUser.{v}" for v in upg["before"]["views"]}
    manifest = {e["sql_name"] for e in LEGACY_MANIFEST if e["sql_name"]}
    assert {n for n in names if n.startswith("SQLUser.")} == present_451 & manifest
    assert "SQLUser.CY_MINE" not in names
    assert {"User.Exec", "User.SQL", "User.PageRankEmbedded"} <= names


def test_removal_leaves_only_the_applications_objects(upg):
    engine, conn = upg["engine"], upg["conn"]
    removed = engine.remove_legacy_objects(dry_run=False)
    assert removed and all(i["removed"] for i in removed)
    assert ns262.routines(conn, "SQLUser") == {"CY_MINE"}
    assert ns262.views(conn, "SQLUser") == set()
    assert ns262.classes(conn, "User.") == {"User.funcCYMINE"}
    assert engine.remove_legacy_objects() == []
    r = engine.execute_cypher("MATCH (a {name: 'Ada'})-[:knows]->(b) RETURN b.name", raise_on_error=True)
    assert [list(x) for x in r.rows] == [["Bes"]]


def test_an_interrupted_upgrade_resumes_to_the_same_end():
    """T038: a raise after the IVG Schema's functions exist and before the record is written; the next run finishes."""
    from build_release_namespace import build

    import iris_vector_graph._engine.schema as setup
    from iris_vector_graph import IRISGraphEngine

    build("v4.5.1", NS)
    conn = ns262.connect(NS)
    try:
        engine = IRISGraphEngine(conn, embedding_dimension=8, namespace=NS, native_cypher=False)

        def interrupt():
            raise RuntimeError("interrupted by the test")

        setup._UPGRADE_HOOK = interrupt
        try:
            with pytest.raises(RuntimeError, match="interrupted"):
                engine.initialize_schema()
        finally:
            setup._UPGRADE_HOOK = None
        assert ns262.native(conn).get("^IVG.Deploy", "schema") is None
        assert "CY_CMP" in ns262.routines(conn, "Graph_KG")
        status = engine.initialize_schema()
        assert status["schema_recorded"] == "inferred" and status["legacy_objects"] > 0
        assert ns262.native(conn).get("^IVG.Deploy", "schema") == "Graph_KG"
        r = engine.execute_cypher("MATCH (a {name: 'Ada'})-[:knows]->(b) RETURN b.name", raise_on_error=True)
        assert [list(x) for x in r.rows] == [["Bes"]]
    finally:
        conn.close()
