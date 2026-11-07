"""4.6.0 R278 (Tom, 2026-11-04): a 4.5.x user holding SELECT and no EXECUTE keeps running Cypher after the upgrade.

Since R218 every `RETURN n.prop` reads through `CY_TYPED`, so a user who could read the tables and ran such a query on
4.5.x was refused (-99) after the upgrade. Every setup now grants EXECUTE on the read path to each grantee holding
SELECT on `rdf_props` (directly or through a schema grant; a column grant is not SELECT on the table), as R170 does for
a grantee holding EXECUTE on one read-path routine.

The read path is derived from the routines setup creates and the IVG Schema's class projects: every function whose body
reads nothing outside the caller's SQL privileges. A body that names a global, or a class other than the dynamic objects,
the regular-expression matcher, exceptions, dynamic SQL (which IRIS checks for the caller) and the classes `CREATE
FUNCTION` made for another such routine, reads around them and is left out (`CY_VLP_*` were too until R280). The live
check is tests/e2e/test_262_upgrade_451.py.
"""

from __future__ import annotations

import pytest

from iris_vector_graph._engine import legacy


@pytest.fixture(autouse=True)
def _grants_read_per_object(monkeypatch):
    """The grant stand-ins below answer per object (`grants_on`); setup reads every object at once (fixes74:
    `grants_by_object`, one statement), so here that read is each object's stand-in answer."""
    monkeypatch.setattr(legacy, "grants_by_object", lambda conn, names: {n: legacy.grants_on(conn, n) for n in names})


def test_the_read_path_holds_every_function_the_translation_calls_that_reads_nothing():
    names = set(legacy.read_path_routines())
    # what the Cypher translation calls (cypher/translator.py's _fn(...)) and reads nothing: R170's list missed the
    # JSON, list and string helpers, so a SELECT holder's `=~`, `split()` or relationship property was refused
    for n in ("CY_TYPED", "CY_CMP", "CY_JSON_NUM", "CY_PROPS_MAP", "CY_RETYPE_MAP", "JSON_VALUE", "JSON_ARRAYGET",
              "JSON_ARRAYLENGTH", "JSON_KEYS", "LIST_CONCAT", "LIST_TAIL", "REGEX_MATCH", "STR_SPLIT", "RAND",
              "NEWID", "CypherFn_IVGJSONSET", "CypherFn_IVGRANGE", "Percentile_PDISC", "Percentile_PCONT"):
        assert n in names, n


def test_a_routine_that_reads_a_global_or_an_ivg_class_is_not_on_it():
    names = set(legacy.read_path_routines())
    for n in ("kg_PPR", "kg_DegreeCentrality", "kg_Betweenness", "kg_SCC", "kg_KCore", "kg_Leiden", "kg_Closeness",
              "kg_Eigenvector", "kg_TriangleCount", "kg_VEC_ROUTE_GUARD", "kg_BM25", "kg_IVF", "MatchEdges",
              "MatchEdgesAllGraphs", "DijkstraPath", "backfill_temporal_rows", "rebuild_edge_indices"):
        assert n not in names, n
    # procedures are not functions of the read path
    for n in ("kg_KNN_VEC", "kg_TXT", "kg_RRF_FUSE", "ledger_records"):
        assert n not in names, n
    # the algorithms check SELECT since R280 (as CY_VLP_ENDS does) and stay off: their classes are not the read path
    assert "kg_PPR" not in names


def _with_extra(monkeypatch, *ddl):
    from iris_vector_graph.schema import GraphSchema

    original = GraphSchema.get_procedures_sql_list
    monkeypatch.setattr(GraphSchema, "get_procedures_sql_list", staticmethod(
        lambda table_schema="Graph_KG": original(table_schema=table_schema) + [
            d.replace("Graph_KG", table_schema) for d in ddl]))


