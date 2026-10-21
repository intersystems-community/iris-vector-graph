"""Spec 253: a whole-label count goes to the SQL path, which answers it from an index.

`MATCH (p:Person) RETURN count(p)` walks every member of the label natively (10.9 ms at SF1 on AWS) where the SQL path
counts the label's index (3.2 ms; Neo4j 1.0). The planner declines that shape: one labelled node, no hop, no filter,
nothing grouped, only counts. Anything more stays native.
"""

import pytest

from iris_vector_graph.cypher.native_plan import plan_query
from iris_vector_graph.cypher.parser import parse_query


@pytest.mark.parametrize(
    "cypher",
    ["MATCH (p:Person) RETURN count(p)", "MATCH (p:Person) RETURN count(*)", "MATCH (p:Person) RETURN count(p) AS n"],
)
def test_a_whole_label_count_takes_the_sql_path(cypher):
    assert plan_query(parse_query(cypher), {}) is None


@pytest.mark.parametrize(
    "cypher",
    [
        "MATCH (p:Person) WHERE p.age > '3' RETURN count(p)",
        "MATCH (p:Person) RETURN p.city AS c, count(p) AS n",
        "MATCH (p:Person)-[:KNOWS]->(f) RETURN count(f)",
        "MATCH (p:Person {city: 'x'}) RETURN count(p)",
    ],
)
def test_anything_more_stays_native(cypher):
    assert plan_query(parse_query(cypher), {}) is not None
