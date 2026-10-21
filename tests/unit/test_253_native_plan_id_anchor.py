"""Spec 253: a labelled node named by its id (`(s:Entity {id: $x})`) is planned natively.

The node identity is not a property row, so the planner declined every `{id: ..}` node and DRKG's queries (all of the
form `MATCH (s:Entity {id: $x})-...`) took the SQL path. With a label the node's existence is one ^KG("label") entry
per graph, so the plan carries the id ("id") and the executor checks it there: as the anchor, the only candidate in
each graph that holds the label; elsewhere, a check of the bound node. Without a label the planner still declines.
The plan format changed, so PLAN_VERSION is 2.
"""

import pytest

from iris_vector_graph.cypher import native_plan
from iris_vector_graph.cypher.native_plan import plan_query
from iris_vector_graph.cypher.parser import parse_query


def _plan(cypher, params=None):
    return plan_query(parse_query(cypher), params or {"x": "Gene::1"})


def test_a_labelled_id_is_the_anchor():
    p = _plan("MATCH (s:Entity {id: $x})-[r]->(n) RETURN count(r) AS cnt")
    assert p["anchor"] == 0
    assert p["nodes"][0]["id"] == {"v": "Gene::1"} and p["nodes"][0]["props"] == []


def test_a_counted_relationship_counts_matches():
    p = _plan("MATCH (s:Entity {id: $x})-[r]->(n) RETURN count(r) AS cnt")
    assert p["agg"]["aggs"] == [{"kind": "count"}]


def test_an_id_anchor_wins_over_a_property_lookup():
    p = _plan("MATCH (a:Entity {name: 'x'})-[:R]->(b:Entity {id: $x}) RETURN a.name")
    assert p["anchor"] == 1


def test_an_id_on_the_far_node_is_carried():
    p = _plan("MATCH (a:Entity {name: 'x'})-[:R]->(b:Entity {node_id: $x}) RETURN count(b)")
    assert p["nodes"][1]["id"] == {"v": "Gene::1"}


def test_an_id_and_a_property_on_one_node():
    p = _plan("MATCH (s:Entity {id: $x, kind: 'gene'})-[r]->(n) RETURN count(r)")
    assert p["nodes"][0]["id"] == {"v": "Gene::1"} and p["nodes"][0]["props"] == [["kind", {"v": "gene"}]]


@pytest.mark.parametrize(
    "cypher",
    [
        "MATCH (s {id: $x})-[r]->(n) RETURN count(r)",  # no label: nothing indexes the node's existence
        "MATCH (s:Entity {id: $x, node_id: $x})-[r]->(n) RETURN count(r)",  # two ids
        "MATCH (s:Entity {id: 1})-[r]->(n) RETURN count(r)",  # not a string: the SQL path decides what it matches
        # a relationship variable read by anything but count(r)
        "MATCH (s:Entity {id: $x})-[r]->(n) WHERE r.weight > 1 RETURN count(r)",
        "MATCH (s:Entity {id: $x})-[r]->(n) RETURN r",
        "MATCH (s:Entity {id: $x})-[r]->(n) RETURN type(r), count(*)",
        "MATCH (s:Entity {id: $x})-[r]->(n) RETURN count(DISTINCT r)",
        "MATCH (s:Entity {id: $x})-[r*1..2]->(n) RETURN count(DISTINCT n)",
        "MATCH (s:Entity {id: $x})-[r]->(n)-[r]->(m) RETURN count(*)",
    ],
)
def test_these_stay_on_the_sql_path(cypher):
    assert _plan(cypher) is None


def test_the_plan_format_version():
    assert native_plan.PLAN_VERSION == 2
