import pytest

def test_multi_relationship_types(execute_cypher):
    query = "MATCH (t:Transaction)-[r:FROM_ACCOUNT|TO_ACCOUNT]->(a:Account) RETURN t.node_id, r LIMIT 5"
    result = execute_cypher(query)

    assert len(result["rows"]) > 0
    rel_types = set(row[1] for row in result["rows"])
    assert "FROM_ACCOUNT" in rel_types or "TO_ACCOUNT" in rel_types
