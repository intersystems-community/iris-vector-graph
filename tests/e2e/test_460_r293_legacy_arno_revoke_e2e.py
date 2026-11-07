"""4.6.0 R293 [SECURITY], live: setup closes the R288 hole in the Arno SQL functions a 4.5.1 namespace kept.

The namespace is built by v4.5.1's own code (`build_release_namespace.build`), then 4.5.1's own Arno bridge makes
its `ivg_arno_zf_*` functions (`run_arno_wrappers`; 4.5.x made them on first Arno use). 4.5.1's DDL names no
schema, so they land in the instance's default SQL schema D: SQLUser on a default instance (fixes46ce, Community
2026.1), Graph_KG on ivg-iris-enterprise. The fixture runs that text as it is, and the same text qualified by
SQLUser when D is not SQLUser (where a default instance puts them, the legacy manifest's case) and by R293App (a
schema neither IVG's nor SQLUser, as on an instance configured with it as default). Each copy opens any library and
calls any function (the 4.5.1 bodies hold no check), for a caller with EXECUTE and `%System_CallOut`.

Then, as the users and roles below, each instance-wide and dropped at the end:

- FIXES46_R293_GRANTOR (%All) grants EXECUTE on each copy's `ivg_arno_zf_probe` and `ivg_arno_zf_call_2s` to the
  role FIXES46_R293_ROLE (SQL connect and the callout resource), whose only user is FIXES46_R293_USER; the grantor
  is another user than setup's, so a REVOKE without `AS` would remove nothing;
- and EXECUTE on each `ivg_arno_zf_probe` to FIXES46_R293_ADMIN, a `%All` holder, which keeps it.

By hand: the dry run lists, for each copy outside the IVG Schema (Graph_KG), the role's two grants (four on a
default instance: SQLUser, R293App), revoked false, and the IVG Schema copies it would make checked (enterprise: the
16 4.5.1 bodies in Graph_KG; Community: none there yet, but the probe and call_2s, whose SQLUser grants are copied);
it changes no grant and no body. Setup then revokes exactly those grants (`legacy_arno_revoked`), leaves the
admin's, keeps every function, and makes those IVG Schema functions the checked text (`arno_wrappers_replaced`),
grants kept. The user, refused SQLUser's and R293App's probe (SQLCODE -99, not privileged), may still run Graph_KG's,
which refuses a library other than the resolved one (R288). A second setup revokes and replaces nothing.

Environment: IVG262_R293_NS names the namespace (default IVG262LEGH: dropped and rebuilt, IVG262* only); container
and port from IVG_TEST_CONTAINER / IVG_PORT (tests/e2e/ns262.py).
"""

from __future__ import annotations

import os
import subprocess

import pytest

from tests.e2e import ns262

pytestmark = [pytest.mark.e2e, ns262.requires_container]

NS = os.environ.get("IVG262_R293_NS", "")
if not NS:  # the E2E gate names one per run (tests/e2e/gate.py GROUPS); never a fixed default
    pytest.skip("set IVG262_R293_NS to a scratch namespace of this run", allow_module_level=True)
ROLE, USER = "FIXES46_R293_ROLE", "FIXES46_R293_USER"
GRANTOR, ADMIN = "FIXES46_R293_GRANTOR", "FIXES46_R293_ADMIN"
PASSWORD = "Fixes46r293"
CONNECT = "%Service_SQL:U,%DB_%DEFAULT:RW,%DB_IRISLIB:R,%DB_IRISSYS:R,%System_CallOut:U"
APP_SCHEMA = "R293App"
GRANTED = ("ivg_arno_zf_probe", "ivg_arno_zf_call_2s")
OTHER_LIB = "/tmp/r293-not-arno.so"
DEFAULT_LIB = "/usr/irissys/mgr/libarno_callout.so"


def _sys(lines: list) -> str:
    script = "\n".join(lines + ["halt"]) + "\n"
    out = subprocess.run(["docker", "exec", "-i", ns262.CONTAINER, "iris", "session", "IRIS", "-U", "%SYS"],
                         input=script, capture_output=True, text=True, timeout=120, check=True)
    return out.stdout


def _drop_principals() -> None:
    _sys([f'if ##class(Security.Users).Exists("{u}") do ##class(Security.Users).Delete("{u}")'
          for u in (USER, GRANTOR, ADMIN)]
         + [f'if ##class(Security.Roles).Exists("{ROLE}") do ##class(Security.Roles).Delete("{ROLE}")'])


