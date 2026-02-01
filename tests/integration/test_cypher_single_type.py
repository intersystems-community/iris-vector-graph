import pytest

def test_single_relationship_type(execute_cypher):
    """Test MATCH (a)-[:TYPE]->(b)"""
    # Assuming sample data from fraud dataset
    query = "MATCH (t:Transaction)-[:FROM_ACCOUNT]->(a:Account) RETURN t, a LIMIT 5"
    result = execute_cypher(query)
    
    assert len(result["rows"]) > 0
    # Nodes expand to {var}_id, {var}_labels, {var}_props
    assert "t_id" in result["columns"]
    assert "a_id" in result["columns"]
    
    # Check that rows contain node IDs (t_id is first column, a_id is 4th column)
    for row in result["rows"]:
        assert row[0].startswith("TXN:")
        assert row[3].startswith("ACCOUNT:")  # a_id is at index 3 (after t_id, t_labels, t_props)

def test_relationship_with_variable(execute_cypher):
    """Test MATCH (a)-[r:TYPE]->(b)"""
    query = "MATCH (t:Transaction)-[r:FROM_ACCOUNT]->(a:Account) RETURN t, r, a LIMIT 5"
    result = execute_cypher(query)
    
    assert len(result["rows"]) > 0
    assert "r" in result["columns"]
    
    # Columns: t_id, t_labels, t_props, r, a_id, a_labels, a_props
    # r is at index 3
    for row in result["rows"]:
        assert row[3] == "FROM_ACCOUNT"  # r maps to edge.p (the relationship type)
