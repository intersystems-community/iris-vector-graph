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


@pytest.mark.parametrize("q, col", [
    ("WITH [1, 2, 3] AS list UNWIND list AS x RETURN *", "list"),
    ("WITH {a: 1} AS m, 1 AS x RETURN *", "m"),
    ("MATCH p = (:S)-[*0..3]->(x) WITH tail(nodes(p)) AS nodes RETURN nodes", "nodes"),
    ("MATCH p = ()-[*]->() WITH count(*) AS count, p AS p RETURN nodes(p) AS nodes", "nodes"),
])
def test_a_list_in_scope_of_a_star_or_a_path_node_list_is_named(q, col):
    assert col in _cols(q), q


def test_collect_of_a_list_literal_is_unwound_as_lists():
    """`WITH collect([0, 0.0]) AS numbers UNWIND numbers AS arr WITH arr[0] AS e` failed -149 in CY_LIST_ELEM once
    collect() listed the lists themselves: the UNWIND reads such a list as a list of lists (R273's null end)."""
    sql = translate_to_sql(parse_query(
        "WITH collect([0, 0.0]) AS numbers UNWIND numbers AS arr WITH arr[0] AS e RETURN e"), {}).sql
    assert "LIST_CONCAT(Stage1.numbers, '[null]')" in sql, sql


@pytest.mark.parametrize("q", [
    "MATCH (a)-[v*1..1]->(b) RETURN v",
    "MATCH (a:A) MATCH (a)-[v*2]->() RETURN v",
])
def test_a_variable_length_relationship_list_is_named(q):
    assert "v" in _cols(q), q


@pytest.mark.parametrize("q", ["RETURN sqrt(12.96) AS v", "RETURN exp(1) AS v", "RETURN log10(100) AS v"])
def test_a_mathematical_function_is_a_float_column(q):
    """TCK Mathematical13 [1]: 5.4.0 answered sqrt(12.96) as Decimal('3.600000000000000000')."""
    t = translate_to_sql(parse_query(q), {})
    assert "v" in {c.strip('"').lower() for c in t.float_expr_columns}, t.sql


@pytest.mark.parametrize("q", [
    "RETURN ([[1], [2, 3], [4, 5]] + [5, [6, 7], [8, 9], 10])[2] AS v",  # TCK Precedence3 [2]
    "UNWIND [[1], [2], [2, 1]] AS x RETURN max(x) AS v",                   # TCK Aggregation2 [9]
    "UNWIND [[1], [2], [2, 1]] AS x RETURN min(x) AS v",
    "MATCH (n) RETURN tail(tail(n.array)) AS v",                            # TCK List9 [1]
])
def test_a_list_element_or_extreme_that_is_a_list_is_named(q):
    assert "v" in _cols(q), q


@pytest.mark.parametrize("q", ["RETURN 'hello'[1..3] AS v", "RETURN [1, 2][0] AS v", "RETURN reverse('ab') AS v"])
def test_a_string_slice_or_a_scalar_element_is_not(q):
    assert "v" not in _cols(q), q


def test_a_slice_reads_each_element_as_its_json():
    """`['a', [1], {k: 1}, true][0..4]` answered ['a', '[1]', '{"k":1}', '1']: JSON_ARRAYAGG of JSON_TABLE's text."""
    sql = translate_to_sql(parse_query("RETURN ['a', [1], {k: 1}, true][0..4] AS v"), {}).sql
    assert "CY_LIST_ELEM(" in sql and "JSON_ARRAYAGG(elem)" not in sql, sql


@pytest.mark.parametrize("v", ['{"a":1}', "[1]", "{}", "[]"])
def test_graphql_answers_a_string_as_the_string(v):
    """The GraphQL cypher field decoded any string that starts and ends with `[]` or `{}`, as Bolt did."""
    from iris_vector_graph.gql.resolvers import serialize_value

    assert serialize_value(v) == v
    assert serialize_value([v, [1, {"k": v}]]) == [v, [1, {"k": v}]]


@pytest.mark.parametrize("q, params", [
    ("RETURN $p[1] AS v", {"p": ["a", [1]]}),
    ("RETURN $m.k AS v", {"m": {"k": [2]}}),
    ("RETURN $m['k'] AS v", {"m": {"k": {"z": 1}}}),
    ("RETURN $p[0..2] AS v", {"p": ["a", [1]]}),
])
def test_an_element_slice_or_key_of_a_parameter_is_named_and_bound(q, params):
    """`$p[1]` failed -23 (read as a node's property) and `$m.k` bound one parameter for its two `?`: the query knows
    the value, so it binds it."""
    t = translate_to_sql(parse_query(q), params)
    assert "v" in {c.strip('"').lower() for c in t.json_value_columns}, t.sql
    assert t.sql.count("?") == len(t.parameters[0]), (t.sql, t.parameters)
    assert "dp" not in t.sql, t.sql


def test_a_boolean_parameter_is_a_boolean_column():
    t = translate_to_sql(parse_query("RETURN $b AS v, $p[0] AS w"), {"b": True, "p": [False]})
    assert {c.strip('"').lower() for c in t.bool_expr_columns} >= {"v", "w"}, t.sql


def test_a_relationship_ends_property_reads_through_cy_typed():
    """TCK Merge5 [11] under 5.4.1: `startNode(r).id` answered the text '2' for 2."""
    t = translate_to_sql(parse_query("MATCH (a)-[r:KNOWS]-(b) RETURN startNode(r).id AS s, endNode(r).id AS e"), {})
    assert {"s", "e"} <= {c.strip('"').lower() for c in t.typed_value_columns}, t.sql


def test_a_star_reads_an_unwound_element_through_cy_json_typed():
    """TCK Unwind1 [11] under 5.4.1: `RETURN *` answered the unwound x as '1'."""
    t = translate_to_sql(parse_query("WITH [1, 2, 3] AS list UNWIND list AS x RETURN *"), {})
    assert "CY_JSON_TYPED(u0.x) AS x" in t.sql and "x" in t.typed_value_columns, t.sql


@pytest.mark.parametrize("raw, want", [
    ('[1, "a", [2], {"k": null}]', [1, "a", [2], {"k": None}]),
    ('{"a": [true, 1.5]}', {"a": [True, 1.5]}),
    ("[NaN]", "nan-list"),
    ([1], [1]),
    ({"a": 1}, {"a": 1}),
    (None, None),
])
def test_decode_json_value_reads_a_marked_columns_json(raw, want):
    """prop_values.decode_json_value: the one decode of a list-, map- or path-valued column (the typed-value contract
    in prop_values.py). Not decode_json_list_map, the stored-text rule: a marked column is never a string, so it needs
    no spelling test, and Cypher's NaN is a float."""
    import math

    from iris_vector_graph.prop_values import decode_json_value

    got = decode_json_value(raw)
    if want == "nan-list":
        assert isinstance(got, list) and len(got) == 1 and math.isnan(got[0])
    else:
        assert got == want
