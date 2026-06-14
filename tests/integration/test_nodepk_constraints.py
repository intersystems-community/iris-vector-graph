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

import contextlib
import pytest
import os
from datetime import datetime
from dotenv import load_dotenv

# NOTE: iris_connection fixture is provided by tests/conftest.py
# Do not define a local fixture here to avoid shadowing


def _close(cursor):
    """Silently close a cursor."""
    with contextlib.suppress(Exception):
        cursor.close()


@pytest.fixture(autouse=True)
def cleanup_test_data(iris_connection):
    """Clean up test data before and after each test."""
    test_prefixes = ['TEST:', 'TEMP:', 'INVALID:', 'NODE:', 'PROTEIN:', 'DISEASE:']

    def _clean(conn):
        try:
            conn.rollback()
        except Exception:
            pass
        cursor = conn.cursor()
        try:
            for prefix in test_prefixes:
                try:
                    cursor.execute("DELETE FROM Graph_KG.rdf_edges WHERE s LIKE ? OR o_id LIKE ?", [f"{prefix}%", f"{prefix}%"])
                    cursor.execute("DELETE FROM Graph_KG.rdf_props WHERE s LIKE ?", [f"{prefix}%"])
                    cursor.execute("DELETE FROM Graph_KG.rdf_labels WHERE s LIKE ?", [f"{prefix}%"])
                    cursor.execute("DELETE FROM Graph_KG.nodes WHERE node_id LIKE ?", [f"{prefix}%"])
                    conn.commit()
                except Exception:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
        finally:
            _close(cursor)

    _clean(iris_connection)
    yield
    _clean(iris_connection)


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
        try:
            cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", ['TEST:node1'])
            iris_connection.commit()

            cursor.execute("SELECT node_id, created_at FROM Graph_KG.nodes WHERE node_id = ?", ['TEST:node1'])
            result = cursor.fetchone()

            assert result is not None, "Node should exist after insertion"
            assert result[0] == 'TEST:node1', "Node ID should match"
            assert isinstance(result[1], datetime), "created_at should be a timestamp"
            assert result[1] is not None, "created_at should be set automatically"
        finally:
            _close(cursor)

    def test_create_node_duplicate_fails(self, iris_connection):
        """
        GIVEN: a node with ID 'TEST:node1' already exists
        WHEN: attempting to insert another node with same ID
        THEN: UNIQUE constraint violation is raised

        Expected: FAIL initially (nodes table doesn't exist)
        """
        cursor = iris_connection.cursor()
        try:
            cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", ['TEST:node1'])
            iris_connection.commit()

            with pytest.raises(Exception) as exc_info:
                cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", ['TEST:node1'])
                iris_connection.commit()
        finally:
            _close(cursor)

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
        try:
            with pytest.raises(Exception) as exc_info:
                cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [None])
                iris_connection.commit()
        finally:
            _close(cursor)

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
        try:
            cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", ['TEST:dest'])
            iris_connection.commit()

            with pytest.raises(Exception) as exc_info:
                cursor.execute(
                    "INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
                    ['INVALID:source', 'relates_to', 'TEST:dest']
                )
                iris_connection.commit()
        finally:
            _close(cursor)

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
        try:
            cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", ['TEST:source'])
            iris_connection.commit()

            with pytest.raises(Exception) as exc_info:
                cursor.execute(
                    "INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
                    ['TEST:source', 'relates_to', 'INVALID:dest']
                )
                iris_connection.commit()
        finally:
            _close(cursor)

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
        try:
            cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", ['PROTEIN:TP53'])
            cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", ['DISEASE:cancer'])
            iris_connection.commit()

            cursor.execute(
                "INSERT INTO Graph_KG.rdf_edges (s, p, o_id, qualifiers) VALUES (?, ?, ?, ?)",
                ['PROTEIN:TP53', 'associated_with', 'DISEASE:cancer', '{"confidence": 0.95}']
            )
            iris_connection.commit()

            cursor.execute(
                "SELECT s, p, o_id FROM Graph_KG.rdf_edges WHERE s = ? AND o_id = ?",
                ['PROTEIN:TP53', 'DISEASE:cancer']
            )
            result = cursor.fetchone()

            assert result is not None, "Edge should exist after insertion"
            assert result[0] == 'PROTEIN:TP53', "Source should match"
            assert result[1] == 'associated_with', "Predicate should match"
            assert result[2] == 'DISEASE:cancer', "Destination should match"
        finally:
            _close(cursor)


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
        # Use a fresh connection to avoid schema-cache stale-state corruption.
        # The IRIS Python driver caches table parameter layouts per connection.
        # After DDL (DROP INDEX / CREATE INDEX) runs anywhere in the process,
        # the session connection's cache for rdf_labels may be stale, causing
        # parametrized INSERTs to fail with LIST ERROR instead of the expected
        # FK constraint error. A fresh connection has no stale cache.
        import iris.dbapi as _dbapi
        import contextlib
        fresh_conn = _dbapi.connect(
            hostname=iris_connection.hostname,
            port=iris_connection.port,
            namespace=iris_connection.namespace,
            username="_SYSTEM",
            password="SYS",
        )
        try:
            cursor = fresh_conn.cursor()
            try:
                with pytest.raises(Exception) as exc_info:
                    cursor.execute(
                        "INSERT INTO Graph_KG.rdf_labels (s, label) VALUES (?, ?)",
                        ['INVALID:node', 'some_label']
                    )
                    fresh_conn.commit()
            finally:
                _close(cursor)
        finally:
            with contextlib.suppress(Exception):
                fresh_conn.close()

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
        try:
            cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", ['PROTEIN:TP53'])
            iris_connection.commit()

            cursor.execute(
                "INSERT INTO Graph_KG.rdf_labels (s, label) VALUES (?, ?)",
                ['PROTEIN:TP53', 'tumor_suppressor']
            )
            iris_connection.commit()

            cursor.execute("SELECT s, label FROM Graph_KG.rdf_labels WHERE s = ?", ['PROTEIN:TP53'])
            result = cursor.fetchone()

            assert result is not None, "Label should exist after insertion"
            assert result[0] == 'PROTEIN:TP53', "Node ID should match"
            assert result[1] == 'tumor_suppressor', "Label should match"
        finally:
            _close(cursor)


