"""4.6.0 R298, offline: what the translator and the engine's routes decide for fixes46's leftovers. The live answers are
in tests/e2e/test_460_r298_fixes46_leftovers_e2e.py.

- The null row of a leading OPTIONAL MATCH with no match held NULL for every aggregate (`RETURN b.name, count(*)`
  answered [null, null]), and with the aggregates alone `count(*)` answered 0.
- A relationship number stored as text ({"w": "7"}) read as text in the variable-length relationship lists.
- A property map other than a string or an integer (`[r*1..2 {w: 1.5}]`, `{f: true}`) left SQL for the engine's
  labeled route, which read the start by its labels only and kept a far end when any relationship into it matched.
"""

from __future__ import annotations

import pytest

from iris_vector_graph._engine.query import _typed_rel_props
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import _null_row_aggregate, _vlp_rel_prop_filters, translate_to_sql
from iris_vector_graph.schema import GraphSchema


def _sql(q, params=None):
    s = translate_to_sql(parse_query(q), params or {}).sql
    return s if isinstance(s, str) else "\n".join(s)


@pytest.mark.parametrize(
    "agg, value",
    [("count(*)", "1"), ("count(b)", "0"), ("count(DISTINCT b)", "0"), ("collect(b.name)", "'[]'"),
     ("sum(b.k)", "NULL"), ("avg(b.k)", "NULL"), ("max(b.k)", "NULL")],
)
def test_an_aggregate_over_the_null_row(agg, value):
    item = parse_query(f"RETURN {agg}").return_clause.items[0].expression
    assert _null_row_aggregate(item) == value


def test_the_null_row_union_carries_the_aggregates():
    sql = _sql("OPTIONAL MATCH (a:L {name: 'c'})-[:knows]->(b) RETURN b.name, count(*), count(b), collect(b.name)")
    assert "SELECT NULL, 1, 0, '[]' WHERE NOT EXISTS" in sql, sql


def test_the_aggregates_alone_count_the_null_row():
    sql = _sql("OPTIONAL MATCH (a:L {name: 'c'})-[:knows]->(b) RETURN count(*), count(b)")
    assert "CASE WHEN COUNT(*) = 0 THEN 1 ELSE COUNT(*) END" in sql, sql
    # not where the query reads more than the OPTIONAL MATCH: a MATCH before it, or one after it
    assert "CASE WHEN COUNT(*) = 0" not in _sql("MATCH (a:L) OPTIONAL MATCH (a)-[:knows]->(b) RETURN count(*)")
    assert "CASE WHEN COUNT(*) = 0" not in _sql(
        "OPTIONAL MATCH (a:L {name: 'c'})-[:knows]->(b) MATCH (z:Z) RETURN count(*)")
    assert "CASE WHEN COUNT(*) = 0" not in _sql("MATCH (a:L)-[:knows]->(b) RETURN count(*)")


def _rel(props):
    return parse_query(f"MATCH (a)-[r*1..2 {props}]->(b) RETURN b").query_parts[0].clauses[0].patterns[0].relationships[0]


class _Ctx:
    input_params = {"p": 1.5, "q": [1], "f": [0.5]}


@pytest.mark.parametrize(
    "props, want",
    [("{w: 1.5}", [("w", 1.5)]), ("{w: -1.5}", [("w", -1.5)]), ("{w: 7}", [("w", 7)]), ("{f: true}", [("f", True)]),
     ("{w: null}", [("w", None)]), ("{s: 'x'}", [("s", "x")]), ("{w: $p}", [("w", 1.5)])],
)
def test_sql_checks_numbers_booleans_strings_and_null(props, want):
    assert _vlp_rel_prop_filters(_rel(props), _Ctx()) == want


@pytest.mark.parametrize("props", ["{w: [1.5, 2]}", "{w: {a: 1}}", "{w: $f}"])
def test_a_list_or_map_keeps_the_engine_route(props):
    # 4.6.0 R301: a list of integers, booleans and plain strings is checked in SQL (test_460_r301_rel_lists); a float
    # in a list, or a map, is not
    assert _vlp_rel_prop_filters(_rel(props), _Ctx()) is None


@pytest.mark.parametrize(
    "q",
    [
        "MATCH (a:L {name: 'a'})-[r*1..2 {w: 1.5}]->(t) RETURN t.name, r",
        "MATCH (a {node_id: $id})-[r:knows*1..2 {w: 1.5}]->(t) RETURN t.name, r",
        "MATCH (a {node_id: $id})-[:flag*1..2 {f: true}]->(t) RETURN t.name",
    ],
)
def test_a_checkable_property_map_expands_in_sql_whatever_the_start(q):
    sql = _sql(q, {"id": "n1"})
    assert "CY_VLP_PATHS(" in sql, sql


def test_a_float_compares_as_a_number_and_a_boolean_by_its_json_type():
    sql = _sql("MATCH (a:L)-[r*1..2 {w: 7.0, f: true}]->(t) RETURN t")
    assert "CAST(vpz" in sql and "AS DOUBLE) <> CAST('7.0' AS DOUBLE)" in sql, sql
    assert "CY_JSON_TYPE(" in sql and "<> 'boolean'" in sql and "<> '1'" in sql, sql


def test_the_routes_type_a_relationship_s_stored_text():
    assert _typed_rel_props('{"w": "7", "x": "1.5", "f": "true", "s": "q", "z": "007", "e": "\\u0000"}') == {
        "w": 7, "x": 1.5, "f": True, "s": "q", "z": "007", "e": ""}
    assert _typed_rel_props(None) == {} and _typed_rel_props("") == {} and _typed_rel_props({"w": 2}) == {"w": 2}


def test_the_sql_expansions_retype_each_relationship_s_properties():
    stmts = [s for s in GraphSchema.get_procedures_sql_list(table_schema="Graph_KG")
             if "FUNCTION Graph_KG.CY_VLP_PATHS(" in s or "FUNCTION Graph_KG.CY_VLP_CHAIN(" in s]
    assert len(stmts) == 2
    for s in stmts:
        assert 'ro.%Set("props", ##class(%Library.DynamicObject).%FromJSON(##class(Graph.KG.funcCYRETYPEMAP)' in s
        body = s.split("{", 1)[1]
        assert "'" not in body and "?" not in body
