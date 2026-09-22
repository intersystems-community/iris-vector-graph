import json

import pytest


def test_untyped_relationship(execute_cypher):
    """Test MATCH (a)-[r]->(b)

    `r` is a relationship value — `{"type": ..., "props": ...}` — not a bare predicate
    name. That is the shape the Bolt encoder tags as a relationship, and this assertion
    only started reporting it once the `FETCH FIRST`-over-a-JOIN segfault stopped killing
    the process first.
    """
    query = "MATCH (t:Transaction)-[r]->(b) RETURN t.node_id, r, b.node_id LIMIT 10"
    result = execute_cypher(query)

    assert len(result["rows"]) > 0

    # Verify we get multiple relationship types if they exist
    rel_types = {json.loads(row[1])["type"] for row in result["rows"]}
    assert len(rel_types) >= 1
    assert "FROM_ACCOUNT" in rel_types or "TO_ACCOUNT" in rel_types
