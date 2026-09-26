"""An empty result still has the RETURN clause's columns (Delete6 [8], Match5 [11],
WithOrderBy4 [17]): several execution routes return columns=[] when no row comes back."""
from types import SimpleNamespace

from iris_vector_graph._engine.query import _fill_empty_columns, _return_item_name
from iris_vector_graph.cypher.parser import parse_query


def _empty():
    return SimpleNamespace(columns=[], rows=[], error=None)


def test_empty_result_gets_return_columns():
    r = _empty()
    _fill_empty_columns(r, parse_query("MATCH (n) RETURN n, n.x AS x, cOuNt( * ) LIMIT 0"))
    assert r.columns == ["n", "x", "cOuNt( * )"]


def test_rows_or_columns_present_left_alone():
    r = SimpleNamespace(columns=["a"], rows=[], error=None)
    _fill_empty_columns(r, parse_query("MATCH (n) RETURN n AS b"))
    assert r.columns == ["a"]
    r = SimpleNamespace(columns=[], rows=[[1]], error=None)
    _fill_empty_columns(r, parse_query("MATCH (n) RETURN n"))
    assert r.columns == []


def test_return_star_and_no_return_left_alone():
    r = _empty()
    _fill_empty_columns(r, parse_query("MATCH (n) RETURN *"))
    assert r.columns == []
    r = _empty()
    _fill_empty_columns(r, parse_query("CREATE (n)"))
    assert r.columns == []


def test_error_result_left_alone():
    r = SimpleNamespace(columns=[], rows=[], error="boom")
    _fill_empty_columns(r, parse_query("MATCH (n) RETURN n"))
    assert r.columns == []


def test_item_name_prefers_source_text():
    item = parse_query("RETURN toInteger( 1 )").return_clause.items[0]
    assert _return_item_name(item) == "toInteger( 1 )"
