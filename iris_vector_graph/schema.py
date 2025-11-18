#!/usr/bin/env python3
"""
Domain-Agnostic Graph Schema Management

Provides RDF-style graph schema utilities that can be used across domains.
Extracted from the biomedical-specific implementation for reusability.
"""

from typing import Dict, List, Optional

class GraphSchema:
    """Domain-agnostic RDF-style graph schema management"""

    @staticmethod
    def execute_schema_sql(cursor, sql: str) -> Dict[str, str]:
        """
        Execute schema SQL with proper error handling for IRIS.

        IRIS doesn't support IF NOT EXISTS for indexes, so we need to handle
        errors gracefully when indexes already exist.

        Args:
            cursor: Database cursor
            sql: Schema SQL string (may contain multiple statements)

        Returns:
            Dictionary mapping statement -> status (success/skipped/error)
        """
        results = {}

        # Split into individual statements and clean comments
        raw_statements = sql.split(';')
        statements = []

        for stmt in raw_statements:
            # Process line by line to remove comments but keep SQL
            lines = []
            for line in stmt.split('\n'):
                # Remove inline comments
                if '--' in line:
                    line = line[:line.index('--')]
                # Keep non-empty lines
                if line.strip():
                    lines.append(line)

            # Reconstruct statement without comments
            clean_stmt = '\n'.join(lines).strip()
            if clean_stmt:
                statements.append(clean_stmt)

        for stmt in statements:
            try:
                cursor.execute(stmt)
                results[stmt[:50] + '...'] = 'success'
            except Exception as e:
                error_msg = str(e).lower()
                # Gracefully handle "already exists" errors for tables and indexes
                if 'already exists' in error_msg or 'duplicate' in error_msg or 'table exists' in error_msg:
                    results[stmt[:50] + '...'] = 'skipped (already exists)'
                # Gracefully handle text index errors (feature may not be available in all IRIS versions)
                elif 'idx_docs_text_find' in stmt and ('input encountered' in error_msg or 'syntax' in error_msg):
                    results[stmt[:50] + '...'] = 'skipped (text index not supported in this IRIS version)'
                else:
                    results[stmt[:50] + '...'] = f'error: {str(e)[:100]}'

        return results

    @staticmethod
    def ensure_schema(cursor) -> Dict[str, str]:
        """
        Ensure all required graph tables and indexes exist in IRIS database.

        This is the recommended way to initialize the schema, providing better
        error handling and reporting than execute_schema_sql().

        Args:
            cursor: Database cursor

        Returns:
            Dictionary mapping table/index name -> status (success/skipped/error)

        Example:
            >>> from iris_vector_graph.schema import GraphSchema
            >>> import iris
            >>> conn = iris.connect("localhost", 1972, "USER", "_SYSTEM", "SYS")
            >>> cursor = conn.cursor()
            >>> results = GraphSchema.ensure_schema(cursor)
            >>> print(results['rdf_labels'])
            'success'
        """
        schema_sql = GraphSchema.get_base_schema_sql()
        return GraphSchema.execute_schema_sql(cursor, schema_sql)

    @staticmethod
    def get_base_schema_sql() -> str:
        """
        Returns the core RDF-style schema SQL without domain-specific constraints

        Returns:
            SQL string for creating base graph tables
        """
        return """
-- Domain-agnostic RDF-style graph schema
-- Supports any domain: biomedical, financial, social, etc.

-- Entity labels/types (subject -> label mappings)
CREATE TABLE IF NOT EXISTS rdf_labels(
  s      VARCHAR(256) NOT NULL,
  label  VARCHAR(128) NOT NULL
);
-- Note: IRIS supports IF NOT EXISTS for tables but not indexes
-- Drop IF NOT EXISTS from index creation for compatibility
CREATE INDEX idx_labels_label_s ON rdf_labels(label, s);
CREATE INDEX idx_labels_s_label ON rdf_labels(s, label);

-- Entity properties (subject -> key/value pairs)
CREATE TABLE IF NOT EXISTS rdf_props(
  s      VARCHAR(256) NOT NULL,
  key    VARCHAR(128) NOT NULL,
  val    VARCHAR(4000)
);
CREATE INDEX idx_props_s_key ON rdf_props(s, key);
CREATE INDEX idx_props_key_val ON rdf_props(key, val);

-- Entity relationships (subject -> predicate -> object)
-- NOTE: Manual ID management required - applications must generate edge_id values
-- NOTE: JSON datatype not universally available - use VARCHAR for compatibility
CREATE TABLE IF NOT EXISTS rdf_edges(
  edge_id    BIGINT PRIMARY KEY,
  s          VARCHAR(256) NOT NULL,
  p          VARCHAR(128) NOT NULL,
  o_id       VARCHAR(256) NOT NULL,
  qualifiers VARCHAR(4000)
);
CREATE INDEX idx_edges_s_p ON rdf_edges(s, p);
CREATE INDEX idx_edges_p_oid ON rdf_edges(p, o_id);
CREATE INDEX idx_edges_s ON rdf_edges(s);

-- Vector embeddings for semantic similarity (configurable dimensions)
CREATE TABLE IF NOT EXISTS kg_NodeEmbeddings(
  id   VARCHAR(256) PRIMARY KEY,
  emb  VECTOR(FLOAT, 768) NOT NULL  -- IRIS syntax: VECTOR(type, dimension)
);

-- HNSW index for high-performance vector search
-- Note: Remove IF NOT EXISTS for IRIS compatibility
CREATE INDEX HNSW_NodeEmb ON kg_NodeEmbeddings(emb)
  AS HNSW(M=16, efConstruction=100, Distance='Cosine');

-- Optimized vector table for production performance
CREATE TABLE IF NOT EXISTS kg_NodeEmbeddings_optimized(
  id   VARCHAR(256) PRIMARY KEY,
  emb  VECTOR(FLOAT, 768) NOT NULL  -- IRIS syntax: VECTOR(type, dimension)
);

-- HNSW index on optimized table
-- Note: Remove IF NOT EXISTS for IRIS compatibility
CREATE INDEX HNSW_NodeEmb_Optimized ON kg_NodeEmbeddings_optimized(emb)
  AS HNSW(M=16, efConstruction=200, Distance='COSINE');

-- Text documents for lexical search
CREATE TABLE IF NOT EXISTS docs(
  id    VARCHAR(256) PRIMARY KEY,
  text  VARCHAR(4000)
);

-- IRIS text index for %FIND functionality
CREATE INDEX idx_docs_text_find ON docs(text)
  TYPE BITMAP
  WITH PARAMETERS('type=word,language=en,stemmer=1,stopwords=1');
"""

    @staticmethod
    def validate_schema(cursor) -> Dict[str, bool]:
        """
        Validates that required schema tables exist

        Args:
            cursor: Database cursor

        Returns:
            Dictionary mapping table names to existence status
        """
        required_tables = [
            'rdf_labels',
            'rdf_props',
            'rdf_edges',
            'kg_NodeEmbeddings',
            'kg_NodeEmbeddings_optimized',
            'docs'
        ]

        status = {}
        for table in required_tables:
            try:
                cursor.execute(f"SELECT TOP 1 * FROM {table}")
                status[table] = True
            except Exception:
                status[table] = False

        return status

    @staticmethod
    def get_embedding_dimension(cursor, table_name: str = "kg_NodeEmbeddings") -> Optional[int]:
        """
        Detects the vector embedding dimension for a table

        Args:
            cursor: Database cursor
            table_name: Name of embedding table

        Returns:
            Vector dimension or None if not detectable
        """
        try:
            # Query system tables to get vector dimension
            cursor.execute(f"""
                SELECT VECTOR_DIMENSION(emb) as dim
                FROM {table_name}
                WHERE emb IS NOT NULL
                LIMIT 1
            """)
            result = cursor.fetchone()
            return result[0] if result else None
        except Exception:
            return None

    @staticmethod
    def create_domain_table(cursor, table_name: str, columns: Dict[str, str], indexes: Optional[List[str]] = None):
        """
        Creates a domain-specific table that integrates with the core schema

        Args:
            cursor: Database cursor
            table_name: Name of table to create
            columns: Dictionary of column_name -> column_definition
            indexes: Optional list of index definitions
        """
        # Build CREATE TABLE statement
        column_defs = []
        for col_name, col_def in columns.items():
            column_defs.append(f"  {col_name} {col_def}")

        create_sql = f"""
CREATE TABLE IF NOT EXISTS {table_name}(
{chr(10).join(column_defs)}
);
"""

        cursor.execute(create_sql)

        # Create indexes if specified
        if indexes:
            for index_sql in indexes:
                try:
                    cursor.execute(index_sql)
                except Exception as e:
                    # Index might already exist, continue
                    pass