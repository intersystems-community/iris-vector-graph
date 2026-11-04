"""Spec 262 US3 (T025, T026): every IVG feature works in a namespace whose IVG Schema is `IVGALT`.

IVG262ALT is dropped and created anew, then set up with `schema_prefix="IVGALT"`. Setup leaves no `Graph_KG` schema,
puts every table, function and procedure in `IVGALT`, and deploys classes that name `Graph_KG` nowhere (SC-004). Then
one case per write, delete and read path, each asserting what the `IVGALT` tables hold afterwards and a known answer.
Scratch namespace: `IVG_NS_IVG262ALT` (default IVG262ALT). The file drops and rebuilds it (4.6.0 R191): point it at
a namespace no other run is using.
"""

from __future__ import annotations

import json
import os

import pytest

from tests.e2e import ns262

pytestmark = [pytest.mark.e2e, ns262.requires_container]

NS = os.environ.get("IVG_NS_IVG262ALT", "IVG262ALT")
SCHEMA = "IVGALT"


@pytest.fixture(scope="module")
def alt():
    ns262.recreate(NS)
    conn = ns262.connect(NS)
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(conn, embedding_dimension=4, namespace=NS, schema_prefix=SCHEMA, native_cypher=False)
    status = engine.initialize_schema()
    yield {"conn": conn, "engine": engine, "status": status}
    conn.close()


def _one(conn, sql, params=None):
    return ns262.rows(conn, sql, params)[0][0]


def _v(x):
    if isinstance(x, str):
        try:
            return json.loads(x)
        except ValueError:
            return x
    return x


def _cy(engine, q, params=None):
    r = engine.execute_cypher(q, params or {}, raise_on_error=True)
    assert ns262.names_outside(r.sql or "", SCHEMA) == [], r.sql
    return [[_v(v) for v in row] for row in r.rows]


# --------------------------------------------------------------------------- T028: no privilege to create the schema
#
# First in the file: it needs IVG262ALT empty, and recreates it; the `alt` fixture recreates it again afterwards.

NOPRIV_USER, NOPRIV_ROLE, NOPRIV_PW = "ivg262_nopriv", "ivg262_nopriv_role", "Nopriv262x"


@pytest.fixture
def empty_ns_as_unprivileged_user():
    """IVG262ALT anew, and a developer who can connect, read the class dictionary and use the Native API, holding no
    SQL DDL privilege: no %CREATE_TABLE, so no IVG Schema of its own. Both removed afterwards."""
    ns262.recreate(NS)
    ns262.make_role(NOPRIV_ROLE, ns262.CONNECT_RESOURCES
                    + ",%Development:U,%Native_ClassExecution:U,%Native_GlobalAccess:U")
    ns262.make_user(NOPRIV_USER, NOPRIV_PW, NOPRIV_ROLE, NS)
    admin = ns262.connect(NS)
    # setup's first read: whether the schema's class package holds someone else's classes (research R2)
    admin.cursor().execute(f"GRANT SELECT ON %Dictionary.ClassDefinition TO {NOPRIV_ROLE}")
    user = ns262.connect_as(NS, NOPRIV_USER, NOPRIV_PW)
    try:
        yield admin, user
    finally:
        user.close()
        admin.close()
        ns262.drop_users_and_roles(NOPRIV_USER, NOPRIV_ROLE)


def test_setup_without_the_privilege_to_create_the_schema_raises_one_error_and_changes_nothing(
    empty_ns_as_unprivileged_user,
):
    from iris_vector_graph import IRISGraphEngine, IVGSchemaError

    admin, user = empty_ns_as_unprivileged_user
    before = ns262.namespace_snapshot(admin)
    engine = IRISGraphEngine(user, embedding_dimension=4, namespace=NS, schema_prefix=SCHEMA, native_cypher=False)
    with pytest.raises(IVGSchemaError) as raised:
        engine.initialize_schema()
    message = str(raised.value)
    assert "%CREATE_TABLE" in message and NOPRIV_USER in message and SCHEMA in message, message
    assert "Nothing was created" in message
    assert ns262.namespace_snapshot(admin) == before
    assert SCHEMA.upper() not in {s.upper() for s in ns262.schemas(admin)}


