"""Spec 262 US2 (T033-T035, T037): a 4.5.1 namespace upgrades without breaking anyone's SQL.

IVG262UPG is built by v4.5.1's own code (`scripts/fixtures/build_release_namespace.py`, seeded), then gains what an
application would own: `SQLUser.CY_MINE` (a name that matches a 4.5.x pattern but is on no manifest) and a role
granted EXECUTE on the 4.5.1 `SQLUser.CY_CMP`. Then 4.6.0's `initialize_schema()` (dry run first), and
`remove_legacy_objects()`.
"""

from __future__ import annotations

import re

import pytest

from tests.e2e import ns262

pytestmark = [pytest.mark.e2e, ns262.requires_container]

NS = ns262.UPG_NS  # IVG262UPG unless IVG262_UPG_NS names another
#: Users and roles are container-wide: a run in another namespace gets its own, so it cannot drop or re-create the ones
#: a run in IVG262UPG is using.
_SUFFIX = "" if NS == "IVG262UPG" else "_" + NS[len("IVG262"):].lower()
ROLE = "ivg262_app" + _SUFFIX
#: T034: a user whose only role is ROLE (EXECUTE on the 4.5.1 function Cypher's comparison calls, SELECT on the graph
#: tables), and a control user whose role has the SELECT grants alone.
APP_USER, APP_PW = "ivg262_appuser" + _SUFFIX, "Appuser262x"
SEL_ROLE, SEL_USER, SEL_PW = "ivg262_selonly" + _SUFFIX, "ivg262_seluser" + _SUFFIX, "Seluser262x"
GRAPH_TABLES = ("nodes", "rdf_edges", "rdf_labels", "rdf_props")
#: `<` on two strings is SQLUser.CY_CMP in 4.5.1 and Graph_KG.CY_CMP after the upgrade.
CMP_QUERY = "MATCH (a)-[:knows]->(b) WHERE a.name < b.name RETURN a.name, b.name"
#: The seed's `knows` edges (generate_old_snapshot): n1 Ada -> n2 Bes and n2 Bes -> n3 Cyd in the default graph, and
#: n5 -> n6 in graph `acme`, whose n5 and n6 rows hold no `name` (the seed writes names in the default graph only).
CMP_ANSWER = [["Ada", "Bes"], ["Bes", "Cyd"]]
ADA_QUERY = "MATCH (a {name: 'Ada'})-[:knows]->(b) RETURN b.name"


def _snapshot(conn) -> dict:
    return {
        "sqluser": sorted(ns262.routines(conn, "SQLUser")),
        "views": sorted(ns262.views(conn, "SQLUser")),
        "graph_kg": sorted(ns262.routines(conn, "Graph_KG")),
        "classes": sorted(ns262.classes(conn, "User.")),
        "edges": ns262.rows(conn, "SELECT COUNT(*) FROM Graph_KG.rdf_edges")[0][0],
        "record": ns262.native(conn).get("^IVG.Deploy", "schema"),
    }


def _state(conn, engine) -> dict:
    """What an upgraded namespace is, for comparing two upgrade routes (T036): its objects and its answers. Not its
    row counts: each release seeds through its own API, and 3.2.0's writes no temporal edge and no node copy per graph.
    The application's own `SQLUser.CY_MINE` (this file's fixture adds it) is left out."""
    return {
        "record": ns262.native(conn).get("^IVG.Deploy", "schema"),
        "graph_kg_routines": sorted(ns262.routines(conn, "Graph_KG")),
        "graph_kg_tables": sorted(r[0] for r in ns262.rows(
            conn, "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'Graph_KG'")),
        "sqluser_routines": sorted(ns262.routines(conn, "SQLUser") - {"CY_MINE"}),
        "sqluser_views": sorted(ns262.views(conn, "SQLUser")),
        "user_classes": sorted(ns262.classes(conn, "User.") - {"User.funcCYMINE"}),
        "ivg_classes": sorted(ns262.classes(conn, "Graph.KG.")),
        "legacy": sorted(i["sql_name"] or i["class_name"] for i in engine.remove_legacy_objects()),
        "cmp": _sorted_rows(engine.execute_cypher(CMP_QUERY, {}, raise_on_error=True).rows),
        "ada": _sorted_rows(engine.execute_cypher(ADA_QUERY, {}, raise_on_error=True).rows),
    }


def _sorted_rows(rows) -> list:
    return sorted([None if v is None else str(v) for v in row] for row in rows)


def _cypher_as(user: str, password: str, query: str) -> dict:
    """`query` through 4.6.0's engine on a connection of `user`'s own: `{"rows", "sql", "error"}`."""
    from iris_vector_graph import IRISGraphEngine

    conn = ns262.connect_as(NS, user, password)
    try:
        engine = IRISGraphEngine(conn, embedding_dimension=8, namespace=NS, native_cypher=False)
        try:
            r = engine.execute_cypher(query, {}, raise_on_error=True)
        except Exception as exc:  # the control user's answer is the error
            return {"rows": None, "sql": "", "error": str(exc)}
        return {"rows": _sorted_rows(r.rows), "sql": r.sql or "", "error": None}
    finally:
        conn.close()


