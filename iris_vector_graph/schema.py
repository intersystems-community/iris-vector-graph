#!/usr/bin/env python3
"""
Domain-Agnostic Graph Schema Management

Provides RDF-style graph schema utilities that can be used across domains.
Extracted from the biomedical-specific implementation for reusability.
"""

from typing import Dict, List, Optional
from .security import sanitize_identifier, validate_table_name

class GraphSchema:
    """Domain-agnostic RDF-style graph schema management"""

    @staticmethod
    def get_base_schema_sql(embedding_dimension: int = 768) -> str:
        """Get SQL for base schema. Using explicit Graph_KG schema qualification and robust types.

        Args:
            embedding_dimension: Dimension of the vector embeddings. Defaults to 768 for
                                 backward compatibility, but should be set to match your
                                 actual embedding model (e.g. 384 for all-MiniLM-L6-v2).
        """
        return f"""
CREATE TABLE Graph_KG.nodes(
  node_id    VARCHAR(256) %EXACT PRIMARY KEY,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE Graph_KG.rdf_labels(
  s          VARCHAR(256) %EXACT NOT NULL,
  label      VARCHAR(128) %EXACT NOT NULL,
  CONSTRAINT pk_labels PRIMARY KEY (s, label),
  CONSTRAINT fk_labels_node FOREIGN KEY (s) REFERENCES Graph_KG.nodes(node_id)
);

CREATE TABLE Graph_KG.rdf_props(
  s      VARCHAR(256) %EXACT NOT NULL,
  key    VARCHAR(128) %EXACT NOT NULL,
  val    VARCHAR(64000) %EXACT,
  CONSTRAINT pk_props PRIMARY KEY (s, key)
);

CREATE TABLE Graph_KG.rdf_edges(
  edge_id    BIGINT IDENTITY PRIMARY KEY,
  s          VARCHAR(256) %EXACT NOT NULL,
  p          VARCHAR(128) %EXACT NOT NULL,
  o_id       VARCHAR(256) %EXACT NOT NULL,
  qualifiers %Library.DynamicObject,
  CONSTRAINT fk_edges_source FOREIGN KEY (s) REFERENCES Graph_KG.nodes(node_id),
  CONSTRAINT fk_edges_dest FOREIGN KEY (o_id) REFERENCES Graph_KG.nodes(node_id),
  CONSTRAINT u_spo UNIQUE (s, p, o_id)
);

CREATE TABLE Graph_KG.kg_NodeEmbeddings (
    id VARCHAR(256) %EXACT PRIMARY KEY,
    emb VECTOR(DOUBLE, {embedding_dimension}),
    metadata %Library.DynamicObject,
    CONSTRAINT fk_emb_node FOREIGN KEY (id) REFERENCES Graph_KG.nodes(node_id)
);

CREATE TABLE Graph_KG.kg_NodeEmbeddings_optimized (
    id VARCHAR(256) %EXACT PRIMARY KEY,
    emb VECTOR(DOUBLE, {embedding_dimension}),
    metadata %Library.DynamicObject,
    CONSTRAINT fk_emb_node_opt FOREIGN KEY (id) REFERENCES Graph_KG.nodes(node_id)
);

CREATE TABLE Graph_KG.docs(
  id    VARCHAR(256) %EXACT PRIMARY KEY,
  text  VARCHAR(4000) %EXACT
);

-- Indexes for graph traversal performance (based on TrustGraph patterns)
-- Single-column indexes for basic lookups
CREATE INDEX idx_labels_s ON Graph_KG.rdf_labels (s);
CREATE INDEX idx_labels_label ON Graph_KG.rdf_labels (label);
CREATE INDEX idx_props_s ON Graph_KG.rdf_props (s);
CREATE INDEX idx_props_key ON Graph_KG.rdf_props (key);
CREATE INDEX idx_edges_s ON Graph_KG.rdf_edges (s);
CREATE INDEX idx_edges_oid ON Graph_KG.rdf_edges (o_id);
CREATE INDEX idx_edges_p ON Graph_KG.rdf_edges (p);

-- Composite indexes for common query patterns
CREATE INDEX idx_props_s_key ON Graph_KG.rdf_props (s, key);
CREATE INDEX idx_edges_s_p ON Graph_KG.rdf_edges (s, p);
CREATE INDEX idx_edges_p_oid ON Graph_KG.rdf_edges (p, o_id);
CREATE INDEX idx_labels_s_label ON Graph_KG.rdf_labels (s, label);

-- Substring indexing on rdf_props.val for fast property lookups
CREATE INDEX idx_props_val_ifind ON Graph_KG.rdf_props(val) INDEXTYPE = %iFind.Index.Basic;

-- Functional index on edge confidence for fast filtering
CREATE INDEX idx_edges_confidence ON Graph_KG.rdf_edges(JSON_VALUE(qualifiers, '$.confidence' RETURNING INTEGER));
"""

    @staticmethod
    def get_indexes_sql() -> str:
        """Get SQL to create performance indexes. Safe to run on existing databases."""
        return """
-- Single-column indexes
CREATE INDEX IF NOT EXISTS idx_labels_s ON Graph_KG.rdf_labels (s);
CREATE INDEX IF NOT EXISTS idx_labels_label ON Graph_KG.rdf_labels (label);
CREATE INDEX IF NOT EXISTS idx_props_s ON Graph_KG.rdf_props (s);
CREATE INDEX IF NOT EXISTS idx_props_key ON Graph_KG.rdf_props (key);
CREATE INDEX IF NOT EXISTS idx_edges_s ON Graph_KG.rdf_edges (s);
CREATE INDEX IF NOT EXISTS idx_edges_oid ON Graph_KG.rdf_edges (o_id);
CREATE INDEX IF NOT EXISTS idx_edges_p ON Graph_KG.rdf_edges (p);
-- Composite indexes for common patterns
CREATE INDEX IF NOT EXISTS idx_props_s_key ON Graph_KG.rdf_props (s, key);
CREATE INDEX IF NOT EXISTS idx_edges_s_p ON Graph_KG.rdf_edges (s, p);
CREATE INDEX IF NOT EXISTS idx_edges_p_oid ON Graph_KG.rdf_edges (p, o_id);
CREATE INDEX IF NOT EXISTS idx_labels_s_label ON Graph_KG.rdf_labels (s, label);

-- Substring indexing on rdf_props.val for fast property lookups
-- Uses IRIS iFind for high-performance substring/text search
CREATE INDEX idx_props_val_ifind ON Graph_KG.rdf_props(val) INDEXTYPE = %iFind.Index.Basic;

-- Functional index on edge confidence for fast filtering
-- Optimizes JSON processing by indexing the value inside the DynamicObject
CREATE INDEX idx_edges_confidence ON Graph_KG.rdf_edges(JSON_VALUE(qualifiers, '$.confidence' RETURNING INTEGER));
"""


    @staticmethod
    def ensure_indexes(cursor) -> Dict[str, bool]:
        """
        Create performance indexes if they don't exist. Safe for existing databases.
        
        Returns:
            Dict mapping index name to success status
        """
        indexes = [
            # Single-column indexes
            ("idx_labels_s", "CREATE INDEX idx_labels_s ON Graph_KG.rdf_labels (s)"),
            ("idx_labels_label", "CREATE INDEX idx_labels_label ON Graph_KG.rdf_labels (label)"),
            ("idx_props_s", "CREATE INDEX idx_props_s ON Graph_KG.rdf_props (s)"),
            ("idx_props_key", "CREATE INDEX idx_props_key ON Graph_KG.rdf_props (key)"),
            ("idx_edges_s", "CREATE INDEX idx_edges_s ON Graph_KG.rdf_edges (s)"),
            ("idx_edges_oid", "CREATE INDEX idx_edges_oid ON Graph_KG.rdf_edges (o_id)"),
            ("idx_edges_p", "CREATE INDEX idx_edges_p ON Graph_KG.rdf_edges (p)"),
            # Composite indexes for common patterns
            ("idx_props_s_key", "CREATE INDEX idx_props_s_key ON Graph_KG.rdf_props (s, key)"),
            ("idx_edges_s_p", "CREATE INDEX idx_edges_s_p ON Graph_KG.rdf_edges (s, p)"),
            ("idx_edges_p_oid", "CREATE INDEX idx_edges_p_oid ON Graph_KG.rdf_edges (p, o_id)"),
            ("idx_labels_s_label", "CREATE INDEX idx_labels_s_label ON Graph_KG.rdf_labels (s, label)"),
            # Substring and Functional Indexes
            ("idx_props_val_ifind", "CREATE INDEX idx_props_val_ifind ON Graph_KG.rdf_props(val) INDEXTYPE = %iFind.Index.Basic"),
            ("idx_edges_confidence", "CREATE INDEX idx_edges_confidence ON Graph_KG.rdf_edges(JSON_VALUE(qualifiers, '$.confidence' RETURNING INTEGER))"),
            # Drop problematic indexes
            ("drop_idx_props_key_val", "DROP INDEX idx_props_key_val"),
        ]
        
        status = {}
        for name, sql in indexes:
            try:
                cursor.execute(sql)
                status[name] = True
            except Exception as e:
                # Index already exists is OK
                if "already exists" in str(e).lower() or "already has" in str(e).lower():
                    status[name] = True
                else:
                    status[name] = False
        
        # Upgrade val column size
        status["upgrade_val_column"] = GraphSchema.upgrade_val_column(cursor)
        
        return status

    @staticmethod
    def disable_indexes(cursor) -> Dict[str, bool]:
        """
        Disable indexes for bulk loading. Re-enable with rebuild_indexes() after loading.
        
        This dramatically speeds up bulk INSERT operations by skipping index maintenance.
        
        Returns:
            Dict mapping index name to success status
        """
        # IRIS: ALTER INDEX ... DISABLE or DROP INDEX
        indexes = [
            "idx_labels_s", "idx_labels_label", "idx_labels_s_label",
            "idx_props_s", "idx_props_key", "idx_props_s_key",
            "idx_edges_s", "idx_edges_oid", "idx_edges_p", "idx_edges_s_p", "idx_edges_p_oid",
        ]
        
        status = {}
        for name in indexes:
            try:
                # Sanitize index name to prevent SQL injection
                safe_name = sanitize_identifier(name)
                cursor.execute(f"DROP INDEX {safe_name}")
                status[name] = True
            except Exception as e:
                if "does not exist" in str(e).lower():
                    status[name] = True  # Already gone
                else:
                    status[name] = False
        return status

    @staticmethod
    def rebuild_indexes(cursor) -> Dict[str, bool]:
        """
        Rebuild all indexes after bulk loading. Call this after disable_indexes() + bulk INSERT.
        
        Returns:
            Dict mapping index name to success status
        """
        return GraphSchema.ensure_indexes(cursor)

    @staticmethod
    def get_bulk_insert_sql(table: str) -> str:
        """
        Get INSERT statement with %NOINDEX hint for bulk loading.
        
        Args:
            table: Table name ('nodes', 'rdf_labels', 'rdf_props', 'rdf_edges', 'kg_NodeEmbeddings')
            
        Returns:
            INSERT SQL with %NOINDEX for fast bulk loading
            
        Example:
            sql = GraphSchema.get_bulk_insert_sql('rdf_labels')
            cursor.execute(sql, [node_id, label])
        """
        templates = {
            'nodes': "INSERT INTO Graph_KG.nodes (node_id) SELECT ? WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.nodes WHERE node_id = ?)",
            'rdf_labels': "INSERT INTO Graph_KG.rdf_labels (s, label) SELECT ?, ? WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.rdf_labels WHERE s = ? AND label = ?)",
            'rdf_props': "INSERT INTO Graph_KG.rdf_props (s, \"key\", val) SELECT ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.rdf_props WHERE s = ? AND \"key\" = ?)",
            'rdf_edges': "INSERT INTO Graph_KG.rdf_edges (s, p, o_id) SELECT ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.rdf_edges WHERE s = ? AND p = ? AND o_id = ?)",
            'kg_NodeEmbeddings': "INSERT INTO Graph_KG.kg_NodeEmbeddings (id, emb, metadata) SELECT ?, TO_VECTOR(?), ? WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.kg_NodeEmbeddings WHERE id = ?)",
        }
        if table not in templates:
            raise ValueError(f"Unknown table: {table}. Valid: {list(templates.keys())}")
        return templates[table]

    @staticmethod
    def upgrade_val_column(cursor) -> bool:
        """
        Upgrade rdf_props.val from VARCHAR(4000) to VARCHAR(64000) for large value support.
        
        Safe to run on existing databases - will alter column type if needed.
        VARCHAR(64000) supports values up to 64KB while keeping REPLACE function compatibility.
        
        Returns:
            True if upgraded or already large enough, False on error
        """
        try:
            cursor.execute("ALTER TABLE Graph_KG.rdf_props ALTER COLUMN val VARCHAR(64000)")
            return True
        except Exception as e:
            # Already correct size or other issue
            if "already" in str(e).lower() or "same" in str(e).lower():
                return True
            return False

    @staticmethod
    def validate_schema(cursor) -> Dict[str, bool]:
        """
        Validates that required schema tables exist
        """
        required_tables = [
            'Graph_KG.rdf_labels',
            'Graph_KG.rdf_props',
            'Graph_KG.rdf_edges',
            'Graph_KG.kg_NodeEmbeddings',
            'Graph_KG.kg_NodeEmbeddings_optimized',
            'Graph_KG.docs'
        ]

        status = {}
        for table in required_tables:
            try:
                # Validate table name against allowlist
                safe_table = validate_table_name(table)
                cursor.execute(f"SELECT TOP 1 * FROM {safe_table}")
                status[table] = True
            except Exception:
                status[table] = False

        return status

    @staticmethod
    def get_embedding_dimension(cursor, table_name: str = "Graph_KG.kg_NodeEmbeddings") -> Optional[int]:
        """
        Detects the vector embedding dimension for a table using IRIS metadata.
        """
        # IRIS stores vector dimension in class metadata
        # Table Graph_KG.kg_NodeEmbeddings is usually class Graph.KG.kgNodeEmbeddings
        # We'll search for the 'emb' property across classes containing 'Graph' and 'NodeEmbeddings'
        try:
            # Query IRIS CompiledProperty metadata directly for the most reliable dimension info
            cursor.execute(
                """
                SELECT Parameters 
                FROM %Dictionary.CompiledProperty 
                WHERE Name = 'emb' 
                  AND (Parent [ 'Graph' OR Parent [ 'graph' )
                  AND (Parent [ 'Embeddings' OR Parent [ 'embeddings' )
                """
            )
            rows = cursor.fetchall()
            for row in rows:
                params = str(row[0])
                if 'LEN,' in params:
                    # Parse 'LEN,768' from params string
                    parts = params.split(',')
                    for i, p in enumerate(parts):
                        if p == 'LEN' and i + 1 < len(parts):
                            return int(parts[i+1])
        except Exception:
            pass

        # Fallback to INFORMATION_SCHEMA (though IRIS often reports VECTOR as VARCHAR there)
        schema = "Graph_KG"
        table = "kg_NodeEmbeddings"
        if "." in table_name:
            schema, table = table_name.split(".", 1)
            
        try:
            result = None
            for s_name, t_name in [(schema, table), (schema.upper(), table.upper())]:
                cursor.execute(
                    """
                    SELECT DTD_IDENTIFIER, COLUMN_TYPE, DATA_TYPE
                    FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_SCHEMA = ?
                      AND TABLE_NAME = ?
                      AND COLUMN_NAME = 'emb'
                    """,
                    [s_name, t_name]
                )
                result = cursor.fetchone()
                if result: break
            
            if result:
                for val in result:
                    if not val: continue
                    val_str = str(val)
                    import re
                    matches = re.findall(r'(\d+)\s*\)', val_str)
                    if matches: return int(matches[-1])
                    digits = "".join(ch for ch in val_str if ch.isdigit())
                    if digits: return int(digits)
        except Exception:
            pass
            
        return None

    @staticmethod
    def create_domain_table(cursor, table_name: str, columns: Dict[str, str], indexes: Optional[List[str]] = None):
        """
        Creates a domain-specific table.
        """
        # Build CREATE TABLE statement
        column_defs = []
        for col_name, col_def in columns.items():
            if "REFERENCES nodes" in col_def:
                col_def = col_def.replace("REFERENCES nodes", "REFERENCES Graph_KG.nodes")
            column_defs.append(f'  "{col_name}" {col_def}')

        # Ensure table name is qualified
        if "." not in table_name:
            table_name = f"Graph_KG.{table_name}"

        create_sql = f"CREATE TABLE {table_name}(\n{','.join(column_defs)}\n)"

        try:
            cursor.execute(create_sql)
        except Exception as e:
            if "already exists" not in str(e).lower():
                raise

        # Create indexes if specified
        if indexes:
            for index_sql in indexes:
                try:
                    cursor.execute(index_sql)
                except Exception as e:
                    if "already exists" not in str(e).lower() and "already has a" not in str(e).lower():
                        print(f"Index creation warning: {e}")