@pytest.mark.requires_database
@pytest.mark.integration
class TestPropertyForeignKeys:
    """Contract 4: Assign Property to Node tests."""

    @pytest.mark.skip(reason="rdf_props.s FK removed to support RDF 1.2 Quoted Triples (edge metadata)")
    def test_property_requires_node(self, iris_connection):
        """
        GIVEN: no node exists with ID 'INVALID:node'
        WHEN: attempting to assign property to 'INVALID:node'
        THEN: FK constraint violation raised

        Expected: FAIL initially (FK constraint doesn't exist)
        """
        cursor = iris_connection.cursor()
        try:
            with pytest.raises(Exception) as exc_info:
                cursor.execute(
                    "INSERT INTO Graph_KG.rdf_props (s, key, val) VALUES (?, ?, ?)",
                    ['INVALID:node', 'some_key', 'some_value']
                )
                iris_connection.commit()
        finally:
            _close(cursor)

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
        try:
            cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", ['PROTEIN:TP53'])
            iris_connection.commit()

            cursor.execute(
                "INSERT INTO Graph_KG.rdf_props (s, key, val) VALUES (?, ?, ?)",
                ['PROTEIN:TP53', 'chromosome', '17']
            )
            iris_connection.commit()

            cursor.execute(
                "SELECT s, key, val FROM Graph_KG.rdf_props WHERE s = ? AND key = ?",
                ['PROTEIN:TP53', 'chromosome']
            )
            result = cursor.fetchone()

            assert result is not None, "Property should exist after insertion"
            assert result[0] == 'PROTEIN:TP53', "Node ID should match"
            assert result[1] == 'chromosome', "Property key should match"
            assert result[2] == '17', "Property value should match"
        finally:
            _close(cursor)


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
        dummy_vector = '[' + ','.join(['0.1'] * 768) + ']'
        cursor = iris_connection.cursor()
        try:
            with pytest.raises(Exception) as exc_info:
                cursor.execute(
                    "INSERT INTO kg_NodeEmbeddings (id, emb) VALUES (?, TO_VECTOR(?))",
                    ['INVALID:node', dummy_vector]
                )
                iris_connection.commit()
        finally:
            _close(cursor)

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
        dummy_vector = '[' + ','.join([str(0.001 * i) for i in range(768)]) + ']'
        cursor = iris_connection.cursor()
        try:
            cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", ['PROTEIN:TP53'])
            iris_connection.commit()

            cursor.execute(
                "INSERT INTO kg_NodeEmbeddings (id, emb) VALUES (?, TO_VECTOR(?))",
                ['PROTEIN:TP53', dummy_vector]
            )
            iris_connection.commit()

            cursor.execute("SELECT id FROM kg_NodeEmbeddings WHERE id = ?", ['PROTEIN:TP53'])
            result = cursor.fetchone()

            assert result is not None, "Embedding should exist after insertion"
            assert result[0] == 'PROTEIN:TP53', "Node ID should match"
        finally:
            _close(cursor)


