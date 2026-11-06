"""4.6.0 R310 follow-up (fixes66): a string literal holding a symbol or a combining mark is not an argument of
JSON_ARRAY.

On 2026.3 `SELECT JSON_ARRAY('☕')` fails at Query Open with `<PROPERTY DOES NOT EXIST>%C0o+8^%sqlcq...
*i%c☕1`: IRIS's cached query names a property after the literal's text, and a character that is not a letter or a
digit past U+00FF (a symbol, punctuation, a combining mark: measured over the whole BMP, 4276 code points, every one
Unicode category M, P, S, Z or C) is no valid name. The same literal bound as a parameter, built from CHAR(n), or
outside JSON_ARRAY reads fine. So `WITH ['café ☕ 日本', duration('P1D')] AS l` failed, and `[n, '☕']`,
`[n.x, 'a' + '☕']`, `keys({`☕`: 1})`. Such a literal is now read through JSON_VALUE of its JSON escape, which is
ASCII. Pinned live in tests/e2e/test_460_r310_json_array_symbols_e2e.py.
"""

from __future__ import annotations

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import _json_array_safe, translate_to_sql


def _sql(cypher):
    sql = translate_to_sql(parse_query(cypher)).sql
    return "\n;\n".join(sql) if isinstance(sql, list) else sql


def _no_symbol_inside_json_array(sql):
    for chunk in sql.split("JSON_ARRAY(")[1:]:
        assert "☕" not in chunk.split(")")[0], sql


def test_a_symbol_literal_is_read_through_json_value():
    assert _json_array_safe("'café ☕ 日本'") == "Graph_KG.JSON_VALUE('{\"v\": \"caf\\u00e9 \\u2615 \\u65e5\\u672c\"}', '$.v')"


def test_a_literal_of_letters_stays_a_literal():
    assert _json_array_safe("'café 日本 it''s'") == "'café 日本 it''s'"


def test_a_quote_and_a_symbol_round_trip():
    out = _json_array_safe("'it''s ☕'")
    assert out == "Graph_KG.JSON_VALUE('{\"v\": \"it''s \\u2615\"}', '$.v')", out


def test_a_with_list_of_a_symbol_and_a_duration():
    _no_symbol_inside_json_array(_sql("WITH ['café ☕ 日本', duration('P1D')] AS l RETURN l"))


def test_a_mixed_graph_list():
    _no_symbol_inside_json_array(_sql("MATCH (n:L) RETURN [n, '☕'] AS l"))


def test_an_expression_inside_a_list():
    _no_symbol_inside_json_array(_sql("MATCH (n:L) RETURN [n.x, 'a' + '☕'] AS l"))


def test_keys_of_a_map_literal():
    _no_symbol_inside_json_array(_sql("RETURN keys({a: 1, `☕`: 2}) AS k"))
