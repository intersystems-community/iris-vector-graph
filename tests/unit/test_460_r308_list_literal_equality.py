"""4.6.0 R308, offline: a stored list or map compared with a literal one reads both spellings of the stored JSON.

A relationship's list property comes back from JSON_VALUE as IRIS writes JSON, compact (`[1,"a"]`); a node's list
property stored by a Cypher SET is Python's spelling (`[1, "a"]`). The literal was rendered the second way only, so
`MATCH (a)-[r*1..2]->(b) WHERE all(x IN r WHERE x.l = [1, 'a'])` and `WHERE r.l = [1, 'a']` matched no relationship
(tests/e2e/test_460_r308_cypher_differential_e2e.py, the varlen_props forms of seeds 2 and 3). The value is compared
with both spellings; a literal holding a string IRIS may escape differently (a quote, a backslash, a slash, non-ASCII)
or a null keeps the plain comparison.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _sql(q):
    s = translate_to_sql(parse_query(q), {}).sql
    return s if isinstance(s, str) else "\n".join(s)


@pytest.mark.parametrize("q, spellings", [
    ("MATCH (a:L)-[r:R]->(b) WHERE r.l = [1, 'a'] RETURN a.name", ("'[1,\"a\"]'", "'[1, \"a\"]'")),
    ("MATCH (a:L)-[r:R*1..2]->(b) WHERE all(x IN r WHERE x.m = {k: [1]}) RETURN b.name",
     ("'{\"k\":[1]}'", "'{\"k\": [1]}'")),
    ("MATCH (a:L) WHERE a.tags = ['p', 'q'] RETURN a.name", ("'[\"p\",\"q\"]'", "'[\"p\", \"q\"]'")),
    ("MATCH (a:L) WHERE a.l = [1.5, 2.0] RETURN a.name", ("'[1.5,2.0]'", "'[1.5, 2.0]'")),
])
def test_a_list_or_map_literal_matches_either_spelling(q, spellings):
    sql = _sql(q)
    assert all(s in sql for s in spellings), sql
    assert " IN (" in sql, sql


def test_not_equal_reads_both_spellings_too():
    sql = _sql("MATCH (a:L)-[r:R]->(b) WHERE r.l <> [1] RETURN a.name")
    assert "NOT IN ('[1]', '[1.0]')" in sql, sql


def test_a_slash_or_non_ascii_string_is_spelled_as_iris_writes_it():
    """Measured on 2026.3: JSON_VALUE answers ["a/b"] and ["é"] unescaped (R307's _stored_json_comparison spells a
    stored property's list; json.dumps escaped é as \\u00e9)."""
    sql = _sql("MATCH (a:L)-[r:R]->(b) WHERE r.l = ['a/b', 'é'] RETURN a.name")
    assert "'[\"a/b\",\"é\"]'" in sql and "'[\"a/b\", \"é\"]'" in sql, sql


def test_a_whole_number_in_the_literal_matches_its_twin():
    """openCypher: [2] = [2.0]. A stored list holds whichever was written."""
    sql = _sql("MATCH (a:L)-[r:R]->(b) WHERE r.l = [2, 'a'] RETURN a.name")
    for text in ("'[2,\"a\"]'", "'[2.0,\"a\"]'", "'[2, \"a\"]'", "'[2.0, \"a\"]'"):
        assert text in sql, sql