# --------------------------------------------------------------------------- T025: setup

def test_setup_reports_no_deploy_error(alt):
    assert [w for w in alt["status"]["warnings"] if "deploy error" in w] == []
    assert alt["status"]["objectscript_deployed"]


def test_no_graph_kg_schema_exists(alt):
    assert "GRAPH_KG" not in {s.upper() for s in ns262.schemas(alt["conn"])}
    assert ns262.rows(alt["conn"], "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE UPPER(TABLE_SCHEMA) = 'GRAPH_KG'"
                      )[0][0] == 0
    assert ns262.routines(alt["conn"], "Graph_KG") == set()


def test_the_tables_functions_and_procedures_are_in_ivgalt(alt):
    conn = alt["conn"]
    tables = {r[0] for r in ns262.rows(conn, "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = ?",
                                       [SCHEMA])}
    assert {"nodes", "rdf_edges", "rdf_labels", "rdf_props", "kg_NodeEmbeddings", "ledger_revisions",
            "ledger_stats"} <= tables
    routines = ns262.routines(conn, SCHEMA)
    assert {"CY_CMP", "JSON_VALUE", "STR_SPLIT", "JSON_KEYS", "CypherFn_IVGJSONSET", "Percentile_PDISC", "kg_PPR",
            "kg_KNN_VEC", "kg_BM25"} <= routines
    assert ns262.routines(conn, "SQLUser") == set() and ns262.views(conn, "SQLUser") == set()


def test_the_table_and_function_classes_are_in_package_ivgalt(alt):
    """Research R2: the schema's package holds its table and function classes."""
    conn = alt["conn"]
    table_classes = {str(t): str(c) for t, c in ns262.rows(
        conn, "SELECT TABLE_NAME, CLASSNAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = ? "
              "AND TABLE_TYPE = 'BASE TABLE' AND TABLE_NAME IN ('nodes', 'rdf_edges')", [SCHEMA])}
    function_classes = {str(r[0]) for r in ns262.rows(
        conn, "SELECT Name FROM %Dictionary.ClassDefinition WHERE Name IN "
              "('IVGALT.funcCYCMP', 'Graph.KG.funcCYCMP', 'IVGALT.funcSTRSPLIT', 'Graph.KG.funcSTRSPLIT')")}
    assert table_classes == {"nodes": "IVGALT.nodes", "rdf_edges": "IVGALT.rdfedges"}, table_classes
    assert function_classes == {"IVGALT.funcCYCMP", "IVGALT.funcSTRSPLIT"}, function_classes


def test_every_table_function_and_procedure_class_is_in_package_ivgalt(alt):
    """R084: every table, function and procedure in IVGALT comes from a class in package IVGALT (the DDL's, and the
    schema classes the deploy moved there: IVGALT.SqlProcs, IVGALT.LedgerRevision, IVGALT.LedgerStats), and nothing
    anywhere in the namespace projects SQL from IVG's code packages."""
    conn = alt["conn"]
    tables = {(str(t), str(c)) for t, c in ns262.rows(
        conn, "SELECT TABLE_NAME, CLASSNAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = ?", [SCHEMA])}
    routines = {(str(r), str(c)) for r, c in ns262.rows(
        conn, "SELECT ROUTINE_NAME, CLASSNAME FROM INFORMATION_SCHEMA.ROUTINES WHERE ROUTINE_SCHEMA = ?", [SCHEMA])}
    assert [x for x in tables | routines if not x[1].startswith("IVGALT.")] == []
    assert {("ledger_revisions", "IVGALT.LedgerRevision"), ("ledger_stats", "IVGALT.LedgerStats")} <= tables
    assert {("kg_BM25", "IVGALT.SqlProcs"), ("ledger_records", "IVGALT.SqlProcs"), ("JSON_KEYS", "IVGALT.SqlProcs"),
            ("Percentile_PDISC", "IVGALT.SqlProcs"), ("CypherFn_IVGJSONSET", "IVGALT.SqlProcs"),
            ("kg_PPR", "IVGALT.funckgPPR")} <= routines
    from_code = ns262.rows(conn, "SELECT ROUTINE_SCHEMA, ROUTINE_NAME, CLASSNAME FROM INFORMATION_SCHEMA.ROUTINES "
                                 "WHERE CLASSNAME %STARTSWITH 'Graph.KG.' OR CLASSNAME %STARTSWITH 'IVG.'")
    assert from_code == []
    assert ns262.rows(conn, "SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
                            "WHERE CLASSNAME %STARTSWITH 'Graph.KG.' OR CLASSNAME %STARTSWITH 'IVG.'") == []


