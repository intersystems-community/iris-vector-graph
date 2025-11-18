"""
Schema Contract Tests

These tests enforce SQL syntax compatibility between iris-vector-graph
and InterSystems IRIS to prevent regressions like the VECTOR(768) bug.

Contract Requirements:
1. VECTOR datatype MUST use IRIS syntax: VECTOR(FLOAT, dim) or VECTOR(DOUBLE, dim)
2. CREATE INDEX MUST NOT use IF NOT EXISTS (IRIS doesn't support it for indexes)
3. HNSW index syntax MUST be AS HNSW(...) format
4. Schema SQL MUST be executable on IRIS without syntax errors
"""

import pytest
import re
from iris_vector_graph.schema import GraphSchema


class TestSchemaContract:
    """Contract tests for IRIS SQL syntax compatibility"""

    def test_vector_datatype_uses_iris_syntax(self):
        """
        Contract: VECTOR datatype MUST use IRIS syntax

        IRIS requires: VECTOR(FLOAT, 768) or VECTOR(DOUBLE, 768)
        PostgreSQL pgvector uses: VECTOR(768)

        This test prevents regressions where PostgreSQL syntax
        sneaks into the schema.
        """
        schema_sql = GraphSchema.get_base_schema_sql()

        # Remove comments to avoid false matches
        schema_sql_no_comments = re.sub(r'--[^\n]*', '', schema_sql)

        # Find all VECTOR(...) declarations
        vector_pattern = r'VECTOR\s*\([^)]+\)'
        matches = re.findall(vector_pattern, schema_sql_no_comments, re.IGNORECASE)

        assert len(matches) > 0, "Schema should contain VECTOR datatype declarations"

        for match in matches:
            # Check for IRIS syntax: VECTOR(FLOAT, dim) or VECTOR(DOUBLE, dim)
            assert re.match(
                r'VECTOR\s*\(\s*(FLOAT|DOUBLE)\s*,\s*\d+\s*\)',
                match,
                re.IGNORECASE
            ), f"VECTOR datatype MUST use IRIS syntax: VECTOR(FLOAT, dim). Found: {match}"

    def test_create_index_no_if_not_exists(self):
        """
        Contract: CREATE INDEX MUST NOT use IF NOT EXISTS

        IRIS doesn't support IF NOT EXISTS for indexes (only for tables).
        Use execute_schema_sql() helper to handle "already exists" errors.
        """
        schema_sql = GraphSchema.get_base_schema_sql()

        # Find CREATE INDEX statements
        index_pattern = r'CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS'
        matches = re.findall(index_pattern, schema_sql, re.IGNORECASE)

        assert len(matches) == 0, (
            f"Found {len(matches)} CREATE INDEX IF NOT EXISTS statements. "
            "IRIS doesn't support IF NOT EXISTS for indexes. "
            "Remove IF NOT EXISTS and use execute_schema_sql() helper."
        )

    def test_hnsw_index_syntax(self):
        """
        Contract: HNSW index MUST use AS HNSW(...) syntax

        IRIS syntax: CREATE INDEX name ON table(column) AS HNSW(...)
        """
        schema_sql = GraphSchema.get_base_schema_sql()

        # Find HNSW index statements
        hnsw_pattern = r'CREATE\s+INDEX\s+\w+\s+ON\s+\w+\([^)]+\)\s+AS\s+HNSW'
        matches = re.findall(hnsw_pattern, schema_sql, re.IGNORECASE)

        assert len(matches) >= 2, (
            f"Expected at least 2 HNSW indexes (NodeEmbeddings + optimized), found {len(matches)}"
        )

    def test_iris_text_index_syntax(self):
        """
        Contract: IRIS text index MUST use TYPE BITMAP WITH PARAMETERS syntax

        IRIS text index syntax:
        CREATE INDEX name ON table(column) TYPE BITMAP WITH PARAMETERS(...)
        """
        schema_sql = GraphSchema.get_base_schema_sql()

        # Find IRIS text index
        text_index_pattern = r'CREATE\s+INDEX\s+\w+\s+ON\s+docs\(text\)\s+TYPE\s+BITMAP'
        matches = re.findall(text_index_pattern, schema_sql, re.IGNORECASE)

        assert len(matches) == 1, (
            f"Expected 1 IRIS text index on docs(text), found {len(matches)}"
        )

    def test_schema_has_required_tables(self):
        """
        Contract: Schema MUST define all required tables

        Required tables for graph+vector operations:
        - rdf_labels (entity types)
        - rdf_props (entity properties)
        - rdf_edges (relationships)
        - kg_NodeEmbeddings (vector embeddings)
        - kg_NodeEmbeddings_optimized (optimized vectors)
        - docs (text documents)
        """
        schema_sql = GraphSchema.get_base_schema_sql()

        required_tables = [
            'rdf_labels',
            'rdf_props',
            'rdf_edges',
            'kg_NodeEmbeddings',
            'kg_NodeEmbeddings_optimized',
            'docs'
        ]

        for table in required_tables:
            pattern = rf'CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+{table}\s*\('
            assert re.search(pattern, schema_sql, re.IGNORECASE), (
                f"Schema MUST define table: {table}"
            )

    def test_execute_schema_sql_helper_exists(self):
        """
        Contract: execute_schema_sql() helper MUST exist for error handling

        Since IRIS doesn't support IF NOT EXISTS for indexes, we need
        a helper function to handle "already exists" errors gracefully.
        """
        assert hasattr(GraphSchema, 'execute_schema_sql'), (
            "GraphSchema MUST provide execute_schema_sql() helper "
            "to handle index creation errors gracefully"
        )

        # Check signature
        import inspect
        sig = inspect.signature(GraphSchema.execute_schema_sql)
        params = list(sig.parameters.keys())

        assert 'cursor' in params, "execute_schema_sql() MUST accept cursor parameter"
        assert 'sql' in params, "execute_schema_sql() MUST accept sql parameter"

    def test_no_postgresql_specific_syntax(self):
        """
        Contract: Schema MUST NOT contain PostgreSQL-specific syntax

        Common PostgreSQL syntax that doesn't work in IRIS:
        - VECTOR(dim) without type specification
        - RETURNING * in INSERT statements
        - ON CONFLICT DO UPDATE (use MERGE instead)
        - :: type casting (use CAST() instead)
        """
        schema_sql = GraphSchema.get_base_schema_sql()

        # Check for :: type casting
        type_cast_pattern = r'\w+::\w+'
        matches = re.findall(type_cast_pattern, schema_sql)
        assert len(matches) == 0, (
            f"Found PostgreSQL-style type casting (::). Use CAST() instead. Matches: {matches}"
        )

        # VECTOR(dim) without FLOAT/DOUBLE already checked by test_vector_datatype_uses_iris_syntax

    def test_json_datatype_compatibility(self):
        """
        Contract: JSON datatype usage MUST be IRIS-compatible

        IRIS supports JSON datatype for storing JSON documents.
        This is used in rdf_edges.qualifiers for metadata.
        """
        schema_sql = GraphSchema.get_base_schema_sql()

        # Find JSON column in rdf_edges
        json_pattern = r'qualifiers\s+JSON'
        matches = re.findall(json_pattern, schema_sql, re.IGNORECASE)

        assert len(matches) == 1, (
            "rdf_edges table MUST have qualifiers JSON column for edge metadata"
        )

    def test_execute_schema_sql_preserves_create_table_statements(self):
        """
        Contract: execute_schema_sql() MUST NOT filter out CREATE TABLE statements

        Regression test for v1.1.5 bug where aggressive comment filtering
        removed CREATE TABLE statements that appeared after comment lines.

        This was a CRITICAL bug that prevented iris-vector-rag from
        initializing the graph schema.
        """
        schema_sql = GraphSchema.get_base_schema_sql()

        # Mock cursor to capture executed statements
        class MockCursor:
            def __init__(self):
                self.executed_statements = []

            def execute(self, sql):
                self.executed_statements.append(sql)

        mock_cursor = MockCursor()

        # Execute schema SQL
        results = GraphSchema.execute_schema_sql(mock_cursor, schema_sql)

        # Verify CREATE TABLE statements were executed
        create_table_count = sum(
            1 for stmt in mock_cursor.executed_statements
            if 'CREATE TABLE' in stmt.upper()
        )

        assert create_table_count >= 6, (
            f"execute_schema_sql() MUST execute at least 6 CREATE TABLE statements. "
            f"Found only {create_table_count}. This indicates comment filtering is too aggressive. "
            f"Tables expected: rdf_labels, rdf_props, rdf_edges, kg_NodeEmbeddings, "
            f"kg_NodeEmbeddings_optimized, docs"
        )

        # Verify we didn't accidentally execute comment-only statements
        for stmt in mock_cursor.executed_statements:
            # Statement should not be pure comments
            lines = stmt.strip().split('\n')
            non_comment_lines = [
                line for line in lines
                if line.strip() and not line.strip().startswith('--')
            ]
            assert len(non_comment_lines) > 0, (
                f"Statement should contain actual SQL, not just comments: {stmt[:100]}"
            )

    def test_no_auto_increment_keywords(self):
        """
        Contract: AUTO-INCREMENT keywords MUST NOT be used

        After 4 failed attempts, IRIS IDENTITY keyword is not universally supported.
        Use manual ID management instead (application-generated IDs).

        SQL standard (NOT supported): BIGINT GENERATED BY DEFAULT AS IDENTITY
        IRIS IDENTITY (NOT supported): BIGINT NOT NULL IDENTITY
        PostgreSQL (NOT supported): SERIAL PRIMARY KEY
        MySQL (NOT supported): BIGINT AUTO_INCREMENT PRIMARY KEY

        This test prevents regressions where auto-increment syntax
        is added back after being removed.
        """
        schema_sql = GraphSchema.get_base_schema_sql()

        # Check for ANY auto-increment syntax (ALL not supported universally)
        invalid_patterns = [
            r'GENERATED\s+BY\s+DEFAULT\s+AS\s+IDENTITY',
            r'GENERATED\s+ALWAYS\s+AS\s+IDENTITY',
            r'SERIAL\s+PRIMARY\s+KEY',
            r'AUTO_INCREMENT',
            r'AUTOINCREMENT',
            r'IDENTITY\s+PRIMARY\s+KEY',
            r'NOT\s+NULL\s+IDENTITY',  # Even this IRIS syntax not universally supported
            r'BIGINT\s+IDENTITY'       # Any IDENTITY usage
        ]

        for pattern in invalid_patterns:
            matches = re.findall(pattern, schema_sql, re.IGNORECASE)
            assert len(matches) == 0, (
                f"Found auto-increment syntax that IRIS doesn't universally support: {pattern}. "
                f"Use manual ID management instead (BIGINT PRIMARY KEY with application-generated IDs). "
                f"See sql/migrations/000_base_schema_iris.sql for correct pattern."
            )
