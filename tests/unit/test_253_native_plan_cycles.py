"""Spec 253: a chain that closes on a node it already bound, and comparisons between two nodes' properties.

SNB BI11 counts triangles: `MATCH (a)-[:KNOWS]-(b)-[:KNOWS]-(c)-[:KNOWS]-(a) WHERE a.sid < b.sid AND b.sid < c.sid`.
The closing hop's far node is the chain's first; the plan names it by index rather than as a new node. A comparison of
two nodes' properties is a "cross" filter, checked once both nodes are bound.
"""

import pytest

from iris_vector_graph.cypher.native_plan import plan_query
from iris_vector_graph.cypher.parser import parse_query

BI11 = (
    "MATCH (a:Person)-[:KNOWS]-(b:Person)-[:KNOWS]-(c:Person)-[:KNOWS]-(a) "
    "WHERE a.sid < b.sid AND b.sid < c.sid RETURN count(*)"
)


def _plan(cypher):
    return plan_query(parse_query(cypher), {})


def test_the_closing_hop_returns_to_the_first_node():
    p = _plan(BI11)
    assert len(p["nodes"]) == 3
    assert [(h["from"], h["to"]) for h in p["hops"]] == [(0, 1), (1, 2), (2, 0)]


def test_comparisons_between_two_nodes_are_cross_filters():
    p = _plan(BI11)
    assert p["cross"] == [[0, "sid", "<", 1, "sid"], [1, "sid", "<", 2, "sid"]]
    assert p["agg"]["aggs"] == [{"kind": "count"}]


def test_an_equality_between_two_nodes_is_a_cross_filter_too():
    p = _plan("MATCH (a:Person)-[:KNOWS]-(b:Person) WHERE a.city = b.city RETURN count(*)")
    assert p["cross"] == [[0, "city", "=", 1, "city"]]


def test_a_mirrored_cross_comparison_is_normalised():
    p = _plan("MATCH (a:Person)-[:KNOWS]-(b:Person) WHERE b.age > a.age RETURN count(*)")
    assert p["cross"] == [[1, "age", ">", 0, "age"]]


def test_the_closing_hop_keeps_relationship_identity():
    # three hops of one type: a relationship may appear once per match
    assert all(h["unique"] for h in _plan(BI11)["hops"])


@pytest.mark.parametrize(
    "cypher",
    [
        # a node bound twice other than by closing the chain on its first node
        "MATCH (a:Person)-[:KNOWS]-(b:Person)-[:KNOWS]-(b) RETURN count(*)",
        # a closing hop of variable length
        "MATCH (a:Person)-[:KNOWS]-(b:Person)-[:KNOWS*1..2]-(a) RETURN count(*)",
    ],
)
def test_the_planner_declines_these_cycles(cypher):
    assert _plan(cypher) is None
