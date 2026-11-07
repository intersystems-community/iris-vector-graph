"""4.6.0 R293 [SECURITY]: setup closes the R288 hole in the Arno SQL functions 4.5.x left behind.

4.5.x created its `ivg_arno_*` functions with unqualified DDL on first Arno use, so they sit in whatever the
instance's default SQL schema was: SQLUser on a default instance (the legacy manifest), Graph_KG on one configured so
(ivg-iris-enterprise), any schema elsewhere. Each opens any library and calls any function in it for a caller holding
EXECUTE and the callout resource. Tom decided (2026-11-04) that every `initialize_schema()` revokes EXECUTE on them
from every grantee except `%All` holders and reports it; the functions stay. With the lead (fixes46): every routine of
those exact names in INFORMATION_SCHEMA.ROUTINES, in any schema, is found:

- in the IVG Schema, a copy whose body calls `$ZF` before `Graph.KG.ArnoAccel.RequireCallout` is replaced by the
  checked text the bridge creates (`arno_wrappers_replaced`), keeping its grants; so is a missing replacement of a
  SQLUser function whose grants the copy carries over (4.5.x made them lazily; the GRANT would fail on a function
  that is not there);
- in any other schema, each direct EXECUTE grant is revoked `AS` its grantor (without AS a REVOKE removes only the
  current user's own grants and answers nothing, R147), except a `%All` holder's (`legacy_arno_revoked`, each record
  naming its routine with its schema). A grant held through a schema grant cannot be revoked per object: a warning.

The dry run reports both and writes nothing. The live checks are tests/e2e/test_460_r293_legacy_arno_revoke_e2e.py.
"""

from __future__ import annotations

import pytest

from iris_vector_graph._engine import legacy
from iris_vector_graph.stores import arno_bridge


@pytest.fixture(autouse=True)
def _grants_read_per_object(monkeypatch):
    """The grant stand-ins below answer per object (`grants_on`); setup reads every object at once (fixes74:
    `grants_by_object`, one statement), so here that read is each object's stand-in answer."""
    monkeypatch.setattr(legacy, "grants_by_object", lambda conn, names: {n: legacy.grants_on(conn, n) for n in names})

UNCHECKED = "\r\n\tset dllid = $ZF(-4,1,libpath)\r\n\tquit:dllid=0 0\r\n\tset fnid = $ZF(-4,3,dllid,fname)\r\n\tquit fnid\r\n"
CHECKED = "\n    do ##class(Graph.KG.ArnoAccel).RequireCallout(libpath, fname)\n    set dllid = $ZF(-4,1,libpath)\n"


class _Cur:
    def __init__(self, conn):
        self.conn, self._rows = conn, []

    def execute(self, sql, params=None):
        self.conn.log.append(sql)
        if self.conn.fail and self.conn.fail in sql:
            raise RuntimeError(f"refused: {sql}")
        if "INFORMATION_SCHEMA.ROUTINES" in sql and "ROUTINE_DEFINITION" in sql:
            self._rows = list(self.conn.routines)
        elif "INFORMATION_SCHEMA.ROUTINES" in sql:
            schema = (params or [None])[0]
            self._rows = [(n,) for s, n, _b in self.conn.routines if s == schema]
        else:
            self._rows = []
        if sql.startswith("REVOKE") and not self.conn.revoke_noop:
            self.conn.revoked.append(sql)

    def fetchall(self):
        return list(self._rows)

    def close(self):
        pass


class _Conn:
    def __init__(self, routines, fail=None, revoke_noop=False):
        self.routines, self.fail, self.revoke_noop = routines, fail, revoke_noop
        self.log: list = []
        self.revoked: list = []

    def cursor(self):
        return _Cur(self)

    def commit(self):
        pass


class _Native:
    """The Native API's get/set over a dict: the routine record (^IVG.Deploy("routines"))."""

    def __init__(self):
        self.g: dict = {}

    def get(self, *key):
        return self.g.get(key)

    def set(self, value, *key):
        self.g[key] = value


