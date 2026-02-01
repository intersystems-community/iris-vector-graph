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
  val    VARCHAR(4000),
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

-- Index for faster discovery
CREATE INDEX idx_edges_oid ON Graph_KG.rdf_edges (o_id);
"""

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
