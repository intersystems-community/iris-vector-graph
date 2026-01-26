import pytest
from iris_devtester.utils.dbapi_compat import get_connection
import os

@pytest.fixture(scope="module")
def db_conn():
    host = os.getenv("IRIS_HOST", "localhost")
    port = int(os.getenv("IRIS_PORT", 1972))
    namespace = os.getenv("IRIS_NAMESPACE", "USER")
    username = os.getenv("IRIS_USERNAME", "_SYSTEM")
    password = os.getenv("IRIS_PASSWORD", "SYS")
    
    conn = get_connection(host, port, namespace, username, password)
    yield conn
    # Cleanup any test data if necessary?
    # For now, just close
    conn.close()

def test_integration_create_delete_lifecycle(execute_cypher):
    """Test CREATE node followed by DELETE"""
    node_id = "TEST:ADV1"
    
    # 0. Cleanup first to ensure clean state
    cleanup_query = f"MATCH (n) WHERE n.node_id = '{node_id}' DETACH DELETE n"
    execute_cypher(cleanup_query)
    
    # 1. Create node
    create_query = f"CREATE (n:TestNode {{id: '{node_id}', status: 'New'}})"
    execute_cypher(create_query)
    
    # 2. Verify exists
    match_query = f"MATCH (n:TestNode) WHERE n.node_id = '{node_id}' RETURN n.status"
    result = execute_cypher(match_query)
    assert len(result["rows"]) == 1
    assert result["rows"][0][0] == "New"
    
    # 3. Update node (SET)
    set_query = f"MATCH (n:TestNode) WHERE n.node_id = '{node_id}' SET n.status = 'Updated'"
    execute_cypher(set_query)
    
    result = execute_cypher(match_query)
    assert result["rows"][0][0] == "Updated"
    
    # 4. Remove property
    remove_query = f"MATCH (n:TestNode) WHERE n.node_id = '{node_id}' REMOVE n.status"
    execute_cypher(remove_query)
    
    result = execute_cypher(match_query)
    # Status should be NULL or row might not match if we join on rdf_props
    # Current translator joins on rdf_props, so it might return 0 rows if key is missing
    # or return NULL if LEFT JOIN.
    # Let's check.
    
    # 5. Delete node
    delete_query = f"MATCH (n:TestNode) WHERE n.node_id = '{node_id}' DELETE n"
    execute_cypher(delete_query)
    
    # 6. Verify gone
    result = execute_cypher(match_query)
    assert len(result["rows"]) == 0

def test_integration_unwind_create(execute_cypher):
    """Test bulk node creation using UNWIND"""
    node_ids = [f"TEST:UNWIND_{i}" for i in range(5)]
    
    # 1. Bulk create
    # Cypher uses $ for parameters
    unwind_query = "UNWIND $ids AS id CREATE (n:UnwindNode {id: id})"
    execute_cypher(unwind_query, params={"ids": node_ids})
    
    # 2. Verify
    match_query = "MATCH (n:UnwindNode) RETURN count(n) AS cnt"
    result = execute_cypher(match_query)
    assert result["rows"][0][0] >= 5
    
    # 3. Cleanup
    execute_cypher("MATCH (n:UnwindNode) DETACH DELETE n")

def test_integration_optional_match(execute_cypher):
    """Test OPTIONAL MATCH returns NULL for missing relationships"""
    # ACCOUNT:MULE1 has relationships, but let's find one that doesn't
    node_id = "TEST:OPT1"
    execute_cypher(f"CREATE (n:OptNode {{id: '{node_id}'}})")
    
    # OPTIONAL MATCH to non-existent
    query = f"MATCH (n:OptNode) WHERE n.node_id = '{node_id}' OPTIONAL MATCH (n)-[:NON_EXISTENT]->(m) RETURN n.node_id, m.node_id"
    result = execute_cypher(query)
    
    assert len(result["rows"]) == 1
    assert result["rows"][0][0] == node_id
    assert result["rows"][0][1] is None
    
    # Cleanup
    execute_cypher(f"MATCH (n:OptNode) WHERE n.node_id = '{node_id}' DELETE n")
