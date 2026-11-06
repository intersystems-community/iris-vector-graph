"""4.6.0 R307: list equality and list elements (the decoding of a stored list or map itself is fixes62's, R307).

- `n.p = $v` with a list compared the stored text '[1, 2, 3]' (json.dumps) with the bound '[1,2,3]', and a list literal
  its own spelling with a relationship's '[1,2,3]': no list ever equalled itself.
- head()/last()/x[i] of a stored list answered text ('1') or a Decimal: the element reads CY_LIST_ELEM's marked JSON.
Found by tests/e2e/test_460_r307_value_matrix_e2e.py.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


@pytest.mark.parametrize("q,params,spellings", [
    ("MATCH (n:L) WHERE n.p = $v RETURN n.id", {"v": [1, 2, 3]}, ("'[1, 2, 3]'", "'[1,2,3]'")),
    ("MATCH (n:L) WHERE n.p = [1, 2.5, 'a'] RETURN n.id", {}, ("'[1, 2.5, \"a\"]'", "'[1,2.5,\"a\"]'")),
    ("MATCH (a)-[r:R]->(b) WHERE r.p = [1, 2, 3] RETURN b.id", {}, ("'[1, 2, 3]'", "'[1,2,3]'")),
    ("MATCH (n:L) WHERE n.p = $v RETURN n.id", {"v": {"a": 1, "b": "x"}}, ("'{\"a\": 1, \"b\": \"x\"}'",)),
])
def test_a_list_or_map_equality_matches_every_json_spelling(q, params, spellings):
    """4.6.0 R307: `n.p = $v` with a list compared the stored text '[1, 2, 3]' (json.dumps) with the bound '[1,2,3]',
    and a list literal its own spelling with a relationship's '[1,2,3]': no list ever equalled itself."""
    sql = translate_to_sql(parse_query(q), params).sql
    assert all(s in sql for s in spellings), sql


def _udf(name):
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "iris_vector_graph" / "schema.py").read_text()
    return next(x for x in src.splitlines() if f"FUNCTION Graph_KG.{name}(" in x)


@pytest.mark.parametrize("item", ["head(n.p)", "last(n.p)", "n.p[0]", "n.p[-1]"])
def test_an_element_of_a_stored_list_keeps_its_type(item):
    """4.6.0 R307: head(n.p) of a stored [1, 2, 3] answered '1' and n.p[0] of [0.5, 1.0] Decimal('0.5'): the element
    reads CY_LIST_ELEM's marked JSON, as an element of a row's list does (R227)."""
    sq = translate_to_sql(parse_query(f"MATCH (n:L) RETURN {item} AS e"), {})
    assert "CY_LIST_ELEM(" in sq.sql and "e" in [c.strip('"').lower() for c in sq.typed_value_columns], sq.sql


def test_cy_list_elem_marks_lists_and_keeps_a_floats_point():
    """4.6.0 R307: last([0.5, 1.0]) answered 1 (%Get lost the point) and head([[1, 2], [3]]) the text '[1,2]'."""
    body = _udf("CY_LIST_ELEM").split("LANGUAGE OBJECTSCRIPT", 1)[1]
    assert 'If $IsObject(v) { Quit $Char(1) _ "J" _ v.%ToJSON() }' in body
    assert 'If t = "number" { Quit $Char(1) _ "J" _ a.%Get(i, , "json") }' in body
