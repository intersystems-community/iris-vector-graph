"""4.6.0 R308, offline: a variable-length property map SQL does not check (a float in a list, a map) is checked by the
engine, path by path, and the paths go back into the query's own SQL.

On the release head (7a796f85) `_vlp_rel_prop_filters` answered None for `{l: [1.5]}` and `{m: {k: 1}}`, and the
pattern went to the engine's labeled route, which read the start by its labels alone (every :V, not the one named
`bob`), kept a far end when any relationship into it held the map (not every relationship of the path), and answered
the target's columns only. Now the translator marks such a hop (`unchecked_props`); the engine walks the trails, checks
each relationship's typed properties against the map by openCypher equality (`checked_trails`), and translates the
query again with those paths as `VariableLength.literal_paths` (`literal_checked`), as shortestPath's projection does:
the statement holds the start to its map, joins the target, and projects the RETURN. The live answers, worked by hand,
are tests/e2e/test_460_r308_cypher_differential_e2e.py's hand cases vl_map_*.
"""

from __future__ import annotations

import copy
import json

import pytest

from iris_vector_graph._engine.query import _vl_relationships, checked_trails, cypher_equal
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

FLOAT_LIST = "MATCH (a:V {name: 'bob'})-[r:R*1..2 {l: [1.5]}]->(b) RETURN a.name, b.name"
MAP = "MATCH (a:V {name: 'ann'})-[r:R*1..2 {m: {k: 1}}]->(b) RETURN a.name, b.name"


def _sql(sq):
    return sq.sql if isinstance(sq.sql, str) else "\n".join(sq.sql)


@pytest.mark.parametrize("q", [FLOAT_LIST, MAP, "MATCH (a:V)-[*1..2 {m: {k: [1]}}]-(b) RETURN b.name"])
def test_a_map_sql_does_not_check_is_left_to_the_checked_route(q):
    sq = translate_to_sql(parse_query(q), {})
    assert sq.var_length_paths, _sql(sq)
    vl0 = sq.var_length_paths[0]
    assert vl0["unchecked_props"] is True
    # parse_query is cached: the engine finds the hop in the same tree by its identity
    assert vl0["rel_id"] == id(_vl_relationships(parse_query(q))[0])


@pytest.mark.parametrize("q", [
    "MATCH (a:V {name: 'bob'})-[r:R*1..2 {l: [1]}]->(b) RETURN b.name",
    "MATCH (a:V {name: 'bob'})-[r:R*1..2 {w: 1.5}]->(b) RETURN b.name",
])
def test_a_map_sql_checks_expands_in_sql(q):
    sq = translate_to_sql(parse_query(q), {})
    assert not sq.var_length_paths
    assert "CY_VLP_PATHS(" in _sql(sq)


def _with_paths(q, paths):
    parsed = copy.deepcopy(parse_query(q))
    rel = _vl_relationships(parsed)[0]
    rel.variable_length.literal_paths = json.dumps(paths)
    rel.variable_length.literal_checked = True
    return translate_to_sql(parsed, {})


def test_checked_paths_are_read_by_the_statement_from_the_start_it_names():
    path = {"s": "cat", "g": "", "t": "dan", "l": 1, "n": '["dan"]', "y": '["R"]',
            "r": '[{"type":"R","props":{"l":[1.5]}}]', "k": "\x02cat\x01R\x01dan\x02"}
    sq = _with_paths(FLOAT_LIST, [path])
    sql = _sql(sq)
    assert not sq.var_length_paths, sql
    assert "CY_VLP_PATHS(" not in sql
    assert "PATH '$.s'" in sql and "PATH '$.g'" in sql, sql
    # the start's own map is the statement's: name = 'bob' (a literal or a bound parameter)
    params = sq.parameters[0] if sq.parameters else []
    assert "'bob'" in sql or "bob" in params, (sql, params)
    # the map is not checked again in SQL: the engine checked it
    assert "$.props.l" not in sql, sql


# --- checked_trails: the walk and the check, over edges given in memory ---

EDGES = [
    # (edge_id, s, p, o, qualifiers, graph)
    (1, "ann", "R", "bob", '{"l": [1], "w": 1}', ""),
    (2, "bob", "R", "cat", '{"l": [1], "w": "2"}', ""),
    (3, "cat", "R", "dan", '{"l": [1.5], "m": {"k": 1}, "w": 2.0}', ""),
    (4, "dan", "R", "eve", '{"l": [1], "m": {"k": 1.0}}', ""),
    (5, "cat", "S", "ann", '{"b": true, "m": {"k": 1, "j": 2}}', ""),
    (6, "fay", "R", "gus", '{"l": [1.5]}', "g2"),
]


def reader(edges=EDGES):
    def neighbours(keys, direction):
        out = {k: [] for k in keys}
        for e in edges:
            eid, s, p, o, q, g = e
            if direction in ("out", "both") and (g, s) in out:
                out[(g, s)].append((e, o))
            if direction in ("in", "both") and (g, o) in out and s != o:
                out[(g, o)].append((e, s))
        return out

    return neighbours


def ends(paths):
    return sorted((p["g"], p["s"], p["t"], p["l"]) for p in paths)


