"""4.6.0 R297, offline: arithmetic raises as openCypher does. The live answers are in
tests/e2e/test_460_r297_error_semantics_e2e.py; this file holds what the translator and the per-row CREATE / MERGE
binder decide before IRIS runs anything.

openCypher (the TCK's README; no TCK scenario covers either): an integer divided by an integer zero raises
ArithmeticError, `-`, `*`, `/`, `%` over a value that is no number raise TypeError, a float divided by zero is IEEE.
Before 4.6.0 R297: `RETURN 'a' * 2` answered 0, `'a' - 1` -1, `x / 0` NaN, and a per-row CREATE of `n.k % 0` failed
"Unsupported argument type" (the row could not be folded, so the static statements bound the AST).
"""

from __future__ import annotations

import math
import re

import pytest

from iris_vector_graph.cypher import ast
from iris_vector_graph.cypher.merge_rows import _fold
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql
from iris_vector_graph.schema import GraphSchema


def _tr(q, params=None):
    return translate_to_sql(parse_query(q), params or {})


def _sql(q, params=None):
    s = _tr(q, params).sql
    return s if isinstance(s, str) else "\n".join(s)


@pytest.mark.parametrize(
    "q, params",
    [
        ("RETURN 'a' * 2", None),
        ("RETURN 'a' - 1", None),
        ("RETURN 2 / 'a'", None),
        ("RETURN 'a' % 2", None),
        ("RETURN true * 2", None),
        ("RETURN [1, 2] - 1", None),
        ("RETURN {k: 1} * 2", None),
        ("RETURN 2 * $s", {"s": "a"}),
        ("RETURN $b - 1", {"b": True}),
        ("MATCH (n) RETURN n.k * 'a'", None),
        ("MATCH (n) WHERE n.k - 'a' > 0 RETURN n", None),
        ("MATCH (n) SET n.q = 'a' * 2", None),
    ],
)
def test_an_operand_known_to_be_no_number_raises_type_error(q, params):
    with pytest.raises(TypeError, match="TypeError: InvalidArgumentType"):
        _tr(q, params)


@pytest.mark.parametrize(
    "q, params",
    [
        ("RETURN null * 2", None),
        ("RETURN 2 - null", None),
        ("RETURN 'a' + 1", None),  # + concatenates a string
        ("RETURN 2 * $k", {"k": 3}),
        ("RETURN $k / 2", {"k": None}),
        ("RETURN duration('P1D') * 2", None),
    ],
)
def test_null_a_number_concatenation_and_a_duration_do_not(q, params):
    _tr(q, params)


_ERROR_CALL = re.compile(r"CypherFn_IVGTYPEERROR\(('[^']*')\)")


@pytest.mark.parametrize(
    "q",
    [
        "MATCH (n) RETURN n.k % 2",
        "MATCH (n) WHERE n.k * 2 > 3 RETURN n",
        "MATCH (n) WITH n.k AS k RETURN k - 1",
        "UNWIND [4, 6] AS x UNWIND [2, 3] AS y RETURN x / y",
        "UNWIND [4, 6] AS x UNWIND [2, 3] AS y RETURN x % y",
        "MATCH (n) WHERE n.k / n.j = 2 RETURN n",
    ],
)
def test_no_error_call_takes_constant_arguments(q):
    # IRIS evaluates a function call whose arguments are all constants once, when the query opens, even in a CASE
    # branch no row reaches: `CASE WHEN ISNUMERIC(p.val) = 0 THEN IVGTYPEERROR('...')` raised for every number
    # (measured on Community 2026.1). Each raise's message depends on the row.
    sql = _sql(q)
    assert not _ERROR_CALL.search(sql), sql


def test_two_integer_variables_raise_only_when_the_divisor_is_zero():
    sql = _sql("UNWIND [4, 6] AS x UNWIND [2, 3] AS y RETURN x / y")
    assert "CASE WHEN" in sql and "ArithmeticError: integer division by zero" in sql, sql
    assert "TRUNCATE(" in sql, sql


@pytest.mark.parametrize(
    "q, params",
    [
        ("MATCH (n) RETURN n.k / $d", {"d": 0}),
        ("MATCH (n) RETURN n.k % $d", {"d": 0}),
        ("MATCH (n) RETURN n.k % $d", {"d": 2}),
        ("RETURN $a / 0", {"a": 4}),
        ("RETURN 4 / $d", {"d": 0}),
        ("RETURN 4.5 / $d", {"d": 0}),
    ],
)
def test_a_parameter_beside_a_zero_divisor_is_bound_once(q, params):
    r = _tr(q, params)
    sqls = r.sql if isinstance(r.sql, list) else [r.sql]
    for s, p in zip(sqls, r.parameters):
        assert s.count("?") == len(p), (s, p)


def test_a_typed_arithmetic_column_keeps_cy_arith_s_message():
    # `CY_TYPED(CY_ARITH(..))` lost CY_ARITH's message (IRIS reports the outer function failing with <ZSQER>); under a
    # COALESCE it is kept (measured on 2026.1)
    sql = _sql("MATCH (n) RETURN n.k / $d", {"d": 0})
    assert "CY_TYPED(COALESCE(" in sql and "CY_ARITH(" in sql, sql


