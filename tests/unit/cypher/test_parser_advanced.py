import pytest
from iris_vector_graph.cypher.parser import parse_query, CypherParseError
from iris_vector_graph.cypher import ast

def test_parse_create():
    query = "CREATE (a:Account {id: 'ACC1', type: 'Savings'})"
    q = parse_query(query)
    
    part = q.query_parts[0]
    create = next(c for c in part.clauses if isinstance(c, ast.CreateClause))
    pattern = create.pattern
    assert pattern.nodes[0].variable == "a"
    assert pattern.nodes[0].labels == ["Account"]
    assert pattern.nodes[0].properties["id"].value == "ACC1"
    assert pattern.nodes[0].properties["type"].value == "Savings"

def test_parse_delete():
    query = "MATCH (a) DELETE a"
    q = parse_query(query)
    
    part = q.query_parts[0]
    delete = next(c for c in part.clauses if isinstance(c, ast.DeleteClause))
    assert delete.expressions[0].name == "a"
    assert not delete.detach

def test_parse_detach_delete():
    query = "MATCH (a) DETACH DELETE a"
    q = parse_query(query)
    
    part = q.query_parts[0]
    delete = next(c for c in part.clauses if isinstance(c, ast.DeleteClause))
    assert delete.detach

def test_parse_merge():
    query = "MERGE (a:Account {id: 'ACC1'}) ON CREATE SET a.c = 1 ON MATCH SET a.m = 2"
    q = parse_query(query)
    
    part = q.query_parts[0]
    merge = next(c for c in part.clauses if isinstance(c, ast.MergeClause))
    assert merge.on_create is not None
    assert merge.on_match is not None
    assert len(merge.on_create.items) == 1
    assert len(merge.on_match.items) == 1

def test_parse_set_remove():
    query = "MATCH (a) SET a.p = 1 REMOVE a.q"
    q = parse_query(query)
    
    part = q.query_parts[0]
    set_cl = next(c for c in part.clauses if isinstance(c, ast.SetClause))
    remove_cl = next(c for c in part.clauses if isinstance(c, ast.RemoveClause))
    assert set_cl is not None
    assert remove_cl is not None

def test_parse_optional_match():
    query = "MATCH (a) OPTIONAL MATCH (a)-[:R]->(b) RETURN a, b"
    q = parse_query(query)
    
    match_clauses = [c for c in q.query_parts[0].clauses if isinstance(c, ast.MatchClause)]
    assert len(match_clauses) == 2
    assert not match_clauses[0].optional
    assert match_clauses[1].optional

def test_parse_unwind():
    query = "UNWIND $ids AS id MATCH (n {id: id}) RETURN n"
    q = parse_query(query)
    
    unwind = next(c for c in q.query_parts[0].clauses if isinstance(c, ast.UnwindClause))
    assert unwind is not None
    assert unwind.alias == "id"
