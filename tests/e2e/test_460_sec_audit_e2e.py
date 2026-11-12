"""4.6.0 security sweep, on a server: a value from an HTTP body or a stored row must not change the SQL IVG runs.

- `POST /admin/embed {"label": ...}` pasted the label into embed_nodes' selection (`embed_selector`).
- `embedding_registry.table_name` is a row any writer of the IVG Schema can insert; three ObjectScript readers pasted
  it into SQL run by someone else: `Graph.KG.Service.GrantWebAppSql` (a GRANT, as whoever runs CreateWebApp),
  `Graph.KG.Eraser` (DELETE and DROP, as whoever erases) and `Graph.KG.PyOps.RouteVectorRows` (the /kg vector search).

Run with IVG_NAMESPACE=<an IVG460FX* scratch namespace> (`scripts/dev/make_scratch_namespace.py IVG460FXSEC`), IVG
Schema Graph_KG, classes deployed from this checkout. Every row, role and grant a test makes is removed at its end.
"""

from __future__ import annotations

import os

import pytest

NS = os.environ.get("IVG_NAMESPACE", "")
PORT = int(os.environ.get("IVG_PORT", "31972"))
CONTAINER = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")
ROLE = "IVG_SECAUDIT_ROLE"
OTHER = "IVG_SECAUDIT_OTHER"

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not NS.startswith("IVG460FX"), reason="needs IVG_NAMESPACE=IVG460FX<...> (a scratch namespace)"),
]


def _session(lines, namespace=None, allow_sys=False) -> str:
    """What `lines` write in `namespace` (NS by default), over TCP (tests/helpers/iris_lines.py; 4.7.0: no
    `iris session`). %SYS only with `allow_sys`: the two roles one test makes and deletes."""
    from tests.helpers.iris_lines import session

    return session(lines, namespace or NS, allow_sys=allow_sys, timeout=180)


def _marked(out: str, tag: str) -> str:
    start = out.index(f"<<{tag}:") + len(tag) + 3
    return out[start:out.index(f":{tag}>>", start)]


@pytest.fixture(scope="module")
def conn():
    import iris

    c = iris.connect("localhost", PORT, NS, "_SYSTEM", "SYS")
    yield c
    c.close()


@pytest.fixture(scope="module")
def engine(conn):
    from iris_vector_graph import IRISGraphEngine

    return IRISGraphEngine(conn, embedding_dimension=4, namespace=NS)


def _rows(conn, sql, params=None):
    cur = conn.cursor()
    cur.execute(sql, params or [])
    return cur.fetchall()


def _run(conn, sql, params=None):
    cur = conn.cursor()
    cur.execute(sql, params or [])
    conn.commit()


@pytest.fixture
def planted(conn):
    """Registry rows a test plants, deleted at its end."""
    names = []

    def plant(table_name, graph_id):
        _run(conn, "INSERT INTO Graph_KG.embedding_registry (table_name, graph_id, kind, dtype, dimension) "
                   "VALUES (?, ?, 'node', 'DOUBLE', 4)", [table_name, graph_id])
        names.append(table_name)

    yield plant
    for n in names:
        _run(conn, "DELETE FROM Graph_KG.embedding_registry WHERE table_name = ?", [n])


@pytest.fixture
def route(conn, engine):
    """A real node route: graph secA's, holding one vector."""
    engine.create_node("sec:a", labels=["Sec"], graph="secA")
    engine.store_embedding("sec:a", [1.0, 0.0, 0.0, 0.0], graph="secA")
    table = _rows(conn, "SELECT table_name FROM Graph_KG.embedding_registry WHERE graph_id = 'secA' "
                        "AND COALESCE(kind, 'node') = 'node'")[0][0]
    yield table
    engine.erase_graph("secA")


def _role_privileges(role) -> list:
    out = _session(['set rs=##class(%ResultSet).%New("%Library.SQLCatalogPriv:SQLRolePrivileges")',
                    f'set sc=rs.Execute("{role}")',
                    'write "<<PRIV:" while rs.Next() { write rs.GetData(2),"|",rs.GetData(3),";" } write ":PRIV>>",!'])
    return sorted(p for p in _marked(out, "PRIV").split(";") if p)


def test_grant_web_app_sql_grants_nothing_a_registry_row_writes(conn, route, planted):
    """The GRANT runs as whoever runs CreateWebApp; the row named a second grantee for one of its tables."""
    _session([f'Do ##class(Security.Roles).Create("{r}")' for r in (ROLE, OTHER)], namespace="%SYS", allow_sys=True)
    try:
        planted(f"rdf_props TO {OTHER} --", "secG")
        out = _session([f'Set sc=##class(Graph.KG.Service).GrantWebAppSql("{ROLE}",.m)',
                        'Write "<<G:",$System.Status.IsOK(sc),"|",$System.Status.GetErrorText(sc),":G>>",!'])
        assert _marked(out, "G").startswith("1|"), out
        assert _role_privileges(OTHER) == []
        assert f"Graph_KG.{route}|SELECT" in _role_privileges(ROLE)  # a real route is still granted
    finally:
        _session([f'Do ##class(Security.Roles).Delete("{r}")' for r in (ROLE, OTHER)], namespace="%SYS", allow_sys=True)


def test_erase_graph_reaches_only_its_own_routes(conn, engine, route, planted):
    planted(f"{route} WHERE 1=1 OR ? IS NULL --", "secB")
    engine.create_node("sec:b", graph="secB")
    engine.erase_graph("secB")
    assert [tuple(r) for r in _rows(conn, f"SELECT node_id FROM Graph_KG.{route}")] == [("sec:a",)]


def test_route_vector_rows_reads_only_a_route(conn, route, planted):
    planted(f"{route} e WHERE 1=0 UNION SELECT 'pwned', 99 WHERE ? IS NOT NULL --", "secC")
    out = _session([
        'Try { Set r=##class(Graph.KG.PyOps).RouteVectorRows("[1,0,0,0]",5,"","secC")'
        ' Write "<<R:",##class(%SYS.Python).Builtins().str(r),":R>>",! }'
        ' Catch e { Write "<<R:ERR ",e.DisplayString(),":R>>",! }'])
    answer = _marked(out, "R")
    # the injected UNION answered [['pwned', 99]]; the row is refused instead
    assert answer.startswith("ERR") and "which is not a node route" in answer, answer


def test_admin_embed_label_selects_only_that_label(engine):
    from fastapi.testclient import TestClient

    from iris_vector_graph import cypher_api

    for i in range(3):
        engine.create_node(f"sec:e{i}", labels=["SecEmbed"], properties={"name": f"n{i}"})
    cypher_api._engine_cache = engine
    try:
        client = TestClient(cypher_api.app)
        r = client.post("/admin/embed", json={"label": "nolabel' OR '1'='1", "force": True})
        assert r.status_code == 200, r.text
        assert r.json()["result"]["total"] == 0, r.json()
        r = client.post("/admin/embed", json={"label": "SecEmbed", "force": True})
        assert r.json()["result"]["total"] == 3, r.json()
    finally:
        cypher_api._engine_cache = None
        for i in range(3):
            engine.delete_node(f"sec:e{i}")
