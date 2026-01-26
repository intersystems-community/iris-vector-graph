import pytest
from iris_vector_graph.cypher.parser import parse_query, CypherParseError
from iris_vector_graph.cypher import ast

def test_parse_basic_match():
    query = "MATCH (n:Person) RETURN n"
    q = parse_query(query)
    
    assert len(q.query_parts) == 1
    part = q.query_parts[0]
    assert len(part.match_clauses) == 1
    pattern = part.match_clauses[0].pattern
    assert len(pattern.nodes) == 1
    assert pattern.nodes[0].variable == "n"
    assert pattern.nodes[0].labels == ["Person"]
    
    assert q.return_clause is not None
    assert len(q.return_clause.items) == 1
    assert isinstance(q.return_clause.items[0].expression, ast.Variable)
    assert q.return_clause.items[0].expression.name == "n"

def test_parse_relationship_patterns():
    queries = [
        "MATCH (a)-[:TYPE]->(b) RETURN a",
        "MATCH (a)-[r:T1|T2]->(b) RETURN r",
        "MATCH (a)-[]->(b) RETURN b",
        "MATCH (a)-[r]->(b) RETURN r"
    ]
    
    for q_str in queries:
        q = parse_query(q_str)
        assert len(q.query_parts[0].match_clauses[0].pattern.relationships) == 1

def test_parse_multi_match():
    query = "MATCH (a:A) MATCH (b:B) RETURN a, b"
    q = parse_query(query)
    
    assert len(q.query_parts) == 1
    assert len(q.query_parts[0].match_clauses) == 2

def test_parse_where_clause():
    query = "MATCH (n) WHERE n.name = 'Alice' AND n.age > 30 RETURN n"
    q = parse_query(query)
    
    where = q.query_parts[0].where_clause
    assert where is not None
    assert isinstance(where.expression, ast.BooleanExpression)
    assert where.expression.operator == ast.BooleanOperator.AND

def test_parse_limit_skip():
    query = "MATCH (n) RETURN n SKIP 10 LIMIT 5"
    q = parse_query(query)
    
    assert q.skip == 10
    assert q.limit == 5

def test_parse_errors():
    with pytest.raises(CypherParseError):
        parse_query("MATCH (n:Person RETURN n") # Missing closing paren
    
    with pytest.raises(CypherParseError):
        parse_query("MATCH (n)-[:TYPE] RETURN n") # Missing target node

def test_parse_with_clause():
    query = """
    MATCH (a:Account)
    WITH a, a.risk_score AS score
    WHERE score > 0.5
    MATCH (a)-[r]->(t:Transaction)
    RETURN a, t
    """
    q = parse_query(query)
    
    assert len(q.query_parts) == 2
    assert q.query_parts[0].with_clause is not None
    assert len(q.query_parts[0].with_clause.items) == 2
    assert q.query_parts[0].with_clause.where_clause is not None
    assert q.query_parts[1].match_clauses[0].pattern.nodes[0].variable == "a"

def test_parse_aggregations():
    query = "MATCH (t:Transaction) RETURN count(t), sum(t.amount), avg(t.amount)"
    q = parse_query(query)
    
    items = q.return_clause.items
    assert len(items) == 3
    assert isinstance(items[0].expression, ast.AggregationFunction)
    assert items[0].expression.function_name == "count"
    assert items[1].expression.function_name == "sum"
    assert items[2].expression.function_name == "avg"

def test_parse_built_in_functions():
    query = "MATCH (n) RETURN id(n), type(r), labels(n)"
    q = parse_query(query)
    
    items = q.return_clause.items
    assert len(items) == 3
    assert isinstance(items[0].expression, ast.FunctionCall)
    assert items[0].expression.function_name == "id"
    assert items[1].expression.function_name == "type"
    assert items[2].expression.function_name == "labels"


