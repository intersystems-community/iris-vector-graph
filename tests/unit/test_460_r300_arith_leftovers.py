"""4.6.0 R300, offline: what the translator and the CY_* function bodies decide for fixes48's arithmetic leftovers.
The live answers, worked by hand from openCypher, are in tests/e2e/test_460_r300_arith_leftovers_e2e.py.

On the release head (6f9e732f): `+` over a stored string read the string as 0 (or CY_ARITH answered null); a string
starting with P went to CY_TEMPORAL_ARITH, which answered null for `*`; a SET's `%` was IRIS SQL's (no such operator,
SQLCODE -1) and its `/ 0.0` IRIS's <DIVIDE>; `$a / n.k` divided in SQL (<DIVIDE> for a stored 0); `1 / 0` under a CASE
branch raised at Query Open; a list predicate over a row's list read a string element as 0; `count(*) + 1` on the null
row was null; `l['a']` read a list as a map and `^` read a string as 0.
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql
from iris_vector_graph.schema import GraphSchema


def _tr(q, params=None):
    return translate_to_sql(parse_query(q), params or {})


def _sql(q, params=None):
    s = _tr(q, params).sql
    return s if isinstance(s, str) else "\n".join(s)


def _udf(name):
    for stmt in GraphSchema.get_procedures_sql_list("Graph_KG"):
        if f"FUNCTION Graph_KG.{name}(" in stmt:
            return stmt
    raise AssertionError(name)


def test_cy_arith_concatenates_a_string_for_plus():
    body = _udf("CY_ARITH")
    assert 'If op = "+" { Quit ##class(Graph.KG.funcLISTCONCAT).LISTCONCAT(a, b) }' in body
    assert 'If op = "+" { Quit "" }' not in body


def test_cy_temporal_arith_raises_type_error_for_a_string_that_is_no_duration():
    body = _udf("CY_TEMPORAL_ARITH")
    # * and / with no duration operand, and - with two values that are no numbers
    assert body.count('"TypeError: InvalidArgumentType: " _ op _ " needs two numbers') == 2
    # + concatenates with each side spelled as toString spells it, SQL's empty string ($Char(0)) as "" (4.6.0 R307)
    assert '$Select(a = $Char(0): "", 1: ##class(Graph.KG.funcCYTOSTRING).CYTOSTRING(a)) _ ' in body
    assert '$Select(b = $Char(0): "", 1: ##class(Graph.KG.funcCYTOSTRING).CYTOSTRING(b))' in body
    assert "'" not in body.split("LANGUAGE OBJECTSCRIPT", 1)[1]  # an apostrophe breaks the DDL (R297's note)


@pytest.mark.parametrize(
    "q",
    [
        "MATCH (n) WHERE n.id + 1 = 'a1' RETURN n",
        "MATCH (n) RETURN n.id + 1.5",
        "MATCH (n) RETURN n.id + n.id",
    ],
)
def test_plus_over_a_stored_value_concatenates_through_cy_arith(q):
    sql = _sql(q)
    assert re.search(r"CY_ARITH\(.*?, '\+', ", sql), sql
    assert "ISNUMERIC(" in sql, sql


def test_plus_of_two_number_literals_is_sql_arithmetic():
    assert "CY_ARITH" not in _sql("RETURN 1 + 2")


def test_set_modulo_and_float_zero_division_use_cy_arith():
    sql = _sql("MATCH (n:L) SET n.q = n.k % 2")
    assert "CY_ARITH(" in sql and "'%'" in sql, sql
    assert not re.search(r"END % 2|\) % 2", sql), sql
    sql = _sql("MATCH (n:L) SET n.q = n.j / 0.0")
    assert "CY_ARITH(" in sql and "CAST(0.0 AS DOUBLE)" in sql, sql
    # a literal divisor other than zero is SQL's division, as before
    assert "CY_ARITH" not in _sql("MATCH (n:L) SET n.q = n.j / 2.5")


def test_a_marker_beside_a_row_divisor_is_cy_arith_with_one_marker():
    r = _tr("MATCH (n:L) WHERE $a / n.k > 1 RETURN n", {"a": 5})
    sql = r.sql if isinstance(r.sql, str) else "\n".join(r.sql)
    assert re.search(r"CY_ARITH\(\?, '/', ", sql), sql
    assert sql.count("?") == sum(len(p) for p in r.parameters), (sql, r.parameters)
    assert re.search(r"CY_ARITH\(\?, '%', ", _sql("MATCH (n:L) RETURN $a % n.k", {"a": 5}))


def test_a_constant_division_by_zero_reads_its_case_condition():
    sql = _sql("MATCH (n:L) RETURN CASE WHEN n.k > 9 THEN 1 / 0 END")
    raise_arg = sql[sql.index("IVGTYPEERROR(") :]
    # the condition, as the outer CASE reads it: n.k as a number (4.7.0 row 24: a stored string is null against 9)
    assert "CAST(%EXACT(p2.val) AS DOUBLE) END) > 9 THEN" in raise_arg, sql
    assert "1 / 0" not in sql, sql  # no SQL division IRIS folds into <DIVIDE> at Query Open


@pytest.mark.parametrize(
    "q",
    [
        "MATCH (n) RETURN any(x IN [n.name] WHERE x * 2 = 0)",
        "MATCH (n) WITH [n.name] AS l RETURN any(x IN l WHERE x % 2 = 0)",
    ],
)
def test_a_list_predicate_over_a_row_list_checks_its_element(q):
    assert re.search(r"CY_ARITH\([^)]*\.x, '[*%]'", _sql(q)), _sql(q)


def test_a_list_predicate_over_a_literal_number_list_is_sql_arithmetic():
    sql = _sql("RETURN any(x IN [1, 2, 3] WHERE x % 2 = 0)")
    assert "CY_ARITH" not in sql and "MOD(" in sql, sql


def test_the_null_row_value_of_an_aggregate_expression():
    sql = _sql("OPTIONAL MATCH (n:L {id: 'zz'}) RETURN count(*) + 1")
    assert "CASE WHEN COUNT(*) = 0 THEN 2 ELSE" in sql, sql
    sql = _sql("OPTIONAL MATCH (n:L {id: 'zz'}) RETURN count(*)")
    assert "CASE WHEN COUNT(*) = 0 THEN 1 ELSE COUNT(*) END" in sql, sql
    sql = _sql("OPTIONAL MATCH (a:L {id: 'a'})-[:R]->(b) RETURN b.k, count(*) * 3")
    assert re.search(r"UNION ALL\nSELECT NULL, 3 WHERE NOT EXISTS", sql), sql


@pytest.mark.parametrize(
    "q",
    [
        "WITH [1, 2] AS l RETURN l['a']",
        "RETURN 2 ^ 'a'",
        "MATCH (n) RETURN n.k ^ 'a'",
    ],
)
def test_a_string_index_or_exponent_known_before_the_query_raises(q):
    with pytest.raises(TypeError, match="TypeError: InvalidArgumentType"):
        _tr(q)


def test_an_exponent_over_a_stored_value_is_checked_per_row():
    sql = _sql("MATCH (n) RETURN n.k ^ n.s")
    assert sql.count("ISNUMERIC(") == 2 and "^ needs two numbers" in sql, sql


def test_a_map_variable_indexed_by_a_string_is_checked_per_row():
    sql = _sql("WITH {a: 1} AS m RETURN m['a']")
    assert "SUBSTRING(Stage1.m, 1, 1) <> '['" in sql and "a list index must be an integer" in sql, sql
