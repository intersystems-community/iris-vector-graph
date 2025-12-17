"""
Integration tests for NodePK foreign key constraints.

These tests validate the SQL contracts defined in specs/001-add-explicit-nodepk/contracts/sql_contracts.md
All tests MUST run against a live IRIS database instance (Constitutional Principle II).

Test Strategy:
- Each contract has dedicated test methods
- Tests are written BEFORE implementation (TDD)
- All tests should FAIL initially (nodes table doesn't exist yet)
- After implementation, all tests should PASS
"""

import pytest
import os
from datetime import datetime
from dotenv import load_dotenv

# NOTE: iris_module is not needed - tests use iris_connection fixture from conftest.py


# NOTE: iris_connection fixture is provided by tests/conftest.py
# Do not define a local fixture here to avoid shadowing


@pytest.fixture(autouse=True)
def cleanup_test_data(iris_connection):
    """Clean up test data before and after each test."""
    cursor = iris_connection.cursor()

    # Clean up before test
    test_prefixes = ['TEST:', 'TEMP:', 'INVALID:', 'NODE:', 'PROTEIN:', 'DISEASE:']
    for prefix in test_prefixes:
        try:
            # Clean dependent tables first (if they have FK constraints)
            # Skip kg_NodeEmbeddings - requires VECTOR type support not available in test environment
            cursor.execute("DELETE FROM rdf_edges WHERE s LIKE ? OR o_id LIKE ?", [f"{prefix}%", f"{prefix}%"])
            cursor.execute("DELETE FROM rdf_props WHERE s LIKE ?", [f"{prefix}%"])
            cursor.execute("DELETE FROM rdf_labels WHERE s LIKE ?", [f"{prefix}%"])
            # Clean nodes table last (if it exists)
            cursor.execute("DELETE FROM nodes WHERE node_id LIKE ?", [f"{prefix}%"])
            iris_connection.commit()
        except:
            # Tables might not exist yet (expected for initial test run)
            iris_connection.rollback()

    yield

    # Clean up after test (same as before)
    for prefix in test_prefixes:
        try:
            cursor.execute("DELETE FROM rdf_edges WHERE s LIKE ? OR o_id LIKE ?", [f"{prefix}%", f"{prefix}%"])
            cursor.execute("DELETE FROM rdf_props WHERE s LIKE ?", [f"{prefix}%"])
            cursor.execute("DELETE FROM rdf_labels WHERE s LIKE ?", [f"{prefix}%"])
            cursor.execute("DELETE FROM nodes WHERE node_id LIKE ?", [f"{prefix}%"])
            iris_connection.commit()
        except:
            iris_connection.rollback()


@pytest.mark.requires_database
@pytest.mark.integration
class TestNodeCreation:
    """Contract 1: Create Node tests."""

    def test_create_node_success(self, iris_connection):
        """
        GIVEN: nodes table exists (after implementation)
        WHEN: inserting a new node with valid node_id
        THEN: node is created with auto-generated created_at timestamp

        Expected: FAIL initially (nodes table doesn't exist)
        """
        cursor = iris_connection.cursor()

        # Insert node
        cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ['TEST:node1'])
        iris_connection.commit()

        # Verify node exists
        cursor.execute("SELECT node_id, created_at FROM nodes WHERE node_id = ?", ['TEST:node1'])
        result = cursor.fetchone()

        assert result is not None, "Node should exist after insertion"
        assert result[0] == 'TEST:node1', "Node ID should match"
        assert isinstance(result[1], datetime), "created_at should be a timestamp"
        assert result[1] is not None, "created_at should be set automatically"

    def test_create_node_duplicate_fails(self, iris_connection):
        """
        GIVEN: a node with ID 'TEST:node1' already exists
        WHEN: attempting to insert another node with same ID
        THEN: UNIQUE constraint violation is raised

        Expected: FAIL initially (nodes table doesn't exist)
        """
        cursor = iris_connection.cursor()

        # First insertion should succeed
        cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ['TEST:node1'])
        iris_connection.commit()

        # Second insertion should fail with UNIQUE violation
        with pytest.raises(Exception) as exc_info:
            cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ['TEST:node1'])
            iris_connection.commit()

        # Check for UNIQUE constraint violation
        error_msg = str(exc_info.value).lower()
        assert 'unique' in error_msg or 'duplicate' in error_msg or 'constraint' in error_msg, \
            f"Expected UNIQUE constraint violation, got: {exc_info.value}"

    def test_create_node_null_id_fails(self, iris_connection):
        """
        GIVEN: nodes table exists
        WHEN: attempting to insert node with NULL node_id
        THEN: NOT NULL constraint violation is raised

        Expected: FAIL initially (nodes table doesn't exist)
        """
        cursor = iris_connection.cursor()

        with pytest.raises(Exception) as exc_info:
            cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", [None])
            iris_connection.commit()

        # Check for NOT NULL constraint violation
        error_msg = str(exc_info.value).lower()
        assert 'null' in error_msg or 'constraint' in error_msg or 'required field' in error_msg, \
            f"Expected NOT NULL constraint violation, got: {exc_info.value}"