def _grants(rows, conn=None):
    """grants_on stand-in: `rows` maps 'Schema.name' to SQLAllPrivileges records; a REVOKE the connection ran (AS
    the row's grantor) removes that Direct row."""

    def grants_on(_iris, sql_name):
        out = []
        for g in rows.get(sql_name, []):
            g = {"subject_type": "ROLE", "privilege": "EXECUTE", "with_grant": False, "granted_by": ["_SYSTEM"], **g}
            subject, grantor = (s.replace('"', '""') for s in (g["subject"], g["granted_by"][0]))
            if conn is not None and g["via"] == "Direct" and any(
                    f'ON {sql_name} FROM "{subject}"' in r and f'AS "{grantor}"' in r for r in conn.revoked):
                continue
            out.append(g)
        return out

    return grants_on


def test_the_names_are_the_manifests_arno_functions_and_no_pattern():
    names = legacy.ARNO_WRAPPERS
    assert "ivg_arno_zf_call_2s" in names and "ivg_arno_zf_probe" in names and "ivg_arno_build_adj" in names
    assert len(names) == 19 and all(n.startswith("ivg_arno_") for n in names)
    manifest = {e["sql_name"].split(".", 1)[1] for e in legacy.LEGACY_MANIFEST
                if e["sql_name"].startswith("SQLUser.ivg_arno_")}
    assert names == manifest


def test_the_bridge_text_is_checked_and_the_451_text_is_not():
    ddls = arno_bridge.wrapper_ddls("Graph_KG")
    assert {n.lower() for n in ddls} == {n.lower() for n in legacy.ARNO_WRAPPERS}
    for name, ddl in ddls.items():
        assert not legacy.unchecked_arno_body(ddl[ddl.index("{"):]), name
    assert legacy.unchecked_arno_body(UNCHECKED)
    assert not legacy.unchecked_arno_body(CHECKED)
    assert not legacy.unchecked_arno_body("\n    quit $Get(^||arnonodeschunks(idx))\n")  # get_node_chunk: no $ZF
    # a check after the first $ZF does not count
    assert legacy.unchecked_arno_body("set d = $zf(-4,1,p)\n do ##class(Graph.KG.ArnoAccel).RequireCallout(p, f)")


def test_copies_are_found_in_any_schema_by_exact_name():
    conn = _Conn([("SQLUser", "ivg_arno_zf_probe", UNCHECKED), ("Graph_KG", "ivg_arno_zf_call_2s", UNCHECKED),
                  ("App", "IVG_ARNO_ZF_PROBE", UNCHECKED), ("App", "ivg_arno_mine", UNCHECKED),
                  ("SQLUser", "CY_CMP", "quit 1")])
    got = sorted((c["schema"], c["name"]) for c in legacy.arno_copies(conn))
    assert got == [("App", "IVG_ARNO_ZF_PROBE"), ("Graph_KG", "ivg_arno_zf_call_2s"), ("SQLUser", "ivg_arno_zf_probe")]


def _copies(*rows):
    return [{"schema": s, "name": n, "body": b} for s, n, b in rows]


def test_an_unchecked_copy_in_the_ivg_schema_is_replaced_by_the_checked_text():
    conn = _Conn([])
    native = _Native()
    copies = _copies(("Graph_KG", "ivg_arno_zf_probe", UNCHECKED), ("Graph_KG", "ivg_arno_zf_call_2s", CHECKED),
                     ("SQLUser", "ivg_arno_zf_scc_run", UNCHECKED))
    out = legacy.replace_arno_wrappers(conn, native, "Graph_KG", copies, [], dry_run=False)
    assert out == [{"routine": "Graph_KG.ivg_arno_zf_probe", "because": "its body calls $ZF unchecked",
                    "replaced": True}]
    creates = [s for s in conn.log if s.startswith("CREATE")]
    assert creates == [arno_bridge.wrapper_ddls("Graph_KG")["IVG_ARNO_ZF_PROBE"]]
    assert "IVG_ARNO_ZF_PROBE" in native.get("^IVG.Deploy", "routines")  # recorded, so the bridge does not redo it


def test_a_recorded_fingerprint_does_not_keep_an_unchecked_body():
    """The record says what 4.6.0 created; a body read back unchecked is replaced whatever it says."""
    from iris_vector_graph.routine_record import RoutineRecord

    conn, native = _Conn([("Graph_KG", "ivg_arno_zf_probe", UNCHECKED)]), _Native()
    rec = RoutineRecord(native, conn.cursor(), "Graph_KG")
    rec.created(arno_bridge.wrapper_ddls("Graph_KG")["IVG_ARNO_ZF_PROBE"])
    rec.save()
    out = legacy.replace_arno_wrappers(conn, native, "Graph_KG", _copies(("Graph_KG", "ivg_arno_zf_probe", UNCHECKED)),
                                       [], dry_run=False)
    assert [r["replaced"] for r in out] == [True]
    assert [s for s in conn.log if s.startswith("CREATE")]