def test_the_list_is_derived_so_a_reader_a_later_release_adds_is_left_out(monkeypatch):
    _with_extra(
        monkeypatch,
        "CREATE OR REPLACE FUNCTION Graph_KG.CY_R278PURE(v VARCHAR(10)) RETURNS VARCHAR(10) LANGUAGE OBJECTSCRIPT "
        "{ Quit ##class(%Library.DynamicArray).%New().%ToJSON() _ v }",
        "CREATE OR REPLACE FUNCTION Graph_KG.CY_R278PEEK(v VARCHAR(10)) RETURNS VARCHAR(10) LANGUAGE OBJECTSCRIPT "
        "{ Quit $Get(^KG(\"out\", 0, v)) }",
        "CREATE OR REPLACE FUNCTION Graph_KG.CY_R278WALK(v VARCHAR(10)) RETURNS VARCHAR(10) LANGUAGE OBJECTSCRIPT "
        "{ Quit ##class(Graph.KG.VarLength).Ends(v) }",
        # through the class CREATE FUNCTION made for the global reader: a reader too
        "CREATE OR REPLACE FUNCTION Graph_KG.CY_R278VIA(v VARCHAR(10)) RETURNS VARCHAR(10) LANGUAGE OBJECTSCRIPT "
        "{ Quit ##class(Graph.KG.funcCYR278PEEK).CYR278PEEK(v) }",
        "CREATE OR REPLACE FUNCTION Graph_KG.CY_R278VIAPURE(v VARCHAR(10)) RETURNS VARCHAR(10) LANGUAGE OBJECTSCRIPT "
        "{ Quit ##class(Graph.KG.funcCYR278PURE).CYR278PURE(v) }",
        # R280: the walk after a SELECT check is within privileges; before it, or another reading class, is not
        "CREATE OR REPLACE FUNCTION Graph_KG.CY_R278CHECKED(v VARCHAR(10)) RETURNS VARCHAR(10) LANGUAGE OBJECTSCRIPT "
        "{ Do ##class(Graph.KG.SqlPrivilege).RequireSelect(\"Graph_KG.rdf_edges\") "
        "Quit ##class(Graph.KG.VarLength).Ends(v) }",
        "CREATE OR REPLACE FUNCTION Graph_KG.CY_R278LATECHECK(v VARCHAR(10)) RETURNS VARCHAR(10) LANGUAGE OBJECTSCRIPT "
        "{ Set x = ##class(Graph.KG.VarLength).Ends(v) "
        "Do ##class(Graph.KG.SqlPrivilege).RequireSelect(\"Graph_KG.rdf_edges\") Quit x }",
        "CREATE OR REPLACE FUNCTION Graph_KG.CY_R278ALGO(v VARCHAR(10)) RETURNS VARCHAR(10) LANGUAGE OBJECTSCRIPT "
        "{ Do ##class(Graph.KG.SqlPrivilege).RequireSelect(\"Graph_KG.rdf_edges\") "
        "Quit ##class(Graph.KG.PageRank).RunJson(v) }",
    )
    names = set(legacy.read_path_routines())
    assert {"CY_R278PURE", "CY_R278VIAPURE", "CY_R278CHECKED"} <= names
    assert not {"CY_R278PEEK", "CY_R278WALK", "CY_R278VIA", "CY_R278LATECHECK", "CY_R278ALGO"} & names


class _Cur:
    def __init__(self, log, present):
        self.log, self.present, self._rows = log, present, []

    def execute(self, sql, params=None):
        self.log.append(sql)
        self._rows = [(n,) for n in self.present] if "INFORMATION_SCHEMA.ROUTINES" in sql else []

    def fetchall(self):
        return list(self._rows)

    def close(self):
        pass


class _Conn:
    def __init__(self, present):
        self.log: list = []
        self.present = present

    def cursor(self):
        return _Cur(self.log, self.present)


def _grants(held):
    """`grants_on` stand-in: `held` maps an object's short name to (subject, privilege, via) rows."""

    def grants_on(_iris, sql_name):
        short = sql_name.split(".", 1)[1]
        return [{"subject": s, "subject_type": "ROLE", "privilege": p, "with_grant": False, "via": v}
                for s, p, v in held.get(short, [])]

    return grants_on


@pytest.fixture
def path(monkeypatch):
    monkeypatch.setattr(legacy, "read_path_routines", lambda: ["CY_CMP", "CY_TYPED", "JSON_VALUE"])
    return _Conn(["CY_CMP", "CY_TYPED", "JSON_VALUE"])


def _granted(conn):
    return sorted(s for s in conn.log if s.startswith("GRANT"))


def test_a_select_holder_on_rdf_props_gets_execute_on_the_read_path(monkeypatch, path):
    monkeypatch.setattr(legacy, "grants_on", _grants({"rdf_props": [("SELROLE", "SELECT", "Direct")]}))
    out = legacy.grant_read_path(path, object(), "Graph_KG", dry_run=False)
    assert _granted(path) == ["GRANT EXECUTE ON Graph_KG.CY_CMP TO SELROLE", "GRANT EXECUTE ON Graph_KG.CY_TYPED TO SELROLE",
                              "GRANT EXECUTE ON Graph_KG.JSON_VALUE TO SELROLE"]
    assert {(g["subject"], g["because"]) for g in out} == {("SELROLE", "SELECT on Graph_KG.rdf_props")}
    assert all(g["granted"] and g["privilege"] == "EXECUTE" for g in out)