@pytest.fixture(scope="module")
def upg():
    from build_release_namespace import build, run_cypher

    built = build("v4.5.1", NS)
    conn = ns262.connect(NS)
    cur = conn.cursor()
    cur.execute("CREATE FUNCTION SQLUser.CY_MINE() RETURNS INTEGER LANGUAGE OBJECTSCRIPT { Quit 7 }")
    # The roles are container-wide: made anew, so no grant of an earlier run survives in them.
    ns262.make_role(ROLE)
    ns262.make_role(SEL_ROLE)
    ns262.make_user(APP_USER, APP_PW, ROLE, NS)
    ns262.make_user(SEL_USER, SEL_PW, SEL_ROLE, NS)
    cur.execute(f"GRANT EXECUTE ON SQLUser.CY_CMP TO {ROLE}")
    for table in GRAPH_TABLES:
        cur.execute(f"GRANT SELECT ON Graph_KG.{table} TO {ROLE}, {SEL_ROLE}")
    app_before = run_cypher("v4.5.1", NS, CMP_QUERY, APP_USER, APP_PW)
    before = _snapshot(conn)
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(conn, embedding_dimension=8, namespace=NS, native_cypher=False)
    dry = engine.initialize_schema(dry_run=True)
    after_dry = _snapshot(conn)
    status = engine.initialize_schema()
    upgraded = _state(conn, engine)
    app_after = _cypher_as(APP_USER, APP_PW, CMP_QUERY)
    sel_after = _cypher_as(SEL_USER, SEL_PW, CMP_QUERY)
    sel_plain = _cypher_as(SEL_USER, SEL_PW, ADA_QUERY)
    yield {"conn": conn, "engine": engine, "built": built, "before": before, "dry": dry, "after_dry": after_dry,
           "status": status, "upgraded": upgraded, "app_before": app_before, "app_after": app_after,
           "sel_after": sel_after, "sel_plain": sel_plain}
    conn.close()
    ns262.drop_users_and_roles(APP_USER, SEL_USER, ROLE, SEL_ROLE)


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


def test_a_user_with_only_the_granted_role_runs_cypher_before_and_after_the_upgrade(upg):
    """T034. Before: 4.5.1's own engine, whose `<` calls SQLUser.CY_CMP. After: 4.6.0's, which calls Graph_KG.CY_CMP,
    runnable only because the upgrade copied the EXECUTE grant: the control user, with the SELECT grants alone, reads
    the same tables through a query that calls no function, and is refused the one that calls CY_CMP."""
    before, after, control, plain = upg["app_before"], upg["app_after"], upg["sel_after"], upg["sel_plain"]
    assert before["error"] is None, before["error"]
    assert "SQLUser.CY_CMP(" in before["sql"]
    assert _sorted_rows(before["rows"]) == CMP_ANSWER
    assert after["error"] is None, after["error"]
    assert "Graph_KG.CY_CMP(" in after["sql"] and ns262.names_outside(after["sql"], "Graph_KG") == []
    assert after["rows"] == CMP_ANSWER
    assert plain["error"] is None and plain["rows"] == [["Bes"]], plain
    assert "CY_" not in plain["sql"]
    assert control["rows"] is None and "<-99>" in control["error"], control


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


#: T036 builds its namespaces in IVG262ALT (IVG262_VIA_NS): NS holds the direct route's, whose state `upg` kept.
NS_VIA_32 = ns262.VIA_NS


def _upgraded_by_460(ns: str, *, migrate: bool) -> dict:
    """4.6.0's setup on `ns`, then, when `migrate` (the 3.x rows are still unmigrated), 4.6.0's `upgrade_to_4_0_0` and
    its setup again; the state it ends in."""
    from iris_vector_graph import IRISGraphEngine
    from iris_vector_graph.migrations import upgrade_to_4_0_0

    conn = ns262.connect(ns)
    try:
        engine = IRISGraphEngine(conn, embedding_dimension=8, namespace=ns, native_cypher=False)
        status = engine.initialize_schema()
        assert status["schema"] == "Graph_KG" and status["schema_recorded"] == "inferred"
        if migrate:
            report = upgrade_to_4_0_0(conn)
            assert report.blocked == (), report
            engine = IRISGraphEngine(conn, embedding_dimension=8, namespace=ns, native_cypher=False)
            engine.initialize_schema()
        return _without_migration_residue(_state(conn, engine), conn)
    finally:
        conn.close()


