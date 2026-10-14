"""A `$parameter` in a node's property map must reach the SQL as its value when the pattern's relationship is undirected.

`MATCH (p:Person {sid: $id})-[:KNOWS]-(f:Person)` joined the property a second time while building the undirected edge and
bound the parameter's AST text ("Variable(name='id')") instead of its value, so the query matched nothing and said nothing.
"""

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

SHAPES = {
    "undirected": "MATCH (p:Person {sid: $id})-[:KNOWS]-(f:Person) RETURN f.sid",
    "undirected-target": "MATCH (f:Person)-[:KNOWS]-(p:Person {sid: $id}) RETURN f.sid",
    "undirected-ordered": "MATCH (p:Person {sid: $id})-[:KNOWS]-(f:Person) RETURN f.sid, f.firstName ORDER BY f.firstName",
    "directed": "MATCH (p:Person {sid: $id})-[:KNOWS]->(f:Person) RETURN f.sid",
}


def _bound(query, params):
    sql = translate_to_sql(parse_query(query), params)
    return [v for row in sql.parameters for v in row]


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_parameter_in_a_property_map_binds_its_value(shape):
    bound = _bound(SHAPES[shape], {"id": "14"})
    assert not [v for v in bound if "Variable" in str(v)], bound
    assert "14" in bound


def test_every_use_of_the_property_filter_binds_the_value():
    bound = _bound(SHAPES["undirected"], {"id": "14"})
    assert bound.count("14") >= 1 and all(isinstance(v, str) and v for v in bound)
