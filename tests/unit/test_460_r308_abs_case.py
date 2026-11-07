"""4.6.0 R308 (cmptypes), offline: abs() keeps its argument's type, so IRIS's cached plan cannot round it.

IRIS caches a statement with its literals replaced, and ABS() kept the type the first call's argument had: after
`RETURN abs(1)`, `RETURN abs(0.5)` answered 1.0, `abs($x)` of 0.5 after 1 answered 1.0, and abs(n.v) of a stored 0.5
after a stored 1 answered 0 (measured on 2026.3, cached queries not purged). abs(x) is `CASE WHEN x < 0 THEN -x ELSE
x END`: negation and the CASE keep x's type. A value known before the query runs (a literal, a parameter) is answered
then, cast to its own type, which the statement text then names. Live: tests/e2e/test_460_r308_abs_cached_e2e.py.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def tr(q, params=None):
    s = translate_to_sql(parse_query(q), params or {})
    return (s.sql if isinstance(s.sql, str) else "\n".join(s.sql)), s.parameters


@pytest.mark.parametrize("q", ["MATCH (n) RETURN abs(n.v) AS a", "UNWIND [1, -2] AS x RETURN abs(x) AS a",
                               "WITH -2 AS y RETURN abs(y) AS a"])
def test_abs_is_a_case_not_iris_abs(q):
    sql, _ = tr(q)
    assert "ABS(" not in sql and "< 0 THEN -(" in sql, sql


@pytest.mark.parametrize("q, params, text", [
    ("RETURN abs(0.5) AS a", {}, "CAST(0.5 AS DOUBLE)"),
    ("RETURN abs(-1) AS a", {}, "CAST(1 AS BIGINT)"),
    ("RETURN abs($x) AS a", {"x": -0.5}, "CAST(0.5 AS DOUBLE)"),
    ("RETURN abs($x) AS a", {"x": 3}, "CAST(3 AS BIGINT)"),
    ("RETURN abs($x) AS a", {"x": -0.0}, "CAST(0.0 AS DOUBLE)"),
    ("RETURN abs(null) AS a", {}, "NULL"),
])
def test_a_value_known_before_the_query_runs_is_answered_in_its_type(q, params, text):
    sql, parameters = tr(q, params)
    assert sql == f"SELECT {text} AS a" and parameters in ([], [[]]), (sql, parameters)


def test_a_stored_value_modulo_keeps_the_row_type():
    """`n.v % 2` of a stored 2.5 answered 0: the int default rounded MOD's 0.5 (release too, either call order);
    CY_ARITH keeps the row's type, as it does for - * / (live: test_460_r308_abs_cached_e2e.py, mod)."""
    sql, _ = tr("MATCH (n) RETURN n.v % 2 AS a")
    assert "CY_ARITH(" in sql and "'%'" in sql, sql
    s = translate_to_sql(parse_query("MATCH (n) RETURN n.v % 2 AS a"), {})
    assert "a" not in s.int_expr_columns, s.int_expr_columns


@pytest.mark.parametrize("fn, sql_fn", [("ceil", "CEILING"), ("floor", "FLOOR")])
def test_ceil_and_floor_answer_a_float(fn, sql_fn):
    """openCypher's ceil() and floor() answer a float: ceil($x) of 2.5 came back '3' under driver 5.4.1, ceil(2.5) 3."""
    sql, _ = tr(f"UNWIND [2.5] AS x RETURN {fn}(x) AS a")
    assert f"CAST({sql_fn}(u0.x) AS DOUBLE)" in sql, sql


def test_a_long_operand_keeps_iris_abs():
    """TCK Return6 [16]: naming `r1.times / H1 - r2.times / H2` three times inside sum() failed with -202."""
    sql, _ = tr("MATCH (a)-[r1]->(b)<-[r2]-(c) WITH a, count(r1) AS h, c MATCH (a)-[r1]->(b)<-[r2]-(c) "
                "RETURN sum(abs(r1.t / h - r2.t / h)) AS s")
    assert "ABS(" in sql, sql


def test_abs_of_a_list_element_reads_its_number():
    """The CASE answered JSON_ARRAYGET's text ('.5') for `abs(l[0])` of a collected 0.5 (R308 cmptypes' math family)."""
    sql, _ = tr("UNWIND [0.5] AS e WITH collect(e) AS l RETURN abs(l[0]) AS a")
    assert "ABS(" in sql and "< 0 THEN -(" not in sql, sql


def test_abs_of_a_list_element_is_read_typed():
    """Driver 5.4.1 read ABS() of a collected 1 as the text '1' (a VARCHAR column): the RETURN reads it through
    CY_TYPED."""
    s = translate_to_sql(parse_query("UNWIND [1] AS e WITH collect(e) AS l RETURN abs(l[0]) AS a"), {})
    assert "CY_TYPED(ABS(" in s.sql and "a" in s.typed_value_columns, s.sql