#: What a 3.x namespace keeps of having been migrated to 4.0.0, which a namespace born 4.x never had:
#: - the four tables upgrade_to_4_0_0 re-keys by copy (docs, kg_NodeEmbeddings, kg_NodeEmbeddings_optimized,
#:   kg_EdgeEmbeddings) are built as `<table>_ivg400` and renamed; the SQL name is the table's, the class keeps the
#:   staging name (`Graph.KG.kgNodeEmbeddingsivg400`, its `..._Extent` routine);
#: - the docs and edge-vector steps create their quarantine tables (empty: the seed has nothing to quarantine);
#: - IRIS names a table's generated child classes (`Graph.KG.rdfprops.VDYdHQ`) by a hash, which differs per build;
#: - 3.2.0's `Graph.KG.Loader`, which no later setup removes; since R119 the legacy manifest lists it, so
#:   `remove_legacy_objects()` names it on this route only (tests/e2e/test_460_legacy_manifest_320_loader_e2e.py).
_STAGING = "ivg400"
_QUARANTINES = ("docs_quarantine", "edge_vector_quarantine")
_HASHED_CHILD = re.compile(r"^Graph\.KG\.\w+\.\w{6}$")
_320_ONLY_CLASSES = {"Graph.KG.Loader"}


def _without_migration_residue(state: dict, conn) -> dict:
    out = dict(state)
    for name in _QUARANTINES:
        if name in state["graph_kg_tables"]:
            assert ns262.rows(conn, f"SELECT COUNT(*) FROM Graph_KG.{name}")[0][0] == 0, name
    out["graph_kg_tables"] = [t for t in state["graph_kg_tables"] if t not in _QUARANTINES]
    out["graph_kg_routines"] = sorted(
        r.replace(_STAGING, "") for r in state["graph_kg_routines"]
        if not r.startswith(("docsquarantine", "edgevectorquarantine")))
    out["ivg_classes"] = sorted(
        c.replace(_STAGING, "") for c in state["ivg_classes"]
        if not _HASHED_CHILD.match(c) and c not in _320_ONLY_CLASSES
        and not c.startswith(("Graph.KG.docsquarantine", "Graph.KG.edgevectorquarantine")))
    out["legacy"] = [name for name in state["legacy"] if name not in _320_ONLY_CLASSES]
    return out


def _differs(via: dict, direct: dict) -> dict:
    """Each key the two states disagree on, a list by what only one side holds."""
    assert direct["cmp"] == CMP_ANSWER and direct["ada"] == [["Bes"]]
    direct = dict(direct, ivg_classes=sorted(c for c in direct["ivg_classes"] if not _HASHED_CHILD.match(c)))
    out = {}
    for k in direct:
        if via[k] == direct[k]:
            continue
        if isinstance(direct[k], list) and k not in ("cmp", "ada"):
            out[k] = {"only_via_320": sorted(set(map(str, via[k])) - set(map(str, direct[k]))),
                      "only_direct_451": sorted(set(map(str, direct[k])) - set(map(str, via[k])))}
        else:
            out[k] = {"via_320": via[k], "direct_451": direct[k]}
    return out


def test_a_320_namespace_upgraded_by_451_then_460_ends_where_the_direct_451_upgrade_does(upg):
    """T036, case A: v3.2.0's own setup and seed, then v4.5.1's upgrade over it (its setup and its upgrade_to_4_0_0, no
    wipe, no seed), then 4.6.0's setup; the end state equals the namespace 4.5.1 built and 4.6.0 upgraded (T033): the
    same objects in Graph_KG, SQLUser and the User package, the same record and legacy list, the same answers.

    The seed leaves out its one cross-graph edge (`acme` n5->n6, whose endpoints 3.2.0 kept in the default graph only):
    4.5.1's own upgrade_to_4_0_0 cannot re-key `rdf_edges` over it (SQLCODE -127 on fk_edges_source, R076), and 4.5.1
    is released, so an install holding such an edge never got through 4.5.1. Case B is that install's route."""
    from build_release_namespace import build

    build("v3.2.0", NS_VIA_32, named_edges=False)
    build("v4.5.1", NS_VIA_32, keep=True, seed=False, migrate=True)
    assert _differs(_upgraded_by_460(NS_VIA_32, migrate=False), upg["upgraded"]) == {}


def test_a_320_namespace_with_a_cross_graph_edge_upgraded_by_460_ends_where_the_direct_451_upgrade_does(upg):
    """T036, case B: v3.2.0's own setup and the full seed, the `acme` edge included; v4.5.1's setup over it without its
    upgrade_to_4_0_0 (which cannot complete on that edge, R076); then 4.6.0's initialize_schema() and
    upgrade_to_4_0_0, which registers n5 and n6 in acme as create_edge has since 4.0.0. The realistic route for a 3.x
    install with cross-graph edges; it ends where the direct 4.5.1 -> 4.6.0 upgrade does."""
    from build_release_namespace import build

    build("v3.2.0", NS_VIA_32)
    build("v4.5.1", NS_VIA_32, keep=True, seed=False, migrate=False)
    assert _differs(_upgraded_by_460(NS_VIA_32, migrate=True), upg["upgraded"]) == {}
