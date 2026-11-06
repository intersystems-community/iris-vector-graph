"""4.6.0 R310 follow-up (fixes66): a node value answers its id, labels and properties only, and a user's own data is
never changed on the way out.

A collected node's JSON carries its graph (`"_graph"`, last) so a later property read finds the node's own graph's
row. The engine drops it from the rows it answers (and so from Bolt and the REST API), but only (1) in RETURN columns
that can hold a collected node (SQLQuery.node_json_columns), and (2) from an object shaped exactly as the builder
writes it: keys `_id`, `_labels`, `_props`, `_graph`, in that order, at any depth of JSON-in-JSON text. A user map with
a `_graph` key, or a string whose text spells such an object in another column, is answered as it is.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from iris_vector_graph._engine.query import _finish_sql_result, _strip_node_graph
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

C = (",", ":")


def _node(nid="a", graph=None):
    d = {"_id": nid, "_labels": ["L"], "_props": [json.dumps({"key": "k", "value": "v"}, separators=C)]}
    if graph is not None:
        d["_graph"] = graph
    return json.dumps(d, separators=C)


def test_depth_0_1_2():
    assert _strip_node_graph(_node(graph="g1")) == _node()
    assert _strip_node_graph(_node(graph="")) == _node()
    one = json.dumps([_node(graph="g1"), _node("b", graph="")], separators=C)
    assert _strip_node_graph(one) == json.dumps([_node(), _node("b")], separators=C)
    two = json.dumps([one], separators=C)
    assert _strip_node_graph(two) == json.dumps([json.dumps([_node(), _node("b")], separators=C)], separators=C)


def test_a_graph_name_holding_a_quote_and_a_backslash():
    g = 'o"brien\\x\\'
    assert _strip_node_graph(_node(graph=g)) == _node()
    assert _strip_node_graph(json.dumps([_node(graph=g)], separators=C)) == json.dumps([_node()], separators=C)


def test_a_map_with_a_graph_key_that_is_not_node_shaped_is_kept():
    for d in ({"a": 1, "_graph": "x"}, {"_id": "a", "_graph": "x"}, {"_id": "a", "_labels": [], "_graph": "x"},
              {"_labels": [], "_id": "a", "_props": [], "_graph": "x"}, {"_id": "a", "_labels": [], "_props": [],
                                                                          "_graph": "x", "z": 1}):
        s = json.dumps(d, separators=C)
        assert _strip_node_graph(s) == s, s
        listed = json.dumps([s], separators=C)
        assert _strip_node_graph(listed) == listed


def test_other_text_is_left_alone():
    for s in ('{"_graph":"g"}', 'a ,"_graph":"g"} b', "[1,2]", _node(), ""):
        assert _strip_node_graph(s) == s


def _sq(cols):
    return SimpleNamespace(bolt_column_types=None, column_name_map={}, typed_value_columns=[], bool_text_columns=[],
                           bool_expr_columns=[], prop_text_expr_columns=[], return_arity=len(cols), node_json_columns=cols)


def test_only_node_columns_are_stripped():
    node = _node(graph="g")
    result = SimpleNamespace(columns=["c", "p"], rows=[[node, node]], error=None, bolt_column_types=None)
    out = _finish_sql_result(result, _sq(["c"]))
    assert out.rows[0] == [_node(), node]  # `p` (a user's string that spells a node) is answered as it is


def _cols(cypher):
    return translate_to_sql(parse_query(cypher)).node_json_columns


def test_the_translator_names_the_columns_that_can_hold_a_collected_node():
    assert _cols("MATCH (n:L) RETURN collect(n) AS c") == ["c"]
    assert _cols("MATCH (n:L) RETURN collect(n)[0] AS c") == ["c"]
    assert _cols("MATCH (n:L) WITH collect(n) AS ns UNWIND ns AS m RETURN m") == ["m"]
    assert _cols("MATCH (n:L) WITH collect(n) AS ns RETURN ns, size(ns) AS s") == ["ns"]
    assert _cols("MATCH (n:L) RETURN n.p AS p, collect(n.p) AS c, {a: 1, _graph: 'x'} AS m") == []


def test_a_property_that_spells_a_node_inside_a_node_is_kept():
    inner = '{"_id":"z","_labels":["L"],"_props":[],"_graph":"g"}'
    prop = json.dumps({"key": "s", "value": json.loads(inner)}, separators=C)
    outer = json.dumps({"_id": "a", "_labels": ["L"], "_props": [prop], "_graph": "g1"}, separators=C)
    want = json.dumps({"_id": "a", "_labels": ["L"], "_props": [prop]}, separators=C)
    assert _strip_node_graph(outer) == want
    assert _strip_node_graph(json.dumps([outer], separators=C)) == json.dumps([want], separators=C)
