"""4.6.0 R314: a node, a relationship or a path answers one shape whatever holds it (round 3's Python walkthrough).

`RETURN [n]` answered the node as a dict and `RETURN collect(n)` as its JSON text; `head(collect(n))` and an UNWIND of
`collect(n)` answered text too; `RETURN r` answered JSON text where `[r]` answered the dict; `collect(p)` and `[p]`
answered a path's JSON text where `RETURN p` answered the dict. The rule, one shape each, as the dict the first of
those answered:

- a node held by a list or map at any depth, picked from one (`head`, a subscript) or unwound from one:
  ``{"_id", "_labels", "_props"}``, whose ``_props`` entries are JSON text (docs/migration/v4.6.0.md); a node
  returned by itself stays three columns (USER_GUIDE §3);
- a relationship, returned by itself or held by a list or map: ``{"type", "props"}``;
- a path, wherever it is: ``{"nodes", "rels"}``.

The rows below are what IRIS answered for each query's translated SQL (tests/fixtures/r314_container_rows.json,
measured on 2026.3 at be6cd07c); the engine's reading of them is what this file checks. The expected values are the
graph the fixture was made from, written out here: (:Film {node_id:'alien', title:'Alien', year:1979})
-[:LIKES {w:2}]->(:Film {node_id:'brazil', title:'Brazil'}).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from iris_vector_graph._engine.query import _finish_sql_result
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql
from iris_vector_graph.result import IVGResult

ROWS = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "r314_container_rows.json").read_text())["shapes"]

NODE = {"_id": "alien", "_labels": ["Film"], "_props": [
    '{"key":"node_id","value":"alien"}', '{"key":"title","value":"Alien"}', '{"key":"year","value":1979}']}
REL = {"type": "LIKES", "props": {"w": 2}}
PATH = {"nodes": ["alien", "brazil"], "rels": ["LIKES"]}

EXPECTED = {
    "[n]": [NODE],
    "{f:n}": {"f": NODE},
    "[[n]]": [[NODE]],
    "collect(n)": [NODE],
    "head(collect(n))": NODE,
    "UNWIND collect(n)": NODE,
    "r": REL,
    "[r]": [REL],
    "p": PATH,
    "collect(p)": [PATH],
    "[p]": [PATH],
}


def _sorted_props(v):
    """`_props` in a fixed order: the fixture's order is the catalog's, which the rule does not fix."""
    if isinstance(v, dict):
        return {k: sorted(x) if k == "_props" else _sorted_props(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_sorted_props(x) for x in v]
    return v


def _answer(shape):
    case = ROWS[shape]
    sql_query = translate_to_sql(parse_query(case["query"]), {})
    result = IVGResult(columns=list(case["columns"]), rows=[list(r) for r in case["rows"]])
    return _finish_sql_result(result, sql_query).rows


@pytest.mark.parametrize("shape", sorted(EXPECTED))
def test_each_graph_value_answers_its_one_shape(shape):
    [row] = _answer(shape)
    assert _sorted_props(row[-1]) == _sorted_props(EXPECTED[shape]), (shape, row[-1])


def test_a_collected_relationship_is_the_relationship_not_its_type():
    """`collect(r)` collected the relationship's type alone (`JSON_ARRAYAGG(e.p)`): ['LIKES'], its properties lost.
    Its element is now what `[r]` builds for the relationship."""
    collected = translate_to_sql(parse_query("MATCH ()-[r:LIKES]->() RETURN collect(r) AS c"), {}).sql
    listed = translate_to_sql(parse_query("MATCH ()-[r:LIKES]->() RETURN [r] AS c"), {}).sql
    assert '"props":' in collected, collected
    element = listed[listed.index("'{\"type\""):listed.index("'}'") + 3]
    assert element in collected, (element, collected)


def test_a_string_property_that_spells_a_node_stays_a_string():
    """Only a value the query knows is a node, relationship or path is read as one: a list of strings is not."""
    text = json.dumps(NODE, separators=(",", ":"))
    sql_query = translate_to_sql(parse_query("MATCH (n) RETURN collect(n.bio) AS c"), {})
    result = IVGResult(columns=["c"], rows=[[json.dumps([text])]])
    assert _finish_sql_result(result, sql_query).rows == [[[text]]]


def test_bolt_packs_a_returned_relationship_with_its_type_and_properties():
    """Bolt read `RETURN r`'s {"type", "props"} for keys it does not have (s, p, o): its Relationship structure had the
    column's name as its type and no properties, before R314 as after."""
    from unittest.mock import MagicMock

    from iris_vector_graph.bolt_server import TAG_RELATIONSHIP, BoltSession

    session = BoltSession.__new__(BoltSession)
    session._websocket = MagicMock()
    session._bolt_version = 4
    data = session._pack_rel_from_value(REL, "v")
    assert TAG_RELATIONSHIP in data
    assert b"\x85LIKES" in data and b"\xa1\x81w\x02" in data, data  # the type, and the map {w: 2}


@pytest.mark.parametrize("q, marked", [
    ("MATCH ()-[r:LIKES]->() RETURN head(collect(r)) AS c", True),
    ("MATCH ()-[r:LIKES]->() RETURN {k: collect(r)} AS c", True),
    ("MATCH p = ()-[:LIKES]->() RETURN collect(nodes(p)) AS c", True),
    ("MATCH (n) WITH [n, 1] AS l RETURN l AS c", True),
    ("MATCH (n) WITH [n, 1] AS l RETURN l[0] AS c", True),
    ("MATCH ()-[r:LIKES]->() RETURN r.w AS c", False),
    ("MATCH ()-[r:LIKES]->() RETURN type(r) AS c", False),
    ("MATCH p = ()-[:LIKES]->() RETURN length(p) AS c", False),
])
def test_the_columns_that_can_hold_a_graph_value_are_marked(q, marked):
    sql_query = translate_to_sql(parse_query(q), {})
    assert ("c" in sql_query.graph_json_columns or "c" in sql_query.node_json_columns) is marked, sql_query


def test_every_collect_of_a_relationship_in_a_return_item_collects_the_relationship():
    sql = translate_to_sql(parse_query("MATCH ()-[r:LIKES]->() RETURN head(collect(r)) AS c"), {}).sql
    assert "JSON_ARRAYAGG('{\"type\":\"'" in sql, sql
