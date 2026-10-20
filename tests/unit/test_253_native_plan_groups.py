"""Spec 253, grouped aggregates: what the planner accepts and the plan it gives.

RETURN mixes node-property grouping keys with count(*), count(var), count(DISTINCT var); ORDER BY names returned
columns (by alias or by the same expression). The plan's `ordered` flag says the order is total (every grouping key
is ordered on), which is when the shadow comparator holds native to the SQL path's row order.
"""

import pytest

from iris_vector_graph.cypher.native_plan import plan_query
from iris_vector_graph.cypher.parser import parse_query


def _plan(cypher):
    return plan_query(parse_query(cypher), {})


def test_a_grouped_count_ordered_by_its_alias():
    p = _plan("MATCH (t:Tag)<-[:HAS_TAG]-(m:Post) RETURN t.name AS name, count(m) AS c ORDER BY c DESC, name LIMIT 10")
    assert p["agg"] == {
        "keys": [[0, "name"]],
        "aggs": [{"kind": "count"}],
        "cols": [["k", 0], ["a", 0]],
        "order": [[1, True], [0, False]],
    }
    assert p["width"] == 2 and p["limit"] == 10 and p["ordered"] is True and p["returns"] == []


def test_ordering_by_the_count_alone_is_not_total():
    p = _plan("MATCH (t:Tag)<-[:HAS_TAG]-(m:Post) RETURN t.name AS name, count(m) AS c ORDER BY c DESC")
    assert p["ordered"] is False


def test_an_order_item_may_repeat_the_returned_expression():
    p = _plan("MATCH (t:Tag)<-[:HAS_TAG]-(m:Post) RETURN t.name, count(*) ORDER BY t.name")
    assert p["agg"]["order"] == [[0, False]] and p["ordered"] is True


def test_an_ungrouped_count_of_a_node():
    p = _plan("MATCH (p:Person) RETURN count(p)")
    assert p["agg"] == {"keys": [], "aggs": [{"kind": "count"}], "cols": [["a", 0]], "order": []}


def test_count_distinct_beside_a_key_over_variable_length():
    p = _plan("MATCH (p:Person)-[:KNOWS*1..2]-(f:Person)<-[:HAS_MEMBER]-(x:Forum) RETURN x.title, count(DISTINCT f)")
    assert p is None  # a variable-length hop followed by more hops is not planned yet
    p = _plan("MATCH (p:Person)-[:KNOWS*1..2]-(f:Person) RETURN p.name AS n, count(DISTINCT f) AS c")
    assert p["agg"]["aggs"] == [{"kind": "count_distinct", "var": 1}]


@pytest.mark.parametrize(
    "cypher",
    [
        # ORDER BY a property that is not a returned column
        "MATCH (t:Tag)<-[:HAS_TAG]-(m:Post) RETURN t.name AS name, count(m) AS c ORDER BY t.url",
        # every path counts: ends alone cannot answer a plain count over a variable-length hop
        "MATCH (p:Person)-[:KNOWS*1..2]-(f:Person) RETURN p.name, count(f)",
        "MATCH (p:Person)-[:KNOWS*1..2]-(f:Person) RETURN p.name, count(*)",
        # aggregates of properties, and functions other than count
        "MATCH (p:Person) RETURN count(p.name)",
        "MATCH (p:Person) RETURN p.city, sum(p.age)",
        "MATCH (p:Person) RETURN p.city, count(DISTINCT p.name)",
        # a grouping key that is a whole node
        "MATCH (p:Person)-[:KNOWS]->(f) RETURN p, count(f)",
    ],
)
def test_the_planner_declines_these_aggregates(cypher):
    assert _plan(cypher) is None
