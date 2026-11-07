"""4.6.0 R316, live: the /kg role may read exactly the tables its routes read, checked per table, and
`kg_EdgeEmbeddings` is registered as an edge route.

The routes and what each reads (Graph.KG.PyOps): `/vectorSearch` reads `kg_NodeEmbeddings` and `rdf_labels` through
`kg_KNN_VEC` for the default graph, or a named graph's own route (`kg_emb_<16 hex>`, found in `embedding_registry`);
`/hybridSearch` also reads `docs` through `kg_TXT`; `/metaPath` reads `^KG`. So a user holding only the role
`IVG_KG_<NS>` may SELECT `kg_NodeEmbeddings`, `rdf_labels`, `docs`, `embedding_registry` and the route of graph
`g316`, and no other table of the IVG Schema: `kg_NodeEmbeddings_optimized` and `kg_EdgeEmbeddings` included, which
ADMIN_GUIDE §4 said the role reads. `$SYSTEM.SQL.Security.CheckPrivilege` answers per table for that user.

Before R316 setup recorded `kg_EdgeEmbeddings` as a node route at the default graph. The fixture puts the row back to
kind `node`, as an earlier setup left it, and runs setup again, which records it as `edge`.

Data, by hand: node r316:a in graph g316 with the vector [1, 0, ...] (zeros to the width); /vectorSearch of [1, 0, ...]
in g316, k 1: r316:a, cosine 1. In the default graph r316:d [1, 0, ...] -[R316]-> r316:e [0, 1, 0, ...], with
kg_NodeEmbeddings_optimized holding r316:d's vector and kg_EdgeEmbeddings the edge's, so both legacy tables are
there and populated while a user holding only the role calls every route: /vectorSearch of [1, 0, ...] in the
default graph answers r316:d at cosine 1, /hybridSearch's best is r316:d, /metaPath from r316:d over R316 reaches
r316:e, and no call answers -99 or <PROTECT>. Were a route to read either table, it would fail there (the role may
not read them), so this is the proof that none does. The namespace is `IVG_NAMESPACE`; the application `/<ns>-r316-kg`, the user and the
graph's data are removed at the end; the role stays, as CreateWebApp leaves it.
"""

from __future__ import annotations

import contextlib
import json

import pytest

from tests.e2e import ns460alt as alt

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]

APP = f"/{alt.NS.lower()}-r316-kg"
USER, PASSWORD = "ivg460r316reader", "r316Reader460"
ROLE = "IVG_KG_" + alt.NS
GRAPH = "g316"
VEC = [1.0] + [0.0] * (alt.DIM - 1)
VEC_E = [0.0, 1.0] + [0.0] * (alt.DIM - 2)


def _sys(lines: list) -> str:
    return alt.session(lines, namespace="%SYS")


def _drop_app_and_user():
    _sys([f'if ##class(Security.Applications).Exists("{APP}") do ##class(Security.Applications).Delete("{APP}")',
          f'if ##class(Security.Users).Exists("{USER}") do ##class(Security.Users).Delete("{USER}")'])


