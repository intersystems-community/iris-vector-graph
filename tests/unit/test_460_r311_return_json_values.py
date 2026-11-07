"""4.6.0 R311: a RETURNed list or map expression answers a Python list or dict, not its JSON text (fixes67).

`MATCH (n) RETURN [n.x, 'a']` answered the string '[1,"a"]', `keys({a: 1})` '["a"]' and `{a: n.x}` '{"a":1}'
through execute_cypher and REST; Bolt guessed the type back from any string that starts with `[` or `{`, which also
turned the string literal '{"a":1}' into a map. The translator now names each RETURN column whose value is a list or a
map (SQLQuery.json_value_columns), and the engine decodes exactly those. A stored property is not one of them: its
column is read through CY_TYPED as it is stored (R218, R307). Pinned live in
tests/e2e/test_460_r311_return_json_values_e2e.py.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _cols(cypher, params=None):
    t = translate_to_sql(parse_query(cypher), params or {})
    return {c.strip('"').lower() for c in t.json_value_columns}


M = "MATCH (n:L) "
P = "MATCH p = (n:L)-[:R]->(m:L) "


@pytest.mark.parametrize("q", [
    M + "RETURN [n.x, 'a'] AS v",
    "RETURN [1, 'a'] AS v",
    "RETURN [] AS v",
    M + "RETURN keys(n) AS v",
    "RETURN keys({a: 1}) AS v",
    M + "RETURN labels(n) AS v",
    P + "RETURN nodes(p) AS v",
    P + "RETURN relationships(p) AS v",
    M + "RETURN [x IN n.l | x * 2] AS v",
    M + "RETURN [x IN n.l WHERE x > 1] AS v",
    M + "RETURN [(n)-[:R]->(m) | m.x] AS v",
    M + "RETURN n.l[0..1] AS v",
    M + "RETURN n.l + [3] AS v",
    M + "RETURN [3] + n.l AS v",
    M + "RETURN [n.x] + [n.y] AS v",
    "RETURN range(1, 3) AS v",
    "RETURN split('a,b', ',') AS v",
    "RETURN tail([1, 2]) AS v",
    "RETURN reverse([1, 2]) AS v",
    M + "RETURN collect(n.x) AS v",
    M + "RETURN collect(DISTINCT n.x) AS v",
    "WITH [1, 2] AS l RETURN l AS v",
    M + "WITH collect(n.x) AS c RETURN c AS v",
    M + "WITH [n.x] AS l RETURN l AS v",
    M + "WITH [n.x] AS l WITH l AS k RETURN k AS v",
    "UNWIND [[1], [2]] AS v RETURN v",
    "RETURN CASE WHEN 1 = 1 THEN [1] ELSE [2] END AS v",
    M + "RETURN [[n.x], {k: n.y}] AS v",
])
def test_a_list_valued_return_item_is_named(q):
    assert "v" in _cols(q), q


@pytest.mark.parametrize("q", [
    M + "RETURN {a: n.x} AS v",
    M + "RETURN {a: n.x, b: [n.y]} AS v",
    "RETURN {} AS v",
    M + "RETURN n {.x, .y} AS v",
    M + "RETURN properties(n) AS v",
    "WITH {a: 1} AS m RETURN m AS v",
    M + "WITH {a: n.x} AS m RETURN m AS v",
])
def test_a_map_valued_return_item_is_named(q):
    assert "v" in _cols(q), q


@pytest.mark.parametrize("q, params", [
    ("RETURN $p AS v", {"p": [1, "a"]}),
    ("RETURN $p AS v", {"p": {"a": 1}}),
    ("WITH $p AS l RETURN l AS v", {"p": [1]}),
])
def test_a_list_or_map_parameter_is_named(q, params):
    assert "v" in _cols(q, params), q


@pytest.mark.parametrize("q, params", [
    (M + "RETURN n.l AS v", None),          # a stored property: CY_TYPED reads it as stored
    (M + "WITH n.l AS l RETURN l AS v", None),
    (M + "RETURN n.x AS v", None),
    ("RETURN size([1, 2]) AS v", None),
    ("RETURN head([[1], [2]]) AS v", None),  # an element of unknown kind
    ("RETURN reverse('ab') AS v", None),
    ("RETURN toString([1]) AS v", None),
    ("RETURN '{\"a\": 1}' AS v", None),       # a string that spells a map is a string
    ("RETURN '[1]' AS v", None),
    ("WITH '[1]' AS s RETURN s AS v", None),
    ("RETURN $p AS v", {"p": "[1]"}),
    ("WITH [1] AS l WITH 'x' AS l RETURN l AS v", None),  # rebound to a string
    ("UNWIND [1, 2] AS v RETURN v", None),
    (M + "RETURN count(n) AS v", None),
])
def test_another_return_item_is_not(q, params):
    assert "v" not in _cols(q, params), q


def _result(columns, rows):
    return SimpleNamespace(columns=columns, rows=rows)


def test_the_named_columns_are_decoded_and_no_other():
    from iris_vector_graph._engine.query import _decode_json_value_columns

    r = _result(["v", "s"], [['[1, "a", [2], {"k": null}]', '[1]'], [None, "x"], [[3], "{}"]])
    _decode_json_value_columns(r, SimpleNamespace(json_value_columns=["v"]))
    assert r.rows == [[[1, "a", [2], {"k": None}], "[1]"], [None, "x"], [[3], "{}"]]


def test_a_map_column_is_decoded_to_a_dict():
    from iris_vector_graph._engine.query import _decode_json_value_columns

    r = _result(["m"], [['{"a": 1, "b": [true, 1.5, "x"]}']])
    _decode_json_value_columns(r, SimpleNamespace(json_value_columns=['"m"']))
    assert r.rows == [[{"a": 1, "b": [True, 1.5, "x"]}]]


def test_text_that_is_not_json_is_left_and_logged(caplog):
    import logging

    from iris_vector_graph._engine.query import _decode_json_value_columns

    r = _result(["v"], [["not json"]])
    with caplog.at_level(logging.WARNING, logger="iris_vector_graph._engine.query"):
        _decode_json_value_columns(r, SimpleNamespace(json_value_columns=["v"]))
    assert r.rows == [["not json"]]
    assert any("not JSON" in rec.getMessage() for rec in caplog.records)


@pytest.mark.parametrize("v", ['{"a":1}', "[1]", "[1, 2", "1e5", "-2E3", " [1]", "x"])
def test_bolt_answers_a_string_as_the_string(v):
    """Bolt decoded any string starting with `[` or `{` and read one with an exponent as a float, so `RETURN
    '{"a":1}'` answered a map and `RETURN '1e5'` 100000.0; the engine now answers lists and maps typed."""
    from iris_vector_graph.bolt_server import BoltSession

    assert BoltSession._coerce_scalar(v) == v


@pytest.mark.parametrize("v", [[1, "a"], {"a": [1]}, 1, 2.5, True, None])
def test_bolt_passes_a_typed_value_on(v):
    from iris_vector_graph.bolt_server import BoltSession

    assert BoltSession._coerce_scalar(v) == v


@pytest.mark.parametrize("q", [
    "MATCH p = (a)-[:R]->(b) RETURN p AS v",
    "MATCH p = (a)-[:R*1..2]->(b) RETURN p AS v",
])
def test_a_path_is_named(q):
    """A path is its JSON text '{"nodes": [...], "rels": [...]}' in SQL, which Bolt decoded by guessing."""
    assert "v" in _cols(q), q


@pytest.mark.parametrize("q", [
    "MATCH p = (a)-[:R]->(b) RETURN *",
    "MATCH p = (a)-[:R]->(b) WITH p RETURN p",
])
def test_a_path_of_a_star_or_a_with_is_named(q):
    assert "p" in _cols(q), q
