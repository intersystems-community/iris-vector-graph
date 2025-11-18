"""
Integration tests for PPR Functional Index with live IRIS database.

These tests validate the complete workflow:
1. Functional Index deployment
2. ^PPR Global population from SQL DML
3. PPR computation using Globals traversal
4. Result correctness vs baseline implementation

CRITICAL: These tests MUST use live IRIS database connection.
"""

import pytest
import os
import iris


@pytest.fixture
def iris_connection():
    """Connect to IRIS database for testing."""
    host = os.getenv('IRIS_HOST', 'localhost')
    port = int(os.getenv('IRIS_PORT', '1972'))
    namespace = os.getenv('IRIS_NAMESPACE', 'USER')
    username = os.getenv('IRIS_USER', '_SYSTEM')
    password = os.getenv('IRIS_PASSWORD', 'SYS')

    conn = iris.connect(
        hostname=host,
        port=port,
        namespace=namespace,
        username=username,
        password=password
    )

    yield conn

    conn.close()


@pytest.fixture
def clean_test_data(iris_connection):
    """Clean up test data before and after tests."""
    cursor = iris_connection.cursor()

    # Clean before
    cursor.execute("DELETE FROM rdf_edges WHERE s LIKE 'TEST_%'")
    cursor.execute("DELETE FROM nodes WHERE node_id LIKE 'TEST_%'")
    iris_connection.commit()

    # Clean ^PPR Global
    irispy = iris.createIRIS(iris_connection)
    try:
        # Kill all TEST_ entries in ^PPR using iterator
        nodes_to_kill = []
        for node_id, _deg in irispy.iterator('^PPR', 'deg'):
            if node_id.startswith('TEST_'):
                nodes_to_kill.append(node_id)

        for node in nodes_to_kill:
            irispy.kill('^PPR', 'deg', node)
            # Also kill out/in edges
            for dst, _val in irispy.iterator('^PPR', 'out', node):
                irispy.kill('^PPR', 'out', node, dst)
    except Exception as e:
        print(f"Warning: Could not clean ^PPR: {e}")

    yield

    # Clean after
    cursor.execute("DELETE FROM rdf_edges WHERE s LIKE 'TEST_%'")
    cursor.execute("DELETE FROM nodes WHERE node_id LIKE 'TEST_%'")
    iris_connection.commit()
    cursor.close()


@pytest.mark.requires_database
@pytest.mark.integration
def test_functional_index_basic_workflow(iris_connection, clean_test_data):
    """
    Test complete Functional Index workflow:
    1. Insert edges via SQL
    2. Verify ^PPR Global is populated
    3. Compute PPR using Functional Index
    4. Validate results
    """
    cursor = iris_connection.cursor()
    irispy = iris.createIRIS(iris_connection)

    # Step 1: Create test graph
    # Simple triangle: TEST_A -> TEST_B -> TEST_C -> TEST_A
    test_edges = [
        ('TEST_A', 'TEST_B'),
        ('TEST_B', 'TEST_C'),
        ('TEST_C', 'TEST_A'),
    ]

    # Insert nodes first
    for node in ['TEST_A', 'TEST_B', 'TEST_C']:
        cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", [node])

    # Insert edges - this should trigger Functional Index callbacks
    for src, dst in test_edges:
        cursor.execute(
            "INSERT INTO rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
            [src, 'interacts_with', dst]
        )

    iris_connection.commit()

    # Step 2: Verify ^PPR Global was populated by Functional Index
    # Check outdegrees
    deg_a = irispy.get('^PPR', 'deg', 'TEST_A')
    deg_b = irispy.get('^PPR', 'deg', 'TEST_B')
    deg_c = irispy.get('^PPR', 'deg', 'TEST_C')

    assert deg_a is not None, "^PPR(deg, TEST_A) should exist after INSERT"
    assert int(deg_a) == 1, f"TEST_A should have outdegree 1, got {deg_a}"
    assert int(deg_b) == 1, f"TEST_B should have outdegree 1, got {deg_b}"
    assert int(deg_c) == 1, f"TEST_C should have outdegree 1, got {deg_c}"

    # Check outgoing edges exist
    out_a_b = irispy.get('^PPR', 'out', 'TEST_A', 'TEST_B')
    assert out_a_b is not None, "^PPR(out, TEST_A, TEST_B) should exist"

    # Check incoming edges exist
    in_b_a = irispy.get('^PPR', 'in', 'TEST_B', 'TEST_A')
    assert in_b_a is not None, "^PPR(in, TEST_B, TEST_A) should exist"

    # Step 3: Compute PPR using Functional Index
    from iris_vector_graph.ppr_functional_index import compute_ppr_functional_index

    scores = compute_ppr_functional_index(
        iris_connection,
        seed_entities=['TEST_A'],
        damping_factor=0.85,
        max_iterations=100,
        tolerance=1e-6
    )

    # Step 4: Validate results
    assert 'TEST_A' in scores, "TEST_A should have a score"
    assert 'TEST_B' in scores, "TEST_B should have a score"
    assert 'TEST_C' in scores, "TEST_C should have a score"

    # Scores should sum to ~1.0
    total = sum(scores.values())
    assert abs(total - 1.0) < 1e-9, f"Scores should sum to 1.0, got {total}"

    # TEST_A (seed) should have highest score in symmetric triangle
    assert scores['TEST_A'] > scores['TEST_B'], "Seed should have higher score"
    assert scores['TEST_A'] > scores['TEST_C'], "Seed should have higher score"

    cursor.close()


