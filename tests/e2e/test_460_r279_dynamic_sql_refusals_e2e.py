"""4.6.0 R279: a function reading rows through dynamic SQL raises the privilege refusal, naming the table.

`CY_PROPS_MAP`, `CY_VLP_CHAIN` and `CY_VLP_OPT` read with `%SQL.Statement.%ExecDirect`, which IRIS checks for the
caller, but a refused read comes back as a result whose `%Next()` answers 0 with `%SQLCODE` -99. They read on as if
there were no row: `properties(n)` answered `{}`, a path chain `[]`, a label filter "no path" (`[{}]`), to a user who
may not read the table. Each now raises the refusal (SQLCODE -99), naming the table (Graph.KG.SqlPrivilege
.RaiseIfRefused); a user holding SELECT gets the answer worked out by hand below.

The namespace is `IVG_NAMESPACE` (an IVG460*/IVG262* scratch namespace with IVG set up). Users and roles are named
`<IVG460_PRIV_PREFIX>_EXEC_*` / `_SEL_*` (default `<namespace>_R279`) and dropped at the end, as are the nodes.
The graph, in the default graph: r279a -knows-> r279b; r279a has the label R279 and the name "Ada".
"""

from __future__ import annotations

import json
import os

import pytest

from tests.e2e import ns460alt as alt

NS = alt.NS
PREFIX = os.environ.get("IVG460_PRIV_PREFIX", f"{NS}_R279")
PASSWORD = "Priv279x"
EXEC_ROLE, EXEC_USER = f"{PREFIX}_EXEC_ROLE", f"{PREFIX}_EXEC_USER"
SEL_ROLE, SEL_USER = f"{PREFIX}_SEL_ROLE", f"{PREFIX}_SEL_USER"
CONNECT = "%Service_SQL:U,%DB_%DEFAULT:RW,%DB_IRISLIB:R,%DB_IRISSYS:R"
FUNCTIONS = ("CY_PROPS_MAP", "CY_VLP_CHAIN", "CY_VLP_OPT", "CY_JSON_NUM")
TABLES = ("nodes", "rdf_edges", "rdf_props", "rdf_labels")

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]


def _sys(lines: list) -> str:
    """Run ObjectScript lines in %SYS, which only this file's role and user setup needs, over TCP
    (tests/helpers/iris_lines.py; 4.7.0: no `iris session`)."""
    from tests.helpers.iris_lines import session

    return session(lines, "%SYS", allow_sys=True, timeout=120)


def _drop_principals() -> None:
    _sys([f'if ##class(Security.Users).Exists("{n}") do ##class(Security.Users).Delete("{n}")'
          for n in (EXEC_USER, SEL_USER)]
         + [f'if ##class(Security.Roles).Exists("{n}") do ##class(Security.Roles).Delete("{n}")'
            for n in (EXEC_ROLE, SEL_ROLE)])


@pytest.fixture(scope="module")
def world():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = alt.connect()
    schema = iris.createIRIS(conn).get("^IVG.Deploy", "schema") or "Graph_KG"
    engine = IRISGraphEngine(conn, embedding_dimension=alt.DIM, namespace=NS, schema_prefix=schema)
    engine.create_node("r279a", labels=["R279"], properties={"name": "Ada"})
    engine.create_node("r279b", labels=["Other"])
    engine.create_edge("r279a", "knows", "r279b")
    cur = conn.cursor()
    cur.execute(f"SELECT edge_id FROM {schema}.rdf_edges WHERE s = 'r279a' AND p = 'knows' AND o_id = 'r279b'")
    edge_id = str(cur.fetchall()[0][0])
    _drop_principals()
    out = _sys([f'set sc=##class(Security.Roles).Create("{r}","4.6.0 R279","{CONNECT}") write "SC=",sc,!'
                for r in (EXEC_ROLE, SEL_ROLE)]
               + [f'set sc=##class(Security.Users).Create("{u}","{r}","{PASSWORD}","4.6.0 R279","{NS}") write "SC=",sc,!'
                  for u, r in ((EXEC_USER, EXEC_ROLE), (SEL_USER, SEL_ROLE))])
    assert out.count("SC=1") == 4, out
    for fn in FUNCTIONS:
        cur.execute(f"GRANT EXECUTE ON {schema}.{fn} TO {EXEC_ROLE}, {SEL_ROLE}")
    for table in TABLES:
        cur.execute(f"GRANT SELECT ON {schema}.{table} TO {SEL_ROLE}")
    cur.close()
    paths = json.dumps([{"t": "r279a", "l": 0}, {"t": "r279b", "l": 1}])
    calls = {
        "CY_PROPS_MAP": (f"SELECT {schema}.CY_PROPS_MAP('r279a', '{schema}.rdf_props', '')", "rdf_props"),
        "CY_VLP_CHAIN": (f"SELECT {schema}.CY_VLP_CHAIN('[\"{edge_id}\"]', 'out', '{schema}.rdf_edges')", "rdf_edges"),
        "CY_VLP_OPT": (f"SELECT {schema}.CY_VLP_OPT('{paths}', 0, '', '[\"R279\"]', '{schema}.rdf_labels')",
                       "rdf_labels"),
    }
    answers = {}
    for who in (EXEC_USER, SEL_USER):
        uconn = iris.connect("localhost", alt.PORT, NS, who, PASSWORD)
        try:
            for fn, (sql, _t) in calls.items():
                c = uconn.cursor()
                try:
                    c.execute(sql)
                    answers[who, fn] = ("ok", json.loads(str(c.fetchall()[0][0])))
                except Exception as exc:
                    answers[who, fn] = ("error", str(exc))
                finally:
                    c.close()
        finally:
            uconn.close()
    yield {"schema": schema, "calls": calls, "answers": answers}
    try:
        _drop_principals()
        for nid in ("r279a", "r279b"):
            engine.delete_node(nid)
    finally:
        conn.close()


@pytest.mark.parametrize("fn", ["CY_PROPS_MAP", "CY_VLP_CHAIN", "CY_VLP_OPT"])
def test_the_refusal_is_raised_naming_the_table(world, fn):
    kind, text = world["answers"][EXEC_USER, fn]
    assert kind == "error", (fn, text)
    table = f"{world['schema']}.{world['calls'][fn][1]}"
    assert "SQLCODE=-99" in text and f"lacks SELECT on {table}" in text, text


def _ok(world, fn):
    kind, value = world["answers"][SEL_USER, fn]
    assert kind == "ok", (fn, value)
    return value


def test_a_select_holder_gets_the_properties(world):
    # create_node writes the node's id as the property "id" when it is not given
    assert _ok(world, "CY_PROPS_MAP") == {"name": "Ada", "id": "r279a"}


def test_a_select_holder_gets_the_chain(world):
    chain = _ok(world, "CY_VLP_CHAIN")
    assert len(chain) == 1
    c = chain[0]
    assert (c["s"], c["t"], c["l"]) == ("r279a", "r279b", 1)
    assert json.loads(c["n"]) == ["r279b"] and json.loads(c["y"]) == ["knows"]


def test_a_select_holder_gets_the_label_filter(world):
    # of the two path ends, r279a holds the label R279 and r279b does not
    assert _ok(world, "CY_VLP_OPT") == [{"t": "r279a", "l": 0}]