def test_a_missing_replacement_of_a_granted_sqluser_function_is_created():
    conn, native = _Conn([]), _Native()
    items = [{"sql_name": "SQLUser.ivg_arno_zf_call_2s", "replacement": "ivg_arno_zf_call_2s",
              "grants": [{"subject": "APP", "privilege": "EXECUTE", "with_grant": False}]},
             {"sql_name": "SQLUser.ivg_arno_zf_probe", "replacement": "ivg_arno_zf_probe", "grants": []},
             {"sql_name": "SQLUser.CY_CMP", "replacement": "CY_CMP",
              "grants": [{"subject": "APP", "privilege": "EXECUTE", "with_grant": False}]}]
    out = legacy.replace_arno_wrappers(conn, native, "RAG", _copies(("SQLUser", "ivg_arno_zf_call_2s", UNCHECKED)),
                                       items, dry_run=False)
    assert out == [{"routine": "RAG.ivg_arno_zf_call_2s", "because": "SQLUser.ivg_arno_zf_call_2s's grants are copied "
                    "to it", "replaced": True}]
    assert [s.split("(")[0] for s in conn.log if s.startswith("CREATE")] == [
        "CREATE OR REPLACE FUNCTION RAG.ivg_arno_zf_call_2s"]


def test_the_replacement_dry_run_creates_nothing():
    conn, native = _Conn([]), _Native()
    out = legacy.replace_arno_wrappers(conn, native, "Graph_KG", _copies(("Graph_KG", "ivg_arno_zf_probe", UNCHECKED)),
                                       [], dry_run=True)
    assert [r["replaced"] for r in out] == [False]
    assert not [s for s in conn.log if s.startswith("CREATE")] and native.g == {}


ROLE_GRANT = {"subject": "APPROLE", "via": "Direct", "granted_by": ["FIXES_GRANTOR"]}
USER_GRANT = {"subject": "app.user", "subject_type": "USER", "via": "Direct"}
ADMIN = [{"subject": "ADMIN", "subject_type": "USER", "via": "Direct"},
         {"subject": "ADMIN", "subject_type": "USER", "via": "SuperUser"}]
ALL_ROLE = [{"subject": "ALLROLE", "via": "Direct"}, {"subject": "ALLROLE", "via": "Role:%All"},
            {"subject": "%All", "via": "Direct"}]


def test_every_direct_grantee_outside_the_ivg_schema_is_revoked_as_its_grantor(monkeypatch):
    conn = _Conn([])
    rows = {"SQLUser.ivg_arno_zf_call_2s": [ROLE_GRANT, USER_GRANT] + ADMIN + ALL_ROLE,
            "App.ivg_arno_zf_probe": [{**ROLE_GRANT, "granted_by": ["A", "B"]}],
            "Graph_KG.ivg_arno_zf_probe": [ROLE_GRANT]}
    monkeypatch.setattr(legacy, "grants_on", _grants(rows, conn))
    copies = _copies(("SQLUser", "ivg_arno_zf_call_2s", UNCHECKED), ("App", "ivg_arno_zf_probe", CHECKED),
                     ("Graph_KG", "ivg_arno_zf_probe", CHECKED))
    out, warnings = legacy.revoke_legacy_arno(conn, object(), "Graph_KG", copies, dry_run=False)
    assert sorted(conn.revoked) == sorted([
        'REVOKE EXECUTE ON SQLUser.ivg_arno_zf_call_2s FROM "APPROLE" CASCADE AS "FIXES_GRANTOR"',
        'REVOKE EXECUTE ON SQLUser.ivg_arno_zf_call_2s FROM "app.user" CASCADE AS "_SYSTEM"',
        'REVOKE EXECUTE ON App.ivg_arno_zf_probe FROM "APPROLE" CASCADE AS "A"',
        'REVOKE EXECUTE ON App.ivg_arno_zf_probe FROM "APPROLE" CASCADE AS "B"',
    ])
    assert sorted((r["routine"], r["subject"], r["revoked"]) for r in out) == [
        ("App.ivg_arno_zf_probe", "APPROLE", True),
        ("SQLUser.ivg_arno_zf_call_2s", "APPROLE", True),
        ("SQLUser.ivg_arno_zf_call_2s", "app.user", True)]
    assert all(r["privilege"] == "EXECUTE" and r["schema"] in ("SQLUser", "App") for r in out)
    assert warnings == []
    assert not [s for s in conn.log if s.startswith(("DROP", "CREATE"))]  # the functions stay


