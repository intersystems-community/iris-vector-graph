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
    # with a filter: a whole-label count with none is left to the SQL path (test_253_native_plan_label_count)
    p = _plan("MATCH (p:Person) WHERE p.age > '1' RETURN count(p)")
    assert p["agg"] == {"keys": [], "aggs": [{"kind": "count"}], "cols": [["a", 0]], "order": []}


def test_count_distinct_beside_a_key_over_variable_length():
    p = _plan("MATCH (p:Person)-[:KNOWS*1..2]-(f:Person) RETURN p.name AS n, count(DISTINCT f) AS c")
    assert p["agg"]["aggs"] == [{"kind": "count_distinct", "var": 1}]


def test_variable_length_then_hops_of_other_types():
    p = _plan("MATCH (p:Person)-[:KNOWS*1..2]-(f:Person)<-[:HAS_MEMBER]-(x:Forum) RETURN x.title, count(DISTINCT f)")
    assert [h.get("min") for h in p["hops"]] == [1, None]


@pytest.mark.parametrize(
    "cypher",
    [
        # beside a variable-length hop, a hop that may share its relationships: uniqueness would need the paths
        "MATCH (p:Person)-[:KNOWS*1..2]-(f:Person)-[:KNOWS]-(g:Person) RETURN DISTINCT g.name",
        "MATCH (p:Person)-[:KNOWS*1..2]-(f:Person)<--(m) RETURN DISTINCT m.name",
        "MATCH (p:Person)-[*1..2]-(f:Person)<-[:HAS_CREATOR]-(m) RETURN DISTINCT m.name",
        # two variable-length hops
        "MATCH (p:Person)-[:KNOWS*1..2]-(f:Person)<-[:HAS_CREATOR*1..2]-(m) RETURN DISTINCT m.name",
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


def test_only_hops_that_could_share_a_relationship_keep_its_identity():
    p = _plan("MATCH (p:Person {sid: 'x'})-[:KNOWS]-(f:Person)-[:KNOWS]-(g:Person)<-[:HAS_CREATOR]-(m) RETURN m.sid")
    assert [h["unique"] for h in p["hops"]] == [True, True, False]
    p = _plan("MATCH (p:Person {sid: 'x'})-[:KNOWS]-(f)<--(m) RETURN m.sid")
    assert [h["unique"] for h in p["hops"]] == [True, True]  # an untyped hop may be any relationship


@pytest.mark.parametrize(
    "cypher,multiplicity",
    [
        ("MATCH (p:Person)-[:KNOWS]->(f) RETURN f.sid", True),
        ("MATCH (p:Person)-[:KNOWS]->(f) RETURN DISTINCT f.sid", False),
        ("MATCH (p:Person)-[:KNOWS]->(f) RETURN p.sid, count(*)", True),
        ("MATCH (p:Person)-[:KNOWS]->(f) RETURN p.sid, count(DISTINCT f)", False),
        ("MATCH (p:Person)-[:KNOWS]->(f) RETURN count(DISTINCT f), count(f)", True),
    ],
)
def test_multiplicity_matters_unless_every_answer_is_distinct(cypher, multiplicity):
    assert _plan(cypher)["multiplicity"] is multiplicity


def test_distinct_orders_only_by_returned_columns():
    assert _plan("MATCH (p:Person)-[:KNOWS]->(f) RETURN DISTINCT f.sid ORDER BY f.sid") is not None
    assert _plan("MATCH (p:Person)-[:KNOWS]->(f) RETURN DISTINCT f.sid ORDER BY f.name") is None


def test_without_a_property_anchor_every_labelled_node_is_an_alternative():
    p = _plan("MATCH (c:Comment)-[:HAS_CREATOR]->(p:Person) RETURN p.sid AS person, count(c) AS n")
    assert [(a["anchor"], [(h["from"], h["to"], h["dir"]) for h in a["hops"]]) for a in p["alts"]] == [
        (0, [(0, 1, "out")]),
        (1, [(1, 0, "in")]),
    ]
    assert _plan("MATCH (c:Comment)-[:HAS_CREATOR]->(p:Person {sid: 'x'}) RETURN c.sid")["alts"] == []


@pytest.mark.parametrize(
    "cypher,tally",
    [
        # the far node of the last hop is read only by count: its matches can be tallied
        ("MATCH (p:Person {sid: 'x'})<-[:HAS_CREATOR]-(m:Post)<-[:LIKES]-(l:Person) RETURN p.sid, count(l)", [False, True]),
        ("MATCH (p:Person {sid: 'x'})<-[:HAS_CREATOR]-(m:Post) RETURN p.sid, count(*)", [True]),
        # read by a key, by count(DISTINCT), or returned: each match is needed
        ("MATCH (p:Person {sid: 'x'})<-[:HAS_CREATOR]-(m:Post) RETURN m.title, count(*)", [False]),
        ("MATCH (p:Person {sid: 'x'})<-[:HAS_CREATOR]-(m:Post) RETURN p.sid, count(DISTINCT m)", [False]),
        ("MATCH (p:Person {sid: 'x'})<-[:HAS_CREATOR]-(m:Post) RETURN m.title", [False]),
        # a hop another could share a relationship with keeps its identity, so is walked
        ("MATCH (p:Person {sid: 'x'})-[:KNOWS]-(f)-[:KNOWS]-(g) RETURN p.sid, count(g)", [False, False]),
    ],
)
def test_the_last_hop_is_tallied_where_only_count_reads_its_far_node(cypher, tally):
    assert [h.get("tally", False) for h in _plan(cypher)["hops"]] == tally


def test_an_alternative_anchor_s_hops_carry_the_same_identity_rules():
    p = _plan("MATCH (a:A)-[:R]-(b:B)-[:R]-(c) RETURN count(*)")
    for alt in p["alts"]:
        assert all(h["unique"] for h in alt["hops"]), alt


@pytest.mark.parametrize(
    "cypher,acc",
    [
        # plain counts keyed by the anchor alone: an anchor's matches all fall in one group
        ("MATCH (p:Person {sid: 'x'})<-[:HAS_CREATOR]-(m:Post)<-[:LIKES]-(l) RETURN p.sid, count(l)", True),
        ("MATCH (p:Person {sid: 'x'})<-[:HAS_CREATOR]-(m:Post) RETURN count(*)", True),
        ("MATCH (p:Person {sid: 'x'})<-[:HAS_CREATOR]-(m:Post) RETURN m.title, count(*)", False),
        ("MATCH (p:Person {sid: 'x'})<-[:HAS_CREATOR]-(m:Post) RETURN p.sid, count(DISTINCT m)", False),
        ("MATCH (p:Person {sid: 'x'})<-[:HAS_CREATOR]-(m:Post) RETURN p.sid, m.sid", False),
    ],
)
def test_an_anchor_s_matches_accumulate_when_only_its_keys_group_them(cypher, acc):
    assert _plan(cypher)["acc"] is acc


def test_each_alternative_anchor_says_whether_it_accumulates():
    p = _plan("MATCH (t:Tag)<-[:HAS_TAG]-(m:Post) RETURN t.name AS name, count(m) AS c")
    assert [(a["anchor"], a["acc"]) for a in p["alts"]] == [(0, True), (1, False)]


def test_a_substring_of_a_property_is_a_grouping_key():
    p = _plan("MATCH (m:Post) WHERE m.creationDate < '2012' RETURN substring(m.creationDate, 0, 4) AS year, count(m) AS n ORDER BY year")
    assert p["agg"]["keys"] == [[0, "creationDate", ["substring", 0, 4]]]
    assert p["agg"]["order"] == [[0, False]]
    assert _plan("MATCH (m:Post) RETURN substring(m.title, 2) AS t, count(*)")["agg"]["keys"] == [[0, "title", ["substring", 2, -1]]]


@pytest.mark.parametrize(
    "cypher",
    [
        "MATCH (m:Post) RETURN substring(m.title, -1, 2) AS t, count(*)",
        "MATCH (m:Post) RETURN substring(m.title, $s, 2) AS t, count(*)",
        "MATCH (m:Post) RETURN substring('abc', 0, 2) AS t, count(*)",
        "MATCH (m:Post) RETURN toUpper(m.title) AS t, count(*)",
        "MATCH (m:Post) RETURN substring(m.id, 0, 2) AS t, count(*)",
    ],
)
def test_other_key_expressions_are_declined(cypher):
    assert _plan(cypher) is None
