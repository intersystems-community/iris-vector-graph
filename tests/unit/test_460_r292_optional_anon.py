"""4.6.0 R292 (the OPTIONAL MATCH item), pinned against the translation (no container).

The answers are proven live in tests/e2e/test_460_r292_optional_anon_e2e.py.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _sql(q: str) -> str:
    return translate_to_sql(parse_query(q), {}).sql


@pytest.mark.parametrize("q", [
    "OPTIONAL MATCH (:L {name: 'b'})-[r:R]->() RETURN r",
    "OPTIONAL MATCH (:L {name: 'b'})<-[r:R]-(m) RETURN m.name",
    "OPTIONAL MATCH (:L)-[r:R]->(m) RETURN m.name",
    "OPTIONAL MATCH (:L {name: 'b'})-[r:R]->() RETURN r SKIP 0",
])
def test_an_anonymous_start_has_the_null_row_of_its_match(q):
    head, sep, tail = _sql(q).partition("\nUNION ALL\n")
    assert sep, q
    # the null row asks whether the match itself has a row, not whether the label has nodes
    assert tail.startswith("SELECT NULL"), tail
    assert "NOT EXISTS (SELECT 1 FROM (SELECT " in tail
    assert "WHERE label = ?" not in tail


def test_skip_1_and_limit_0_drop_it():
    for q in ("OPTIONAL MATCH (:L {name: 'b'})-[r:R]->() RETURN r SKIP 1",
              "OPTIONAL MATCH (:L {name: 'b'})-[r:R]->() RETURN r LIMIT 0"):
        assert "UNION ALL" not in _sql(q), q


def test_not_after_a_match():
    # (:L) is not the anchor of a leading OPTIONAL MATCH here: a MATCH has bound a; no null-row fallback is added
    sql = _sql("MATCH (a:K) OPTIONAL MATCH (:L {name: 'b'})-[r:R]->(a) RETURN a, r")
    assert "UNION ALL" not in sql


def test_a_plain_match_has_none():
    assert "UNION ALL" not in _sql("MATCH (:L {name: 'b'})-[r:R]->() RETURN r")
