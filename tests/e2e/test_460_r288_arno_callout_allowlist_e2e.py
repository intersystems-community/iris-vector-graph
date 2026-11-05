"""4.6.0 R288 [SECURITY], live: the `ivg_arno_*` SQL functions open only the resolved Arno library and call only the
functions IVG calls.

Measured 2026-11-04 on 2026.3.0AI before the fix: `ivg_arno_zf_call_2s('/tmp/libarno_callout.so',
'kg_triangle_count_json', ...)` loaded a library the resolver had not chosen and answered from it, and
`ivg_arno_zf_probe(<library>, 'capabilities')` looked up a function no wrapper calls. Each wrapper now refuses any
other path or function with SQLCODE -99 naming it (Graph.KG.ArnoAccel.RequireCallout).

The caller is a user holding only what a wrapper needs: SQL connect, the callout resource `%System_CallOut:U`,
EXECUTE on the wrappers and SELECT on `rdf_edges` (the R280 check those reaching the graph make first), so a refusal
is the new check's. The namespace is `IVG_NAMESPACE` (an IVG460*/IVG262* scratch namespace with IVG set up); the
user and role are instance-wide, named `<IVG460_PRIV_PREFIX>_USER` / `_ROLE` (default `<namespace>_R288`), and
dropped at the end. Skipped where the resolved library file is not on the server. `IVG_R288_OTHER_LIB` names the
other library (default `/tmp/libarno_callout.so`, the test container's pinned build at a path the resolver does not
use); it need not exist for the refusal, which comes before any load.

The graph handed to the JSON kernel, worked by hand: a-b, b-c, c-a, c-d, undirected. abc is the one triangle. a and b
have two neighbours, joined: clustering 1.0. c has three (a, b, d), one pair of three joined: 1/3. d has one: 0.0.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from tests.e2e import ns460alt as alt

NS = alt.NS
PREFIX = os.environ.get("IVG460_PRIV_PREFIX", f"{NS}_R288")
ROLE, USER = f"{PREFIX}_ROLE", f"{PREFIX}_USER"
PASSWORD = "Priv288x"
OTHER_LIB = os.environ.get("IVG_R288_OTHER_LIB", "/tmp/libarno_callout.so")
CONNECT = "%Service_SQL:U,%DB_%DEFAULT:RW,%DB_IRISLIB:R,%DB_IRISSYS:R,%System_CallOut:U"
GRAPH = json.dumps({"nodes": ["a", "b", "c", "d"],
                    "edges": [{"s": "a", "d": "b"}, {"s": "b", "d": "c"}, {"s": "c", "d": "a"}, {"s": "c", "d": "d"}]})
TRIANGLES = {"a": (1, 1.0), "b": (1, 1.0), "c": (1, 1 / 3), "d": (0, 0.0)}

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]


def _sys(lines: list) -> str:
    script = "\n".join(lines + ["halt"]) + "\n"
    out = subprocess.run(["docker", "exec", "-i", alt.CONTAINER, "iris", "session", "IRIS", "-U", "%SYS"],
                         input=script, capture_output=True, text=True, timeout=120, check=True)
    return out.stdout


def _drop_principals() -> None:
    _sys([f'if ##class(Security.Users).Exists("{USER}") do ##class(Security.Users).Delete("{USER}")',
          f'if ##class(Security.Roles).Exists("{ROLE}") do ##class(Security.Roles).Delete("{ROLE}")'])


@pytest.fixture(scope="module")
def world():
    import iris

    from iris_vector_graph.stores import arno_bridge

    conn = alt.connect()
    native = iris.createIRIS(conn)
    schema = native.get("^IVG.Deploy", "schema") or "Graph_KG"
    lib = str(native.classMethodValue("Graph.KG.ArnoAccel", "LibPath"))
    if not int(native.classMethodValue("Graph.KG.ArnoAccel", "LibExists", lib)):
        conn.close()
        pytest.skip(f"no Arno library at {lib} on the server")
    arno_bridge.clear_probe_cache()
    arno_bridge._ensure_zf_call_function(conn, schema=schema)
    _drop_principals()
    out = _sys([f'set sc=##class(Security.Roles).Create("{ROLE}","4.6.0 R288","{CONNECT}") write "SC=",sc,!',
                f'set sc=##class(Security.Users).Create("{USER}","{ROLE}","{PASSWORD}","4.6.0 R288","{NS}") '
                'write "SC=",sc,!'])
    assert out.count("SC=1") == 2, out
    cur = conn.cursor()
    for fn in ("ivg_arno_zf_call_2s", "ivg_arno_zf_probe", "ivg_arno_zf_triangle_json"):
        cur.execute(f"GRANT EXECUTE ON {schema}.{fn} TO {ROLE}")
    cur.execute(f"GRANT SELECT ON {schema}.rdf_edges TO {ROLE}")
    cur.close()
    user = iris.connect("localhost", alt.PORT, NS, USER, PASSWORD)

    def call(sql: str, args: list):
        c = user.cursor()
        try:
            c.execute(f"SELECT {schema}.{sql}", args)
            return "ok", c.fetchall()[0][0]
        except Exception as exc:
            return "error", str(exc)
        finally:
            c.close()

    yield {"lib": lib, "call": call, "schema": schema}
    try:
        user.close()
        _drop_principals()
    finally:
        conn.close()


def _refused(answer, named: str) -> None:
    kind, text = answer
    assert kind == "error", answer
    assert "SQLCODE=-99" in text and named in text and "refused" in text, text


def test_another_library_path_is_refused(world):
    call = world["call"]
    _refused(call("ivg_arno_zf_call_2s(?, ?, ?, ?)", [OTHER_LIB, "kg_triangle_count_json", GRAPH, "10"]), OTHER_LIB)
    _refused(call("ivg_arno_zf_triangle_json(?, ?, ?)", [OTHER_LIB, GRAPH, 10]), OTHER_LIB)
    _refused(call("ivg_arno_zf_probe(?, ?)", [OTHER_LIB, "version"]), OTHER_LIB)


def test_another_function_is_refused(world):
    call, lib = world["call"], world["lib"]
    _refused(call("ivg_arno_zf_probe(?, ?)", [lib, "capabilities"]), "capabilities")
    _refused(call("ivg_arno_zf_call_2s(?, ?, ?, ?)", [lib, "system", "x", "y"]), "system")
    _refused(call("ivg_arno_zf_call_2s(?, ?, ?, ?)", [lib, "init", "", ""]), "init")


def _triangles(answer) -> dict:
    kind, value = answer
    assert kind == "ok", answer
    return {r["id"]: (r["triangles"], r["lcc"]) for r in json.loads(value)}


def test_the_resolved_library_still_answers_the_hand_worked_triangles(world):
    call, lib = world["call"], world["lib"]
    for answer in (call("ivg_arno_zf_triangle_json(?, ?, ?)", [lib, GRAPH, 10]),
                   call("ivg_arno_zf_call_2s(?, ?, ?, ?)", [lib, "kg_triangle_count_json", GRAPH, "10"])):
        got = _triangles(answer)
        assert set(got) == set(TRIANGLES)
        for nid, (t, lcc) in TRIANGLES.items():
            assert got[nid][0] == t and got[nid][1] == pytest.approx(lcc), (nid, got)
    kind, fid = call("ivg_arno_zf_probe(?, ?)", [lib, "version"])
    assert kind == "ok" and int(fid) > 0, fid


def test_the_engine_path_still_answers(world):
    from iris_vector_graph.stores import arno_bridge

    conn = alt.connect()
    try:
        arno_bridge.clear_probe_cache()
        got = {r["id"]: (r["triangles"], r["lcc"])
               for r in json.loads(arno_bridge.arno_call(conn, "kg_triangle_count_json", GRAPH, 10,
                                                         schema=world["schema"]))}
        assert got["c"][0] == 1 and got["c"][1] == pytest.approx(1 / 3) and got["d"] == (0, 0.0)
    finally:
        conn.close()