def test_a_second_run_revokes_nothing(monkeypatch):
    conn = _Conn([])
    monkeypatch.setattr(legacy, "grants_on", _grants({"SQLUser.ivg_arno_zf_probe": ADMIN + ALL_ROLE}))
    out, _ = legacy.revoke_legacy_arno(conn, object(), "Graph_KG", _copies(("SQLUser", "ivg_arno_zf_probe", UNCHECKED)),
                                       dry_run=False)
    assert out == [] and conn.revoked == [] and not [s for s in conn.log if s.startswith("REVOKE")]


def test_the_revoke_dry_run_lists_and_revokes_nothing(monkeypatch):
    conn = _Conn([])
    monkeypatch.setattr(legacy, "grants_on", _grants({"SQLUser.ivg_arno_zf_probe": [ROLE_GRANT]}))
    out, _ = legacy.revoke_legacy_arno(conn, object(), "Graph_KG", _copies(("SQLUser", "ivg_arno_zf_probe", UNCHECKED)),
                                       dry_run=True)
    assert [(r["routine"], r["subject"], r["granted_by"], r["revoked"]) for r in out] == [
        ("SQLUser.ivg_arno_zf_probe", "APPROLE", ["FIXES_GRANTOR"], False)]
    assert not [s for s in conn.log if s.startswith("REVOKE")]


def test_a_revoke_that_leaves_the_grant_raises(monkeypatch):
    """REVOKE removing nothing answers no error (SQLCODE 100): the grant is read back, and one still there raises."""
    conn = _Conn([], revoke_noop=True)
    monkeypatch.setattr(legacy, "grants_on", _grants({"SQLUser.ivg_arno_zf_probe": [ROLE_GRANT]}, conn))
    with pytest.raises(RuntimeError, match="APPROLE still holds EXECUTE on SQLUser.ivg_arno_zf_probe"):
        legacy.revoke_legacy_arno(conn, object(), "Graph_KG", _copies(("SQLUser", "ivg_arno_zf_probe", UNCHECKED)),
                                  dry_run=False)


def test_a_refused_revoke_raises(monkeypatch):
    conn = _Conn([], fail="REVOKE")
    monkeypatch.setattr(legacy, "grants_on", _grants({"SQLUser.ivg_arno_zf_probe": [ROLE_GRANT]}, conn))
    with pytest.raises(RuntimeError, match="refused"):
        legacy.revoke_legacy_arno(conn, object(), "Graph_KG", _copies(("SQLUser", "ivg_arno_zf_probe", UNCHECKED)),
                                  dry_run=False)


def test_a_schema_grant_is_a_warning_not_a_revoke(monkeypatch):
    conn = _Conn([])
    monkeypatch.setattr(legacy, "grants_on", _grants(
        {"SQLUser.ivg_arno_zf_probe": [{"subject": "WIDE", "via": "Schema Privilege"}]}))
    out, warnings = legacy.revoke_legacy_arno(conn, object(), "Graph_KG",
                                              _copies(("SQLUser", "ivg_arno_zf_probe", UNCHECKED)), dry_run=False)
    assert out == [] and conn.revoked == []
    assert len(warnings) == 1 and "WIDE" in warnings[0] and "SQLUser.ivg_arno_zf_probe" in warnings[0]
    assert "remove_legacy_objects" in warnings[0]


def test_quotes_in_a_name_are_doubled_and_an_odd_schema_is_refused(monkeypatch):
    conn = _Conn([])
    monkeypatch.setattr(legacy, "grants_on", _grants(
        {"SQLUser.ivg_arno_zf_probe": [{"subject": 'x" FROM y', "via": "Direct", "granted_by": ['g"h']}]}, conn))
    legacy.revoke_legacy_arno(conn, object(), "Graph_KG", _copies(("SQLUser", "ivg_arno_zf_probe", UNCHECKED)),
                              dry_run=False)
    assert conn.revoked == ['REVOKE EXECUTE ON SQLUser.ivg_arno_zf_probe FROM "x"" FROM y" CASCADE AS "g""h"']
    with pytest.raises(ValueError, match="unexpected name"):
        legacy.revoke_legacy_arno(conn, object(), "Graph_KG", _copies(("a; DROP", "ivg_arno_zf_probe", UNCHECKED)),
                                  dry_run=False)