def test_select_through_a_schema_grant_counts(monkeypatch, path):
    monkeypatch.setattr(legacy, "grants_on", _grants({"rdf_props": [("SCHROLE", "SELECT", "Schema Privilege")]}))
    legacy.grant_read_path(path, object(), "Graph_KG", dry_run=False)
    assert len(_granted(path)) == 3 and all(s.endswith(" TO SCHROLE") for s in _granted(path))


def test_a_column_grant_or_select_on_another_table_gives_nothing(monkeypatch, path):
    # SQLAllPrivileges lists a column-only grant with no privilege name (HAS_COLUMN_PRIV 1)
    monkeypatch.setattr(legacy, "grants_on", _grants({
        "rdf_props": [("COLROLE", "None", "Direct")], "nodes": [("NODEROLE", "SELECT", "Direct")]}))
    assert legacy.grant_read_path(path, object(), "Graph_KG", dry_run=False) == []
    assert _granted(path) == []


def test_a_routine_held_through_a_schema_grant_is_not_granted_again(monkeypatch, path):
    monkeypatch.setattr(legacy, "grants_on", _grants({
        "rdf_props": [("FULLROLE", "SELECT", "Schema Privilege")],
        **{n: [("FULLROLE", "EXECUTE", "Schema Privilege")] for n in ("CY_CMP", "CY_TYPED", "JSON_VALUE")}}))
    assert legacy.grant_read_path(path, object(), "Graph_KG", dry_run=False) == []


def test_a_routine_held_through_execute_on_the_schema_is_not_granted_again(monkeypatch, path):
    """Round 3, expert W13: setup gave 63 EXECUTE grants to a role holding `GRANT EXECUTE ON SCHEMA Graph_KG`.
    SQLAllPrivileges lists such a grant once per routine with GRANTED_VIA null, read as "None", not as the
    "Schema Privilege" a schema SELECT shows (measured 2026-11-07 on 2026.3.0AI.113.0, role PC_SCHEMA2: 108 routine
    rows, every one EXECUTE via null)."""
    monkeypatch.setattr(legacy, "grants_on", _grants({
        "rdf_props": [("FULLROLE", "SELECT", "Schema Privilege"), ("SELROLE", "SELECT", "Schema Privilege")],
        **{n: [("FULLROLE", "EXECUTE", "None")] for n in ("CY_CMP", "CY_TYPED", "JSON_VALUE")}}))
    out = legacy.grant_read_path(path, object(), "Graph_KG", dry_run=False)
    assert {g["subject"] for g in out} == {"SELROLE"}  # the SELECT-only role still gets the read path
    assert not [s for s in _granted(path) if s.endswith(" TO FULLROLE")]


def test_a_null_via_on_rdf_props_is_still_no_select_holder(monkeypatch, path):
    monkeypatch.setattr(legacy, "grants_on", _grants({"rdf_props": [("ODDROLE", "SELECT", "None")]}))
    assert legacy.grant_read_path(path, object(), "Graph_KG", dry_run=False) == []


def test_a_second_run_grants_nothing(monkeypatch, path):
    monkeypatch.setattr(legacy, "grants_on", _grants({
        "rdf_props": [("SELROLE", "SELECT", "Direct")],
        **{n: [("SELROLE", "EXECUTE", "Direct")] for n in ("CY_CMP", "CY_TYPED", "JSON_VALUE")}}))
    assert legacy.grant_read_path(path, object(), "Graph_KG", dry_run=False) == []


def test_the_dry_run_lists_the_grants_and_makes_none(monkeypatch):
    monkeypatch.setattr(legacy, "read_path_routines", lambda: ["CY_CMP", "CY_TYPED"])
    conn = _Conn([])  # a 4.5.x namespace: no IVG Schema routine yet; rdf_props is in Graph_KG already
    monkeypatch.setattr(legacy, "grants_on", _grants({"rdf_props": [("SELROLE", "SELECT", "Direct")]}))
    out = legacy.grant_read_path(conn, object(), "Graph_KG", dry_run=True)
    assert sorted(g["routine"] for g in out) == ["Graph_KG.CY_CMP", "Graph_KG.CY_TYPED"]
    assert not any(g["granted"] for g in out) and _granted(conn) == []


def test_owner_and_superuser_rows_are_not_holders(monkeypatch, path):
    monkeypatch.setattr(legacy, "grants_on", _grants({
        "rdf_props": [("_SYSTEM", "SELECT", "SuperUser"), ("OWNER", "SELECT", "Owner Privilege")]}))
    assert legacy.grant_read_path(path, object(), "Graph_KG", dry_run=False) == []