@pytest.mark.requires_database
@pytest.mark.integration
class TestEdgeForeignKeys:
    """Contract 2: Create Edge with Node Validation tests."""

    def test_edge_insert_requires_source_node(self, iris_connection):
        """
        GIVEN: nodes table with no node 'INVALID:source'
        WHEN: inserting edge with s='INVALID:source'
        THEN: FK constraint violation raised

        Expected: FAIL initially (FK constraint doesn't exist)
        """
        cursor = iris_connection.cursor()

        # Create destination node only
        cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ['TEST:dest'])
        iris_connection.commit()

        # Try to insert edge with non-existent source
        with pytest.raises(Exception) as exc_info:
            cursor.execute(
                "INSERT INTO rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
                ['INVALID:source', 'relates_to', 'TEST:dest']
            )
            iris_connection.commit()

        # Check for FK constraint violation
        error_msg = str(exc_info.value).lower()
        assert 'foreign key' in error_msg or 'constraint' in error_msg or 'fk_edges_source' in error_msg, \
            f"Expected FK constraint violation for source node, got: {exc_info.value}"

    def test_edge_insert_requires_dest_node(self, iris_connection):
        """
        GIVEN: nodes table with no node 'INVALID:dest'
        WHEN: inserting edge with o_id='INVALID:dest'
        THEN: FK constraint violation raised

        Expected: FAIL initially (FK constraint doesn't exist)
        """
        cursor = iris_connection.cursor()

        # Create source node only
        cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ['TEST:source'])
        iris_connection.commit()

        # Try to insert edge with non-existent destination
        with pytest.raises(Exception) as exc_info:
            cursor.execute(
                "INSERT INTO rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
                ['TEST:source', 'relates_to', 'INVALID:dest']
            )
            iris_connection.commit()

        # Check for FK constraint violation
        error_msg = str(exc_info.value).lower()
        assert 'foreign key' in error_msg or 'constraint' in error_msg or 'fk_edges_dest' in error_msg, \
            f"Expected FK constraint violation for destination node, got: {exc_info.value}"

    def test_edge_insert_success_both_nodes_exist(self, iris_connection):
        """
        GIVEN: both source and destination nodes exist
        WHEN: inserting edge between them
        THEN: edge is created successfully

        Expected: FAIL initially (nodes table doesn't exist)
        """
        cursor = iris_connection.cursor()

        # Create both nodes
        cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ['PROTEIN:TP53'])
        cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ['DISEASE:cancer'])
        iris_connection.commit()

        # Insert edge - should succeed
        cursor.execute(
            "INSERT INTO rdf_edges (s, p, o_id, qualifiers) VALUES (?, ?, ?, ?)",
            ['PROTEIN:TP53', 'associated_with', 'DISEASE:cancer', '{"confidence": 0.95}']
        )
        iris_connection.commit()

        # Verify edge exists
        cursor.execute(
            "SELECT s, p, o_id FROM rdf_edges WHERE s = ? AND o_id = ?",
            ['PROTEIN:TP53', 'DISEASE:cancer']
        )
        result = cursor.fetchone()

        assert result is not None, "Edge should exist after insertion"
        assert result[0] == 'PROTEIN:TP53', "Source should match"
        assert result[1] == 'associated_with', "Predicate should match"
        assert result[2] == 'DISEASE:cancer', "Destination should match"