def test_iris_maps_each_schema_to_its_default_package(alt):
    """R084: the deploy remaps no package, so IRIS's own mapping holds both ways. Forward: each package's SQL schema.
    Back ($$GetPackage^%apiDDL, which the Native API may not call): where a table DDL makes in the schema compiles."""
    conn, nat = alt["conn"], ns262.native(alt["conn"])
    forward = {p: nat.classMethodValue("%SYSTEM.SQL.Schema", "GetSchemaFromPackage", p)
               for p in ("Graph.KG", "IVG", "IVGALT")}
    assert forward == {"Graph.KG": "Graph_KG", "IVG": "IVG", "IVGALT": "IVGALT"}
    back = {}
    for schema in ("IVGALT", "Graph_KG"):
        conn.cursor().execute(f"CREATE TABLE {schema}.r084probe (x INT)")
        try:
            back[schema] = ns262.rows(conn, "SELECT CLASSNAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = ? "
                                            "AND TABLE_NAME = 'r084probe'", [schema])[0][0]
        finally:
            conn.cursor().execute(f"DROP TABLE {schema}.r084probe")
    assert back == {"IVGALT": "IVGALT.r084probe", "Graph_KG": "Graph.KG.r084probe"}
    assert "GRAPH_KG" not in {s.upper() for s in ns262.schemas(conn)}


def test_the_deployed_classes_name_graph_kg_nowhere(alt):
    """SC-004, read from what compiled: method bodies and class keywords of every IVG class."""
    rows = ns262.rows(alt["conn"], "SELECT parent, Implementation FROM %Dictionary.CompiledMethod "
                                   "WHERE parent %STARTSWITH 'Graph.KG.' OR parent %STARTSWITH 'IVG.'")
    assert rows
    survivors = sorted({p for p, impl in rows if impl and ("Graph_KG" in str(impl) or "Graph.KG.rdf" in str(impl))})
    assert survivors == []


# --------------------------------------------------------------------------- T026: one case per path

def test_node_and_edge_create_row(alt):
    e, conn = alt["engine"], alt["conn"]
    e.create_node("alt:a", labels=["AltP"], properties={"name": "a"})
    e.create_node("alt:b", labels=["AltP"], properties={"name": "b"})
    e.create_edge("alt:a", "ALT_R", "alt:b")
    assert _one(conn, f"SELECT COUNT(*) FROM {SCHEMA}.nodes WHERE node_id IN ('alt:a', 'alt:b')") == 2
    assert _one(conn, f"SELECT o_id FROM {SCHEMA}.rdf_edges WHERE s = 'alt:a' AND p = 'ALT_R'") == "alt:b"
    assert _cy(e, "MATCH (x:AltP {name: 'a'})-[:ALT_R]->(y) RETURN y.name") == [["b"]]


