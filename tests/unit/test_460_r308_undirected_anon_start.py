"""4.6.0 R308, offline: an anonymous start with a property map on an undirected hop reads the map through the edge.

`OPTIONAL MATCH (:B {name: 'nobody'})-[:S]-(b) RETURN b.name` (and the same without OPTIONAL) failed at Prepare with
SQLCODE -23, Label 'N1' is not listed among the applicable tables: the undirected hop's CTE has no nodes row for an
anonymous start, and its property join read `n1.node_id`. Found by tests/e2e/test_460_r308_cypher_differential_e2e.py
(seed 1 #96, #239, seed 3 #31); release-4.6.0 translates it the same way.
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _declared(sql):
    return set(re.findall(r"\b(?:FROM|JOIN)\s+(?:\S+\s+)?(\w+)\s+(?:ON\b|$|\n)", sql + "\n"))


@pytest.mark.parametrize("q", [
    "MATCH (:B {name: 'x'})-[:S]-(b) RETURN b.name",
    "OPTIONAL MATCH (:B {name: 'x'})-[:S]-(b) RETURN b.name",
    "MATCH (:B {name: 'x', kind: $k})-[:S|R]-(b) RETURN b.name",
])
def test_the_start_s_map_is_read_through_the_edge(q):
    sql = translate_to_sql(parse_query(q), {"k": "y"}).sql
    used = set(re.findall(r"\b(n\d+)\.node_id\b", sql))
    assert used <= _declared(sql), (used - _declared(sql), sql)
    assert re.search(r"rdf_props p\d+ ON p\d+\.s = e\d+\._src", sql), sql