@pytest.mark.requires_database
@pytest.mark.integration
class TestLabelForeignKeys:
    """Contract 3: Assign Label to Node tests."""

    def test_label_requires_node(self, iris_connection):
        """
        GIVEN: no node exists with ID 'INVALID:node'
        WHEN: attempting to assign label to 'INVALID:node'
        THEN: FK constraint violation raised

        Expected: FAIL initially (FK constraint doesn't exist)
        """
        cursor = iris_connection.cursor()

        with pytest.raises(Exception) as exc_info:
            cursor.execute(
                "INSERT INTO rdf_labels (s, label) VALUES (?, ?)",
                ['INVALID:node', 'some_label']
            )
            iris_connection.commit()

        # Check for FK constraint violation
        error_msg = str(exc_info.value).lower()
        assert 'foreign key' in error_msg or 'constraint' in error_msg or 'fk_labels_node' in error_msg, \
            f"Expected FK constraint violation, got: {exc_info.value}"

    def test_label_success_node_exists(self, iris_connection):
        """
        GIVEN: node 'PROTEIN:TP53' exists
        WHEN: assigning label 'tumor_suppressor' to it
        THEN: label is assigned successfully

        Expected: FAIL initially (nodes table doesn't exist)
        """
        cursor = iris_connection.cursor()

        # Create node
        cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ['PROTEIN:TP53'])
        iris_connection.commit()

        # Assign label - should succeed
        cursor.execute(
            "INSERT INTO rdf_labels (s, label) VALUES (?, ?)",
            ['PROTEIN:TP53', 'tumor_suppressor']
        )
        iris_connection.commit()

        # Verify label exists
        cursor.execute("SELECT s, label FROM rdf_labels WHERE s = ?", ['PROTEIN:TP53'])
        result = cursor.fetchone()

        assert result is not None, "Label should exist after insertion"
        assert result[0] == 'PROTEIN:TP53', "Node ID should match"
        assert result[1] == 'tumor_suppressor', "Label should match"


@pytest.mark.requires_database
@pytest.mark.integration
class TestPropertyForeignKeys:
    """Contract 4: Assign Property to Node tests."""

    def test_property_requires_node(self, iris_connection):
        """
        GIVEN: no node exists with ID 'INVALID:node'
        WHEN: attempting to assign property to 'INVALID:node'
        THEN: FK constraint violation raised

        Expected: FAIL initially (FK constraint doesn't exist)
        """
        cursor = iris_connection.cursor()

        with pytest.raises(Exception) as exc_info:
            cursor.execute(
                "INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
                ['INVALID:node', 'some_key', 'some_value']
            )
            iris_connection.commit()

        # Check for FK constraint violation
        error_msg = str(exc_info.value).lower()
        assert 'foreign key' in error_msg or 'constraint' in error_msg or 'fk_props_node' in error_msg, \
            f"Expected FK constraint violation, got: {exc_info.value}"

    def test_property_success_node_exists(self, iris_connection):
        """
        GIVEN: node 'PROTEIN:TP53' exists
        WHEN: assigning property 'chromosome'='17' to it
        THEN: property is assigned successfully

        Expected: FAIL initially (nodes table doesn't exist)
        """
        cursor = iris_connection.cursor()

        # Create node
        cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ['PROTEIN:TP53'])
        iris_connection.commit()

        # Assign property - should succeed
        cursor.execute(
            "INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
            ['PROTEIN:TP53', 'chromosome', '17']
        )
        iris_connection.commit()

        # Verify property exists
        cursor.execute(
            "SELECT s, key, val FROM rdf_props WHERE s = ? AND key = ?",
            ['PROTEIN:TP53', 'chromosome']
        )
        result = cursor.fetchone()

        assert result is not None, "Property should exist after insertion"
        assert result[0] == 'PROTEIN:TP53', "Node ID should match"
        assert result[1] == 'chromosome', "Property key should match"
        assert result[2] == '17', "Property value should match"


