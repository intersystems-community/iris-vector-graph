"""4.6.0 R278 (Tom, 2026-11-04): a 4.5.x user holding SELECT and no EXECUTE keeps running Cypher after the upgrade.

Since R218 every `RETURN n.prop` reads through `CY_TYPED`, so a user who could read the tables and ran such a query on
4.5.x was refused (-99) after the upgrade. Every setup now grants EXECUTE on the read path to each grantee holding
SELECT on `rdf_props` (directly or through a schema grant; a column grant is not SELECT on the table), as R170 does for
a grantee holding EXECUTE on one read-path routine.

The read path is derived from the routines setup creates and the IVG Schema's class projects: every function whose body
reads nothing outside the caller's SQL privileges. A body that names a global, or a class other than the dynamic objects,
the regular-expression matcher, exceptions, dynamic SQL (which IRIS checks for the caller) and the classes `CREATE
FUNCTION` made for another such routine, reads around them and is left out, as are `CY_VLP_*` (Tom's decision). The live
check is tests/e2e/test_262_upgrade_451.py.
"""

from __future__ import annotations

import pytest

from iris_vector_graph._engine import legacy


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
    assert [n for n in names if n.startswith("CY_VLP_")] == []


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
    )
    names = set(legacy.read_path_routines())
    assert {"CY_R278PURE", "CY_R278VIAPURE"} <= names
    assert not {"CY_R278PEEK", "CY_R278WALK", "CY_R278VIA"} & names


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