@pytest.mark.requires_database
@pytest.mark.integration
def test_functional_index_update_callback(iris_connection, clean_test_data):
    """
    Test that UPDATE on edges triggers Functional Index correctly:
    1. Insert edge TEST_A -> TEST_B
    2. Update to TEST_A -> TEST_C
    3. Verify ^PPR reflects the change
    """
    cursor = iris_connection.cursor()
    irispy = iris.createIRIS(iris_connection)

    # Create nodes
    for node in ['TEST_A', 'TEST_B', 'TEST_C']:
        cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", [node])

    # Insert initial edge: TEST_A -> TEST_B
    cursor.execute(
        "INSERT INTO rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
        ['TEST_A', 'interacts_with', 'TEST_B']
    )
    iris_connection.commit()

    # Verify initial state
    assert irispy.get('^PPR', 'out', 'TEST_A', 'TEST_B') is not None
    # .get() may return int or str depending on IRIS version
    assert int(irispy.get('^PPR', 'deg', 'TEST_A')) == 1

    # Update edge: TEST_A -> TEST_C
    cursor.execute(
        "UPDATE rdf_edges SET o_id = ? WHERE s = ? AND p = ?",
        ['TEST_C', 'TEST_A', 'interacts_with']
    )
    iris_connection.commit()

    # Verify updated state
    # Old edge should be removed
    old_edge = irispy.get('^PPR', 'out', 'TEST_A', 'TEST_B')
    assert old_edge is None, "Old edge TEST_A -> TEST_B should be removed from ^PPR"

    # New edge should exist
    new_edge = irispy.get('^PPR', 'out', 'TEST_A', 'TEST_C')
    assert new_edge is not None, "New edge TEST_A -> TEST_C should exist in ^PPR"

    # Outdegree should remain 1
    deg = irispy.get('^PPR', 'deg', 'TEST_A')
    assert int(deg) == 1, f"TEST_A outdegree should still be 1 after UPDATE, got {deg}"

    cursor.close()


@pytest.mark.requires_database
@pytest.mark.integration
def test_functional_index_delete_callback(iris_connection, clean_test_data):
    """
    Test that DELETE on edges triggers Functional Index correctly:
    1. Insert edges TEST_A -> TEST_B, TEST_A -> TEST_C
    2. Delete TEST_A -> TEST_B
    3. Verify ^PPR is updated (outdegree decremented)
    """
    cursor = iris_connection.cursor()
    irispy = iris.createIRIS(iris_connection)

    # Create nodes
    for node in ['TEST_A', 'TEST_B', 'TEST_C']:
        cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", [node])

    # Insert two edges from TEST_A
    cursor.execute(
        "INSERT INTO rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
        ['TEST_A', 'interacts_with', 'TEST_B']
    )
    cursor.execute(
        "INSERT INTO rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
        ['TEST_A', 'regulates', 'TEST_C']
    )
    iris_connection.commit()

    # Verify initial state: outdegree = 2
    deg_before = irispy.get('^PPR', 'deg', 'TEST_A')
    assert int(deg_before) == 2, f"TEST_A should have outdegree 2, got {deg_before}"

    # Delete one edge
    cursor.execute(
        "DELETE FROM rdf_edges WHERE s = ? AND p = ? AND o_id = ?",
        ['TEST_A', 'interacts_with', 'TEST_B']
    )
    iris_connection.commit()

    # Verify updated state
    # Edge should be removed
    deleted_edge = irispy.get('^PPR', 'out', 'TEST_A', 'TEST_B')
    assert deleted_edge is None, "Deleted edge should not exist in ^PPR"

    # Outdegree should be decremented to 1
    deg_after = irispy.get('^PPR', 'deg', 'TEST_A')
    assert int(deg_after) == 1, f"TEST_A outdegree should be 1 after DELETE, got {deg_after}"

    # Remaining edge should still exist
    remaining_edge = irispy.get('^PPR', 'out', 'TEST_A', 'TEST_C')
    assert remaining_edge is not None, "Remaining edge should still exist"

    cursor.close()


