"""4.6.0 R292 (the comparison items), pinned against the translation (no container).

The answers are proven live in tests/e2e/test_460_r292_flag_compare_e2e.py.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _sql(q: str, params=None) -> str:
    return translate_to_sql(parse_query(q), dict(params or {})).sql


@pytest.mark.parametrize("q,flag", [
    ("UNWIND [true, 1] AS v RETURN v = true", "u0.v__b = 1 AND u0.v = 1"),
    ("UNWIND [true, 1] AS v RETURN v = 1", "u0.v__b = 0 AND u0.v IN ('1', '1.0')"),
    ("UNWIND [true, 1] AS v RETURN v = $t", "u0.v__b = 1 AND u0.v = 1"),
    ("UNWIND [true, 1] AS v WITH v RETURN v = true", "Stage1.v__b = 1 AND Stage1.v = 1"),
    ("WITH ['true', true] AS l WITH l[1] AS b RETURN b = true", "Stage2.b__b = 1 AND Stage2.b = 'true'"),
    ("WITH ['true', true] AS l WITH l[0] AS s RETURN s = 'true'", "Stage2.s__b = 0 AND Stage2.s = "),
])
def test_an_equality_with_a_flagged_value_reads_the_flag(q, flag):
    sql = _sql(q, {"t": True})
    assert flag in sql, sql
    # null in, null out
    assert "IS NULL THEN NULL WHEN" in sql


def test_not_equals_is_the_negation():
    sql = _sql("UNWIND [true, 1] AS v RETURN v <> true")
    assert "WHEN u0.v__b = 1 AND u0.v = 1 THEN 0 ELSE 1 END = 1" in sql, sql


@pytest.mark.parametrize("q,col", [
    ("WITH [1.0, 2] AS l WITH l[0] AS x RETURN x = 1.0", "Stage2.x"),
    ("UNWIND [1.0, 2] AS x RETURN x = 2.0", "u0.x"),
    ("MATCH (n:K) WITH n.k AS k RETURN k = 2.0", "Stage1.k"),
])
def test_a_whole_float_is_compared_with_each_spelling(q, col):
    sql = _sql(q)
    assert f"{col} IN (" in sql and ".0'" in sql, sql


@pytest.mark.parametrize("q,frag", [
    ("UNWIND [1, 2] AS v RETURN v = 1", "(u0.v = 1)"),  # no boolean, no whole float: as it was
    ("WITH [1.5, 2] AS l WITH l[0] AS x RETURN x = 1.5", "(Stage2.x = 1.5)"),
])
def test_other_comparisons_keep_their_form(q, frag):
    assert frag in _sql(q)


def test_a_node_is_not_a_number():
    assert " IN ('" not in _sql("MATCH (n:K) RETURN n = 1.0")


def test_a_with_s_where_reads_the_flag_too():
    sql = _sql("UNWIND [true, 1] AS v WITH v WHERE v = true RETURN v")
    where = sql.partition("WHERE ")[2].partition("\n")[0]
    assert "u0.v__b = 1 AND u0.v = 1" in where, sql