def test_node_and_edge_create_bulk(alt):
    e, conn = alt["engine"], alt["conn"]
    e.bulk_create_nodes([{"id": f"altb:{i}", "labels": ["AltB"], "properties": {"i": i}} for i in range(3)])
    e.bulk_create_edges([{"source_id": "altb:0", "predicate": "ALT_B", "target_id": "altb:1"},
                         {"source_id": "altb:1", "predicate": "ALT_B", "target_id": "altb:2"}])
    assert _one(conn, f"SELECT COUNT(*) FROM {SCHEMA}.rdf_labels WHERE label = 'AltB'") == 3
    assert _one(conn, f"SELECT COUNT(*) FROM {SCHEMA}.rdf_edges WHERE p = 'ALT_B'") == 2
    assert _cy(e, "MATCH (:AltB)-[:ALT_B*2]->(z) RETURN z.i") == [[2]]


def test_property_and_label_set_and_remove(alt):
    e, conn = alt["engine"], alt["conn"]
    _cy(e, "CREATE (:AltS {name: 's'})")
    _cy(e, "MATCH (x:AltS) SET x.colour = 'red', x:AltT")
    assert _cy(e, "MATCH (x:AltT) RETURN x.colour") == [["red"]]
    _cy(e, "MATCH (x:AltS) REMOVE x.colour, x:AltT")
    assert _cy(e, "MATCH (x:AltS) RETURN x.colour") == [[None]]
    assert _one(conn, f"SELECT COUNT(*) FROM {SCHEMA}.rdf_labels WHERE label = 'AltT'") == 0


def test_merge(alt):
    e, conn = alt["engine"], alt["conn"]
    for _ in range(2):
        _cy(e, "MERGE (:AltM {name: 'm'})")
    assert _one(conn, f"SELECT COUNT(*) FROM {SCHEMA}.rdf_labels WHERE label = 'AltM'") == 1


def test_detach_delete(alt):
    e, conn = alt["engine"], alt["conn"]
    _cy(e, "CREATE (:AltD {name: 'd1'})-[:ALT_D]->(:AltD {name: 'd2'})")
    _cy(e, "MATCH (x:AltD {name: 'd1'}) DETACH DELETE x")
    assert _cy(e, "MATCH (x:AltD) RETURN x.name") == [["d2"]]
    assert _one(conn, f"SELECT COUNT(*) FROM {SCHEMA}.rdf_edges WHERE p = 'ALT_D'") == 0


def test_delete_nodes(alt):
    e, conn = alt["engine"], alt["conn"]
    e.create_node("altx:1", labels=["AltX"])
    e._store.delete_nodes(["altx:1"])
    assert _one(conn, f"SELECT COUNT(*) FROM {SCHEMA}.nodes WHERE node_id = 'altx:1'") == 0


def test_erase_graph(alt):
    e, conn = alt["engine"], alt["conn"]
    e.create_node("alte:1", labels=["AltE"], graph="alt_g")
    e.create_node("alte:2", labels=["AltE"], graph="alt_g")
    e.create_edge("alte:1", "ALT_E", "alte:2", graph="alt_g")
    e.erase_graph("alt_g")
    assert _one(conn, f"SELECT COUNT(*) FROM {SCHEMA}.nodes WHERE graph_id = 'alt_g'") == 0
    assert _one(conn, f"SELECT COUNT(*) FROM {SCHEMA}.rdf_edges WHERE graph_id = 'alt_g'") == 0


def test_temporal_edge_write_and_window_read(alt):
    e = alt["engine"]
    e.create_edge_temporal("altt:1", "ALT_AT", "altt:2", timestamp=1700000000, weight=2.0)
    e.create_edge_temporal("altt:1", "ALT_AT", "altt:3", timestamp=1700000300, weight=1.0)
    got = e.get_edges_in_window("altt:1", "ALT_AT", 1699999990, 1700000010)
    assert [(g.get("target") or g.get("o"), g.get("ts", g.get("timestamp"))) for g in got] == [("altt:2", 1700000000)]


