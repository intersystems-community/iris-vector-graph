import pytest

@pytest.fixture(scope="module")
def db_conn(iris_connection):
    """Use the managed test container connection."""
    yield iris_connection

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
    """Test bulk node creation using UNWIND

    Marked xfail on "IRIS JSON_TABLE doesn't support parameterized input" until
    the translator stopped using JSON_TABLE here: `UNWIND` + `CREATE` is expanded
    at translation time into three plain INSERTs per element, so a parameterized
    list works and the marker was xpassing.
    """
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


def test_integration_direction_symmetry_optional_match(execute_cypher):
    """Direction-symmetry E2E gate: (t)-[:R]->(f) == (f)<-[:R]-(t) when f is pre-bound.

    Covers the openCypher TCK gap documented in CBM-BUG-optional-match-cross-join.md.
    A cross-join implementation produces row counts that are multiples of the outer
    group size.  The correct result is that each hub node reports exactly its real
    incoming-edge count, and both query forms return identical rows.
    """
    prefix = "DIRSYM"
    hub_a = f"{prefix}:HUB_A"
    hub_b = f"{prefix}:HUB_B"
    src1  = f"{prefix}:SRC1"
    src2  = f"{prefix}:SRC2"
    src3  = f"{prefix}:SRC3"

    # Cleanup from any prior run
    for nid in (hub_a, hub_b, src1, src2, src3):
        execute_cypher(f"MATCH (n) WHERE n.node_id = '{nid}' DETACH DELETE n")

    # Graph: src1 -> hub_a, src2 -> hub_a, src3 -> hub_b; hub_b has no callers
    execute_cypher(f"CREATE (n:DSHub {{id: '{hub_a}'}})")
    execute_cypher(f"CREATE (n:DSHub {{id: '{hub_b}'}})")
    execute_cypher(f"CREATE (n:DSSrc {{id: '{src1}'}})")
    execute_cypher(f"CREATE (n:DSSrc {{id: '{src2}'}})")
    execute_cypher(f"CREATE (n:DSSrc {{id: '{src3}'}})")
    execute_cypher(f"MATCH (s) WHERE s.node_id='{src1}' MATCH (h) WHERE h.node_id='{hub_a}' CREATE (s)-[:DSCALLS]->(h)")
    execute_cypher(f"MATCH (s) WHERE s.node_id='{src2}' MATCH (h) WHERE h.node_id='{hub_a}' CREATE (s)-[:DSCALLS]->(h)")
    execute_cypher(f"MATCH (s) WHERE s.node_id='{src3}' MATCH (h) WHERE h.node_id='{hub_b}' CREATE (s)-[:DSCALLS]->(h)")

    # Canonical form: bound hub is nodes[0]
    canonical = execute_cypher(
        "MATCH (h:DSHub) OPTIONAL MATCH (h)<-[:DSCALLS]-(t) "
        "RETURN h.node_id, count(t) ORDER BY h.node_id"
    )
    # Bug form: bound hub is nodes[1]
    bug_form = execute_cypher(
        "MATCH (h:DSHub) OPTIONAL MATCH (t)-[:DSCALLS]->(h) "
        "RETURN h.node_id, count(t) ORDER BY h.node_id"
    )

    # Cleanup
    for nid in (hub_a, hub_b, src1, src2, src3):
        execute_cypher(f"MATCH (n) WHERE n.node_id = '{nid}' DETACH DELETE n")

    # Both forms must return the same rows
    assert canonical["rows"] == bug_form["rows"], (
        f"Direction-symmetry violated on live IRIS:\n"
        f"  canonical (h)<-[:R]-(t): {canonical['rows']}\n"
        f"  bug form (t)-[:R]->(h):  {bug_form['rows']}"
    )
    # Sanity: correct counts (hub_a=2, hub_b=1)
    rows_by_hub = {r[0]: r[1] for r in canonical["rows"]}
    assert rows_by_hub.get(hub_a) == 2, f"hub_a should have 2 callers, got {rows_by_hub}"
    assert rows_by_hub.get(hub_b) == 1, f"hub_b should have 1 caller, got {rows_by_hub}"
