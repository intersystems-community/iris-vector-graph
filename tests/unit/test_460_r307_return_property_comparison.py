"""4.6.0 R307: a range comparison of two properties in RETURN or WITH compares by Cypher orderability, as WHERE does.

`RETURN a.p < b.p` compared CAST(val AS DOUBLE) on both sides, so two strings (each 0) were never less than each
other and two durations answered false where openCypher answers null (CIP2015-08-06-date-time). `WHERE a.p < b.p`
already calls CY_CMP, which ranks values by their sort key and answers null across kinds and between durations.
Found by tests/e2e/test_460_r307_value_matrix_e2e.py::test_durations_do_not_compare.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


@pytest.mark.parametrize("q", [
    "MATCH (a:L), (b:L) RETURN a.p < b.p",
    "MATCH (a:L), (b:L) RETURN a.p >= b.p AS ge",
    "MATCH (a:L), (b:L) WITH a.p > b.p AS gt RETURN gt",
])
def test_two_properties_compare_by_cy_cmp(q):
    sql = translate_to_sql(parse_query(q), {}).sql
    assert "CY_CMP(" in sql, sql
    assert "AS DOUBLE) <" not in sql and "AS DOUBLE) >" not in sql, sql


def test_where_is_unchanged():
    sql = translate_to_sql(parse_query("MATCH (a:L), (b:L) WHERE a.p < b.p RETURN a.id"), {}).sql
    assert "CY_CMP(" in sql


def test_min_max_of_a_property_reads_its_booleans():
    """4.6.0 R307: max(n.flag) answered the text 'true'; the column is read as RETURN n.flag reads it."""
    sq = translate_to_sql(parse_query("MATCH (n:L) RETURN min(n.p) AS lo, max(n.p) AS hi"), {})
    assert {"lo", "hi"} <= {c.strip('"').lower() for c in sq.prop_text_expr_columns}, sq.prop_text_expr_columns


def test_a_returned_comparison_is_null_when_cy_cmp_is():
    """IRIS's NOT of a comparison with null is true, so the 3VL CASE answered false for two durations (4.6.0 R307)."""
    sql = translate_to_sql(parse_query("MATCH (a:L), (b:L) RETURN a.p < b.p"), {}).sql
    assert "CASE WHEN Graph_KG.CY_CMP(" in sql and "IS NULL THEN NULL" in sql, sql


@pytest.mark.parametrize("fn", ["min", "max"])
def test_min_max_of_a_relationship_property_is_not_rounded(fn):
    """4.6.0 R307: min(r.w) of 0.95 answered 1: the column was typed integer (a bare property's default) over
    JSON_VALUE's text. It reads CY_REL_PROP's text, decoded as RETURN r.w is, with no integer typing."""
    sq = translate_to_sql(parse_query(f"MATCH (a)-[r:R]->(b) RETURN {fn}(r.w) AS m"), {})
    assert f"{fn.upper()}(" in sq.sql and "CY_REL_PROP(" in sq.sql, sq.sql
    assert "m" not in [c.strip('"').lower() for c in sq.int_expr_columns], sq.int_expr_columns
    assert 0 in sq.rel_prop_text_columns, sq.rel_prop_text_columns


def test_arithmetic_reads_a_relationship_property_as_return_does():
    """4.6.0 R307: `r.w + 1` of create_edge's 1.0 answered 2: JSON_VALUE spells the JSON number 1.0 as 1."""
    sql = translate_to_sql(parse_query("MATCH (a)-[r:R]->(b) RETURN r.w + 1"), {}).sql
    assert "CY_ARITH(CASE WHEN e2.qualifiers IS NULL THEN NULL ELSE Graph_KG.CY_REL_PROP(" in sql, sql
