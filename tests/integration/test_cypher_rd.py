import pytest

def test_integration_basic_node_match(execute_cypher):
    """Test MATCH (a:Account) RETURN a.node_id"""
    query = "MATCH (a:Account) RETURN a.node_id LIMIT 5"
    result = execute_cypher(query)
    
    assert len(result["rows"]) > 0
    assert result["columns"] == ["a_node_id"]
    for row in result["rows"]:
        assert row[0].startswith("ACCOUNT:")

def test_integration_relationship_match(execute_cypher):
    """Test MATCH (t:Transaction)-[:FROM_ACCOUNT]->(a:Account)"""
    query = "MATCH (t:Transaction)-[:FROM_ACCOUNT]->(a:Account) RETURN t.node_id, a.node_id LIMIT 5"
    result = execute_cypher(query)
    
    assert len(result["rows"]) > 0
    assert "t_node_id" in result["columns"]
    assert "a_node_id" in result["columns"]
    for row in result["rows"]:
        assert row[0].startswith("TXN:")
        assert row[1].startswith("ACCOUNT:")

def test_integration_where_clause(execute_cypher):
    """Test WHERE clause with comparisons"""
    query = "MATCH (a:Account) WHERE a.risk_score >= 0.05 RETURN a.node_id, a.risk_score LIMIT 5"
    result = execute_cypher(query)
    
    assert len(result["rows"]) >= 0 # Might be 0 depending on data
    if len(result["rows"]) > 0:
        for row in result["rows"]:
            assert float(row[1]) >= 0.05

def test_integration_multi_match(execute_cypher):
    """Test multiple MATCH clauses"""
    query = "MATCH (a:Account) MATCH (t:Transaction) WHERE a.node_id = 'ACCOUNT:MULE1' AND t.node_id = 'TXN:MULE1_IN1' RETURN DISTINCT a.node_id, t.node_id"
    result = execute_cypher(query)
    
    assert len(result["rows"]) >= 1
    assert result["rows"][0][0] == "ACCOUNT:MULE1"
    assert result["rows"][0][1] == "TXN:MULE1_IN1"

def test_integration_untyped_relationship(execute_cypher):
    """Test untyped relationship pattern -[r]->"""
    query = "MATCH (t:Transaction)-[r]->(a:Account) RETURN t.node_id, r, a.node_id LIMIT 5"
    result = execute_cypher(query)
    
    assert len(result["rows"]) > 0
    assert "r" in result["columns"]
    # Relationship variable should return the predicate name
    for row in result["rows"]:
        assert row[1] in ["FROM_ACCOUNT", "TO_ACCOUNT"]

def test_integration_with_clause(execute_cypher):
    """Test multi-stage query with WITH clause"""
    query = """
    MATCH (a:Account) 
    WHERE a.node_id = 'ACCOUNT:MULE1'
    WITH a
    MATCH (t:Transaction)-[:FROM_ACCOUNT]->(a)
    RETURN t.node_id, a.node_id
    """
    result = execute_cypher(query)
    
    assert len(result["rows"]) > 0
    assert result["rows"][0][1] == "ACCOUNT:MULE1"

def test_integration_aggregations(execute_cypher):
    """Test aggregation functions COUNT, SUM, AVG"""
    query = "MATCH (t:Transaction) RETURN count(t), sum(t.amount), avg(t.amount)"
    result = execute_cypher(query)
    
    assert len(result["rows"]) == 1
    assert "count_res" in result["columns"]
    # Check that we got numeric results
    assert result["rows"][0][0] > 0
    assert result["rows"][0][1] > 0
    assert result["rows"][0][2] > 0

def test_integration_built_in_functions(execute_cypher):
    """Test built-in functions id() and type()"""
    query = "MATCH (t:Transaction)-[r]->(a:Account) RETURN id(t), type(r) LIMIT 5"
    result = execute_cypher(query)
    
    assert len(result["rows"]) > 0
    assert "id_res" in result["columns"]
    assert "type_res" in result["columns"]
    for row in result["rows"]:
        assert row[0].startswith("TXN:")
        assert row[1] in ["FROM_ACCOUNT", "TO_ACCOUNT"]


