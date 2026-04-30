import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def test_translate_return_node_includes_labels_and_properties():
    query = "MATCH (n) RETURN n"
    parsed = parse_query(query)
    sql_query = translate_to_sql(parsed)
    sql = "\n".join(sql_query.sql) if isinstance(sql_query.sql, list) else sql_query.sql

    assert "JSON_ARRAYAGG(label)" in sql
    assert "JSON_ARRAYAGG" in sql


def test_translate_labels_function():
    query = "MATCH (n) RETURN labels(n)"
    parsed = parse_query(query)
    sql_query = translate_to_sql(parsed)
    sql = "\n".join(sql_query.sql) if isinstance(sql_query.sql, list) else sql_query.sql

    assert "JSON_ARRAYAGG(label)" in sql


def test_translate_properties_function():
    query = "MATCH (n) RETURN properties(n)"
    parsed = parse_query(query)
    sql_query = translate_to_sql(parsed)
    sql = "\n".join(sql_query.sql) if isinstance(sql_query.sql, list) else sql_query.sql

    assert "JSON_ARRAYAGG" in sql


def test_translate_order_by_limit():
    """Test ORDER BY and LIMIT translation. NULLS LAST removed for IRIS compatibility."""
    query = "MATCH (n) RETURN n.id ORDER BY n.created_at DESC LIMIT 10"
    parsed = parse_query(query)
    sql_query = translate_to_sql(parsed)
    sql = "\n".join(sql_query.sql) if isinstance(sql_query.sql, list) else sql_query.sql

    assert "ORDER BY" in sql
    assert "DESC" in sql
    # IRIS doesn't support NULLS LAST, so we don't emit it
    assert "LIMIT 10" in sql


def test_translate_numeric_comparison_cast():
    query = "MATCH (n) WHERE n.score >= 0.5 RETURN n.id"
    parsed = parse_query(query)
    sql_query = translate_to_sql(parsed)
    sql = "\n".join(sql_query.sql) if isinstance(sql_query.sql, list) else sql_query.sql

    assert "CAST" in sql
    assert "rdf_props" in sql
    assert 'p1."key" = ?' in sql


def test_translate_type_function():
    """Test that type(r) returns the relationship type from rdf_edges.p"""
    query = "MATCH (a)-[r]->(b) RETURN type(r)"
    parsed = parse_query(query)
    sql_query = translate_to_sql(parsed)
    sql = "\n".join(sql_query.sql) if isinstance(sql_query.sql, list) else sql_query.sql

    # type(r) should reference the edge alias's p column
    assert ".p AS type_res" in sql
    assert "rdf_edges" in sql or "MatchEdges" in sql


def test_translate_type_function_in_where():
    """Test that type(r) can be used in WHERE clause"""
    query = 'MATCH (a)-[r]->(b) WHERE type(r) = "KNOWS" RETURN a'
    parsed = parse_query(query)
    sql_query = translate_to_sql(parsed)
    sql = "\n".join(sql_query.sql) if isinstance(sql_query.sql, list) else sql_query.sql

    assert "rdf_edges" in sql or "MatchEdges" in sql
    assert ".p = ?" in sql or ".p =" in sql


def test_translate_pattern_operators():
    """Test CONTAINS, STARTS WITH, ENDS WITH operators"""
    # CONTAINS
    query = "MATCH (n) WHERE n.name CONTAINS 'test' RETURN n"
    parsed = parse_query(query)
    sql = translate_to_sql(parsed)
    sql_str = "\n".join(sql.sql) if isinstance(sql.sql, list) else sql.sql
    assert "LIKE" in sql_str

    # STARTS WITH
    query2 = "MATCH (n) WHERE n.name STARTS WITH 'foo' RETURN n"
    parsed2 = parse_query(query2)
    sql2 = translate_to_sql(parsed2)
    sql_str2 = "\n".join(sql2.sql) if isinstance(sql2.sql, list) else sql2.sql
    assert "LIKE" in sql_str2

    # ENDS WITH
    query3 = "MATCH (n) WHERE n.name ENDS WITH 'bar' RETURN n"
    parsed3 = parse_query(query3)
    sql3 = translate_to_sql(parsed3)
    sql_str3 = "\n".join(sql3.sql) if isinstance(sql3.sql, list) else sql3.sql
    assert "LIKE" in sql_str3