def _grants(conn, routine: str) -> set:
    """(subject, via) of every EXECUTE on `routine`, through the code setup itself reads them with."""
    from iris_vector_graph._engine import legacy

    return {(g["subject"], g["via"]) for g in legacy.grants_on(conn, routine)
            if g["privilege"].upper() == "EXECUTE"}


def _bodies(conn) -> dict:
    return {(str(s), str(n)): str(b) for s, n, b in ns262.rows(
        conn, "SELECT ROUTINE_SCHEMA, ROUTINE_NAME, ROUTINE_DEFINITION FROM INFORMATION_SCHEMA.ROUTINES "
              "WHERE ROUTINE_NAME %STARTSWITH 'ivg_arno_'")}


def _call(routine: str, lib: str, fn: str):
    """`routine(lib, fn)` as USER: ("ok", value) or ("error", message)."""
    conn = ns262.connect_as(NS, USER, PASSWORD)
    try:
        cur = conn.cursor()
        try:
            cur.execute(f"SELECT {routine}(?, ?)", [lib, fn])
            return "ok", cur.fetchall()[0][0]
        except Exception as exc:  # the answer under test is the refusal
            return "error", str(exc)
    finally:
        conn.close()


def _not_privileged(answer) -> bool:
    kind, text = answer
    return kind == "error" and "SQLCODE" in text and "-99" in text and "privilege" in text.lower()


@pytest.fixture(scope="module")
def world():
    from build_release_namespace import build, run_arno_wrappers

    from iris_vector_graph import IRISGraphEngine

    build("v4.5.1", NS)
    conn = ns262.connect(NS)
    default = str(ns262.native(conn).classMethodValue("%SYSTEM.SQL.Schema", "Default"))
    placements = [""] + (["SQLUser"] if default.upper() != "SQLUSER" else []) + [APP_SCHEMA]
    made = run_arno_wrappers("v4.5.1", NS, placements)
    legacy_schemas = sorted({default if p == "" else p for p in placements} - {"Graph_KG"})
    _drop_principals()
    out = _sys([f'set sc=##class(Security.Roles).Create("{ROLE}","4.6.0 R293","{CONNECT}") write "SC=",sc,!',
                f'set sc=##class(Security.Users).Create("{USER}","{ROLE}","{PASSWORD}","R293","{NS}") write "SC=",sc,!',
                f'set sc=##class(Security.Users).Create("{GRANTOR}","%All","{PASSWORD}","R293","{NS}") '
                'write "SC=",sc,!',
                f'set sc=##class(Security.Users).Create("{ADMIN}","%All","{PASSWORD}","R293","{NS}") write "SC=",sc,!'])
    assert out.count("SC=1") == 4, out
    schemas = sorted({default if p == "" else p for p in placements})
    g = ns262.connect_as(NS, GRANTOR, PASSWORD)
    try:
        cur = g.cursor()
        for schema in schemas:
            for fn in GRANTED:
                cur.execute(f"GRANT EXECUTE ON {schema}.{fn} TO {ROLE}")
            cur.execute(f"GRANT EXECUTE ON {schema}.ivg_arno_zf_probe TO {ADMIN}")
    finally:
        g.close()
    before = {"bodies": _bodies(conn),
              "grants": {f"{s}.{fn}": _grants(conn, f"{s}.{fn}") for s in schemas for fn in GRANTED},
              "calls": {s: _call(f"{s}.ivg_arno_zf_probe", DEFAULT_LIB, "capabilities") for s in schemas},
              "namespace": ns262.namespace_snapshot(conn)}
    engine = IRISGraphEngine(conn, embedding_dimension=8, namespace=NS, native_cypher=False)
    dry = engine.initialize_schema(dry_run=True)
    after_dry = {"bodies": _bodies(conn),
                 "grants": {f"{s}.{fn}": _grants(conn, f"{s}.{fn}") for s in schemas for fn in GRANTED},
                 "namespace": ns262.namespace_snapshot(conn)}
    status = engine.initialize_schema()
    lib = str(ns262.native(conn).classMethodValue("Graph.KG.ArnoAccel", "LibPath"))
    after = {"bodies": _bodies(conn),
             "grants": {f"{s}.{fn}": _grants(conn, f"{s}.{fn}") for s in schemas + ["Graph_KG"] for fn in GRANTED},
             "calls": {s: _call(f"{s}.ivg_arno_zf_probe", lib, "version") for s in schemas + ["Graph_KG"]},
             "other_lib": _call("Graph_KG.ivg_arno_zf_probe", OTHER_LIB, "version")}
    again = engine.initialize_schema()
    yield {"default": default, "made": made, "legacy_schemas": legacy_schemas, "schemas": schemas, "before": before,
           "dry": dry, "after_dry": after_dry, "status": status, "after": after, "again": again, "conn": conn}
    try:
        _drop_principals()
    finally:
        conn.close()