@pytest.fixture(scope="module")
def webapp():
    from iris_vector_graph.routing import route_table_name

    alt.make_namespace()
    conn = alt.connect()
    engine = alt.engine(conn)
    engine.initialize_schema()
    engine.create_node("r316:a", labels=["R316"], graph=GRAPH)
    assert engine.store_embedding("r316:a", VEC, graph=GRAPH)
    # as an earlier setup recorded it
    conn.cursor().execute(f"UPDATE {alt.SCHEMA}.embedding_registry SET kind = 'node' "
                          "WHERE table_name = 'kg_EdgeEmbeddings'")
    conn.commit()
    before = alt.rows(conn, f"SELECT kind FROM {alt.SCHEMA}.embedding_registry WHERE table_name = 'kg_EdgeEmbeddings'")
    engine.initialize_schema()
    # the default graph, with both legacy vector tables populated
    for node, vec in (("r316:d", VEC), ("r316:e", VEC_E)):
        engine.create_node(node, labels=["R316D"])
        assert engine.store_embedding(node, vec)
    engine.create_edge("r316:d", "R316", "r316:e")
    engine.store_edge_embedding("r316:d", "R316", "r316:e", VEC)
    conn.cursor().execute(f"INSERT INTO {alt.SCHEMA}.kg_NodeEmbeddings_optimized (graph_id, node_id, emb) "
                          f"VALUES ('', 'r316:d', TO_VECTOR(?, DOUBLE, {alt.DIM}))", [json.dumps(VEC)])
    conn.commit()
    engine.sync()
    _drop_app_and_user()
    out = alt.session([f'set sc=##class(Graph.KG.Service).CreateWebApp("{APP}")',
                       'write "<<CREATED:",$system.Status.GetErrorText(sc),"|",sc,":CREATED>>",!'])
    made = _sys([f'set sc=##class(Security.Users).Create("{USER}","{ROLE}","{PASSWORD}","R316 test","{alt.NS}")',
                 'write "<<SC:",sc,":SC>>",!'])
    assert alt.marked(made, "SC") == "1", made
    yield {"engine": engine, "conn": conn, "created": alt.marked(out, "CREATED"), "before": before,
           "route": route_table_name(GRAPH, None)}
    _drop_app_and_user()
    with contextlib.suppress(Exception):
        engine.erase_graph(GRAPH)
    conn.commit()
    conn.close()


def test_create_web_app_succeeds(webapp):
    assert webapp["created"].endswith("|1"), webapp["created"]


def test_setup_records_kg_edge_embeddings_as_an_edge_route(webapp):
    assert webapp["before"] == [("node",)]
    assert alt.rows(webapp["conn"], f"SELECT kind FROM {alt.SCHEMA}.embedding_registry "
                                    "WHERE table_name = 'kg_EdgeEmbeddings'") == [("edge",)]


def test_graph_g316_has_its_own_route(webapp):
    assert alt.rows(webapp["conn"], f"SELECT table_name FROM {alt.SCHEMA}.embedding_registry "
                                    "WHERE graph_id = ? AND COALESCE(kind, 'node') = 'node'", [GRAPH]) == [
        (webapp["route"],)]


def _check_privileges(tables: list) -> dict:
    lines = [f'write "<<P:",$SYSTEM.SQL.Security.CheckPrivilege("{USER}",1,"{alt.SCHEMA}.{t}","s","{alt.NS}"),":P>>",!'
             for t in tables]
    out = _sys(lines)
    answers = [part.split(":P>>", 1)[0] for part in out.split("<<P:")[1:]]
    assert len(answers) == len(tables), out
    return dict(zip(tables, answers))


def test_the_role_may_select_exactly_the_tables_its_routes_read(webapp):
    tables = [t for (t,) in alt.rows(webapp["conn"], "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
                                                     "WHERE TABLE_SCHEMA = ? AND TABLE_TYPE = 'BASE TABLE'",
                                     [alt.SCHEMA])]
    assert {"kg_NodeEmbeddings_optimized", "kg_EdgeEmbeddings", webapp["route"]} <= set(tables), tables
    readable = sorted(t for t, ok in _check_privileges(tables).items() if ok == "1")
    assert readable == sorted(["kg_NodeEmbeddings", "rdf_labels", "docs", "embedding_registry", webapp["route"]])


def test_pyops_reads_kg_node_embeddings_for_the_default_graph(webapp):
    import iris

    native = iris.createIRIS(webapp["conn"])
    assert native.classMethodValue("Graph.KG.PyOps", "getVectorTable", "", "") == f"{alt.SCHEMA}.kg_NodeEmbeddings"
    assert native.classMethodValue("Graph.KG.PyOps", "getExpectedDimension", "", "") == alt.DIM