# ----------------------------------------------------------- 4.6.0 R050 round 2 (expert walkthrough W4): through a role


def _typed_grants(held):
    """`grants_on` stand-in: `held` maps a short name to (subject, subject type, privilege, via) rows."""

    def grants_on(_iris, sql_name):
        short = sql_name.split(".", 1)[1]
        return [{"subject": s, "subject_type": t, "privilege": p, "with_grant": False, "via": v}
                for s, t, p, v in held.get(short, [])]

    return grants_on


def test_a_user_holding_select_only_through_a_role_gets_no_grant_of_its_own(monkeypatch, path):
    """SQLAllPrivileges lists the role's schema SELECT once more under each member user, with the same GRANTED_VIA
    (measured on 2026.3.0AI: role IVG460FX61_W4_ROLE and user IVG460FX61_W4_USER both 'Schema Privilege'), so setup
    granted the read path to the role and to every member. The role's grants serve its members."""
    monkeypatch.setattr(legacy, "grants_on", _typed_grants({"rdf_props": [
        ("READROLE", "ROLE", "SELECT", "Schema Privilege"), ("reader", "USER", "SELECT", "Schema Privilege")]}))
    monkeypatch.setattr(legacy, "user_roles", lambda _iris, user: {"reader": {"READROLE"}}.get(user, set()))
    out = legacy.grant_read_path(path, object(), "Graph_KG", dry_run=False)
    assert {g["subject"] for g in out} == {"READROLE"}
    assert all(s.endswith(" TO READROLE") for s in _granted(path)) and len(_granted(path)) == 3


def test_the_dry_run_reports_the_role_and_not_its_members(monkeypatch, path):
    monkeypatch.setattr(legacy, "grants_on", _typed_grants({"rdf_props": [
        ("READROLE", "ROLE", "SELECT", "Schema Privilege"), ("reader", "USER", "SELECT", "Schema Privilege")]}))
    monkeypatch.setattr(legacy, "user_roles", lambda _iris, user: {"READROLE"})
    out = legacy.grant_read_path(path, object(), "Graph_KG", dry_run=True)
    assert {g["subject"] for g in out} == {"READROLE"} and not any(g["granted"] for g in out)
    assert _granted(path) == []


def test_a_role_holding_execute_on_the_schema_gets_nothing_and_neither_do_its_members(monkeypatch, path):
    """The walkthrough's IVGREAD_A: SELECT and EXECUTE on the schema, held by the role; reader_a got 58 grants."""
    routines = {n: [("READROLE", "ROLE", "EXECUTE", "Schema Privilege"), ("reader", "USER", "EXECUTE", "Schema Privilege")]
                for n in ("CY_CMP", "CY_TYPED", "JSON_VALUE")}
    monkeypatch.setattr(legacy, "grants_on", _typed_grants({"rdf_props": [
        ("READROLE", "ROLE", "SELECT", "Schema Privilege"), ("reader", "USER", "SELECT", "Schema Privilege")],
        **routines}))
    monkeypatch.setattr(legacy, "user_roles", lambda _iris, user: {"READROLE"})
    assert legacy.grant_read_path(path, object(), "Graph_KG", dry_run=False) == []


def test_a_user_with_select_of_its_own_and_no_qualifying_role_is_still_granted(monkeypatch, path):
    monkeypatch.setattr(legacy, "grants_on", _typed_grants({"rdf_props": [("alone", "USER", "SELECT", "Direct")]}))
    monkeypatch.setattr(legacy, "user_roles", lambda _iris, user: {"%Developer"})
    out = legacy.grant_read_path(path, object(), "Graph_KG", dry_run=False)
    assert {g["subject"] for g in out} == {"alone"} and len(_granted(path)) == 3


def test_a_user_holding_execute_on_one_routine_through_a_role_is_left_to_the_role(monkeypatch, path):
    """R170's qualifier (EXECUTE on one read-path routine), held through a role: the same rule."""
    monkeypatch.setattr(legacy, "grants_on", _typed_grants({"CY_CMP": [
        ("EXROLE", "ROLE", "EXECUTE", "Direct"), ("member", "USER", "EXECUTE", "Direct")]}))
    monkeypatch.setattr(legacy, "user_roles", lambda _iris, user: {"EXROLE"})
    out = legacy.grant_read_path(path, object(), "Graph_KG", dry_run=False)
    assert {g["subject"] for g in out} == {"EXROLE"}
