"""
Schema Initialization Integration Tests

Tests that GraphSchema.ensure_schema() and execute_schema_sql()
work correctly with live IRIS database.

These tests verify the fix for the v1.1.5 bug where comment filtering
was too aggressive and prevented CREATE TABLE statements from executing.
"""

import pytest
import iris
import os
from iris_vector_graph.schema import GraphSchema


@pytest.fixture
def iris_cursor():
    """Provide a live IRIS database cursor for testing"""
    host = os.getenv('IRIS_HOST', 'localhost')
    port = int(os.getenv('IRIS_PORT', '1972'))
    namespace = os.getenv('IRIS_NAMESPACE', 'USER')
    username = os.getenv('IRIS_USER', '_SYSTEM')
    password = os.getenv('IRIS_PASSWORD', 'SYS')

    conn = iris.connect(host, port, namespace, username, password)
    cursor = conn.cursor()

    yield cursor

    # Cleanup after tests
    cursor.close()
    conn.close()


@pytest.fixture
def clean_schema(iris_cursor):
    """Drop all graph tables before test to ensure clean state"""
    tables_to_drop = [
        'kg_NodeEmbeddings_optimized',
        'kg_NodeEmbeddings',
        'docs',
        'rdf_edges',
        'rdf_props',
        'rdf_labels'
    ]

    for table in tables_to_drop:
        try:
            iris_cursor.execute(f"DROP TABLE {table}")
        except Exception:
            pass  # Table might not exist

    yield iris_cursor


@pytest.mark.requires_database
@pytest.mark.integration
class TestSchemaInitialization:
    """Integration tests for schema initialization with live IRIS"""

    def test_ensure_schema_creates_all_tables(self, clean_schema):
        """
        Test that ensure_schema() successfully creates all required tables

        This is the regression test for v1.1.5 bug where CREATE TABLE
        statements were filtered out by aggressive comment removal.
        """
        cursor = clean_schema

        # Execute schema initialization
        results = GraphSchema.ensure_schema(cursor)

        # Verify we got results
        assert len(results) > 0, "Should have executed statements"

        # Count successful table creations
        successful_tables = [
            key for key, status in results.items()
            if 'CREATE TABLE' in key and status == 'success'
        ]

        # Should have created at least 6 tables
        assert len(successful_tables) >= 6, (
            f"Should create at least 6 tables, got {len(successful_tables)}: {successful_tables}"
        )

        # Verify tables actually exist by querying them
        required_tables = [
            'rdf_labels',
            'rdf_props',
            'rdf_edges',
            'kg_NodeEmbeddings',
            'kg_NodeEmbeddings_optimized',
            'docs'
        ]

        for table in required_tables:
            try:
                cursor.execute(f"SELECT TOP 1 * FROM {table}")
                # Success - table exists
            except Exception as e:
                pytest.fail(f"Table {table} should exist but query failed: {e}")

    def test_ensure_schema_idempotent(self, iris_cursor):
        """
        Test that ensure_schema() can be called multiple times safely

        Second call should skip already existing tables/indexes.
        """
        # First call - creates schema
        results1 = GraphSchema.ensure_schema(iris_cursor)

        # Count successes and skips
        success_count1 = sum(1 for v in results1.values() if v == 'success')
        skip_count1 = sum(1 for v in results1.values() if 'skipped' in v or 'already exists' in v)

        # Second call - should skip existing objects
        results2 = GraphSchema.ensure_schema(iris_cursor)

        # Count successes and skips
        success_count2 = sum(1 for v in results2.values() if v == 'success')
        skip_count2 = sum(1 for v in results2.values() if 'skipped' in v or 'already exists' in v)

        # Second call should have more skips, fewer successes
        assert skip_count2 >= skip_count1, (
            f"Second call should skip existing objects. "
            f"First: {skip_count1} skips, Second: {skip_count2} skips"
        )

    def test_execute_schema_sql_removes_comments(self, clean_schema):
        """
        Test that execute_schema_sql() properly removes comments
        while preserving SQL statements.

        Regression test for v1.1.5 bug.
        """
        cursor = clean_schema

        # Test SQL with inline comments
        test_sql = """
-- This is a comment
CREATE TABLE IF NOT EXISTS test_table(
  id VARCHAR(256) PRIMARY KEY,
  value VARCHAR(256)
);

-- Another comment
CREATE INDEX test_idx ON test_table(value);
"""

        results = GraphSchema.execute_schema_sql(cursor, test_sql)

        # Should have executed 2 statements
        assert len(results) == 2, f"Should execute 2 statements, got {len(results)}: {results}"

        # Both should succeed
        create_table_result = [v for k, v in results.items() if 'CREATE TABLE' in k][0]
        assert create_table_result == 'success', f"CREATE TABLE should succeed: {create_table_result}"

        # Verify table exists
        cursor.execute("SELECT TOP 1 * FROM test_table")

        # Cleanup
        cursor.execute("DROP TABLE test_table")

    def test_validate_schema_after_initialization(self, iris_cursor):
        """
        Test that validate_schema() returns True for all tables
        after ensure_schema() runs.
        """
        # Ensure schema is initialized
        GraphSchema.ensure_schema(iris_cursor)

        # Validate schema
        status = GraphSchema.validate_schema(iris_cursor)

        # All required tables should exist
        required_tables = [
            'rdf_labels',
            'rdf_props',
            'rdf_edges',
            'kg_NodeEmbeddings',
            'kg_NodeEmbeddings_optimized',
            'docs'
        ]

        for table in required_tables:
            assert status[table] is True, f"Table {table} should exist after ensure_schema()"

    def test_schema_sql_syntax_is_iris_compatible(self, clean_schema):
        """
        Test that schema SQL executes without syntax errors on IRIS.

        This is an end-to-end validation of IRIS SQL compatibility.
        """
        cursor = clean_schema

        schema_sql = GraphSchema.get_base_schema_sql()

        # Execute schema SQL
        results = GraphSchema.execute_schema_sql(cursor, schema_sql)

        # Count errors
        errors = [k for k, v in results.items() if v.startswith('error:')]

        # We expect no errors (except possibly "already exists" which are handled)
        syntax_errors = [
            k for k, v in results.items()
            if v.startswith('error:') and 'already exists' not in v.lower()
        ]

        assert len(syntax_errors) == 0, (
            f"Schema SQL should execute without syntax errors on IRIS. "
            f"Found {len(syntax_errors)} syntax errors: {syntax_errors}"
        )