def every(edges=EDGES):
    return sorted({(e[5], e[1]) for e in edges} | {(e[5], e[3]) for e in edges})


def test_every_relationship_of_a_path_holds_the_map():
    paths = checked_trails(reader(), every(), ["R"], "out", 1, 2, {"l": [1.5]})
    # cat->dan holds [1.5]; dan->eve holds [1], so the walk stops; fay->gus in g2
    assert ends(paths) == [("", "cat", "dan", 1), ("g2", "fay", "gus", 1)]


def test_a_map_compares_by_cypher_equality():
    paths = checked_trails(reader(), every(), ["R"], "out", 1, 2, {"m": {"k": 1}})
    assert ends(paths) == [("", "cat", "dan", 1), ("", "cat", "eve", 2), ("", "dan", "eve", 1)]
    # a map with another key set is not equal
    assert checked_trails(reader(), every(), ["S"], "out", 1, 1, {"m": {"k": 1}}) == []


def test_a_number_equals_a_number_of_the_other_type_and_a_stored_text_number():
    # w: 1 (int), "2" (a Cypher CREATE's text, the number 2), 2.0 (float)
    paths = checked_trails(reader(), every(), ["R"], "out", 1, 1, {"w": 2})
    assert ends(paths) == [("", "bob", "cat", 1), ("", "cat", "dan", 1)]


def test_a_boolean_is_not_a_number():
    assert checked_trails(reader(), every(), ["S"], "out", 1, 1, {"b": 1}) == []
    assert ends(checked_trails(reader(), every(), ["S"], "out", 1, 1, {"b": True})) == [("", "cat", "ann", 1)]


def test_a_trail_uses_no_relationship_twice_and_zero_hops_is_the_start():
    paths = checked_trails(reader(), [("", "dan")], ["R"], "both", 0, 3, {"l": [1]})
    # dan -R- eve (l [1]); cat -R- dan holds [1.5]; from eve no other relationship
    assert ends(paths) == [("", "dan", "dan", 0), ("", "dan", "eve", 1)]


def test_a_path_is_written_as_cy_vlp_paths_writes_it():
    (p,) = checked_trails(reader(), [("", "cat")], ["R"], "out", 1, 1, {"l": [1.5]})
    assert p["s"] == "cat" and p["t"] == "dan" and p["l"] == 1 and p["g"] == ""
    assert json.loads(p["n"]) == ["dan"] and json.loads(p["y"]) == ["R"]
    assert json.loads(p["r"]) == [{"type": "R", "props": {"l": [1.5], "m": {"k": 1}, "w": 2.0}}]
    assert p["k"] == "\x02cat\x01R\x01dan\x02"


def test_an_incoming_hop_keys_the_stored_edge():
    (p,) = checked_trails(reader(), [("", "dan")], ["R"], "in", 1, 1, {"l": [1.5]})
    assert (p["s"], p["t"]) == ("dan", "cat") and p["k"] == "\x02cat\x01R\x01dan\x02"


def test_too_many_paths_raise_rather_than_answer_part():
    many = [(i, "x", "R", f"y{i}", "{}", "") for i in range(30)]
    with pytest.raises(ValueError, match="more than 10 paths"):
        checked_trails(reader(many), [("", "x")], ["R"], "out", 1, 1, {}, limit=10)


def test_cypher_equal():
    assert cypher_equal([1, 2.0], [1.0, 2]) is True
    assert cypher_equal({"k": [1]}, {"k": [1.0]}) is True
    assert cypher_equal({"k": 1}, {"k": 1, "j": 2}) is False
    assert cypher_equal(True, 1) is False
    assert cypher_equal("1", 1) is False
    assert cypher_equal(None, 1) is None
    assert cypher_equal([None], [1]) is None


@pytest.mark.parametrize("q", [
    "MATCH p = (a {node_id: $x})-[:R*1..2]-(b) RETURN b.name, length(p)",
    "MATCH p = (a {node_id: $x})-[:R*1..2]->(b) RETURN b.name, nodes(p)",
    "MATCH p = (a:V {node_id: $x})<-[:R*1..3]-(b) RETURN length(p), relationships(p)",
])
def test_a_path_function_from_an_id_bound_start_expands_in_sql(q):
    """The engine's path route walked node-distinct paths and listed a far end once per length: from o308:bo over an
    undirected hop, two paths of length 2 to pam answered one row (seed 3 #121). CY_VLP_PATHS walks trails."""
    sq = translate_to_sql(parse_query(q), {"x": "n1"})
    assert not sq.var_length_paths, _sql(sq)
    assert "CY_VLP_PATHS(" in _sql(sq)


def test_the_literal_hides_bind_markers_and_parentheses():
    """The literal is inlined into the join text, where the translator counts `?` to place binds and scans
    parentheses; JSON_TABLE reads the escapes back (fixes63, measured)."""
    from iris_vector_graph._engine.query import literal_paths_json

    paths = [{"s": "a?(1)", "g": "", "t": "b)", "l": 1, "n": '["b)"]', "y": '["R"]', "r": "[]", "k": "\x02a?(1)\x01R\x01b)\x02"}]
    text = literal_paths_json(paths)
    assert "?" not in text and "(" not in text and ")" not in text, text
    assert json.loads(text) == paths
