"""4.6.0 R307: a node CREATE or MERGE of a map literal or a nested list literal is InvalidPropertyType, as SET is.

A property holds a scalar or a list of scalars (TCK Set1 [10]; `_check_property_value_type`, which SET calls). The
value matrix found the node writers disagreeing: `CREATE (:L {p: {a: 1}})` failed with the driver's
`<ARGUMENT ERROR> Unsupported argument type` wrapped in GraphWriteError, and `CREATE (:L {p: [[1, 2], [3]]})` stored
the nested list, where `SET n.p = {a: 1}` and `SET n.p = [[1, 2], [3]]` raise TypeError. A map or nested list passed
as a parameter is stored as JSON, as before. A relationship between matched nodes stores a map literal
(docs/KNOWN_ISSUES.md), so that writer is unchanged.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


@pytest.mark.parametrize("value", ["{a: 1, b: 'x'}", "{a: {b: [1, 2]}}", "[[1, 2], [3]]", "[1, {a: 1}]"])
@pytest.mark.parametrize("verb", ["CREATE", "MERGE"])
def test_a_node_map_or_nested_list_literal_is_refused(verb, value):
    with pytest.raises(TypeError, match="InvalidPropertyType: property `p`"):
        translate_to_sql(parse_query(f"{verb} (:L {{id: 'x', p: {value}}})"), {})


@pytest.mark.parametrize("q, params", [
    ("CREATE (:L {id: 'x', p: [1, 'a', 2.5, true]})", {}),
    ("CREATE (:L {id: 'x', p: $v})", {"v": {"a": 1}}),
    ("MERGE (:L {id: 'x', p: [1, 2]})", {}),
    ("MATCH (a {node_id: 'a'}), (b {node_id: 'b'}) CREATE (a)-[:T {m: {k: 'x'}}]->(b)", {}),
])
def test_what_stays_storable(q, params):
    translate_to_sql(parse_query(q), params)
