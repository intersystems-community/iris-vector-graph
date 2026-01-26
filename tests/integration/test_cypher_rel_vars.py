import pytest

def test_relationship_variable_return(execute_cypher):
    """Test returning a relationship variable"""
    query = "MATCH (t:Transaction)-[r:FROM_ACCOUNT]->(a:Account) RETURN r LIMIT 1"
    result = execute_cypher(query)
    
    assert len(result["rows"]) > 0
    assert result["columns"] == ["r"]
    # Check that r returns the predicate name for now
    assert result["rows"][0][0] == "FROM_ACCOUNT"

def test_multiple_nodes_and_rel_vars(execute_cypher):
    """Test returning multiple nodes and a relationship variable"""
    query = "MATCH (t:Transaction)-[r:TO_ACCOUNT]->(a:Account) RETURN t.amount, r, a.node_id LIMIT 1"
    result = execute_cypher(query)
    print(f"\nSQL: {result['sql']}")
    print(f"Params: {result['params']}")
    print(f"Rows: {result['rows']}")
    
    assert len(result["rows"]) > 0
    # result["rows"][0] should be (amount, "TO_ACCOUNT", node_id)
    assert result["rows"][0][1] == "TO_ACCOUNT"
