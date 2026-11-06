"""4.6.0 R307: a stored list or map property reads back as the list or map, not its JSON text.

A node property is text in `rdf_props.val`: the Python API and Cypher both store a list (`[1, 2, 3]`) and the API a
map as JSON, and a relationship's qualifiers hold them as JSON. `RETURN x.p`, a WITH column, min/max and the
variable-length reads answered the text '[1, 2, 3]', where get_node() already answered the list. The readers decode
the stored text; the stored form is unchanged, so a string that spells a JSON list or map reads as one too
(docs/KNOWN_ISSUES.md). Found by tests/e2e/test_460_r307_value_matrix_e2e.py.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.prop_values import parse_prop_text, parse_rel_prop_text


@pytest.mark.parametrize("text,value", [
    ("[1, 2, 3]", [1, 2, 3]), ("[0.5, 1.0]", [0.5, 1.0]), ('[1, "a", 2.5, true]', [1, "a", 2.5, True]),
    ("[[1, 2], [3]]", [[1, 2], [3]]), ("[]", []), ('{"a": 1, "b": "x"}', {"a": 1, "b": "x"}),
])
@pytest.mark.parametrize("parse", [parse_prop_text, parse_rel_prop_text])
def test_json_list_and_map_text_is_the_value(parse, text, value):
    got = parse(text)
    assert got == value and type(got) is type(value)
    assert [type(e) for e in got] == [type(e) for e in value] if isinstance(value, list) else True


@pytest.mark.parametrize("text", ["[a]", "[1, 2", "{", "[1]x", "{'a': 1}", "Pat", "[", "1"])
def test_other_text_stays_text(text):
    assert parse_prop_text(text) == text


from iris_vector_graph.cypher.parser import parse_query  # noqa: E402
from iris_vector_graph.cypher.translator import translate_to_sql  # noqa: E402


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


def test_cy_json_num_answers_a_stored_list_or_map_as_its_json():
    """4.6.0 R307: collect(n.p), n {.p}, RETURN n (n_props) and properties(n) wrote a stored list as the JSON string
    "[1, 2, 3]": CY_JSON_NUM, which spells a stored value as JSON for them, now answers stored JSON list or map text
    that parses as itself (and NULL for any other string, which stays a JSON string)."""
    ddl = _udf("CY_JSON_NUM")
    assert "RETURNS VARCHAR(32000)" in ddl
    assert "%FromJSON(v)" in ddl and "Catch" in ddl
    body = ddl.split("LANGUAGE OBJECTSCRIPT", 1)[1]
    assert "'" not in body and "?" not in body


def test_a_relationship_list_is_embedded_as_json():
    sql = translate_to_sql(parse_query("MATCH (a)-[r:R]->(b) RETURN collect(r.p) AS c"), {}).sql
    assert "SUBSTRING(" in sql and "IN ('[', '{')" in sql, sql