@pytest.mark.requires_database
@pytest.mark.integration
def test_ppr_correctness_vs_baseline(iris_connection, clean_test_data):
    """
    Test that Functional Index PPR produces same results as Pure Python baseline.

    Create a realistic graph and compare results.
    """
    cursor = iris_connection.cursor()

    # Create test graph: 5 nodes, 7 edges
    test_nodes = ['TEST_A', 'TEST_B', 'TEST_C', 'TEST_D', 'TEST_E']
    test_edges = [
        ('TEST_A', 'TEST_B'),
        ('TEST_A', 'TEST_C'),
        ('TEST_B', 'TEST_C'),
        ('TEST_B', 'TEST_D'),
        ('TEST_C', 'TEST_D'),
        ('TEST_D', 'TEST_E'),
        ('TEST_E', 'TEST_A'),  # cycle back
    ]

    # Insert nodes
    for node in test_nodes:
        cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", [node])

    # Insert edges
    for src, dst in test_edges:
        cursor.execute(
            "INSERT INTO rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
            [src, 'interacts_with', dst]
        )

    iris_connection.commit()

    # Compute PPR using Functional Index
    from iris_vector_graph.ppr_functional_index import compute_ppr_functional_index

    scores_fi = compute_ppr_functional_index(
        iris_connection,
        seed_entities=['TEST_A'],
        damping_factor=0.85,
        max_iterations=100,
        tolerance=1e-6
    )

    # Compute PPR using Pure Python baseline (via engine)
    from iris_vector_graph.engine import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    scores_baseline = engine.kg_PERSONALIZED_PAGERANK(
        seed_entities=['TEST_A'],
        damping_factor=0.85,
        max_iterations=100,
        tolerance=1e-6,
        use_functional_index=False  # Force Pure Python
    )

    # Filter baseline to TEST_ nodes only
    scores_baseline = {k: v for k, v in scores_baseline.items() if k.startswith('TEST_')}

    # Compare results
    assert set(scores_fi.keys()) == set(scores_baseline.keys()), \
        "Functional Index and baseline should return same nodes"

    # Scores should be very close (within 1e-6)
    for node in scores_fi:
        diff = abs(scores_fi[node] - scores_baseline[node])
        assert diff < 1e-6, \
            f"Node {node}: Functional Index={scores_fi[node]:.10f}, " \
            f"Baseline={scores_baseline[node]:.10f}, diff={diff:.2e}"

    cursor.close()


@pytest.mark.requires_database
@pytest.mark.integration
def test_sink_node_handling(iris_connection, clean_test_data):
    """
    Test that sink nodes (zero outdegree) are handled correctly.

    Graph: TEST_A -> TEST_B -> TEST_C (TEST_C has no outgoing edges)
    """
    cursor = iris_connection.cursor()

    # Create nodes
    for node in ['TEST_A', 'TEST_B', 'TEST_C']:
        cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", [node])

    # Create chain: A -> B -> C (C is sink)
    cursor.execute("INSERT INTO rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
                   ['TEST_A', 'interacts_with', 'TEST_B'])
    cursor.execute("INSERT INTO rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
                   ['TEST_B', 'interacts_with', 'TEST_C'])

    iris_connection.commit()

    # Compute PPR
    from iris_vector_graph.ppr_functional_index import compute_ppr_functional_index

    scores = compute_ppr_functional_index(
        iris_connection,
        seed_entities=['TEST_A'],
        damping_factor=0.85,
        max_iterations=100,
        tolerance=1e-6
    )

    # All nodes should have scores (no division by zero errors)
    assert 'TEST_A' in scores
    assert 'TEST_B' in scores
    assert 'TEST_C' in scores

    # Scores should sum to 1.0
    total = sum(scores.values())
    assert abs(total - 1.0) < 1e-9

    cursor.close()


@pytest.mark.requires_database
@pytest.mark.integration
def test_invalid_seed_entity_error(iris_connection, clean_test_data):
    """
    Test that providing non-existent seed entity raises ValueError.
    """
    cursor = iris_connection.cursor()

    # Create one node
    cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ['TEST_A'])
    cursor.execute("INSERT INTO rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
                   ['TEST_A', 'self_loop', 'TEST_A'])
    iris_connection.commit()

    from iris_vector_graph.ppr_functional_index import compute_ppr_functional_index

    # Try to compute PPR with non-existent seed
    with pytest.raises(ValueError, match="Seed entities not found"):
        compute_ppr_functional_index(
            iris_connection,
            seed_entities=['NONEXISTENT_NODE'],
            damping_factor=0.85
        )

    cursor.close()


if __name__ == '__main__':
    # Run with: pytest tests/integration/test_ppr_functional_index_live.py -v
    pytest.main([__file__, '-v', '--tb=short'])
