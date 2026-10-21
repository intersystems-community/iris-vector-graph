"""USE GRAPH scopes a node the statement crosses in, as it scopes the first.

`USE GRAPH 'g' MATCH (a {node_id: $a}), (b {node_id: $b})` translates the second node to `CROSS JOIN Graph_KG.nodes n1`,
which has no ON clause, so the scoping pass (it matched `JOIN <table> <alias> ON`) left it unscoped: b matched its id
in every graph, the read returned a row per graph holding it, and a `CREATE (a)-[:R]->(b)` after it made one edge per
row (two parallel edges in a multigraph, found by the degree-tally test).
"""

import re

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _sql(cypher):
    q = translate_to_sql(parse_query(cypher), {"a": "x", "b": "y"})
    return q.sql if isinstance(q.sql, str) else " ".join(q.sql)


def test_a_crossed_in_node_is_scoped():
    sql = _sql("USE GRAPH 'g1' MATCH (a {node_id: $a}), (b {node_id: $b}) RETURN count(*) AS c")
    aliases = re.findall(r"CROSS JOIN \S*nodes (\w+)", sql)
    assert aliases, sql
    for alias in aliases:
        assert f"COALESCE({alias}.graph_id, '') = COALESCE('g1', '')" in sql, sql


def test_a_create_after_it_reads_the_scoped_pair():
    sql = _sql("USE GRAPH 'g1' MATCH (a {node_id: $a}), (b {node_id: $b}) CREATE (a)-[:R]->(b)")
    for alias in re.findall(r"CROSS JOIN \S*nodes (\w+)", sql):
        assert f"COALESCE({alias}.graph_id, '') = COALESCE('g1', '')" in sql, sql
