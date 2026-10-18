"""Relationship uniqueness is not compared between hops whose types cannot overlap.

`NOT (e6.s = e3.s AND e6.p = e3.p AND e6.o_id = e3.o_id)` is always true when e3 is a
KNOWS edge and e6 an IS_LOCATED_IN edge: p differs. SNB BI14 carried ten of them per
statement; each is one more OR for the IRIS optimizer to cost.
"""

import re

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _excl(text):
    sql = translate_to_sql(parse_query(text), {}).sql
    return len(re.findall(r"NOT \(e\d+\.", sql if isinstance(sql, str) else sql[0]))


def test_disjoint_types_are_not_compared():
    assert _excl("MATCH (a)-[:R]->(b)-[:S]->(c) RETURN c") == 0


def test_the_same_type_is_compared():
    assert _excl("MATCH (a)-[:R]->(b)-[:R]->(c) RETURN c") == 1


def test_overlapping_alternatives_are_compared():
    assert _excl("MATCH (a)-[:R|S]->(b)-[:S]->(c) RETURN c") == 1


def test_an_untyped_hop_is_compared():
    assert _excl("MATCH (a)-[]->(b)-[:S]->(c) RETURN c") == 1


def test_three_hops_compare_only_the_pairs_that_can_collide():
    assert _excl("MATCH (a)-[:R]->(b)-[:S]->(c)-[:R]->(d) RETURN d") == 1


def test_undirected_hops_follow_the_same_rule():
    assert _excl("MATCH (a)-[:R]-(b)-[:S]-(c) RETURN c") == 0
    assert _excl("MATCH (a)-[:R]-(b)-[:R]-(c) RETURN c") == 1
