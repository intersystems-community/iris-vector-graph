"""4.6.0 R265: a named graph whose vectors have a route of their own is refused by the SQL procedures and answered by
the REST searches.

`kg_KNN_VEC` reads `kg_NodeEmbeddings` (its FROM is fixed), and `kg_RRF_FUSE`'s vector leg is `kg_KNN_VEC`. A named
graph's vectors written through the API live in the graph's route, a `kg_emb_*` table (spec 227), which
`engine.kg_KNN_VEC`, `engine.kg_RRF_FUSE` and `CALL ivg.retrieve` read. So the procedures answered no rows, or failed
on the route's width, for a graph full of vectors, and `/vectorSearch` and `/hybridSearch` (which call them) with it.
Now the procedures raise, naming the route's table and the calls that read it; the two routes read the route; and
`CreateWebApp` grants its role SELECT on each node route there is, so a user holding only that role gets the answer.

The graph, by hand (G = r265g_<run>, 3 wide, label R265, written by `store_embedding(graph=G)`, which makes G's
route). Documents in G: a "r265token once", c "r265token r265token r265token".

    node  vector          cosine to q = [1,0,0]   occurrences of r265token
    a     [1, 0, 0]       1.0  (rank 1)           1 (rank 2)
    b     [0.6, 0.8, 0]   0.6  (rank 2)           -
    c     [0, 1, 0]       0.0  (rank 3)           3 (rank 1)

- `/vectorSearch` q, k 2: a 1.0, b 0.6 (the same with label R265).
- `/hybridSearch` q, "r265token", k 3, c 60, as kg_RRF_FUSE fuses (a missing rank is 1e9): a 1/61 + 1/62 (vs 1, bm25 1),
  c 1/63 + 1/61 (vs 0, bm25 3), b 1/62 + 1/(60 + 1e9) (vs 0.6, bm25 null).
- With SELECT on G's route revoked from the role, `/vectorSearch` answers the privilege error, which names the table.
- A graph with no route of its own, and the default graph, are read by the procedures as before (no refusal).

The REST requests run the dispatcher as tests/e2e/test_460_kg_webapp_least_privilege_e2e.py does: a user holding the
role and nothing else logs in (`$SYSTEM.Security.Login`) and drives `Graph.KG.Service` with stub `%request`.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import uuid

import pytest

pytestmark = [pytest.mark.e2e]

CONTAINER = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")
NS = os.environ.get("IVG_NAMESPACE", "USER")
APP = "/ivg460-r265-kg"
USER, PASSWORD = "ivg460r265reader", "r265Reader460"
ROLE = "IVG_KG_" + NS.upper()
TERM = "r265token"
VECTORS = {"a": [1.0, 0.0, 0.0], "b": [0.6, 0.8, 0.0], "c": [0.0, 1.0, 0.0]}
DOCS = {"a": f"{TERM} once", "c": f"{TERM} {TERM} {TERM}"}
Q = [1.0, 0.0, 0.0]


def _session(lines: list, namespace: str = "") -> str:
    script = "\n".join(lines + ["halt"]) + "\n"
    out = subprocess.run(["docker", "exec", "-i", CONTAINER, "iris", "session", "IRIS", "-U", namespace or NS],
                         input=script, capture_output=True, text=True, timeout=180)
    return out.stdout + out.stderr


def _marked(out: str, tag: str) -> str:
    start = f"<<{tag}:"
    if start not in out:
        raise AssertionError(f"no {tag} marker in the session output:\n{out[-3000:]}")
    return out.split(start, 1)[1].split(f":{tag}>>", 1)[0]


def _drop_app_and_user():
    _session([f'if ##class(Security.Applications).Exists("{APP}") do ##class(Security.Applications).Delete("{APP}")',
              f'if ##class(Security.Users).Exists("{USER}") do ##class(Security.Users).Delete("{USER}")'], "%SYS")


def _make_user(roles: str):
    out = _session([f'if ##class(Security.Users).Exists("{USER}") do ##class(Security.Users).Delete("{USER}")',
                    f'set sc=##class(Security.Users).Create("{USER}","{roles}","{PASSWORD}","R265 test","{NS}")',
                    'write "<<SC:",sc,":SC>>",!'], "%SYS")
    assert _marked(out, "SC") == "1", out


def _role_privileges() -> list:
    out = _session([
        'set rs=##class(%ResultSet).%New("%Library.SQLCatalogPriv:SQLUserPrivs")',
        f'set sc=rs.Execute("{ROLE}")',
        'write "<<PRIV:" while rs.Next() { write rs.GetData(1),"|",rs.GetData(2),"|",rs.GetData(3),";" } '
        'write ":PRIV>>",!'])
    return sorted(tuple(p.split("|")) for p in _marked(out, "PRIV").split(";") if p)


def _as_user(url: str, body: dict) -> tuple:
    """Log in as USER and run the dispatcher on `url`, on one terminal line: (login, status, body, output)."""
    escaped = json.dumps(body).replace('"', '""')
    line = " ".join([
        f'set ok=$SYSTEM.Security.Login("{USER}","{PASSWORD}")',
        'write "<<LOGIN:",ok,"|",$Username,"|",$Roles,":LOGIN>>",!',
        "try {",
        "set %request=##class(%CSP.Request).%New(), %response=##class(%CSP.Response).%New()",
        "set %session=##class(%CSP.Session).%New(), s=##class(%Stream.TmpCharacter).%New()",
        f'do s.Write("{escaped}"), s.Rewind() set %request.Content=s',
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
    out = _session([line])
    login = _marked(out, "LOGIN").split("|")
    text = _marked(out, "B").strip() if "<<B:" in out and ":B>>" in out else ""
    status = _marked(out, "ST") if "<<ST:" in out else ""
    return login, status, text, out


def _width(conn) -> int:
    from iris_vector_graph.schema import GraphSchema

    cur = conn.cursor()
    try:
        return int(GraphSchema.get_embedding_dimension(cur, "Graph_KG.kg_NodeEmbeddings"))
    finally:
        cur.close()


@pytest.fixture(scope="module")
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    run = uuid.uuid4().hex[:8]
    graph = f"r265g_{run}"
    ids = {k: f"r265_{run}_{k}" for k in "abc"}
    for k, nid in ids.items():
        engine.create_node(nid, labels=["R265"], graph=graph)
        assert engine.store_embedding(nid, VECTORS[k], graph=graph)
    cur = iris_connection.cursor()
    for k, text in DOCS.items():
        cur.execute("INSERT INTO Graph_KG.docs (graph_id, id, text) VALUES (?, ?, ?)", (graph, ids[k], text))
    iris_connection.commit()
    route = engine.resolve_route(graph=graph, model_key=None)
    assert route is not None and route.table_name.startswith("kg_emb_"), route
    try:
        yield {"engine": engine, "graph": graph, "ids": ids, "route": route.table_name}
    finally:
        with contextlib.suppress(Exception):
            cur.execute("DELETE FROM Graph_KG.docs WHERE graph_id = ?", (graph,))
            iris_connection.commit()
        engine.erase_graph(graph)
        iris_connection.commit()


def _call(conn, sql, args):
    cur = conn.cursor()
    try:
        cur.execute(sql, args)
        return list(cur.fetchall())
    finally:
        cur.close()


@pytest.mark.parametrize(
    "sql,args",
    [
        ("SELECT node_id, score FROM Graph_KG.kg_KNN_VEC(?, ?, ?, ?)", lambda gr: [json.dumps(Q), 5, None, gr]),
        ("SELECT id, rrf, vs, ts FROM Graph_KG.kg_RRF_FUSE(?, ?, ?, ?, ?, ?, ?)",
         lambda gr: [3, 10, 10, 60, json.dumps(Q), TERM, gr]),
    ],
    ids=["kg_KNN_VEC", "kg_RRF_FUSE"],
)
def test_the_procedure_refuses_a_graph_whose_vectors_have_their_own_route(g, iris_connection, sql, args):
    with pytest.raises(Exception) as caught:
        _call(iris_connection, sql, args(g["graph"]))
    message = str(caught.value)
    for named in (g["route"], g["graph"], "engine.kg_RRF_FUSE", "ivg.retrieve", "/hybridSearch"):
        assert named in message, message


def test_a_graph_with_no_route_and_the_default_graph_are_read_as_before(g, iris_connection):
    q = json.dumps([1.0] + [0.0] * (_width(iris_connection) - 1))
    assert _call(iris_connection, "SELECT node_id FROM Graph_KG.kg_KNN_VEC(?, ?, ?, ?)",
                 [q, 5, None, f"{g['graph']}_none"]) == []
    _call(iris_connection, "SELECT node_id FROM Graph_KG.kg_KNN_VEC(?, ?, ?, ?)", [q, 1, None, ""])


@pytest.fixture(scope="module")
def webapp(g):
    _drop_app_and_user()
    out = _session([f'set sc=##class(Graph.KG.Service).CreateWebApp("{APP}")',
                    'write "<<CREATED:",$system.Status.GetErrorText(sc),"|",sc,":CREATED>>",!'])
    created = _marked(out, "CREATED")
    assert created.endswith("|1"), created
    _make_user(ROLE)
    try:
        yield g
    finally:
        _drop_app_and_user()


def test_create_web_app_grants_select_on_the_route(webapp):
    assert ("TABLE", f"Graph_KG.{webapp['route']}", "SELECT") in _role_privileges()


def test_a_user_holding_only_the_role_gets_the_named_graphs_vector_search(webapp):
    names = {v: k for k, v in webapp["ids"].items()}
    for label in ("", "R265"):
        login, status, text, out = _as_user("/vectorSearch", {"vector": Q, "k": 2, "label": label,
                                                              "graph": webapp["graph"]})
        assert login == ["1", USER, ROLE], out
        assert status.startswith("200"), out
        assert [(names.get(r["id"], r["id"]), round(r["score"], 6)) for r in json.loads(text)] == [
            ("a", 1.0), ("b", 0.6)], out


def test_a_user_holding_only_the_role_gets_the_named_graphs_hybrid_search(webapp):
    names = {v: k for k, v in webapp["ids"].items()}
    login, status, text, out = _as_user("/hybridSearch", {"vector": Q, "text": TERM, "k": 3, "c": 60,
                                                          "graph": webapp["graph"]})
    assert login == ["1", USER, ROLE], out
    assert status.startswith("200"), out
    got = [(names.get(r["id"], r["id"]), round(r["score"], 9), r["extras"]["vs"], r["extras"]["bm25"])
           for r in json.loads(text)]
    got = [(n, s, None if vs is None else round(vs, 6), bm) for n, s, vs, bm in got]
    assert got == [
        ("a", round(1 / 61 + 1 / 62, 9), 1.0, 1.0),
        ("c", round(1 / 63 + 1 / 61, 9), 0.0, 3.0),
        ("b", round(1 / 62 + 1 / (60 + 1_000_000_000), 9), 0.6, None),
    ], out


def test_a_route_the_role_may_not_read_answers_the_privilege_error_naming_it(webapp):
    """A route made after CreateWebApp ran is not granted: the request fails naming the table (R143), and
    ADMIN_GUIDE says to run CreateWebApp again. Revoking the grant stands in for that route."""
    table = f"Graph_KG.{webapp['route']}"
    _session([f'set rs=##class(%SQL.Statement).%ExecDirect(,"REVOKE SELECT ON {table} FROM {ROLE}")'])
    try:
        login, status, text, out = _as_user("/vectorSearch", {"vector": Q, "k": 2, "graph": webapp["graph"]})
        assert login == ["1", USER, ROLE], out
        assert not status.startswith("200"), out
        assert webapp["route"] in out, out
    finally:
        _session([f'set rs=##class(%SQL.Statement).%ExecDirect(,"GRANT SELECT ON {table} TO {ROLE}")'])
