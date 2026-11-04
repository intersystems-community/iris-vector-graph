"""4.6.0 R286 (the OPTIONAL MATCH items), pinned against the translation (no container).

The answers are proven live in tests/e2e/test_460_r286_optional_skip_e2e.py.
"""

from __future__ import annotations

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

M = "OPTIONAL MATCH (n:L {k: 999}) RETURN n.name"


def _sql(q: str, params=None) -> str:
    return translate_to_sql(parse_query(q), dict(params or {})).sql


def test_a_skip_of_one_or_more_drops_the_null_row():
    for q in (M + " SKIP 1", M + " SKIP $s", M + " LIMIT 0", "OPTIONAL MATCH (n:L) RETURN n SKIP 2"):
        assert "UNION ALL" not in _sql(q, {"s": 3}), q


def test_skip_0_keeps_the_null_row_after_the_paged_select():
    sql = _sql(M + " SKIP 0")
    head, _, tail = sql.partition("\nUNION ALL\n")
    assert head.startswith("SELECT n_name FROM (SELECT TOP ALL "), head
    assert "OFFSET 0" in head
    # the null row asks whether the match has a row, not whether a page of it does
    assert tail.startswith("SELECT NULL WHERE NOT EXISTS (SELECT 1 FROM (SELECT ")
    assert "OFFSET" not in tail and "FETCH FIRST" not in tail


def test_a_limit_without_order_by_tests_the_match_not_the_label():
    tail = _sql(M + " LIMIT 1").partition("\nUNION ALL\n")[2]
    assert "NOT EXISTS (SELECT 1 FROM (SELECT " in tail
    assert "FETCH FIRST" not in tail
    assert "WHERE label = ?" not in tail


def test_the_anchor_s_property_map_is_required():
    sql = _sql(M)
    assert "IS NULL OR" not in sql.partition("\nUNION ALL\n")[0]


def test_an_optional_match_after_a_match_keeps_its_optional_property():
    sql = _sql("MATCH (a:L) OPTIONAL MATCH (a)-[:R]->(b:L {k: 1}) RETURN a, b")
    assert ".s IS NULL OR" in sql
