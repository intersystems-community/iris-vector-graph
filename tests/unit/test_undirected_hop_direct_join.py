"""An undirected hop from a bound node, with an anonymous relationship, joins rdf_edges once (s = src OR o_id = src).

The UNION ALL CTE it used before holds every edge of the type in both directions, and IRIS materializes it on each call
before the join: an SNB SF1 friend lookup (IS3) read all 440,000 KNOWS rows to find 64 friends, 364 ms against 1.1 ms for the
direct join. A named relationship variable keeps the CTE: the translator reads its traversal-relative ends (`r.s`, edge
equality across stages).
"""

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _sql(q, params=None):
    r = translate_to_sql(parse_query(q), params or {})
    return r.sql if isinstance(r.sql, str) else "\n;\n".join(r.sql)


IS3 = "MATCH (p:Person)-[:KNOWS]-(f:Person) WHERE p.sid = $id RETURN f.sid ORDER BY f.sid"


def test_bound_source_anonymous_relationship_joins_edges_directly():
    sql = _sql(IS3, {"id": "1"})
    assert "UNION ALL" not in sql and "_ue" not in sql, sql
    assert "JOIN rdf_edges e" in sql or "JOIN Graph_KG.rdf_edges e" in sql, sql
    assert ".s = n0.node_id OR " in sql and ".o_id = n0.node_id" in sql, sql
    assert "CASE WHEN" in sql and "THEN" in sql, sql


def test_the_edge_type_is_a_join_predicate_on_the_edge_row():
    sql = _sql(IS3, {"id": "1"})
    assert ".p = 'KNOWS'" in sql, sql
    two = _sql("MATCH (p:Person)-[:KNOWS|LIKES]-(f) WHERE p.sid = $id RETURN f", {"id": "1"})
    assert ".p IN ('KNOWS', 'LIKES')" in two and "UNION ALL" not in two, two


def test_a_named_relationship_keeps_the_cte():
    sql = _sql("MATCH (p:Person)-[r:KNOWS]-(f:Person) WHERE p.sid = $id RETURN f.sid, type(r)", {"id": "1"})
    assert "UNION ALL" in sql, sql


def test_an_unbound_first_hop_keeps_the_cte():
    sql = _sql("MATCH ()-[:KNOWS]-(f:Person) RETURN count(f)")
    assert "UNION ALL" in sql, sql


def test_a_named_graph_scopes_the_directly_joined_edge():
    sql = _sql("USE GRAPH g MATCH (p:Person)-[:KNOWS]-(f:Person) WHERE p.sid = $id RETURN f.sid", {"id": "1"})
    edge_join = next(line for line in sql.splitlines() if "rdf_edges e" in line and " OR " in line)
    assert "graph_id" in edge_join, edge_join


def test_a_self_loop_pattern_does_not_read_cte_columns():
    sql = _sql("MATCH (a:Person)-[:KNOWS]-(a) WHERE a.sid = $id RETURN a.sid", {"id": "1"})
    assert "._src" not in sql and "._dst" not in sql, sql


def test_an_optional_hop_never_puts_a_case_expression_in_an_outer_join_on():
    """IRIS rejects `x = (CASE ...)` in an outer join's ON with SQLCODE -94."""
    sql = _sql("MATCH (x:L) WHERE x.id = $id OPTIONAL MATCH (x)-[:KNOWS]-(y) RETURN x.id, y.id", {"id": "a"})
    for line in sql.splitlines():
        if line.lstrip().upper().startswith("LEFT"):
            assert "CASE WHEN" not in line, line
    assert "UNION ALL" not in sql
