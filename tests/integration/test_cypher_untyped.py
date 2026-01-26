import pytest

def test_untyped_relationship(execute_cypher):
    """Test MATCH (a)-[r]->(b)"""
    query = "MATCH (t:Transaction)-[r]->(b) RETURN t.node_id, r, b.node_id LIMIT 10"
    result = execute_cypher(query)
    
    assert len(result["rows"]) > 0
    
    # Verify we get multiple relationship types if they exist
    rel_types = set(row[1] for row in result["rows"])
    assert len(rel_types) >= 1
    assert "FROM_ACCOUNT" in rel_types or "TO_ACCOUNT" in rel_types
