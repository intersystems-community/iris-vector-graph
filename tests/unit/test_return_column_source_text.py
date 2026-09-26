"""An un-aliased RETURN item's column is named by its source text, as openCypher requires.

The TCK headers are the query text of each projection (``cOuNt( * )``, ``$age``,
``a.name='Andres'``), not a re-rendering of the parsed expression.
"""
import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _names(query: str, params=None) -> list[str]:
    result = translate_to_sql(parse_query(query), params or {})
    safe = re.findall(r" AS (\w+)", result.sql)
    return [result.column_name_map.get(a, a) for a in safe]


@pytest.mark.parametrize(
    "query, column",
    [
        ("MATCH (n) RETURN cOuNt( * )", "cOuNt( * )"),
        ("MATCH (a) RETURN a.name='Andres'", "a.name='Andres'"),
        ("MATCH (a) RETURN toInteger(a.x)", "toInteger(a.x)"),
        ("MATCH (a) RETURN a.x   +  1", "a.x   +  1"),
    ],
)
def test_unaliased_column_is_source_text(query, column):
    assert column in _names(query)


def test_parameter_column_keeps_dollar():
    assert "$age" in _names("RETURN $age", {"age": 3})


def test_null_literal_column_is_null():
    assert "null" in _names("RETURN null")


def test_user_alias_wins():
    assert "x" in _names("MATCH (n) RETURN count(*) AS x")


def test_property_column_unchanged():
    assert "a.name" in _names("MATCH (a) RETURN a.name")


def test_source_text_on_return_item():
    q = parse_query("MATCH (n) RETURN cOuNt( * ) , n.x AS y")
    items = q.return_clause.items if hasattr(q, "return_clause") and q.return_clause else None
    if items is None:
        pytest.skip("AST shape has no top-level return_clause")
    assert items[0].source_text == "cOuNt( * )"
