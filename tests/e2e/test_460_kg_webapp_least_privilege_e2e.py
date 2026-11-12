"""4.6.0 R131 [SECURITY]: the `/kg` web application `Graph.KG.Service.CreateWebApp` adds one role, not %All.

`CreateWebApp` set `MatchRoles ":%All"`, so every user the application authenticated ran the dispatch with %All. It
now adds one role, `IVG_KG_<namespace>`, which holds read on the namespace's database and, in the IVG Schema,
EXECUTE on `kg_KNN_VEC`, `kg_RRF_FUSE`, `kg_TXT` and (4.6.0 R265) `kg_VEC_ROUTE_GUARD`, and SELECT on
`kg_NodeEmbeddings`, `rdf_labels`, `docs`, `embedding_registry` and each node route the registry names. Each was found needed here: the procedures read with the caller's privileges and answer no
rows (not an error) for a table the caller may not read, and PyOps reads the vector width from the table's column,
which INFORMATION_SCHEMA shows only to a user with a privilege on the table. The fixture first revokes whatever the
role holds, so the privileges read back are the ones `CreateWebApp` granted.

The application is read back from `Security.Applications` in %SYS, the role from `Security.Roles`, its SQL
privileges from `%Library.SQLCatalogPriv:SQLUserPrivs`. A user holding that role and nothing else then logs in
(`$SYSTEM.Security.Login`) and drives the dispatcher with stub `%request`/`%response`, as
tests/integration/test_rest_service_handlers.py does: the enterprise image has no web gateway, so this runs the
dispatch code a gateway request runs, with that user's privileges, but not the gateway's own authentication. The
login and the request are one terminal line ending in `halt`: the terminal prompt itself needs %Development, which
the user does not hold and a web request never asks for.

Data, by hand, in the default graph of the namespace emptied first (`Graph.KG.Eraser.EraseAll`): kg131:a with label
KG131 and vector [1,0,...] (zeros to the width), kg131:b with label KG131, [0,1,0,...] and the document "zebra
crossing", and kg131:a -R131-> kg131:b.
- /vectorSearch [1,0,...], k 1, label KG131: kg131:a, cosine 1.
- /hybridSearch [1,0,...], "zebra", k 2, c 60: vector ranks a 1, b 2; text ranks b 1 (the only document); a row
  missing from a leg ranks 1e9. rrf b = 1/62 + 1/61, a = 1/61 + 1/(60 + 1e9): b, then a.
- /metaPath from kg131:a along R131, one hop: the edge kg131:a R131 kg131:b.
The same user without the role gets none of these answers.
"""

from __future__ import annotations

import contextlib
import json

import pytest

from tests.e2e import ns460alt as alt

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]

APP = "/ivg460-r131-kg"
USER, PASSWORD = "ivg460r131reader", "r131Reader460"
ROLE = "IVG_KG_" + alt.NS


def _sys(lines: list) -> str:
    return alt.session(lines, namespace="%SYS", allow_sys=True)


def _drop_app_and_user():
    _sys([f'if ##class(Security.Applications).Exists("{APP}") do ##class(Security.Applications).Delete("{APP}")',
          f'if ##class(Security.Users).Exists("{USER}") do ##class(Security.Users).Delete("{USER}")'])


def _make_user(roles: str):
    out = _sys([f'if ##class(Security.Users).Exists("{USER}") do ##class(Security.Users).Delete("{USER}")',
                f'set sc=##class(Security.Users).Create("{USER}","{roles}","{PASSWORD}","R131 test","{alt.NS}")',
                'write "<<SC:",sc,":SC>>",!'])
    assert alt.marked(out, "SC") == "1", out


def _role_privileges() -> list:
    out = alt.session([
        'set rs=##class(%ResultSet).%New("%Library.SQLCatalogPriv:SQLUserPrivs")',
        f'set sc=rs.Execute("{ROLE}")',
        'write "<<PRIV:" while rs.Next() { write rs.GetData(1),"|",rs.GetData(2),"|",rs.GetData(3),";" } '
        'write ":PRIV>>",!'])
    return sorted(tuple(p.split("|")) for p in alt.marked(out, "PRIV").split(";") if p)