def test_the_grantor_list_is_decoded():
    """SQLAllPrivileges' GRANTED_BY is a $List for a grant another user made (measured 2026-11-04, 2026.3.0AI:
    '\\x13\\x01FIXES46_P_GRANTOR'), plain text for the rows it makes up."""
    assert legacy._grantors("\x13\x01FIXES46_P_GRANTOR") == ["FIXES46_P_GRANTOR"]
    assert legacy._grantors("\x03\x01A\x03\x01B") == ["A", "B"]
    assert legacy._grantors("_SYSTEM") == ["_SYSTEM"]
    assert legacy._grantors("\x06\x02A\x00B\x00") == ["AB"]


def test_setup_and_its_dry_run_report_both(monkeypatch):
    """_record_and_initialize: the replacements run before the grants are copied, the revokes after; both keys in the
    status and in the dry run."""
    from iris_vector_graph import IRISGraphEngine

    calls = []
    monkeypatch.setattr(legacy, "present_legacy_objects", lambda conn, native: [])
    monkeypatch.setattr(legacy, "arno_copies", lambda conn: calls.append("find") or ["c"])
    monkeypatch.setattr(legacy, "replace_arno_wrappers",
                        lambda conn, native, schema, copies, items, dry_run: calls.append(("replace", dry_run)) or
                        [{"routine": "Graph_KG.ivg_arno_zf_probe", "replaced": not dry_run}])
    monkeypatch.setattr(legacy, "copy_grants", lambda conn, found, schema, dry_run: calls.append("copy") or [])
    monkeypatch.setattr(legacy, "revoke_legacy_arno",
                        lambda conn, native, schema, copies, dry_run: (calls.append(("revoke", dry_run)) or
                                                                       [{"routine": "SQLUser.x", "revoked": not dry_run}],
                                                                       ["held through a schema grant"]))
    monkeypatch.setattr(legacy, "grant_read_path", lambda *a, **k: [])
    monkeypatch.setattr(legacy, "copy_ledger_history", lambda conn, schema, dry_run: {})
    monkeypatch.setattr("iris_vector_graph.schema_name.read_record", lambda native: "Graph_KG")
    monkeypatch.setattr("iris_vector_graph.schema_name.refuse_package_clash", lambda conn, schema: None)
    monkeypatch.setattr("iris_vector_graph.schema_name.native_namespace", lambda native: "NS")
    monkeypatch.setattr(IRISGraphEngine, "_iris_obj", lambda self: object(), raising=False)
    monkeypatch.setattr(IRISGraphEngine, "_plan_vector_dimensions", lambda self, cur, dim: None, raising=False)
    monkeypatch.setattr(IRISGraphEngine, "_initialize_schema", lambda self, deploy: {"warnings": []}, raising=False)
    monkeypatch.setattr(IRISGraphEngine, "_plan_setup", lambda self, *a: {})  # R050's plan: not this test's subject

    class Conn:
        def cursor(self):
            return _Cur(_Conn([]))

        def commit(self):
            pass

    engine = IRISGraphEngine.__new__(IRISGraphEngine)
    engine.conn, engine.embedding_dimension = Conn(), 8
    dry = engine._record_and_initialize(True, True)
    assert dry["arno_wrappers_replaced"] == [{"routine": "Graph_KG.ivg_arno_zf_probe", "replaced": False}]
    assert dry["legacy_arno_revoked"] == [{"routine": "SQLUser.x", "revoked": False}]
    assert calls == ["find", ("replace", True), "copy", ("revoke", True)]
    calls.clear()
    status = engine._record_and_initialize(True, False)
    assert calls == ["find", ("replace", False), "copy", ("revoke", False)]
    assert status["arno_wrappers_replaced"][0]["replaced"] and status["legacy_arno_revoked"][0]["revoked"]
    assert "held through a schema grant" in status["warnings"]
