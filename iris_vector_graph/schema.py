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
    def get_base_schema_sql() -> str:
        """Get SQL for base schema. Using explicit Graph_KG schema qualification and robust types."""
        return """
CREATE TABLE Graph_KG.nodes(
  node_id    VARCHAR(256) PRIMARY KEY,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE Graph_KG.rdf_labels(
  s          VARCHAR(256) NOT NULL,
  label      VARCHAR(128) NOT NULL,
  CONSTRAINT pk_labels PRIMARY KEY (s, label),
  CONSTRAINT fk_labels_node FOREIGN KEY (s) REFERENCES Graph_KG.nodes(node_id)
);

CREATE TABLE Graph_KG.rdf_props(
  s      VARCHAR(256) NOT NULL,
  key    VARCHAR(128) NOT NULL,
  val    LONGVARCHAR,
  CONSTRAINT pk_props PRIMARY KEY (s, key)
);

CREATE TABLE Graph_KG.rdf_edges(
  edge_id    BIGINT IDENTITY PRIMARY KEY,
  s          VARCHAR(256) NOT NULL,
  p          VARCHAR(128) NOT NULL,
  o_id       VARCHAR(256) NOT NULL,
  qualifiers %Library.DynamicObject,
  CONSTRAINT fk_edges_source FOREIGN KEY (s) REFERENCES Graph_KG.nodes(node_id),
  CONSTRAINT fk_edges_dest FOREIGN KEY (o_id) REFERENCES Graph_KG.nodes(node_id),
  CONSTRAINT u_spo UNIQUE (s, p, o_id)
);

CREATE TABLE Graph_KG.kg_NodeEmbeddings (
    id VARCHAR(256) PRIMARY KEY,
    emb VECTOR(DOUBLE, 768),
    metadata %Library.DynamicObject,
    CONSTRAINT fk_emb_node FOREIGN KEY (id) REFERENCES Graph_KG.nodes(node_id)
);

CREATE TABLE Graph_KG.kg_NodeEmbeddings_optimized (
    id VARCHAR(256) PRIMARY KEY,
    emb VECTOR(DOUBLE, 768),
    metadata %Library.DynamicObject,
    CONSTRAINT fk_emb_node_opt FOREIGN KEY (id) REFERENCES Graph_KG.nodes(node_id)
);

CREATE TABLE Graph_KG.docs(
  id    VARCHAR(256) PRIMARY KEY,
  text  VARCHAR(4000)
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
CREATE INDEX idx_props_key_val ON Graph_KG.rdf_props (key, val);
CREATE INDEX idx_props_s_key ON Graph_KG.rdf_props (s, key);
CREATE INDEX idx_edges_s_p ON Graph_KG.rdf_edges (s, p);
CREATE INDEX idx_edges_p_oid ON Graph_KG.rdf_edges (p, o_id);
CREATE INDEX idx_labels_s_label ON Graph_KG.rdf_labels (s, label);
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
CREATE INDEX IF NOT EXISTS idx_props_key_val ON Graph_KG.rdf_props (key, val);
CREATE INDEX IF NOT EXISTS idx_props_s_key ON Graph_KG.rdf_props (s, key);
CREATE INDEX IF NOT EXISTS idx_edges_s_p ON Graph_KG.rdf_edges (s, p);
CREATE INDEX IF NOT EXISTS idx_edges_p_oid ON Graph_KG.rdf_edges (p, o_id);
CREATE INDEX IF NOT EXISTS idx_labels_s_label ON Graph_KG.rdf_labels (s, label);
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
            ("idx_props_key_val", "CREATE INDEX idx_props_key_val ON Graph_KG.rdf_props (key, val)"),
            ("idx_props_s_key", "CREATE INDEX idx_props_s_key ON Graph_KG.rdf_props (s, key)"),
            ("idx_edges_s_p", "CREATE INDEX idx_edges_s_p ON Graph_KG.rdf_edges (s, p)"),
            ("idx_edges_p_oid", "CREATE INDEX idx_edges_p_oid ON Graph_KG.rdf_edges (p, o_id)"),
            ("idx_labels_s_label", "CREATE INDEX idx_labels_s_label ON Graph_KG.rdf_labels (s, label)"),
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
            "idx_props_s", "idx_props_key", "idx_props_key_val", "idx_props_s_key",
            "idx_edges_s", "idx_edges_oid", "idx_edges_p", "idx_edges_s_p", "idx_edges_p_oid",
        ]
        
        status = {}
        for name in indexes:
            try:
                cursor.execute(f"DROP INDEX {name}")
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
            'nodes': "INSERT %NOINDEX INTO Graph_KG.nodes (node_id) VALUES (?)",
            'rdf_labels': "INSERT %NOINDEX INTO Graph_KG.rdf_labels (s, label) VALUES (?, ?)",
            'rdf_props': "INSERT %NOINDEX INTO Graph_KG.rdf_props (s, key, val) VALUES (?, ?, ?)",
            'rdf_edges': "INSERT %NOINDEX INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
            'kg_NodeEmbeddings': "INSERT %NOINDEX INTO Graph_KG.kg_NodeEmbeddings (id, emb, metadata) VALUES (?, TO_VECTOR(?), ?)",
        }
        if table not in templates:
            raise ValueError(f"Unknown table: {table}. Valid: {list(templates.keys())}")
        return templates[table]

    @staticmethod
    def upgrade_val_column(cursor) -> bool:
        """
        Upgrade rdf_props.val from VARCHAR(4000) to LONGVARCHAR for JSON document support.
        
        Safe to run on existing databases - will alter column type if needed.
        LONGVARCHAR supports values up to 2GB.
        
        Returns:
            True if upgraded or already LONGVARCHAR, False on error
        """
        try:
            cursor.execute("ALTER TABLE Graph_KG.rdf_props ALTER COLUMN val LONGVARCHAR")
            return True
        except Exception as e:
            # Already LONGVARCHAR or other issue
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
                cursor.execute(f"SELECT TOP 1 * FROM {table}")
                status[table] = True
            except Exception:
                status[table] = False

        return status

    @staticmethod
    def get_embedding_dimension(cursor, table_name: str = "Graph_KG.kg_NodeEmbeddings") -> Optional[int]:
        """
        Detects the vector embedding dimension for a table
        """
        try:
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