@pytest.fixture(scope="module")
def webapp():
    import iris

    alt.make_namespace()
    conn = alt.connect()
    engine = alt.engine(conn)
    engine.initialize_schema()
    iris.createIRIS(conn).classMethodValue("Graph.KG.Eraser", "EraseAll")
    conn.commit()
    for nid, vec in (("kg131:a", [1.0] + [0.0] * (alt.DIM - 1)), ("kg131:b", [0.0, 1.0] + [0.0] * (alt.DIM - 2))):
        engine.create_node(nid, labels=["KG131"])
        assert engine.store_embedding(nid, vec)
    engine.create_edge("kg131:a", "R131", "kg131:b")
    conn.cursor().execute(f"INSERT INTO {alt.SCHEMA}.docs (graph_id, id, text) VALUES ('', 'kg131:b', 'zebra crossing')")
    conn.commit()
    _drop_app_and_user()
    # A REVOKE removes only the grantor's own grants: run it as the session user CreateWebApp's grants came from.
    alt.session([f'set rs=##class(%SQL.Statement).%ExecDirect(,"REVOKE {privilege} ON {name} FROM {ROLE}")'
                 for _, name, privilege in _role_privileges()])
    assert _role_privileges() == []
    out = alt.session([f'set sc=##class(Graph.KG.Service).CreateWebApp("{APP}")',
                       'write "<<CREATED:",$system.Status.GetErrorText(sc),"|",sc,":CREATED>>",!'])
    created = alt.marked(out, "CREATED")
    read = _sys([f'set sc=##class(Security.Applications).Get("{APP}",.p)',
                 'write "<<MATCH:",$get(p("MatchRoles")),":MATCH>>",!',
                 'write "<<DISPATCH:",$get(p("DispatchClass")),":DISPATCH>>",!',
                 f'set sc=##class(Security.Roles).Get("{ROLE}",.r)',
                 'write "<<RES:",$get(r("Resources")),":RES>>",!',
                 f'set db=##class(SYS.Database).%OpenId("/usr/irissys/mgr/{alt.NS.lower()}/")',
                 'write "<<DBRES:",db.ResourceName,":DBRES>>",!'])
    yield {"engine": engine, "conn": conn, "created": created, "read": read, "privileges": _role_privileges()}
    _drop_app_and_user()
    conn.cursor().execute(f"DELETE FROM {alt.SCHEMA}.docs WHERE id = 'kg131:b'")
    for nid in ("kg131:a", "kg131:b"):
        with contextlib.suppress(Exception):
            engine.delete_node(nid, graph=None)
    conn.commit()
    conn.close()


def test_create_web_app_succeeds(webapp):
    assert webapp["created"].endswith("|1"), webapp["created"]


def test_the_application_adds_its_own_role_and_not_all(webapp):
    match = alt.marked(webapp["read"], "MATCH")
    assert match == f":{ROLE}", match
    assert alt.marked(webapp["read"], "DISPATCH") == "Graph.KG.Service"


def test_the_role_reads_the_namespace_database_and_nothing_else(webapp):
    resources = alt.marked(webapp["read"], "RES")
    db_resource = alt.marked(webapp["read"], "DBRES")
    assert resources.split(",") == [f"{db_resource}:R"], resources


