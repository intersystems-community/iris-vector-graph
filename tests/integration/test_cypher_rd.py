import json

import pytest


def test_integration_basic_node_match(execute_cypher):
    """Test MATCH (a:Account) RETURN a.node_id"""
    query = "MATCH (a:Account) RETURN a.node_id LIMIT 5"
    result = execute_cypher(query)
    
    assert len(result["rows"]) > 0
    assert result["columns"] == ["a_node_id"]
    for row in result["rows"]:
        assert "ACCOUNT:" in row[0]

def test_integration_relationship_match(execute_cypher):
    """Test MATCH (t:Transaction)-[:FROM_ACCOUNT]->(a:Account)"""
    query = "MATCH (t:Transaction)-[:FROM_ACCOUNT]->(a:Account) RETURN t.node_id, a.node_id LIMIT 5"
    result = execute_cypher(query)
    
    assert len(result["rows"]) > 0
    assert "t_node_id" in result["columns"]
    assert "a_node_id" in result["columns"]
    for row in result["rows"]:
        assert "TXN:" in row[0]
        assert "ACCOUNT:" in row[1]

def test_integration_where_clause(execute_cypher):
    """Test WHERE clause with comparisons"""
    query = "MATCH (a:Account) WHERE a.risk_score >= 0.05 RETURN a.node_id, a.risk_score LIMIT 5"
    result = execute_cypher(query)
    
    assert len(result["rows"]) >= 0 # Might be 0 depending on data
    if len(result["rows"]) > 0:
        for row in result["rows"]:
            assert float(row[1]) >= 0.05

def test_integration_multi_match(execute_cypher):
    query = "MATCH (a:Account) MATCH (t:Transaction) RETURN DISTINCT a.node_id, t.node_id LIMIT 5"
    result = execute_cypher(query)

    assert len(result["rows"]) >= 1
    assert "ACCOUNT:" in str(result["rows"][0][0])
    assert "TXN:" in str(result["rows"][0][1])

def test_integration_untyped_relationship(execute_cypher):
    """Test untyped relationship pattern -[r]->"""
    query = "MATCH (t:Transaction)-[r]->(a:Account) RETURN t.node_id, r, a.node_id LIMIT 5"
    result = execute_cypher(query)
    
    assert len(result["rows"]) > 0
    assert "r" in result["columns"]
    # `r` is a relationship value carrying its type and properties, not a bare predicate.
    for row in result["rows"]:
        assert json.loads(row[1])["type"] in ["FROM_ACCOUNT", "TO_ACCOUNT"]

def test_integration_with_clause(execute_cypher):
    query = """
    MATCH (a:Account)
    WITH a
    MATCH (t:Transaction)-[:FROM_ACCOUNT]->(a)
    RETURN t.node_id, a.node_id
    LIMIT 5
    """
    result = execute_cypher(query)

    assert len(result["rows"]) > 0
    assert "ACCOUNT:" in str(result["rows"][0][1])

def test_integration_aggregations(execute_cypher):
    """Test aggregation functions COUNT, SUM, AVG"""
    query = "MATCH (t:Transaction) RETURN count(t), sum(t.amount), avg(t.amount)"
    result = execute_cypher(query)
    
    assert len(result["rows"]) == 1
    # An un-aliased projection gets a SQL-safe alias built from its expression with the
    # punctuation flattened: `count(t)` becomes `count_t_`. The translator also records
    # `count_t_` → `count(t)` in `column_name_map`, and `_engine/query.py:473-476`
    # applies that map, so `engine.execute_cypher` answers the Cypher text. This fixture
    # runs the statement itself and does not, which is why the raw alias shows here. The
    # `count_res` this line used to expect was never a name either layer produced.
    assert result["columns"] == ["count_t_", "sum_t_amount_", "avg_t_amount_"]
    # Check that we got numeric results
    assert result["rows"][0][0] > 0
    assert result["rows"][0][1] > 0
    assert result["rows"][0][2] > 0

def test_integration_built_in_functions(execute_cypher):
    """Test built-in functions id() and type()"""
    query = "MATCH (t:Transaction)-[r]->(a:Account) RETURN id(t), type(r) LIMIT 5"
    result = execute_cypher(query)
    
    assert len(result["rows"]) > 0
    # Raw SQL aliases again — see the note in test_integration_aggregations.
    assert result["columns"] == ["id_t_", "type_r_"]
    for row in result["rows"]:
        assert "TXN:" in row[0]
        # `type(r)` answers the predicate itself, unlike a bare `r`.
        assert row[1] in ["FROM_ACCOUNT", "TO_ACCOUNT"]


