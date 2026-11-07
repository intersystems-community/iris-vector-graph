"""4.6.0 R273 (the OPTIONAL MATCH and collected-list WHERE cases), pinned against the translation (no container).

The answers are proven live in tests/e2e/test_460_r273_optional_match_e2e.py.
"""

from __future__ import annotations

import re

from iris_vector_graph.cypher import ast
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import _split_aggregate_with_where, _top_all, translate_to_sql


def _sql(q: str) -> str:
    return translate_to_sql(parse_query(q), {}).sql


def test_a_leading_optional_match_joins_its_relationship_as_a_match_does():
    sql = _sql("OPTIONAL MATCH (a:L)-[r]->() RETURN a.name, r.w")
    main = sql.split("UNION ALL", 1)[0]
    assert "LEFT OUTER JOIN" not in main and re.search(r"\nJOIN (\w+\.)?rdf_edges e", main), main
    assert "NOT EXISTS (SELECT 1 FROM (SELECT" in sql and "__om)" in sql


def test_an_optional_match_after_a_match_keeps_its_left_join():
    sql = _sql("MATCH (a:L) OPTIONAL MATCH (a)-[r]->() RETURN a.name, r.w")
    assert "LEFT OUTER JOIN" in sql


def test_an_ordered_optional_match_selects_from_the_ordered_query():
    sql = _sql("OPTIONAL MATCH (a:L)-[r]->() RETURN a.name, r.w ORDER BY r.w")
    assert sql.startswith("SELECT a_name, r_w FROM (SELECT TOP ALL "), sql
    # R286: the null row tests the match before its ORDER BY, SKIP and LIMIT (it was the ordered query, under a TOP ALL)
    tail = sql.partition(") __omq\nUNION ALL\n")[2]
    assert tail.startswith("SELECT NULL, NULL WHERE NOT EXISTS (SELECT 1 FROM (SELECT "), sql
    assert "ORDER BY" not in tail, sql


def test_a_label_alone_ordered_keeps_its_label_test():
    sql = _sql("OPTIONAL MATCH (a:L) RETURN a.name ORDER BY a.name")
    assert re.search(r"\) __omq\nUNION ALL\nSELECT NULL WHERE NOT EXISTS \(SELECT 1 FROM (\w+\.)?rdf_labels", sql), sql


def test_top_all():
    assert _top_all("SELECT a FROM t ORDER BY a") == "SELECT TOP ALL a FROM t ORDER BY a"
    assert _top_all("SELECT DISTINCT a FROM t") == "SELECT DISTINCT TOP ALL a FROM t"
    assert _top_all("SELECT TOP 2 * FROM (x) __ob ORDER BY s") == "SELECT TOP 2 * FROM (x) __ob ORDER BY s"


def test_a_with_where_over_a_collected_list_reads_a_later_stage():
    q = parse_query("UNWIND [1, 2, 3] AS v WITH collect(v) AS x WHERE x[2] = 3 RETURN size(x)")
    split = _split_aggregate_with_where(q)
    assert len(split.query_parts) == len(q.query_parts) + 1
    first, second = split.query_parts[0].with_clause, split.query_parts[1].with_clause
    assert first.where_clause is None and second.where_clause == q.query_parts[0].with_clause.where_clause
    assert [it.expression for it in second.items] == [ast.Variable("x")]
    sql = translate_to_sql(q, {}).sql
    # the element compared by type (4.6.0 R308: CY_JCMP over its JSON)
    assert "rdf_props" not in sql and "CY_JCMP(Graph_KG.CY_LIST_ELEM(Stage1.x, 2), 'j', '3', 'j', '=')" in sql, sql


def test_a_having_comparison_is_left_as_it_was():
    q = parse_query("UNWIND [1, 2, 3] AS v WITH count(v) AS c WHERE c = 3 RETURN c")
    assert _split_aggregate_with_where(q) is q
    assert "HAVING COUNT(u0.v) = 3" in translate_to_sql(q, {}).sql