def test_drop_temporal_structural_rows_dry_run_reads_ivgalt(alt):
    e, conn = alt["engine"], alt["conn"]
    e.set_temporal_structural("ALT_AT", False)
    rows = _one(conn, f"SELECT COUNT(*) FROM {SCHEMA}.rdf_edges WHERE p = 'ALT_AT'")
    report = e.drop_temporal_structural_rows("ALT_AT", dry_run=True)
    assert report.rows == rows


def test_ledger_commit_verify(alt):
    from iris_vector_graph.ledger import Changeset

    e, conn = alt["engine"], alt["conn"]
    e.ledger.enable()
    try:
        cs = Changeset(actor="t262", actor_type="system")
        cs.create_node("altl:a", labels=["AltL"])
        cs.create_node("altl:b", labels=["AltL"])
        cs.create_relationship("altl:a", "ALT_L", "altl:b")
        e.ledger.commit(cs)
        assert _one(conn, f"SELECT COUNT(*) FROM {SCHEMA}.ledger_revisions") >= 1
        assert _one(conn, f"SELECT o_id FROM {SCHEMA}.rdf_edges WHERE s = 'altl:a'") == "altl:b"
        verdict = e.ledger.verify()
        # Edges written outside the ledger (the other cases) make the whole verdict diverge; the committed rows must not.
        assert not [d for d in verdict.differences if "altl:" in str(d)]
    finally:
        e.ledger.disable()


def test_embeddings_store_and_knn(alt):
    e, conn = alt["engine"], alt["conn"]
    e.store_embedding("alt:a", [1.0, 0.0, 0.0, 0.0])
    e.store_embedding("alt:b", [0.0, 1.0, 0.0, 0.0])
    assert _one(conn, f"SELECT COUNT(*) FROM {SCHEMA}.kg_NodeEmbeddings WHERE node_id IN ('alt:a', 'alt:b')") == 2
    hits = e.kg_KNN_VEC(json.dumps([0.9, 0.1, 0.0, 0.0]), k=1)
    assert [h[0] for h in hits] == ["alt:a"]


def test_bm25_text_search(alt):
    e = alt["engine"]
    e.create_node("altbm:1", labels=["AltBM"], properties={"name": "quokka262 island"})
    e.create_node("altbm:2", labels=["AltBM"], properties={"name": "otter river"})
    e.bm25_build("alt_bm25", text_props=["name"])
    hits = e.bm25_search("alt_bm25", "quokka262", k=5)
    assert [h[0] for h in hits] == ["altbm:1"]


def test_pagerank_ppr_and_betweenness(alt):
    e = alt["engine"]
    _cy(e, "CREATE (:AltG {name: 'g1'})-[:ALT_G]->(:AltG {name: 'g2'})-[:ALT_G]->(:AltG {name: 'g3'})")
    ids = dict(_cy(e, "MATCH (x:AltG) RETURN x.name, id(x)"))
    scores = {s["id"]: s["score"] for s in e.ppr(ids["g1"], top_k=3)["scores"]}
    assert set(scores) == set(ids.values()) and max(scores, key=scores.get) == ids["g1"]
    between = e.betweenness_centrality()
    middle = {s["id"]: s["score"] for s in between}.get(ids["g2"])
    assert middle and middle > 0


def test_bfs_and_shortest_path(alt):
    e = alt["engine"]
    assert _cy(e, "MATCH p = shortestPath((a:AltB {i: 0})-[:ALT_B*]->(b:AltB {i: 2})) RETURN length(p)") == [[2]]
    reached = e.khop("altb:0", hops=2)
    assert {"altb:1", "altb:2"} <= set(json.dumps(reached).replace('"', " ").split())


