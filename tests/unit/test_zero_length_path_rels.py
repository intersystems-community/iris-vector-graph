"""A zero-length path has no relationships: its value's "rels" is [], not [null].

IRIS renders an empty ``JSON_ARRAY()`` as ``[null]`` (Match6 [1], Merge1 [13])."""
import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _sql(q):
    s = translate_to_sql(parse_query(q), {}).sql
    return s if isinstance(s, str) else " ".join(s)


@pytest.mark.parametrize("q", ["MATCH p = (a) RETURN p", "MATCH p = (a) RETURN *"])
def test_zero_length_path_has_empty_rels(q):
    s = _sql(q)
    assert "JSON_ARRAY()" not in s
    assert "'[]'" in s or "\"rels\":[]" in s


def test_zero_length_relationships_function_is_empty():
    assert "JSON_ARRAY()" not in _sql("MATCH p = (a) RETURN relationships(p) AS r")


def test_one_hop_path_keeps_its_rel():
    assert "JSON_ARRAY()" not in _sql("MATCH p = (a)-[:T]->(b) RETURN p")
    assert "\"rels\":' || JSON_ARRAY(" in _sql("MATCH p = (a)-[:T]->(b) RETURN p")
