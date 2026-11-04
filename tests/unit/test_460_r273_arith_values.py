"""4.6.0 R273 (the arithmetic cases), pinned against the translation and the engine's decode (no container).

The answers are proven live in tests/e2e/test_460_r273_arith_values_e2e.py.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from iris_vector_graph._engine.query import _int_expr_value
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _t(q: str, params=None):
    return translate_to_sql(parse_query(q), dict(params or {}))


def test_an_unwind_of_integers_divides_as_integers_and_reads_as_one():
    t = _t("UNWIND [-7, 7] AS x RETURN x / 2, x * 2")
    assert "TRUNCATE(u0.x / 2, 0)" in t.sql
    assert len(t.int_expr_columns) == 2, t.int_expr_columns


def test_an_unwind_of_floats_and_of_mixed_values_is_not_an_integer():
    assert "TRUNCATE(" not in _t("UNWIND [-7.0, 7.5] AS x RETURN x / 2").sql
    assert "TRUNCATE(" not in _t("UNWIND [1, 'a'] AS x RETURN x / 2").sql
    assert "TRUNCATE(" not in _t("UNWIND [1, 2.5] AS x RETURN x / 2").sql


def test_a_with_integer_divides_as_one():
    sql = _t("WITH -7 AS x RETURN 2 / x").sql
    assert "TRUNCATE(2 / " in sql


def test_an_integer_parameter_divides_by_a_cast_not_truncate():
    """TRUNCATE() over a marker fails 'Incorrect number of parameters' (measured on 2026.1)."""
    t = _t("RETURN $k / 2, $k / $d, $f / $d", {"k": 7, "d": -2, "f": 7.0})
    assert "CAST(? / 2 AS BIGINT)" in t.sql and "CAST(? / ? AS BIGINT)" in t.sql
    assert "TRUNCATE(" not in t.sql
    assert t.sql.count("?") == 5  # each parameter once
    assert len(t.int_expr_columns) == 2 and len(t.float_expr_columns) == 1


@pytest.mark.parametrize("value,want", [("-3", -3), ("12", 12), ("0", 0), (Decimal("-3.000"), -3), (4.0, 4)])
def test_an_integer_column_reads_a_number_text_as_the_integer(value, want):
    got = _int_expr_value(value)
    assert got == want and type(got) is int


@pytest.mark.parametrize("value", ["abc", "", "1,2", None, True])
def test_an_integer_column_leaves_other_values(value):
    assert _int_expr_value(value) is value


def test_tostring_of_a_typed_coalesce_reads_the_winning_value_with_its_type():
    sql = _t("MATCH (n:L) RETURN toString(coalesce(n.f, $k))", {"k": 0}).sql
    assert "CY_TOSTRING(CASE WHEN " in sql and "AS VARCHAR" not in sql


def test_a_float_literal_in_a_typed_coalesce_is_a_double():
    sql = _t("MATCH (n:L) RETURN coalesce(n.zz, 1.0), coalesce(n.zz, 2)").sql
    assert "ELSE CAST((1.0) AS DOUBLE) END" in sql and "ELSE ((2)) END" in sql