@pytest.mark.requires_database
@pytest.mark.integration
@pytest.mark.skip(reason="kg_NodeEmbeddings requires VECTOR type support not available in test environment")
class TestEmbeddingForeignKeys:
    """Contract 5: Create Embedding for Node tests."""

    def test_embedding_requires_node(self, iris_connection):
        """
        GIVEN: no node exists with ID 'INVALID:node'
        WHEN: attempting to create embedding for 'INVALID:node'
        THEN: FK constraint violation raised

        Expected: FAIL initially (FK constraint doesn't exist)
        """
        cursor = iris_connection.cursor()

        # Create dummy 768-dimensional vector
        dummy_vector = '[' + ','.join(['0.1'] * 768) + ']'

        with pytest.raises(Exception) as exc_info:
            cursor.execute(
                "INSERT INTO kg_NodeEmbeddings (id, emb) VALUES (?, TO_VECTOR(?))",
                ['INVALID:node', dummy_vector]
            )
            iris_connection.commit()

        # Check for FK constraint violation
        error_msg = str(exc_info.value).lower()
        assert 'foreign key' in error_msg or 'constraint' in error_msg or 'fk_embeddings_node' in error_msg, \
            f"Expected FK constraint violation, got: {exc_info.value}"

    def test_embedding_success_node_exists(self, iris_connection):
        """
        GIVEN: node 'PROTEIN:TP53' exists
        WHEN: creating embedding for it
        THEN: embedding is created successfully

        Expected: FAIL initially (nodes table doesn't exist)
        """
        cursor = iris_connection.cursor()

        # Create node
        cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ['PROTEIN:TP53'])
        iris_connection.commit()

        # Create dummy 768-dimensional vector
        dummy_vector = '[' + ','.join([str(0.001 * i) for i in range(768)]) + ']'

        # Create embedding - should succeed
        cursor.execute(
            "INSERT INTO kg_NodeEmbeddings (id, emb) VALUES (?, TO_VECTOR(?))",
            ['PROTEIN:TP53', dummy_vector]
        )
        iris_connection.commit()

        # Verify embedding exists
        cursor.execute("SELECT id FROM kg_NodeEmbeddings WHERE id = ?", ['PROTEIN:TP53'])
        result = cursor.fetchone()

        assert result is not None, "Embedding should exist after insertion"
        assert result[0] == 'PROTEIN:TP53', "Node ID should match"


@pytest.mark.requires_database
@pytest.mark.integration
class TestNodeDeletion:
    """Contract 6: Delete Node (Cascade Behavior) tests."""

    def test_delete_node_blocked_by_edge(self, iris_connection):
        """
        GIVEN: node 'NODE:A' has edges referencing it
        WHEN: attempting to delete 'NODE:A'
        THEN: FK constraint violation (ON DELETE RESTRICT)

        Expected: FAIL initially (FK constraints don't exist)
        """
        cursor = iris_connection.cursor()

        # Create nodes and edge
        cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ['NODE:A'])
        cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ['NODE:B'])
        cursor.execute(
            "INSERT INTO rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
            ['NODE:A', 'relates_to', 'NODE:B']
        )
        iris_connection.commit()

        # Try to delete node with edges - should fail
        with pytest.raises(Exception) as exc_info:
            cursor.execute("DELETE FROM nodes WHERE node_id = ?", ['NODE:A'])
            iris_connection.commit()

        # Check for FK constraint violation
        error_msg = str(exc_info.value).lower()
        assert 'foreign key' in error_msg or 'constraint' in error_msg or 'restrict' in error_msg, \
            f"Expected FK constraint violation (ON DELETE RESTRICT), got: {exc_info.value}"

    def test_delete_node_blocked_by_label(self, iris_connection):
        """
        GIVEN: node has labels assigned
        WHEN: attempting to delete node
        THEN: FK constraint violation

        Expected: FAIL initially (FK constraints don't exist)
        """
        cursor = iris_connection.cursor()

        # Create node and label
        cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ['NODE:A'])
        cursor.execute("INSERT INTO rdf_labels (s, label) VALUES (?, ?)", ['NODE:A', 'test_label'])
        iris_connection.commit()

        # Try to delete node with labels - should fail
        with pytest.raises(Exception) as exc_info:
            cursor.execute("DELETE FROM nodes WHERE node_id = ?", ['NODE:A'])
            iris_connection.commit()

        error_msg = str(exc_info.value).lower()
        assert 'foreign key' in error_msg or 'constraint' in error_msg, \
            f"Expected FK constraint violation, got: {exc_info.value}"

    def test_delete_node_blocked_by_property(self, iris_connection):
        """
        GIVEN: node has properties assigned
        WHEN: attempting to delete node
        THEN: FK constraint violation

        Expected: FAIL initially (FK constraints don't exist)
        """
        cursor = iris_connection.cursor()

        # Create node and property
        cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ['NODE:A'])
        cursor.execute("INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)", ['NODE:A', 'key1', 'val1'])
        iris_connection.commit()

        # Try to delete node with properties - should fail
        with pytest.raises(Exception) as exc_info:
            cursor.execute("DELETE FROM nodes WHERE node_id = ?", ['NODE:A'])
            iris_connection.commit()

        error_msg = str(exc_info.value).lower()
        assert 'foreign key' in error_msg or 'constraint' in error_msg, \
            f"Expected FK constraint violation, got: {exc_info.value}"

    @pytest.mark.skip(reason="kg_NodeEmbeddings requires VECTOR type support not available in test environment")
    def test_delete_node_blocked_by_embedding(self, iris_connection):
        """
        GIVEN: node has embedding
        WHEN: attempting to delete node
        THEN: FK constraint violation

        Expected: FAIL initially (FK constraints don't exist)
        """
        cursor = iris_connection.cursor()

        # Create node and embedding
        cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ['NODE:A'])
        dummy_vector = '[' + ','.join(['0.1'] * 768) + ']'
        cursor.execute(
            "INSERT INTO kg_NodeEmbeddings (id, emb) VALUES (?, TO_VECTOR(?))",
            ['NODE:A', dummy_vector]
        )
        iris_connection.commit()

        # Try to delete node with embedding - should fail
        with pytest.raises(Exception) as exc_info:
            cursor.execute("DELETE FROM nodes WHERE node_id = ?", ['NODE:A'])
            iris_connection.commit()

        error_msg = str(exc_info.value).lower()
        assert 'foreign key' in error_msg or 'constraint' in error_msg, \
            f"Expected FK constraint violation, got: {exc_info.value}"

    def test_delete_node_success_no_dependencies(self, iris_connection):
        """
        GIVEN: node with no dependencies (no edges, labels, props, embeddings)
        WHEN: deleting the node
        THEN: deletion succeeds

        Expected: FAIL initially (nodes table doesn't exist)
        """
        cursor = iris_connection.cursor()

        # Create bare node
        cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ['NODE:BARE'])
        iris_connection.commit()

        # Delete should succeed
        cursor.execute("DELETE FROM nodes WHERE node_id = ?", ['NODE:BARE'])
        iris_connection.commit()

        # Verify node is gone
        cursor.execute("SELECT node_id FROM nodes WHERE node_id = ?", ['NODE:BARE'])
        result = cursor.fetchone()

        assert result is None, "Node should be deleted"