def test_cy_arith_raises_both_kinds_and_is_ieee_for_a_float():
    (stmt,) = [s for s in GraphSchema.get_procedures_sql_list(table_schema="Graph_KG") if "CY_ARITH(" in s]
    body = stmt.split("{", 1)[1]
    assert "'" not in body and "?" not in body, "the DB-API DDL tokenizer breaks on these in an ObjectScript body"
    assert '"ArithmeticError: integer "' in body and '"TypeError: InvalidArgumentType: "' in body, body
    assert '$DOUBLE("INF")' in body and '$DOUBLE("-INF")' in body, body
    # + of a string is concatenation's, not an error (4.6.0 R300: it answered null; LIST_CONCAT appends a list)
    assert 'If bad { If op = "+" { Quit ##class(Graph.KG.funcLISTCONCAT).LISTCONCAT(a, b) }' in body, body


def _f(op, x, y):
    return _fold(ast.FunctionCall(function_name=f"__arith_{op}", arguments=[ast.Literal(value=x), ast.Literal(value=y)]))


@pytest.mark.parametrize("op, x, y", [("*", "a", 2), ("-", "a", 1), ("/", 2, "b"), ("%", True, 2), ("*", [1], 2)])
def test_a_row_value_that_is_no_number_raises_type_error(op, x, y):
    with pytest.raises(TypeError, match="TypeError: InvalidArgumentType"):
        _f(op, x, y)


@pytest.mark.parametrize("op", ["/", "%"])
def test_a_row_integer_over_zero_raises_arithmetic_error(op):
    with pytest.raises(ArithmeticError, match="ArithmeticError: integer"):
        _f(op, 2, 0)
    with pytest.raises(ArithmeticError, match="ArithmeticError: integer"):
        _f(op, "7", 0)  # the API's stored text '7' is the number 7


def test_a_row_float_over_zero_is_ieee_and_numbers_fold_as_before():
    assert _f("/", 2.5, 0).value == math.inf
    assert _f("/", -2.5, 0).value == -math.inf
    assert math.isnan(_f("/", 0.0, 0).value)
    assert math.isnan(_f("%", 2.5, 0).value)
    assert _f("/", 2, 0.0).value == math.inf
    assert _f("/", 7, 2).value == 3 and _f("/", -7, 2).value == -3 and _f("%", -7, 2).value == -1
    assert _f("*", "7", 2).value == 14  # stored text of a number
    assert _f("*", None, 2).value is None
    assert _f("+", "a", 1).value == "a1"


def test_a_zero_that_is_not_a_divisor_is_just_a_number():
    assert _f("-", 2.0, 0).value == 2.0 and _f("*", 2.0, 0).value == 0.0
    assert _f("-", 3, 0.0).value == 3.0 and _f("-", 0.0, 0).value == 0.0


def test_a_create_s_property_insert_does_not_filter_on_its_value():
    # IRIS 2026.3 pushed `WHERE _cg._v IS NOT NULL` below the MATCH's joins and computed the value (and its errors) for
    # nodes the MATCH does not read; the null is removed by a DELETE after the INSERT instead
    r = _tr("MATCH (n:K) CREATE (:H {v: n.k * 2})")
    stmts = list(r.sql)
    (i,) = [j for j, s in enumerate(stmts) if " AS _v" in s]
    assert "_cg._v IS NOT NULL" not in stmts[i], stmts[i]
    assert stmts[i + 1].startswith('DELETE FROM Graph_KG.rdf_props WHERE "key" = ? AND val IS NULL'), stmts[i + 1]
    assert r.parameters[i + 1][0] == "v" and stmts[i + 1].count("?") == len(r.parameters[i + 1])


def test_the_raise_reads_both_operands():
    # A WITH's date is a constant in its stage, and IRIS evaluated a raise reading it alone when the query opened,
    # though the duration CASE answers the row (Temporal8 [1]-[5]: `x - d.dur` raised TypeError)
    sql = _sql("WITH date({year: 1984, month: 10, day: 11}) AS x MATCH (d:D) RETURN x - d.dur AS diff")
    assert "CY_TEMPORAL_ARITH(" in sql, sql
    for m in re.finditer(r"CypherFn_IVGTYPEERROR\((.*?)\) AS DOUBLE\)", sql):
        assert "Stage1.x" in m.group(1) and "p2.val" in m.group(1), m.group(1)


def test_a_with_column_keeps_its_number_form():
    # `x % 3` of a WITH column answered 0.0 once the checked operand was CAST to DOUBLE (WithOrderBy4 [9], [10])
    sql = _sql("MATCH (a:A) WITH a.num2 AS x WITH x % 3 AS x RETURN x")
    assert "ELSE Stage1.x END, 3)" in sql, sql


def test_a_list_predicate_s_variable_gets_no_row_check():
    # Quantifier7 [3] and Quantifier11 [3]: the check, repeated by the 3VL counts and a comparison's NaN decomposition,
    # failed the statement at Prepare (-202; <LIST> in LoadTableFunction)
    sql = _sql("RETURN (single(x IN [1, 2, 3] WHERE x % 2 = 0) OR all(x IN [1, 2, 3] WHERE x % 2 = 0)) "
               "<= any(x IN [1, 2, 3] WHERE x % 2 = 0) AS result")
    assert "ISNUMERIC(" not in sql, sql
    assert "ISNUMERIC(" not in _sql("MATCH (n) RETURN any(x IN n.l WHERE x % 2 = 0) AS r")
    assert "ISNUMERIC(" in _sql("MATCH (n) RETURN any(x IN n.l WHERE n.k % 2 = 0) AS r")  # other operands still are
