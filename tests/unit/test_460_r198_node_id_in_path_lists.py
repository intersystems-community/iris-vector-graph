"""4.6.0 R198: `node_id` of a node held in a list is the node's identity, never a property row.

`[n IN nodes(p) | n.node_id]` answered `'[]'`: the comprehension read `rdf_props` for a `node_id` key, which no node
has (`node_id` is the `nodes` row's key). The answers are pinned live in
tests/e2e/test_460_r198_node_id_in_path_lists_e2e.py; this checks the translation reads no such row on every route
a node reaches a list by, and that other properties still do.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

SP = "MATCH p = shortestPath((x {node_id:'a'})-[*]->(y {node_id:'f'})) "
PLAIN = "MATCH p = (x {node_id:'a'})-[:ROAD]->(y) "
NODE_ID_ROW = "\"key\" = 'node_id'"


def _sql(cypher: str) -> str:
    return translate_to_sql(parse_query(cypher)).sql


@pytest.mark.parametrize(
    "cypher",
    [
        SP + "RETURN [n IN nodes(p) | n.node_id]",
        PLAIN + "RETURN [n IN nodes(p) | n.node_id]",
        PLAIN + "RETURN [n IN nodes(p) WHERE n.node_id <> 'b' | n.node_id]",
        PLAIN + "RETURN nodes(p)[1].node_id",
        "MATCH (n:L) WITH collect(n) AS ns RETURN [x IN ns | x.node_id]",
        "MATCH (n:L) RETURN [x IN collect(n) | x.node_id]",
    ],
    ids=["shortest", "plain", "filtered", "element", "collected-list", "collect-inline"],
)
def test_node_id_is_not_read_as_a_property(cypher):
    sql = _sql(cypher)
    assert NODE_ID_ROW not in sql, sql


def test_over_collect_the_element_is_the_node_id_itself():
    # the element is n0.node_id: it was read as JSON (`JSON_VALUE(n0.node_id, '$.node_id')`), so every property, name
    # too, was null
    sql = _sql("MATCH (n:L) RETURN [x IN collect(n) | x.node_id]")
    assert "JSON_ARRAYAGG(n0.node_id)" in sql, sql


@pytest.mark.parametrize(
    "cypher",
    [
        PLAIN + "RETURN [n IN nodes(p) | n.name]",
        PLAIN + "RETURN nodes(p)[1].name",
        "MATCH (n:L) WITH collect(n) AS ns RETURN [x IN ns | x.name]",
        "MATCH (n:L) RETURN [x IN collect(n) | x.name]",
    ],
    ids=["plain", "element", "collected-list", "collect-inline"],
)
def test_other_properties_are_still_property_rows(cypher):
    assert "\"key\" = 'name'" in _sql(cypher)