@pytest.mark.requires_database
@pytest.mark.integration
class TestConcurrentNodeInsertion:
    """Contract 7 (partial): Test concurrent node insertion handling."""

    def test_concurrent_insert_same_node_id(self, iris_connection):
        """
        GIVEN: two processes trying to insert same node_id
        WHEN: executing concurrent INSERTs
        THEN: one succeeds, other gets UNIQUE violation

        Expected: FAIL initially (nodes table doesn't exist)
        """
        import threading
        import time

        results = {'thread1': None, 'thread2': None}
        errors = {'thread1': None, 'thread2': None}

        def insert_node(thread_name):
            try:
                cursor = iris_connection.cursor()
                cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ['TEST:concurrent'])
                iris_connection.commit()
                results[thread_name] = 'success'
            except Exception as e:
                iris_connection.rollback()
                errors[thread_name] = str(e)
                results[thread_name] = 'error'

        # Create two threads trying to insert same node
        thread1 = threading.Thread(target=insert_node, args=('thread1',))
        thread2 = threading.Thread(target=insert_node, args=('thread2',))

        # Start threads nearly simultaneously
        thread1.start()
        thread2.start()

        # Wait for completion
        thread1.join(timeout=5.0)
        thread2.join(timeout=5.0)

        # One should succeed, one should fail
        success_count = sum(1 for r in results.values() if r == 'success')
        error_count = sum(1 for r in results.values() if r == 'error')

        assert success_count == 1, f"Exactly one insert should succeed, got {success_count}"
        assert error_count == 1, f"Exactly one insert should fail, got {error_count}"

        # Check that the error is a UNIQUE constraint violation
        error_thread = 'thread1' if results['thread1'] == 'error' else 'thread2'
        error_msg = errors[error_thread].lower()
        assert 'unique' in error_msg or 'duplicate' in error_msg or 'constraint' in error_msg, \
            f"Expected UNIQUE constraint violation, got: {errors[error_thread]}"