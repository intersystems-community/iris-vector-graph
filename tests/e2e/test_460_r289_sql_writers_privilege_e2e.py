"""4.6.0 R289 [SECURITY], live: the SQL-callable writers check the write privilege on the tables they write.

`backfill_temporal_rows` writes node and relationship rows through embedded SQL, which IRIS does not check against the
caller's privileges: a user holding SELECT on the IVG Schema and EXECUTE on it wrote them (measured 2026-11-04,
2026.3.0AI). It now checks INSERT on nodes and rdf_edges first (Graph.KG.SqlPrivilege.RequireWrite).
`rebuild_edge_indices` runs BUILD INDEX for nodes, rdf_labels, rdf_props and rdf_edges through dynamic SQL, which IRIS
refuses without %BUILD_INDEX (INSERT, UPDATE and DELETE do not do); it caught the refusal and answered 0, and now
raises it, naming the table.

The namespace is `IVG_NAMESPACE` (an IVG460*/IVG262* scratch namespace with IVG set up). The users and roles are
instance-wide, named `<IVG460_PRIV_PREFIX>_SEL_*` (SELECT only) and `_WRT_*` (SELECT, INSERT, UPDATE and
%BUILD_INDEX), default prefix
`<namespace>_R289`, and dropped at the end, as are the rows and the temporal entry.

The data, worked by hand: one temporal entry `^KG("tout", 0, 1700000289, "r289a", "r289p", "r289b") = 1` and no row
for it; the namespace's ^KG("tout") holds nothing else (checked). A backfill asserts that one triple: nodes r289a and
r289b in the default graph, and the relationship r289a -r289p-> r289b with qualifiers {"temporal": true}; it answers 1.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from tests.e2e import ns460alt as alt

NS = alt.NS
PREFIX = os.environ.get("IVG460_PRIV_PREFIX", f"{NS}_R289")
PASSWORD = "Priv289x"
SEL_ROLE, SEL_USER = f"{PREFIX}_SEL_ROLE", f"{PREFIX}_SEL_USER"
WRT_ROLE, WRT_USER = f"{PREFIX}_WRT_ROLE", f"{PREFIX}_WRT_USER"
CONNECT = "%Service_SQL:U,%DB_%DEFAULT:RW,%DB_IRISLIB:R,%DB_IRISSYS:R"
TS = 1700000289

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]


def _sys(lines: list) -> str:
    script = "\n".join(lines + ["halt"]) + "\n"
    out = subprocess.run(["docker", "exec", "-i", alt.CONTAINER, "iris", "session", "IRIS", "-U", "%SYS"],
                         input=script, capture_output=True, text=True, timeout=120, check=True)
    return out.stdout


def _drop_principals() -> None:
    lines = []
    for n in (SEL_USER, WRT_USER):
        lines.append(f'if ##class(Security.Users).Exists("{n}") do ##class(Security.Users).Delete("{n}")')
    for n in (SEL_ROLE, WRT_ROLE):
        lines.append(f'if ##class(Security.Roles).Exists("{n}") do ##class(Security.Roles).Delete("{n}")')
    _sys(lines)


def _rows(conn, schema):
    return {
        "nodes": sorted(tuple(r) for r in alt.rows(
            conn, f"SELECT node_id, graph_id FROM {schema}.nodes WHERE node_id IN ('r289a', 'r289b')")),
        "edges": [(s, p, o, g, json.loads(q)) for s, p, o, g, q in alt.rows(
            conn, f"SELECT s, p, o_id, graph_id, qualifiers FROM {schema}.rdf_edges WHERE s = 'r289a'")],
    }


def _clear_rows(conn, schema):
    cur = conn.cursor()
    cur.execute(f"DELETE FROM {schema}.rdf_edges WHERE s = 'r289a'")
    cur.execute(f"DELETE FROM {schema}.nodes WHERE node_id IN ('r289a', 'r289b')")
    conn.commit()
    cur.close()


@pytest.fixture(scope="module")
def world():
    import iris

    conn = alt.connect()
    native = iris.createIRIS(conn)
    schema = native.get("^IVG.Deploy", "schema") or "Graph_KG"
    if native.isDefined("^KG", "tout"):
        conn.close()
        pytest.skip(f"{NS}'s ^KG(\"tout\") holds temporal entries: the hand-worked count is for an empty one")
    _drop_principals()
    out = _sys([f'set sc=##class(Security.Roles).Create("{r}","4.6.0 R289","{CONNECT}") write "SC=",sc,!'
                for r in (SEL_ROLE, WRT_ROLE)]
               + [f'set sc=##class(Security.Users).Create("{u}","{r}","{PASSWORD}","4.6.0 R289","{NS}") write "SC=",sc,!'
                  for u, r in ((SEL_USER, SEL_ROLE), (WRT_USER, WRT_ROLE))])
    assert out.count("SC=1") == 4, out
    cur = conn.cursor()
    for fn in ("backfill_temporal_rows", "rebuild_edge_indices"):
        cur.execute(f"GRANT EXECUTE ON {schema}.{fn} TO {SEL_ROLE}, {WRT_ROLE}")
    cur.execute(f"GRANT SELECT ON SCHEMA {schema} TO {SEL_ROLE}, {WRT_ROLE}")
    cur.execute(f"GRANT INSERT, UPDATE ON SCHEMA {schema} TO {WRT_ROLE}")
    cur.execute(f"GRANT %BUILD_INDEX TO {WRT_ROLE}")
    cur.close()
    _clear_rows(conn, schema)
    native.set(1, "^KG", "tout", 0, TS, "r289a", "r289p", "r289b")

    def call(who: str, sql: str):
        uconn = iris.connect("localhost", alt.PORT, NS, who, PASSWORD)
        try:
            c = uconn.cursor()
            c.execute(sql)
            return "ok", c.fetchall()[0][0]
        except Exception as exc:
            return "error", str(exc)
        finally:
            uconn.close()

    yield {"conn": conn, "schema": schema, "call": call}
    try:
        native.kill("^KG", "tout", 0, TS, "r289a")
        if not native.isDefined("^KG", "tout", 0):
            native.kill("^KG", "tout")
        _clear_rows(conn, schema)
        _drop_principals()
    finally:
        conn.close()


def _refused(answer, user: str, privilege: str, table: str) -> None:
    kind, text = answer
    assert kind == "error", answer
    assert "SQLCODE=-99" in text and f"User {user} lacks {privilege} on {table}" in text, text


def test_a_select_only_user_cannot_backfill_and_nothing_is_written(world):
    schema = world["schema"]
    _refused(world["call"](SEL_USER, f"SELECT {schema}.backfill_temporal_rows()"), SEL_USER, "INSERT",
             f"{schema}.nodes")
    assert _rows(world["conn"], schema) == {"nodes": [], "edges": []}


def test_a_select_only_user_is_told_the_rebuild_was_refused(world):
    schema = world["schema"]
    kind, text = world["call"](SEL_USER, f"SELECT {schema}.rebuild_edge_indices('{schema}')")
    assert kind == "error", text  # it answered ("ok", 0): no table rebuilt, nothing said
    assert "SQLCODE=-99" in text and "%BUILD_INDEX" in text and f"{schema}.nodes" in text, text


def test_a_user_who_may_insert_backfills_the_one_triple(world):
    schema = world["schema"]
    kind, value = world["call"](WRT_USER, f"SELECT {schema}.backfill_temporal_rows()")
    assert (kind, value) == ("ok", 1)
    assert _rows(world["conn"], schema) == {
        "nodes": [("r289a", ""), ("r289b", "")],
        "edges": [("r289a", "r289p", "r289b", "", {"temporal": True})],
    }


def test_a_user_holding_build_index_rebuilds_the_four_tables(world):
    schema = world["schema"]
    assert world["call"](WRT_USER, f"SELECT {schema}.rebuild_edge_indices('{schema}')") == ("ok", 4)
