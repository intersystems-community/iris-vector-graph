import pytest

def test_multi_relationship_types(execute_cypher):
    """Test MATCH (a)-[:T1|T2]->(b)"""
    query = "MATCH (t:Transaction)-[r:FROM_ACCOUNT|TO_ACCOUNT]->(a:Account) WHERE a.node_id = 'ACCOUNT:MULE1' RETURN t.node_id, r"
    result = execute_cypher(query)
    print(f"\nSQL: {result['sql']}")
    print(f"Params: {result['params']}")
    
    assert len(result["rows"]) > 0
    
    # Verify we get both types of relationships
    rel_types = set(row[1] for row in result["rows"])
    assert "FROM_ACCOUNT" in rel_types
    assert "TO_ACCOUNT" in rel_types
