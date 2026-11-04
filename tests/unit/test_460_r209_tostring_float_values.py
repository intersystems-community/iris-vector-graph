"""4.6.0 R209: `toString()` of a float that is not a literal answers openCypher's text.

R190 routed a float literal through CY_FLOAT_TEXT and R182 a stored property; every other argument kept
`CAST(x AS VARCHAR)`, IRIS's spelling: `UNWIND [0.5, 1.0] AS x RETURN toString(x)` answered `'.5'`, `'1'`, and
`toString(0.1 + 0.2)` `'.30000000000000004441'` (openCypher: `'0.5'`, `'1.0'`, `'0.30000000000000004'`).

A value whose type is FLOAT before the query runs (a float in an UNWIND list or a WITH, a float parameter, arithmetic
with a float operand, avg(), a float-valued function) is read as a $DOUBLE by CY_TOSTRING, which spells its shortest
decimal through CY_FLOAT_TEXT. Arithmetic over stored values, whose type only the row knows, answers its digits when
the result is whole and CY_TOSTRING's text when it is not. Integers, strings and booleans keep their translation.

These pin the SQL; tests/e2e/test_460_r209_tostring_float_values_e2e.py checks the answers.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _select(q, params=None):
    r = translate_to_sql(parse_query(q), params or {})
    sql = r.sql if isinstance(r.sql, str) else r.sql[-1]
    return sql


@pytest.mark.parametrize(
    "q, params",
    [
        ("UNWIND [0.5, 1.0] AS x RETURN toString(x)", {}),
        ("RETURN toString(0.1 + 0.2)", {}),
        ("WITH 0.5 AS x RETURN toString(x)", {}),
        ("WITH 0.1 + 0.2 AS x RETURN toString(x)", {}),
        ("RETURN toString(1.5 * 2)", {}),
        ("RETURN toString(7 / 2.0)", {}),
        ("RETURN toString($v)", {"v": 2.5}),
        ("UNWIND [1, 2] AS x RETURN toString(avg(x))", {}),
        ("RETURN toString(sqrt(2))", {}),
    ],
)
def test_a_float_value_reads_through_cy_tostring_as_a_double(q, params):
    sql = _select(q, params)
    assert "CY_TOSTRING(CAST(" in sql and "AS DOUBLE))" in sql, sql
    assert "AS VARCHAR(4096))" not in sql, sql


@pytest.mark.parametrize(
    "q, params",
    [
        ("UNWIND [1, 2] AS x RETURN toString(x)", {}),
        ("UNWIND ['a', '.5'] AS x RETURN toString(x)", {}),
        ("RETURN toString(7 / 2)", {}),
        ("RETURN toString(7)", {}),
        ("WITH 3 AS x RETURN toString(x)", {}),
        ("RETURN toString($v)", {"v": 3}),
        ("RETURN toString($v)", {"v": "007"}),
        ("UNWIND [1, 2] AS x RETURN toString(count(x))", {}),
    ],
)
def test_an_integer_or_string_value_keeps_its_translation(q, params):
    assert "CY_TOSTRING" not in _select(q, params)


def _select_params(q, params=None):
    r = translate_to_sql(parse_query(q), params or {})
    sql = r.sql if isinstance(r.sql, str) else r.sql[-1]
    return sql, r.parameters[-1]


def test_arithmetic_over_a_stored_value_reads_its_digits_when_whole():
    """Since R240 the row's type: CY_ARITH answers an integer or a $DOUBLE, which CY_TOSTRING spells ('6', '4.0');
    the whole-number CASE answered '4' for 2.0 * 2."""
    sql, params = _select_params("MATCH (n:T) RETURN toString(n.f * 2)")
    assert "CY_TOSTRING(CASE WHEN" in sql and "ELSE Graph_KG.CY_ARITH(" in sql and "FLOOR(" not in sql, sql
    assert sql.count("?") == len(params) == 2, (sql, params)  # the label and the property key, once each


def test_arithmetic_with_a_parameter_over_a_stored_value_binds_it_once():
    """It kept its CAST while the CASE repeated the expression (and the marker); since R240 CY_ARITH reads it once."""
    sql, params = _select_params("MATCH (n:T) RETURN toString(n.f * $k)", {"k": 3})
    assert "CY_TOSTRING(Graph_KG.CY_ARITH(" in sql, sql
    assert sql.count("?") == len(params) and params[0] == 3, (sql, params)


def test_a_float_with_a_parameter_binds_it_once():
    sql, params = _select_params("MATCH (n:T) RETURN toString($k * 2.0)", {"k": 3})
    assert "CY_TOSTRING(CAST((? * CAST('2.0' AS DOUBLE)) AS DOUBLE))" in sql, sql
    assert sql.count("?") == len(params) and params[0] == 3, (sql, params)


def test_a_stored_duration_keeps_its_text():
    """`n.d * 2` may be a duration: the CASE's duration branch is left as it is, its number branch is spelled."""
    sql, params = _select_params("MATCH (n:T) RETURN toString(n.d * 2)")
    assert "THEN Graph_KG.CY_TEMPORAL_ARITH(" in sql and "ELSE Graph_KG.CY_ARITH(" in sql, sql
    assert sql.count("?") == len(params), (sql, params)