def test_inline_property_filter_on_relationship_target():
    """Inline props on relationship target must generate WHERE conditions, not be silently dropped.

    Bug: MATCH (t)-[:R]->(c:Label {id: 'x'}) returned all nodes instead of filtering,
    because translate_relationship_pattern did not apply target_node.properties after joining.
    """
    query = "MATCH (t:IServiceTicket)-[:TICKET_FOR]->(c:Customer {id: 'Customer:Northwell'}) RETURN t.id as id"
    parsed = parse_query(query)
    sql_result = translate_to_sql(parsed)
    sql_str = "\n".join(sql_result.sql) if isinstance(sql_result.sql, list) else sql_result.sql
    # SQLQuery.parameters is a list of param-lists (one per statement); flatten to check
    params = [p for plist in sql_result.parameters for p in plist]

    # The literal is passed as a bind param — check the params list, not the SQL string
    assert "Customer:Northwell" in params, \
        f"Inline property filter on relationship target was dropped — literal not in params: {params}"
    # Must use node_id equality for id/node_id keys (not rdf_props join)
    assert "node_id" in sql_str


def test_inline_property_filter_on_relationship_source():
    """Inline props on relationship source must also generate WHERE conditions."""
    query = "MATCH (t:IServiceTicket {status: 'Open'})-[:TICKET_FOR]->(c:Customer) RETURN c.id as cid"
    parsed = parse_query(query)
    sql_result = translate_to_sql(parsed)
    params = [p for plist in sql_result.parameters for p in plist]

    assert "Open" in params, \
        f"Inline property filter on relationship source was dropped — literal not in params: {params}"


def test_anonymous_source_node_pattern():
    """MATCH ()-[r]->() must not raise KeyError for anonymous source node."""
    sql = translate_to_sql(parse_query("MATCH ()-[r]->() RETURN count(r) AS c"))
    assert "rdf_edges" in sql.sql or "MatchEdges" in sql.sql
    assert "COUNT" in sql.sql.upper()


def test_anonymous_target_node_pattern():
    """MATCH (a:Gene)-[]->() must not raise KeyError for anonymous target."""
    sql = translate_to_sql(parse_query("MATCH (a:Gene)-[]->() RETURN a.id LIMIT 3"))
    assert "rdf_edges" in sql.sql or "MatchEdges" in sql.sql


def test_anonymous_both_nodes_pattern():
    """MATCH ()-[r]->(b) works without crashing."""
    sql = translate_to_sql(parse_query("MATCH ()-[r]->(b) RETURN b.id LIMIT 3"))
    assert "rdf_edges" in sql.sql or "MatchEdges" in sql.sql


def test_in_list_param_expands_placeholders():
    """WHERE a.id IN $ids with a list param must produce IN (?,?,?) not IN ?."""
    q = "MATCH (a)-[r]-(b) WHERE a.id IN $node_ids RETURN a.id AS src, type(r) AS rel, b.id AS dst LIMIT 100"
    result = translate_to_sql(parse_query(q), {"node_ids": ["node1", "node2", "node3"]})
    assert "IN (?, ?, ?)" in result.sql or "IN (?,?,?)" in result.sql.replace(" ", ""), \
        f"Expected IN (?,?,?) expansion, got: {result.sql}"
    params = result.parameters[0] if result.parameters else []
    assert "node1" in params and "node2" in params and "node3" in params, \
        f"Expected list values as individual params, got: {params}"
    assert not any(isinstance(p, list) for p in params), \
        f"No nested list should appear in params after expansion, got: {params}"


