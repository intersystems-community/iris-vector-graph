import pytest

def test_single_relationship_type(execute_cypher):
    """Test MATCH (a)-[:TYPE]->(b)"""
    # Assuming sample data from fraud dataset
    query = "MATCH (t:Transaction)-[:FROM_ACCOUNT]->(a:Account) RETURN t, a LIMIT 5"
    result = execute_cypher(query)
    
    assert len(result["rows"]) > 0
    assert "t" in result["columns"]
    assert "a" in result["columns"]
    
    # Check that rows contain node IDs
    for row in result["rows"]:
        assert row[0].startswith("TXN:")
        assert row[1].startswith("ACCOUNT:")

def test_relationship_with_variable(execute_cypher):
    """Test MATCH (a)-[r:TYPE]->(b)"""
    query = "MATCH (t:Transaction)-[r:FROM_ACCOUNT]->(a:Account) RETURN t, r, a LIMIT 5"
    result = execute_cypher(query)
    
    assert len(result["rows"]) > 0
    assert "r" in result["columns"]
    
    # Check that r is returned as a relationship ID or metadata
    # In current implementation, it might be the predicate or the edge ID if we return it
    for row in result["rows"]:
        # row[0]=t, row[1]=r, row[2]=a
        assert row[1] == "FROM_ACCOUNT" # Currently translator maps r to edge.p in some cases or we need to fix it
