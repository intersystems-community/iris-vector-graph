"""4.6.0 R280 [SECURITY]: IVG's SQL-callable functions that read a global check SELECT on the table it stands for.

`CY_VLP_ENDS` with the adjacency index declared walks `^KG` (Graph.KG.VarLength.Ends), and the `kg_*` algorithms,
`MatchEdges`, `MatchEdgesAllGraphs`, `DijkstraPath` and `kg_BM25` read `^KG`, `^NKG` or `^BM25Idx`. IRIS checks only
the database resource there, so a caller holding EXECUTE on them and no SELECT read the graph (measured 2026-11-04 on
2026.3.0AI: CY_VLP_ENDS, kg_PPR, kg_DegreeCentrality, MatchEdges and DijkstraPath all answered such a user). Each now
calls Graph.KG.SqlPrivilege.RequireSelect first: the user without SELECT gets SQLCODE -99 naming the table, the user
with it the answer worked out by hand below.

The namespace is `IVG_NAMESPACE` (an IVG460*/IVG262* scratch namespace with IVG set up, whatever its IVG Schema). The
users and roles are instance-wide, named `<IVG460_PRIV_PREFIX>_EXEC_*` / `_SEL_*` (default `<namespace>_R280`), and
dropped at the end, as are the nodes.

The graph, in the default graph: r280a -knows-> r280b -knows-> r280c; names "Ada Lovelace", "Bes", "Cyd".
"""

from __future__ import annotations

import os

import pytest

from tests.e2e import ns460alt as alt

NS = alt.NS
PREFIX = os.environ.get("IVG460_PRIV_PREFIX", f"{NS}_R280")
PASSWORD = "Priv280x"
EXEC_ROLE, EXEC_USER = f"{PREFIX}_EXEC_ROLE", f"{PREFIX}_EXEC_USER"
SEL_ROLE, SEL_USER = f"{PREFIX}_SEL_ROLE", f"{PREFIX}_SEL_USER"
#: What a role needs to connect over SQL with no SQL privilege of its own (the scratch database's resource is
#: %DB_%DEFAULT: make_scratch_namespace creates it with none).
CONNECT = "%Service_SQL:U,%DB_%DEFAULT:RW,%DB_IRISLIB:R,%DB_IRISSYS:R"
FUNCTIONS = ("CY_VLP_ENDS", "kg_PPR", "kg_DegreeCentrality", "kg_SCC", "MatchEdges", "MatchEdgesAllGraphs",
             "DijkstraPath", "kg_BM25")
TABLES = ("nodes", "rdf_edges", "rdf_props", "rdf_labels")
NODES = ("r280a", "r280b", "r280c")
BM25 = "r280bm25"

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]


def _sys(lines: list) -> str:
    """Run ObjectScript lines in %SYS, which only this file's role and user setup needs, over TCP
    (tests/helpers/iris_lines.py; 4.7.0: no `iris session`)."""
    from tests.helpers.iris_lines import session

    return session(lines, "%SYS", allow_sys=True, timeout=120)


def _drop_principals() -> None:
    lines = []
    for n in (EXEC_USER, SEL_USER):
        lines.append(f'if ##class(Security.Users).Exists("{n}") do ##class(Security.Users).Delete("{n}")')
    for n in (EXEC_ROLE, SEL_ROLE):
        lines.append(f'if ##class(Security.Roles).Exists("{n}") do ##class(Security.Roles).Delete("{n}")')
    _sys(lines)


def _make_principals() -> None:
    _drop_principals()
    out = _sys([f'set sc=##class(Security.Roles).Create("{r}","4.6.0 R280","{CONNECT}") write "SC=",sc,!'
                for r in (EXEC_ROLE, SEL_ROLE)]
               + [f'set sc=##class(Security.Users).Create("{u}","{r}","{PASSWORD}","4.6.0 R280","{NS}") write "SC=",sc,!'
                  for u, r in ((EXEC_USER, EXEC_ROLE), (SEL_USER, SEL_ROLE))])
    assert out.count("SC=1") == 4, out


def _scalar(conn, sql: str):
    cur = conn.cursor()
    try:
        cur.execute(sql)
        return cur.fetchall()[0][0]
    finally:
        cur.close()