def test_in_literal_list_expands_placeholders():
    """WHERE a.id IN ['x','y','z'] must produce IN (?,?,?) not IN [literal]."""
    q = "MATCH (a) WHERE a.id IN ['python','rust','go'] RETURN a.id"
    result = translate_to_sql(parse_query(q), {})
    assert "IN (?, ?, ?)" in result.sql or "IN (?,?,?)" in result.sql.replace(" ", ""), \
        f"Expected IN (?,?,?) expansion for literal list, got: {result.sql}"
    params = result.parameters[0] if result.parameters else []
    assert "python" in params and "rust" in params and "go" in params


def test_in_param_empty_list_handled():
    """WHERE a.id IN $ids with empty list should not crash (returns no results)."""
    q = "MATCH (a) WHERE a.id IN $ids RETURN a.id"
    result = translate_to_sql(parse_query(q), {"ids": []})
    assert result.sql is not None


def test_undirected_1hop_uses_union_all_not_or_join():
    q = "MATCH (a)-[r]-(b) WHERE a.id = $x RETURN b.id, type(r)"
    r = translate_to_sql(parse_query(q), {"x": "hla-b27"})
    assert "UNION ALL" in r.sql, "undirected pattern must use UNION ALL not OR-join"
    assert " OR " not in r.sql or "o_id" not in r.sql.split("OR")[0], \
        "OR-join pattern must be eliminated for undirected 1-hop"


def test_undirected_with_node_props_uses_union_all():
    q = "MATCH (a)-[r]-(b) WHERE a.id = $x RETURN a.id, b.id, b.name, type(r)"
    r = translate_to_sql(parse_query(q), {"x": "hla-b27"})
    assert "UNION ALL" in r.sql
    assert "e2._p AS type_res" in r.sql, "type(r) must use _p column alias in UNION subquery"


def test_undirected_typed_rel_uses_union_all():
    q = "MATCH (a)-[r:KNOWS]-(b) WHERE a.id = $x RETURN b.id"
    r = translate_to_sql(parse_query(q), {"x": "hla-b27"})
    assert "UNION ALL" in r.sql
    assert "'KNOWS'" in r.sql, "predicate inlined as literal inside UNION arms (not as bind param)"
    qmarks = r.sql.count("?")
    pcount = len(r.parameters[0]) if r.parameters else 0
    assert qmarks == pcount, f"param count mismatch: {qmarks} ?s vs {pcount} params"


def test_directed_outgoing_still_uses_single_join():
    q = "MATCH (a)-[r]->(b) WHERE a.id = $x RETURN b.id, type(r)"
    r = translate_to_sql(parse_query(q), {"x": "hla-b27"})
    assert "UNION ALL" not in r.sql
    assert "JOIN rdf_edges" in r.sql
    assert "e2.p AS type_res" in r.sql


def test_directed_incoming_still_uses_single_join():
    q = "MATCH (a)<-[r]-(b) WHERE a.id = $x RETURN b.id"
    r = translate_to_sql(parse_query(q), {"x": "hla-b27"})
    assert "UNION ALL" not in r.sql
    assert "JOIN rdf_edges" in r.sql


def test_multi_query_parts_parses():
    from iris_vector_graph.cypher.parser import parse_query
    q = "MATCH (a) RETURN a.id MATCH (b) WHERE b.id = 1 RETURN b.id"
    parsed = parse_query(q)
    assert len(parsed.subsequent_queries) == 1

def test_multi_query_three_parts_parses():
    from iris_vector_graph.cypher.parser import parse_query
    q = "MATCH (a) RETURN a.id MATCH (b) RETURN b.id MATCH (c) RETURN c.id"
    parsed = parse_query(q)
    assert len(parsed.subsequent_queries) >= 1

def test_single_query_unchanged():
    from iris_vector_graph.cypher.parser import parse_query
    q = "MATCH (a) WHERE a.id = $x RETURN a.id"
    parsed = parse_query(q)
    assert len(parsed.subsequent_queries) == 0

def test_gqs_oracle_pattern_parses():
    from iris_vector_graph.cypher.parser import parse_query
    q = "MATCH (a)-[r]-(b) WHERE a.id = 1 RETURN count(*) MATCH (a)-[r2]-(b) WHERE a.id = 1 RETURN DISTINCT b.id"
    parsed = parse_query(q)
    assert len(parsed.subsequent_queries) >= 1
