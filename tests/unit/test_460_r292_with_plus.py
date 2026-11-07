"""4.6.0 R292 (the WITH + item), pinned against the translation (no container).

The answers are proven live in tests/e2e/test_460_r292_with_plus_e2e.py.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _t(q: str):
    return translate_to_sql(parse_query(q), {})


@pytest.mark.parametrize("q", [
    "MATCH (n:K) WITH n.k + 1 AS k RETURN k",
    "MATCH (n:K) WITH 1 + n.k AS k RETURN k",
])
def test_a_with_sum_over_a_stored_value_is_typed_as_minus_is(q):
    t = _t(q)
    stage = t.sql.partition(")\nSELECT ")[0]
    assert "Graph_KG.CY_ARITH(" in stage and "'+'" in stage, stage
    assert "AS DOUBLE) + 1" not in stage
    # the column is the row's type, not a static integer the engine rounds (3.5 read as 4)
    assert "k" not in [c.strip('"').lower() for c in (t.int_expr_columns or [])]
    assert "CY_TYPED(Stage1.k)" in t.sql


def test_minus_is_unchanged():
    stage = _t("MATCH (n:K) WITH n.k - 1 AS k RETURN k").sql
    assert "ELSE Graph_KG.CY_ARITH(p2.val, '-', 1) END AS k" in stage


@pytest.mark.parametrize("q", [
    "MATCH (n:K) WITH n.k + 'a' AS k RETURN k",  # a string: concatenation
    "MATCH (n:K) WITH n.k + n.j AS k RETURN k",  # no integer side: list, string or number
    "MATCH (n:K) WITH n.k + 1.5 AS k RETURN k",  # a float side: a float whatever the row (R240)
])
def test_other_sums_keep_their_form(q):
    stage = _t(q).sql.partition(")\nSELECT ")[0]
    # 4.6.0 R300: the one CY_ARITH such a sum may hold is the branch for a row's string (ISNUMERIC 0): concatenation
    assert stage.count("CY_ARITH(") == stage.count("COALESCE(Graph_KG.CY_ARITH("), stage
    assert "CY_ARITH" not in stage or "ISNUMERIC(" in stage, stage


def test_a_return_sum_keeps_its_form():
    assert "SELECT Graph_KG.CY_TYPED(Graph_KG.CY_ARITH(%EXACT(p2.val), '+', 1)) AS n_k___1" in _t(
        "MATCH (n:K) RETURN n.k + 1"
    ).sql


@pytest.mark.parametrize("q", [
    "MATCH (n:K) RETURN toString(n.k + 1)",
    "MATCH (n:K) WITH toString(n.k + 1) AS s RETURN s",
    "MATCH (n:K) WITH n.k + 1 AS k RETURN toString(k)",
])
def test_to_string_of_the_sum_is_spelled_as_a_number(q):
    # CAST(... AS VARCHAR) spelled -0.5 as IRIS does, '-.5'
    assert "CY_TOSTRING(" in _t(q).sql, q


def test_to_string_of_a_concatenation_keeps_the_cast():
    assert "CY_TOSTRING(" not in _t("MATCH (n:K) RETURN toString(n.k + 'a')").sql


@pytest.mark.parametrize("q", [
    "MATCH (n:K) WITH n.k + 1 AS k RETURN k + 1",
    "MATCH (n:K) WITH n.k - 1 AS k RETURN 1 + k",
])
def test_a_typed_column_plus_an_integer_is_typed(q):
    sql = _t(q).sql.partition(")\nSELECT ")[2]
    assert "LIST_CONCAT" not in sql, sql
    assert "CY_TYPED(CASE WHEN SUBSTRING(CAST(Stage1.k AS VARCHAR(3641144)), 1, 1) = 'P' THEN" in sql
    assert "Graph_KG.CY_ARITH(" in sql


@pytest.mark.parametrize("q", [
    "WITH [1] AS k RETURN k + 1",  # a list column concatenates
    "MATCH (n:K) WITH n.k AS k RETURN k + 1",  # a property's column may be a list
])
def test_other_columns_keep_list_concat(q):
    assert "LIST_CONCAT" in _t(q).sql or "[1,1]" in _t(q).sql, q