def test_the_role_holds_execute_on_four_routines_and_select_on_four_tables_and_the_routes_only(webapp):
    """4.6.0 R265 added EXECUTE on kg_VEC_ROUTE_GUARD (kg_KNN_VEC calls it with the caller's privileges) and SELECT on
    each node route the registry names (/vectorSearch and /hybridSearch read a named graph's vectors there)."""
    routes = sorted(
        ("TABLE", f"{alt.SCHEMA}.{t.split('.')[-1]}", "SELECT")
        for (t,) in alt.rows(webapp["conn"], f"SELECT DISTINCT table_name FROM {alt.SCHEMA}.embedding_registry "
                                             "WHERE COALESCE(kind, 'node') = 'node'")
        if t.split(".")[-1].upper() != "KG_NODEEMBEDDINGS"
    )
    assert webapp["privileges"] == sorted([
        ("STORED PROCEDURE", f"{alt.SCHEMA}.kg_VEC_ROUTE_GUARD", "EXECUTE"),
        ("STORED PROCEDURE", f"{alt.SCHEMA}.kg_KNN_VEC", "EXECUTE"),
        ("STORED PROCEDURE", f"{alt.SCHEMA}.kg_RRF_FUSE", "EXECUTE"),
        ("STORED PROCEDURE", f"{alt.SCHEMA}.kg_TXT", "EXECUTE"),
        ("TABLE", f"{alt.SCHEMA}.docs", "SELECT"),
        ("TABLE", f"{alt.SCHEMA}.embedding_registry", "SELECT"),
        ("TABLE", f"{alt.SCHEMA}.kg_NodeEmbeddings", "SELECT"),
        ("TABLE", f"{alt.SCHEMA}.rdf_labels", "SELECT"),
        *routes,
    ]), webapp["privileges"]


def _as_user(url: str, body: dict) -> tuple:
    """Log in as USER in NS and run the dispatcher on `url`, on one terminal line: (login, status, body, output)."""
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
    out = alt.session([line])
    login = alt.marked(out, "LOGIN").split("|")
    text = alt.marked(out, "B").strip() if "<<B:" in out and ":B>>" in out else ""
    status = alt.marked(out, "ST") if "<<ST:" in out else ""
    return login, status, text, out


VECTOR_SEARCH = ("/vectorSearch", {"vector": [1.0] + [0.0] * (alt.DIM - 1), "k": 1, "label": "KG131"})
HYBRID_SEARCH = ("/hybridSearch", {"vector": [1.0] + [0.0] * (alt.DIM - 1), "text": "zebra", "k": 2, "c": 60})
META_PATH = ("/metaPath", {"srcId": "kg131:a", "predicates": ["R131"], "maxHops": 1})


def test_a_user_holding_only_the_role_gets_the_vector_search_answer(webapp):
    _make_user(ROLE)
    login, status, text, out = _as_user(*VECTOR_SEARCH)
    assert login == ["1", USER, ROLE], out
    assert status.startswith("200"), out
    assert [(r["id"], round(r["score"], 6)) for r in json.loads(text)] == [("kg131:a", 1.0)], out


def test_a_user_holding_only_the_role_gets_the_hybrid_search_answer(webapp):
    _make_user(ROLE)
    login, status, text, out = _as_user(*HYBRID_SEARCH)
    assert login == ["1", USER, ROLE], out
    assert status.startswith("200"), out
    assert [(r["id"], round(r["score"], 9)) for r in json.loads(text)] == [
        ("kg131:b", round(1 / 62 + 1 / 61, 9)), ("kg131:a", round(1 / 61 + 1 / (60 + 1_000_000_000), 9))], out


def test_a_user_holding_only_the_role_gets_the_meta_path_answer(webapp):
    _make_user(ROLE)
    login, status, text, out = _as_user(*META_PATH)
    assert login == ["1", USER, ROLE], out
    assert status.startswith("200"), out
    assert [(e["s"], e["p"], e["o"]) for e in json.loads(text)] == [("kg131:a", "R131", "kg131:b")], out


def test_the_same_user_without_the_role_gets_neither_answer(webapp):
    """The role is what grants the reads: without it the user has only the public permissions."""
    _make_user("")
    for url, body in (VECTOR_SEARCH, HYBRID_SEARCH, META_PATH):
        login, status, text, out = _as_user(url, body)
        assert login == ["1", USER, ""], out
        assert "kg131:a" not in text and "kg131:b" not in text, (url, out)
