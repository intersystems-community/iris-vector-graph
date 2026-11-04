"""4.6.0 R263, R267 (the CALL subquery case), offline: the translations and the handler tail.

The live answers, under both driver versions and on the native and SQL paths, are in
tests/e2e/test_460_r263_read_shapes_e2e.py. Here:
- a CALL subquery whose body is MATCH/WHERE and that returns its own variables is the same MATCH in the outer query,
  so its node is a node (it was SubQuery0.n, a column that does not exist: -29);
- `x IN [1, null]` is a predicate a WHERE can take (`(CASE ... END) = 1`; the bare CASE failed -14), and a RETURN of
  it binds its markers in the SELECT list (they were bound after the WHERE's: "Incorrect number of parameters");
- a sum carried through WITH is R250's typed sum, read through CY_TYPED where it is returned, not decoded as an
  integer when whole;
- an OPTIONAL MATCH that filters more than a label answers its null row when the match itself has no row;
- a system procedure's rows are cut by the query's ORDER BY, SKIP and LIMIT.
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql
from iris_vector_graph.result import IVGResult


def _q(query: str, params=None):
    return translate_to_sql(parse_query(query), dict(params or {}))


def _markers_match(r):
    sql = r.sql if isinstance(r.sql, str) else r.sql[-1]
    params = r.parameters[0] if isinstance(r.sql, str) else r.parameters[-1]
    return sql.count("?") == len(params), (sql, params)


def test_a_plain_call_subquery_is_the_outer_match():
    r = _q("CALL { MATCH (n:L) RETURN n } RETURN n.name")
    assert "SubQuery" not in r.sql, r.sql
    assert "n_name" in r.sql


def test_a_correlated_plain_call_subquery_is_the_outer_match():
    r = _q("MATCH (y:L {id: 'a'}) CALL { WITH y MATCH (y)-[:K]->(z) RETURN z } RETURN z.name")
    assert "SubQuery" not in r.sql and "LATERAL" not in r.sql, r.sql


def test_a_call_subquery_with_an_aggregate_is_left_as_it_was():
    r = _q("CALL { MATCH (n:L) RETURN count(n) AS c } RETURN c")
    assert "SubQuery0" in r.sql, r.sql


def test_x_in_a_list_holding_null_is_a_where_predicate():
    r = _q("UNWIND [1, 2] AS x WITH x WHERE x IN [1, null] RETURN x")
    assert re.search(r"WHERE \(\(CASE WHEN u0\.x IN \(\?\) THEN 1 ELSE NULL END\) = 1\)", r.sql), r.sql
    assert _markers_match(r)


def test_a_stored_value_in_a_list_holding_null_returned_binds_its_markers_in_order():
    r = _q("MATCH (n:L) RETURN n.name, n.g IN [5.0, null] ORDER BY n.name")
    ok, why = _markers_match(r)
    assert ok, why
    sql = r.sql
    # the SELECT list's IN marker binds before the FROM's: the 5.0 comes first among the markers after the label's
    head = sql.split("\nFROM ")[0]
    assert "IN (?)" in head, head
    assert r.parameters[0][0] == 5.0, r.parameters


def test_a_sum_through_with_is_typed_and_read_through_cy_typed():
    # a list mixing integers and floats: only the row knows the sum's type (an UNWIND of floats alone is statically
    # float since R273, below)
    r = _q("UNWIND [1, 2.5] AS v WITH sum(v) AS s RETURN s, toString(s)")
    assert "CASE WHEN MAX(Graph_KG.CY_IS_FLOAT(u0.v)) = 1 THEN CAST(SUM(u0.v) AS DOUBLE)" in r.sql, r.sql
    assert "CY_TYPED(Stage1.s) AS s" in r.sql, r.sql
    assert "CY_TOSTRING(Stage1.s)" in r.sql, r.sql
    assert "s" in r.typed_value_columns
    assert "s" not in (r.sum_expr_columns or []) and "s" not in (r.int_expr_columns or [])


def test_a_sum_of_an_unwind_of_floats_is_a_float_column():
    """4.6.0 R273: an UNWIND of float literals is statically float, so its sum is (4.0 for [1.5, 2.5])."""
    r = _q("UNWIND [1.5, 2.5] AS v WITH sum(v) AS s RETURN s, toString(s)")
    assert "s" in r.float_expr_columns
    assert "CY_TOSTRING(CAST(Stage1.s AS DOUBLE))" in r.sql, r.sql


def test_a_sum_of_stored_values_through_with_is_not_decoded_as_an_integer():
    r = _q("MATCH (n:L) WITH sum(n.g) AS s RETURN s")
    assert "s" in r.typed_value_columns
    assert "s" not in (r.int_expr_columns or []) and "s" not in (r.sum_expr_columns or [])


def test_an_optional_match_that_filters_tests_the_match_for_its_null_row():
    r = _q("OPTIONAL MATCH (n:L {id: 'nope'}) RETURN n.name")
    union = r.sql.split("UNION ALL", 1)[1]
    assert "NOT EXISTS (SELECT 1 FROM (SELECT" in union and "__om)" in union, union
    assert _markers_match(r)


def test_an_optional_match_of_a_label_alone_keeps_the_label_test():
    r = _q("OPTIONAL MATCH (n:L) RETURN n.name")
    union = r.sql.split("UNION ALL", 1)[1]
    assert "__om" not in union and "rdf_labels" in union, union


def _proc_result():
    return IVGResult(columns=["label"], rows=[["b"], ["a"], [None], ["c"]])


@pytest.mark.parametrize(
    "query,want",
    [
        ("CALL db.labels() YIELD label RETURN label LIMIT 1", [["b"]]),
        ("CALL db.labels() YIELD label RETURN label SKIP 1 LIMIT 2", [["a"], [None]]),
        ("CALL db.labels() YIELD label RETURN label ORDER BY label", [["a"], ["b"], ["c"], [None]]),
        ("CALL db.labels() YIELD label RETURN label ORDER BY label DESC LIMIT 2", [[None], ["c"]]),
        ("CALL db.labels() YIELD label RETURN label LIMIT $n", [["b"], ["a"]]),
        ("CALL db.labels() YIELD label RETURN label", [["b"], ["a"], [None], ["c"]]),
    ],
)
def test_a_handler_s_rows_are_cut_by_the_query_s_tail(query, want):
    from iris_vector_graph._engine.query import _apply_handler_tail

    got = _apply_handler_tail(_proc_result(), parse_query(query), {"n": 2})
    assert got.rows == want