def test_snapshot_save_restore(alt, tmp_path):
    e, conn = alt["engine"], alt["conn"]
    path = str(tmp_path / "alt.zip")
    e.save_snapshot(path, layers=["sql", "globals"])
    before = _one(conn, f"SELECT COUNT(*) FROM {SCHEMA}.rdf_edges")
    e.restore_snapshot(path, merge=False)
    assert _one(conn, f"SELECT COUNT(*) FROM {SCHEMA}.rdf_edges") == before
    assert _cy(e, "MATCH (x:AltP {name: 'a'})-[:ALT_R]->(y) RETURN y.name") == [["b"]]


def test_wcc(alt):
    e = alt["engine"]
    ids = dict(_cy(e, "MATCH (x:AltG) RETURN x.name, id(x)"))
    # engine.algorithms.execute_wcc forwards to an engine method that does not exist (pre-existing); the store answers.
    result = e._store.execute_wcc()
    assert result.error is None, result.error
    by_node = {row[0]: row[1] for row in result.rows}
    assert len({by_node[ids[n]] for n in ("g1", "g2", "g3")}) == 1
    assert by_node[ids["g1"]] != by_node.get("alt:a")


def test_bare_node_retire(alt):
    """A node whose only edges were temporal ones is retired with them: the row goes from IVGALT.nodes."""
    e, conn = alt["engine"], alt["conn"]
    e.create_edge_temporal("altr:1", "ALT_RT", "altr:2", timestamp=1600000000)
    assert _one(conn, f"SELECT COUNT(*) FROM {SCHEMA}.nodes WHERE node_id = \x27altr:2\x27") == 1
    e.purge_before(1600000001, retire_bare_nodes=True)
    assert _one(conn, f"SELECT COUNT(*) FROM {SCHEMA}.nodes WHERE node_id = \x27altr:2\x27") == 0


def test_ledger_redact(alt):
    from iris_vector_graph.ledger import Changeset

    e, conn = alt["engine"], alt["conn"]
    e.ledger.enable()
    try:
        cs = Changeset(actor="t262", actor_type="system")
        cs.create_node("altrd|x", labels=["AltRd"], properties={"secret": "s262"})
        e.ledger.commit(cs)
        e._store.delete_nodes(["altrd|x"])
        e.ledger.redact(None, prefix="altrd")
        assert _one(conn, f"SELECT COUNT(*) FROM {SCHEMA}.nodes WHERE node_id = \x27altrd:x\x27") == 0
    finally:
        e.ledger.disable()


# --------------------------------------------------------------------------- T030: package clash

def test_a_schema_whose_package_holds_someone_elses_class_is_refused(alt):
    """Research R2: IVGCLASH maps to package IVGCLASH; an application class there would sit among IVG's table classes."""
    from iris_vector_graph import IRISGraphEngine, IVGSchemaError

    conn = alt["conn"]
    conn.cursor().execute("CREATE TABLE IVGCLASH.mine (x INT)")
    nat = ns262.native(ns262.connect(NS))
    recorded = nat.get("^IVG.Deploy", "schema")
    stamp = nat.get("^IVG.Deploy", "schema", "recorded")
    try:
        engine = IRISGraphEngine(conn, embedding_dimension=4, namespace=NS, schema_prefix="IVGCLASH")
        # recorded IVGALT: the mismatch is what refuses it first (FR-006)
        with pytest.raises(IVGSchemaError, match="records IVGALT"):
            engine.initialize_schema()
        # with no record, IVGCLASH named explicitly beside IVGALT's tables reaches the package check (spec 262 T030)
        nat.kill("^IVG.Deploy", "schema")
        engine = IRISGraphEngine(conn, embedding_dimension=4, namespace=NS, schema_prefix="IVGCLASH")
        with pytest.raises(IVGSchemaError, match="IVGCLASH.mine"):
            engine.initialize_schema()
        tables = {r[0] for r in ns262.rows(conn, "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'IVGCLASH'")}
        assert tables == {"mine"}
    finally:
        nat.set(recorded, "^IVG.Deploy", "schema")
        nat.set(stamp, "^IVG.Deploy", "schema", "recorded")
        conn.cursor().execute("DROP TABLE IVGCLASH.mine")