@pytest.mark.requires_database
@pytest.mark.integration
class TestNodeDeletion:
    """Contract 6: Delete Node (Cascade Behavior) tests."""

    @pytest.mark.xfail(reason="IRIS does not enforce FK constraints by default")
    def test_delete_node_blocked_by_edge(self, iris_connection):
        """
        GIVEN: node 'NODE:A' has edges referencing it
        WHEN: attempting to delete 'NODE:A'
        THEN: FK constraint violation (ON DELETE RESTRICT)

        Expected: FAIL initially (FK constraints don't exist)
        """
        cursor = iris_connection.cursor()
        try:
            cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", ['NODE:A'])
            cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", ['NODE:B'])
            cursor.execute(
                "INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
                ['NODE:A', 'relates_to', 'NODE:B']
            )
            iris_connection.commit()

            with pytest.raises(Exception) as exc_info:
                cursor.execute("DELETE FROM Graph_KG.nodes WHERE node_id = ?", ['NODE:A'])
                iris_connection.commit()
        finally:
            _close(cursor)

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
        try:
            cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", ['NODE:A'])
            cursor.execute("INSERT INTO Graph_KG.rdf_labels (s, label) VALUES (?, ?)", ['NODE:A', 'test_label'])
            iris_connection.commit()

            with pytest.raises(Exception) as exc_info:
                cursor.execute("DELETE FROM Graph_KG.nodes WHERE node_id = ?", ['NODE:A'])
                iris_connection.commit()
        finally:
            _close(cursor)

        error_msg = str(exc_info.value).lower()
        assert 'foreign key' in error_msg or 'constraint' in error_msg, \
            f"Expected FK constraint violation, got: {exc_info.value}"

    @pytest.mark.skip(reason="rdf_props.s FK removed to support RDF 1.2 Quoted Triples")
    def test_delete_node_blocked_by_property(self, iris_connection):
        """
        GIVEN: node has properties assigned
        WHEN: attempting to delete node
        THEN: FK constraint violation

        Expected: FAIL initially (FK constraints don't exist)
        """
        cursor = iris_connection.cursor()
        try:
            cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", ['NODE:A'])
            cursor.execute("INSERT INTO Graph_KG.rdf_props (s, key, val) VALUES (?, ?, ?)", ['NODE:A', 'key1', 'val1'])
            iris_connection.commit()

            with pytest.raises(Exception) as exc_info:
                cursor.execute("DELETE FROM Graph_KG.nodes WHERE node_id = ?", ['NODE:A'])
                iris_connection.commit()
        finally:
            _close(cursor)

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
        dummy_vector = '[' + ','.join(['0.1'] * 768) + ']'
        cursor = iris_connection.cursor()
        try:
            cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", ['NODE:A'])
            cursor.execute(
                "INSERT INTO kg_NodeEmbeddings (id, emb) VALUES (?, TO_VECTOR(?))",
                ['NODE:A', dummy_vector]
            )
            iris_connection.commit()

            with pytest.raises(Exception) as exc_info:
                cursor.execute("DELETE FROM Graph_KG.nodes WHERE node_id = ?", ['NODE:A'])
                iris_connection.commit()
        finally:
            _close(cursor)

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
        try:
            cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", ['NODE:BARE'])
            iris_connection.commit()

            cursor.execute("DELETE FROM Graph_KG.nodes WHERE node_id = ?", ['NODE:BARE'])
            iris_connection.commit()

            cursor.execute("SELECT node_id FROM Graph_KG.nodes WHERE node_id = ?", ['NODE:BARE'])
            result = cursor.fetchone()

            assert result is None, "Node should be deleted"
        finally:
            _close(cursor)


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

        results = {'thread1': None, 'thread2': None}
        errors = {'thread1': None, 'thread2': None}

        def insert_node(thread_name):
            cursor = iris_connection.cursor()
            try:
                cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", ['TEST:concurrent'])
                iris_connection.commit()
                results[thread_name] = 'success'
            except Exception as e:
                try:
                    iris_connection.rollback()
                except Exception:
                    pass
                errors[thread_name] = str(e)
                results[thread_name] = 'error'
            finally:
                _close(cursor)

        thread1 = threading.Thread(target=insert_node, args=('thread1',))
        thread2 = threading.Thread(target=insert_node, args=('thread2',))

        thread1.start()
        thread2.start()

        thread1.join(timeout=5.0)
        thread2.join(timeout=5.0)

        success_count = sum(1 for r in results.values() if r == 'success')
        error_count = sum(1 for r in results.values() if r == 'error')

        assert success_count == 1, f"Exactly one insert should succeed, got {success_count}"
        assert error_count == 1, f"Exactly one insert should fail, got {error_count}"

        error_thread = 'thread1' if results['thread1'] == 'error' else 'thread2'
        error_msg = errors[error_thread].lower()
        assert 'unique' in error_msg or 'duplicate' in error_msg or 'constraint' in error_msg, \
            f"Expected UNIQUE constraint violation, got: {errors[error_thread]}"