@pytest.fixture(scope="module")
def world():
    import json

    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = alt.connect()
    schema = iris.createIRIS(conn).get("^IVG.Deploy", "schema") or "Graph_KG"
    engine = IRISGraphEngine(conn, embedding_dimension=alt.DIM, namespace=NS, schema_prefix=schema)
    # The hand-worked answers (degree over n - 1 = 2, PPR over these three) hold only in an otherwise empty namespace,
    # which the scratch namespace is not: other modules of the run share it. A node one of them left in the default
    # graph made the degree scores 1/3 (gate run 2d1d6f46). Empty it, and check.
    iris.createIRIS(conn).classMethodValue("Graph.KG.Eraser", "EraseAll")
    conn.commit()
    assert engine.node_count() == 0 and engine.edge_count() == 0
    for nid, name in zip(NODES, ("Ada Lovelace", "Bes", "Cyd")):
        engine.create_node(nid, labels=["R280"], properties={"name": name})
    engine.create_edge("r280a", "knows", "r280b")
    engine.create_edge("r280b", "knows", "r280c")
    engine.sync()  # ^NKG, which kg_SCC reads
    engine.bm25_build(BM25, ["name"])
    _make_principals()
    cur = conn.cursor()
    for fn in FUNCTIONS:
        cur.execute(f"GRANT EXECUTE ON {schema}.{fn} TO {EXEC_ROLE}, {SEL_ROLE}")
    for table in TABLES:
        cur.execute(f"GRANT SELECT ON {schema}.{table} TO {SEL_ROLE}")
    cur.close()
    calls = {
        "CY_VLP_ENDS": (f"SELECT {schema}.CY_VLP_ENDS('r280a', '[\"knows\"]', 'out', 1, 2, 1, '', "
                        f"'{schema}.rdf_edges')", "rdf_edges"),
        "kg_PPR": (f"SELECT {schema}.kg_PPR('[\"r280a\"]', 0.85, 100, 0, 1.0, '')", "rdf_edges"),
        "kg_DegreeCentrality": (f"SELECT {schema}.kg_DegreeCentrality('out', '', 10000, '')", "rdf_edges"),
        "kg_SCC": (f"SELECT {schema}.kg_SCC(10000)", "rdf_edges"),
        "MatchEdges": (f"SELECT {schema}.MatchEdges('r280a', 'knows', 0, 0)", "rdf_edges"),
        "MatchEdgesAllGraphs": (f"SELECT {schema}.MatchEdgesAllGraphs('r280a', 'knows', 0)", "rdf_edges"),
        "DijkstraPath": (f"SELECT {schema}.DijkstraPath('r280a', 'r280c', '', 99, 5, 'out', '', '')", "rdf_edges"),
        "kg_BM25": (f"SELECT {schema}.kg_BM25('{BM25}', 'lovelace', 5)", "rdf_props"),
    }
    answers = {}
    for who in (EXEC_USER, SEL_USER):
        uconn = iris.connect("localhost", alt.PORT, NS, who, PASSWORD)
        try:
            for fn, (sql, _table) in calls.items():
                try:
                    answers[who, fn] = ("ok", json.loads(str(_scalar(uconn, sql))))
                except Exception as exc:
                    answers[who, fn] = ("error", str(exc))
        finally:
            uconn.close()
    yield {"schema": schema, "calls": calls, "answers": answers}
    try:
        _drop_principals()
        iris.createIRIS(conn).classMethodValue("Graph.KG.BM25Index", "Drop", BM25)
        for nid in NODES:
            engine.delete_node(nid)
    finally:
        conn.close()


@pytest.mark.parametrize("fn", FUNCTIONS)
def test_execute_without_select_is_refused_naming_the_table(world, fn):
    kind, text = world["answers"][EXEC_USER, fn]
    table = f"{world['schema']}.{world['calls'][fn][1]}"
    assert kind == "error", (fn, text)
    assert "SQLCODE=-99" in text and f"User {EXEC_USER} lacks SELECT on {table}" in text, text


def _ok(world, fn):
    kind, value = world["answers"][SEL_USER, fn]
    assert kind == "ok", (fn, value)
    return value


def test_select_holder_gets_the_variable_length_ends(world):
    assert _ok(world, "CY_VLP_ENDS") == [{"t": "r280b", "l": 1}, {"t": "r280c", "l": 2}]


def test_select_holder_gets_the_edge_scans(world):
    one = [{"s": "r280a", "p": "knows", "o": "r280b", "w": 1, "g": ""}]
    assert _ok(world, "MatchEdges") == one
    assert [{k: e[k] for k in ("s", "p", "o")} for e in _ok(world, "MatchEdgesAllGraphs")] == [
        {"s": "r280a", "p": "knows", "o": "r280b"}]


def test_select_holder_gets_the_shortest_path(world):
    path = _ok(world, "DijkstraPath")
    assert path["nodes"] == ["r280a", "r280b", "r280c"] and path["rels"] == ["knows", "knows"]
    assert path["totalCost"] == 2 and path["length"] == 2


def test_select_holder_gets_the_degrees(world):
    # out-degree over n - 1 = 2 other nodes: a and b have one edge out, c none
    got = {r["id"]: (r["degree"], r["score"]) for r in _ok(world, "kg_DegreeCentrality") if r["id"] in NODES}
    assert got == {"r280a": (1, 0.5), "r280b": (1, 0.5), "r280c": (0, 0)}


def test_select_holder_gets_the_personalized_pagerank(world):
    # seed a, damping 0.85, c's dangling mass back to the seed (R270): x_a = 0.15 + 0.85 x_c, x_b = 0.85 x_a,
    # x_c = 0.85 x_b, so x_a = 0.15 / (1 - 0.85^3) = 0.388727, x_b = 0.330418, x_c = 0.280855
    got = {r["id"]: r["score"] for r in _ok(world, "kg_PPR")}
    assert set(got) == set(NODES)
    for nid, want in (("r280a", 0.388727), ("r280b", 0.330418), ("r280c", 0.280855)):
        assert got[nid] == pytest.approx(want, abs=1e-4), (nid, got)


def test_select_holder_gets_the_components(world):
    # a chain has no cycle: each node is its own strongly connected component
    rows = [r for r in _ok(world, "kg_SCC") if r["id"] in NODES]
    assert sorted(r["id"] for r in rows) == list(NODES)
    assert [r["size"] for r in rows] == [1, 1, 1] and len({r["component"] for r in rows}) == 3


def test_select_holder_gets_the_bm25_hit(world):
    # "lovelace" is in a's document alone (N 3, df 1, dl 2, avgdl 4/3, k1 1.5, b 0.75)
    hits = _ok(world, "kg_BM25")
    assert [h["id"] for h in hits] == ["r280a"]