def _as_reader(url: str, request: dict) -> tuple:
    """(login line, HTTP status, body text) of `url` dispatched to Graph.KG.Service as the user holding only the role."""
    body = json.dumps(request).replace('"', '""')
    line = " ".join([
        f'set ok=$SYSTEM.Security.Login("{USER}","{PASSWORD}")',
        'write "<<LOGIN:",ok,"|",$Roles,":LOGIN>>",!',
        "try {",
        "set %request=##class(%CSP.Request).%New(), %response=##class(%CSP.Response).%New()",
        "set %session=##class(%CSP.Session).%New(), s=##class(%Stream.TmpCharacter).%New()",
        f'do s.Write("{body}"), s.Rewind() set %request.Content=s',
        f'set %request.Method="POST", %request.URL="{url}"',
        f'set %request.CgiEnvs("REQUEST_METHOD")="POST", %request.CgiEnvs("PATH_INFO")="{url}"',
        'set %request.CgiEnvs("CONTENT_TYPE")="application/json", %request.CgiEnvs("HTTP_ACCEPT")="application/json"',
        'write "<<B:",!',
        f'set sc=##class(Graph.KG.Service).DispatchRequest("{url}","POST",0)',
        'write !,":B>>",!',
        'write "<<ST:",%response.Status,":ST>>",!',
        '} catch e { write !,"<<ERR:",e.DisplayString(),":ERR>>",! }',
        "halt",
    ])
    out = alt.session([line])
    return alt.marked(out, "LOGIN"), alt.marked(out, "ST"), alt.marked(out, "B").strip(), out


def test_the_legacy_tables_are_there_and_populated(webapp):
    for table in ("kg_NodeEmbeddings_optimized", "kg_EdgeEmbeddings"):
        assert alt.rows(webapp["conn"], f"SELECT COUNT(*) FROM {alt.SCHEMA}.{table} WHERE COALESCE(graph_id, '') = ''")[0][0] >= 1


@pytest.mark.parametrize("url, request_, expect", [
    ("/vectorSearch", {"vector": VEC, "k": 1}, "r316:d"),
    ("/vectorSearch", {"vector": VEC, "k": 1, "graph": GRAPH}, "r316:a"),
    ("/hybridSearch", {"vector": VEC, "text": "r316", "k": 1}, "r316:d"),
    ("/metaPath", {"srcId": "r316:d", "predicates": ["R316"], "maxHops": 1}, "r316:e"),
], ids=["vector-default", "vector-named", "hybrid-default", "metapath"])
def test_every_route_answers_a_user_holding_only_the_role(webapp, url, request_, expect):
    login, status, body, out = _as_reader(url, request_)
    assert login == f"1|{ROLE}", out
    assert status.startswith("200"), out
    assert "-99" not in out and "PROTECT" not in out, out
    assert expect in body, out


def test_the_default_graph_search_answers_from_kg_node_embeddings(webapp):
    _, status, body, out = _as_reader("/vectorSearch", {"vector": VEC, "k": 1})
    assert [(r["id"], round(r["score"], 6)) for r in json.loads(body)] == [("r316:d", 1.0)], out


def test_a_user_holding_only_the_role_searches_the_named_graph(webapp):
    body = json.dumps({"vector": VEC, "k": 1, "graph": GRAPH}).replace('"', '""')
    line = " ".join([
        f'set ok=$SYSTEM.Security.Login("{USER}","{PASSWORD}")',
        'write "<<LOGIN:",ok,"|",$Roles,":LOGIN>>",!',
        "try {",
        "set %request=##class(%CSP.Request).%New(), %response=##class(%CSP.Response).%New()",
        "set %session=##class(%CSP.Session).%New(), s=##class(%Stream.TmpCharacter).%New()",
        f'do s.Write("{body}"), s.Rewind() set %request.Content=s',
        'set %request.Method="POST", %request.URL="/vectorSearch"',
        'set %request.CgiEnvs("REQUEST_METHOD")="POST", %request.CgiEnvs("PATH_INFO")="/vectorSearch"',
        'set %request.CgiEnvs("CONTENT_TYPE")="application/json", %request.CgiEnvs("HTTP_ACCEPT")="application/json"',
        'write "<<B:",!',
        'set sc=##class(Graph.KG.Service).DispatchRequest("/vectorSearch","POST",0)',
        'write !,":B>>",!',
        'write "<<ST:",%response.Status,":ST>>",!',
        '} catch e { write !,"<<ERR:",e.DisplayString(),":ERR>>",! }',
        "halt",
    ])
    out = alt.session([line])
    assert alt.marked(out, "LOGIN") == f"1|{ROLE}", out
    assert alt.marked(out, "ST").startswith("200"), out
    assert [(r["id"], round(r["score"], 6)) for r in json.loads(alt.marked(out, "B").strip())] == [("r316:a", 1.0)], out