def _checked(body: str) -> bool:
    """The R288 check before the first $ZF: read here by hand, not through the code under test."""
    lower = body.lower()
    return "requirecallout(" in lower and lower.index("requirecallout(") < lower.index("$zf(")


def test_the_fixture_holds_451_bodies_where_the_instance_put_them(world):
    routines = world["made"]["routines"]
    for schema in world["schemas"]:
        assert len(routines.get(schema, [])) == 16, (schema, routines)
        assert not _checked(world["before"]["bodies"][(schema, "ivg_arno_zf_probe")])
    # the hole is real: before setup the role's user runs each copy (no privilege refusal)
    for schema, answer in world["before"]["calls"].items():
        assert not _not_privileged(answer), (schema, answer)
        assert (ROLE, "Direct") in world["before"]["grants"][f"{schema}.ivg_arno_zf_probe"]


def _expected_revokes(world) -> set:
    return {(f"{s}.{fn}", ROLE) for s in world["legacy_schemas"] for fn in GRANTED}


def test_the_dry_run_lists_the_revokes_and_replacements_and_changes_nothing(world):
    dry = world["dry"]
    assert {(r["routine"], r["subject"]) for r in dry["legacy_arno_revoked"]} == _expected_revokes(world)
    assert not any(r["revoked"] for r in dry["legacy_arno_revoked"])
    assert all(r["granted_by"] == [GRANTOR] for r in dry["legacy_arno_revoked"])
    replaced = {r["routine"] for r in dry["arno_wrappers_replaced"]}
    if world["default"] == "Graph_KG":
        assert len(replaced) == 16 and "Graph_KG.ivg_arno_zf_probe" in replaced
    else:
        assert replaced == {f"Graph_KG.{fn}" for fn in GRANTED}
    assert not any(r["replaced"] for r in dry["arno_wrappers_replaced"])
    assert world["after_dry"] == {k: world["before"][k] for k in ("bodies", "grants", "namespace")}


def test_setup_revokes_every_grant_outside_the_ivg_schema_but_a_percent_all_holders(world):
    status, after = world["status"], world["after"]
    for schema in world["legacy_schemas"]:
        assert _not_privileged(after["calls"][schema]), (schema, after["calls"][schema])
        for fn in GRANTED:
            assert (ROLE, "Direct") not in after["grants"][f"{schema}.{fn}"], (schema, fn)
        assert (ADMIN, "Direct") in after["grants"][f"{schema}.ivg_arno_zf_probe"]
        # the functions stay, 4.5.1's text unchanged
        assert after["bodies"][(schema, "ivg_arno_zf_probe")] == world["before"]["bodies"][(schema,
                                                                                          "ivg_arno_zf_probe")]
    assert {(r["routine"], r["subject"]) for r in status["legacy_arno_revoked"]} == _expected_revokes(world)
    assert all(r["revoked"] for r in status["legacy_arno_revoked"])


def test_setup_makes_the_ivg_schema_copies_checked_and_keeps_their_grants(world):
    status, after = world["status"], world["after"]
    kind, text = after["other_lib"]
    assert kind == "error" and "-99" in text and OTHER_LIB in text and "refused" in text, after["other_lib"]
    assert not _not_privileged(after["calls"]["Graph_KG"]), after["calls"]["Graph_KG"]
    for (schema, name), body in after["bodies"].items():
        if schema == "Graph_KG" and "$zf(" in body.lower():
            assert _checked(body), name
    for fn in GRANTED:
        assert (ROLE, "Direct") in after["grants"][f"Graph_KG.{fn}"]  # kept (enterprise) or copied (Community)
    assert {r["routine"] for r in status["arno_wrappers_replaced"]} == {
        r["routine"] for r in world["dry"]["arno_wrappers_replaced"]}
    assert all(r["replaced"] for r in status["arno_wrappers_replaced"])


def test_a_second_setup_revokes_and_replaces_nothing(world):
    assert world["again"]["legacy_arno_revoked"] == []
    assert world["again"]["arno_wrappers_replaced"] == []
