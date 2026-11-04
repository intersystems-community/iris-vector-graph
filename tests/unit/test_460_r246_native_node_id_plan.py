"""4.6.0 R246: the native planner takes an unlabelled `{node_id: ...}` anchor.

Since R204 the guides listed it as a read the native executor declines, so it took the SQL path and never compiled to
an `IVGC.*` class (walkthrough 1 W7).

An id on a node with no label is the node itself, as with a label (`"id"` in the node's spec, `labels` empty): as the
anchor, the executor finds it in `Graph_KG.nodes`, scoped by graph, so a node with no edge and no label still matches a
read with no hop (`Exec.Anchors`). tests/e2e/test_460_r246_native_node_id_e2e.py runs these against IRIS.
"""

import pytest

from iris_vector_graph.cypher.native_plan import plan_query
from iris_vector_graph.cypher.parser import parse_query


def _plan(cypher, params=None):
    return plan_query(parse_query(cypher), params if params is not None else {"x": "a"})


# --- the anchor half ---------------------------------------------------------------------------------------------


def test_an_unlabelled_node_id_is_the_anchor():
    p = _plan("MATCH (a {node_id: $x})-[:KNOWS]->(b) RETURN b.name")
    assert p is not None
    assert p["anchor"] == 0
    assert p["nodes"][0] == {"labels": [], "props": [], "cmp": [], "id": {"v": "a"}}


def test_an_unlabelled_id_is_the_anchor_too():
    """{id: x} names the node x, as {node_id: x} does (R194)."""
    p = _plan("MATCH (a {id: $x})-[:KNOWS]->(b) RETURN b.name")
    assert p["anchor"] == 0 and p["nodes"][0]["id"] == {"v": "a"}


def test_an_unlabelled_node_id_with_no_hop_is_planned():
    """The node alone: the executor finds it in the nodes table, so one with no edge and no label still matches."""
    p = _plan("MATCH (a {node_id: $x}) RETURN a.name")
    assert p is not None
    assert p["hops"] == [] and p["anchor"] == 0 and p["nodes"][0]["id"] == {"v": "a"}


def test_an_unlabelled_node_id_wins_over_a_property_lookup():
    p = _plan("MATCH (a:P {name: 'A'})-[:KNOWS]->(b {node_id: $x}) RETURN a.name")
    assert p["anchor"] == 1
    assert p["nodes"][1]["labels"] == [] and p["nodes"][1]["id"] == {"v": "a"}


def test_an_unlabelled_node_id_with_a_property_check():
    p = _plan("MATCH (a {node_id: $x, kind: 'k'})-[:KNOWS]->(b) RETURN b.name")
    assert p["nodes"][0]["id"] == {"v": "a"} and p["nodes"][0]["props"] == [["kind", {"v": "k"}]]


@pytest.mark.parametrize(
    "cypher, params",
    [
        ("MATCH (a {node_id: 1})-[:KNOWS]->(b) RETURN b.name", {}),  # not a string: the SQL path decides
        ("MATCH (a {node_id: $x})-[:KNOWS]->(b) RETURN b.name", {"x": 1}),
        ("MATCH (a {id: $x, node_id: $x})-[:KNOWS]->(b) RETURN b.name", None),  # two ids
        ("MATCH (a {node_id: $missing})-[:KNOWS]->(b) RETURN b.name", None),
    ],
)
def test_these_anchors_stay_on_the_sql_path(cypher, params):
    assert _plan(cypher, params) is None

