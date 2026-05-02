#!/usr/bin/env python3
"""
Domain-Agnostic Graph Schema Management

Provides RDF-style graph schema utilities that can be used across domains.
Extracted from the biomedical-specific implementation for reusability.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from .capabilities import IRISCapabilities
from .security import sanitize_identifier, validate_table_name

logger = logging.getLogger(__name__)


def _call_classmethod(conn_or_cursor, class_name: str, method_name: str, *args) -> Any:
    if hasattr(conn_or_cursor, "cursor"):
        conn = conn_or_cursor
    elif hasattr(conn_or_cursor, "_connection"):
        conn = conn_or_cursor._connection
    else:
        conn = conn_or_cursor
    import iris as _iris_pkg
    iris_obj = _iris_pkg.createIRIS(conn)
    return iris_obj.classMethodValue(class_name, method_name, *args)



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

CREATE TABLE IF NOT EXISTS Graph_KG.kg_EdgeEmbeddings (
    s    VARCHAR(256) %EXACT NOT NULL,
    p    VARCHAR(512) %EXACT NOT NULL,
    o_id VARCHAR(256) %EXACT NOT NULL,
    emb  VECTOR(DOUBLE, {embedding_dimension}),
    CONSTRAINT pk_edge_emb PRIMARY KEY (s, p, o_id)
);

CREATE TABLE Graph_KG.docs(
  id    VARCHAR(256) %EXACT PRIMARY KEY,
  text  VARCHAR(4000) %EXACT
);

CREATE TABLE IF NOT EXISTS Graph_KG.fhir_bridges (
    fhir_code        VARCHAR(64) %EXACT NOT NULL,
    kg_node_id       VARCHAR(256) %EXACT NOT NULL,
    fhir_code_system VARCHAR(128) NOT NULL DEFAULT 'ICD10CM',
    bridge_type      VARCHAR(64) NOT NULL DEFAULT 'icd10_to_mesh',
    confidence       FLOAT DEFAULT 1.0,
    source_cui       VARCHAR(16),
    CONSTRAINT pk_bridge PRIMARY KEY (fhir_code, kg_node_id)
);

CREATE INDEX idx_bridges_code_type ON Graph_KG.fhir_bridges (fhir_code, bridge_type);
CREATE INDEX idx_bridges_kg_node ON Graph_KG.fhir_bridges (kg_node_id);
CREATE INDEX idx_bridges_type ON Graph_KG.fhir_bridges (bridge_type);

CREATE TABLE IF NOT EXISTS Graph_KG.rdf_reifications (
    reifier_id VARCHAR(256) %EXACT NOT NULL,
    edge_id BIGINT NOT NULL,
    CONSTRAINT pk_reifications PRIMARY KEY (reifier_id)
);

CREATE INDEX idx_reif_edge ON Graph_KG.rdf_reifications (edge_id);

CREATE TABLE IF NOT EXISTS Graph_KG.table_mappings (
    label         VARCHAR(255) NOT NULL PRIMARY KEY,
    sql_table     VARCHAR(500) NOT NULL,
    id_column     VARCHAR(255) NOT NULL,
    prop_columns  VARCHAR(4000),
    registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS Graph_KG.relationship_mappings (
    predicate    VARCHAR(255) NOT NULL,
    source_label VARCHAR(255) NOT NULL,
    target_label VARCHAR(255) NOT NULL,
    target_fk    VARCHAR(255),
    via_table    VARCHAR(500),
    via_source   VARCHAR(255),
    via_target   VARCHAR(255),
    CONSTRAINT pk_rel_mappings PRIMARY KEY (source_label, predicate, target_label)
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
            (
                "idx_labels_label",
                "CREATE INDEX idx_labels_label ON Graph_KG.rdf_labels (label)",
            ),
            ("idx_props_s", "CREATE INDEX idx_props_s ON Graph_KG.rdf_props (s)"),
            ("idx_props_key", "CREATE INDEX idx_props_key ON Graph_KG.rdf_props (key)"),
            ("idx_edges_s", "CREATE INDEX idx_edges_s ON Graph_KG.rdf_edges (s)"),
            (
                "idx_edges_oid",
                "CREATE INDEX idx_edges_oid ON Graph_KG.rdf_edges (o_id)",
            ),
            ("idx_edges_p", "CREATE INDEX idx_edges_p ON Graph_KG.rdf_edges (p)"),
            # Composite indexes for common patterns
            (
                "idx_props_s_key",
                "CREATE INDEX idx_props_s_key ON Graph_KG.rdf_props (s, key)",
            ),
            (
                "idx_edges_s_p",
                "CREATE INDEX idx_edges_s_p ON Graph_KG.rdf_edges (s, p)",
            ),
            (
                "idx_edges_p_oid",
                "CREATE INDEX idx_edges_p_oid ON Graph_KG.rdf_edges (p, o_id)",
            ),
            (
                "idx_labels_s_label",
                "CREATE INDEX idx_labels_s_label ON Graph_KG.rdf_labels (s, label)",
            ),
            (
                "idx_props_val_ifind",
                "CREATE INDEX idx_props_val_ifind ON Graph_KG.rdf_props(val) INDEXTYPE = %iFind.Index.Basic",
            ),
            (
                "idx_edges_confidence",
                "CREATE INDEX idx_edges_confidence ON Graph_KG.rdf_edges(JSON_VALUE(qualifiers, '$.confidence' RETURNING INTEGER))",
            ),
            # Drop problematic indexes
            ("drop_idx_props_key_val", "DROP INDEX idx_props_key_val"),
        ]

        _OPTIONAL_INDEXES = {"idx_props_val_ifind", "idx_edges_confidence"}

        status = {}
        for name, sql in indexes:
            try:
                cursor.execute(sql)
                status[name] = True
            except Exception as e:
                err = str(e).lower()
                sqlcode = ""
                import re as _re

                m = _re.search(r"sqlcode.*?<(-?\d+)>", err)
                if m:
                    sqlcode = m.group(1)

                if (
                    "already exists" in err
                    or "already has" in err
                    or "already has index" in err
                ):
                    status[name] = True
                elif sqlcode == "-400" and "rdf_edges" in sql.lower():
                    alt_sql = GraphSchema._create_index_alter_table(name, sql)
                    if alt_sql:
                        try:
                            cursor.execute(alt_sql)
                            status[name] = True
                            continue
                        except Exception as e2:
                            e2_err = str(e2).lower()
                            if "already exists" in e2_err or "already has" in e2_err:
                                status[name] = True
                            else:
                                logger.debug(
                                    "Index %s ALTER TABLE fallback failed: %s", name, e2
                                )
                                status[name] = False
                    else:
                        logger.debug(
                            "Index %s skipped (-400, no ALTER TABLE equivalent): %s",
                            name,
                            e,
                        )
                        status[name] = False
                elif name in _OPTIONAL_INDEXES or sqlcode == "-400":
                    logger.debug("Optional/fatal index %s skipped: %s", name, e)
                    status[name] = False
                else:
                    status[name] = False

        status["upgrade_val_column"] = GraphSchema.upgrade_val_column(cursor)
        status["add_graph_id_column"] = GraphSchema.add_graph_id_column(cursor)
        status["add_graph_id_index"] = GraphSchema.add_graph_id_index(cursor)
        status["update_spo_unique_constraint"] = (
            GraphSchema.update_spo_unique_constraint(cursor)
        )

        return status

    @staticmethod
    def update_spo_unique_constraint(cursor) -> bool:
        try:
            cursor.execute("ALTER TABLE Graph_KG.rdf_edges DROP CONSTRAINT u_spo")
        except Exception:
            pass
        try:
            cursor.execute(
                "ALTER TABLE Graph_KG.rdf_edges ADD CONSTRAINT u_spo_graph UNIQUE (s, p, o_id, graph_id)"
            )
            return True
        except Exception as e:
            if "already exists" in str(e).lower():
                return True
            return False

    @staticmethod
    def add_graph_id_index(cursor) -> bool:
        try:
            cursor.execute(
                "CREATE INDEX idx_edges_graph_id ON Graph_KG.rdf_edges (graph_id)"
            )
            return True
        except Exception as e:
            if "already exists" in str(e).lower() or "already has" in str(e).lower():
                return True
            return False

    @staticmethod
    def _create_index_alter_table(name: str, create_sql: str) -> Optional[str]:
        import re as _re

        m = _re.match(
            r"CREATE\s+INDEX\s+(\w+)\s+ON\s+([\w\.]+)\s*\(([^)]+)\)",
            create_sql.strip(),
            _re.IGNORECASE,
        )
        if not m:
            return None
        idx_name, table, cols = m.group(1), m.group(2), m.group(3)
        return f"ALTER TABLE {table} ADD INDEX {idx_name} ({cols})"

    @staticmethod
    def add_graph_id_column(cursor) -> bool:
        try:
            cursor.execute(
                "ALTER TABLE Graph_KG.rdf_edges ADD COLUMN graph_id VARCHAR(256) %EXACT NULL"
            )
            return True
        except Exception as e:
            if "already exists" in str(e).lower() or "duplicate" in str(e).lower():
                return True
            return False

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
            "idx_labels_s",
            "idx_labels_label",
            "idx_labels_s_label",
            "idx_props_s",
            "idx_props_key",
            "idx_props_s_key",
            "idx_edges_s",
            "idx_edges_oid",
            "idx_edges_p",
            "idx_edges_s_p",
            "idx_edges_p_oid",
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
            "nodes": "INSERT INTO Graph_KG.nodes (node_id) SELECT ? WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.nodes WHERE node_id = ?)",
            "rdf_labels": "INSERT INTO Graph_KG.rdf_labels (s, label) SELECT ?, ? WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.rdf_labels WHERE s = ? AND label = ?)",
            "rdf_props": 'INSERT INTO Graph_KG.rdf_props (s, "key", val) SELECT ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.rdf_props WHERE s = ? AND "key" = ?)',
            "rdf_edges": "INSERT INTO Graph_KG.rdf_edges (s, p, o_id) SELECT ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.rdf_edges WHERE s = ? AND p = ? AND o_id = ?)",
            "rdf_edges_with_graph": "INSERT INTO Graph_KG.rdf_edges (s, p, o_id, graph_id) SELECT ?, ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.rdf_edges WHERE s = ? AND p = ? AND o_id = ? AND (graph_id = ? OR (graph_id IS NULL AND ? IS NULL)))",
            "kg_NodeEmbeddings": "INSERT INTO Graph_KG.kg_NodeEmbeddings (id, emb, metadata) SELECT ?, TO_VECTOR(?), ? WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.kg_NodeEmbeddings WHERE id = ?)",
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
            cursor.execute(
                "ALTER TABLE Graph_KG.rdf_props ALTER COLUMN val VARCHAR(64000)"
            )
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
            "Graph_KG.rdf_labels",
            "Graph_KG.rdf_props",
            "Graph_KG.rdf_edges",
            "Graph_KG.kg_NodeEmbeddings",
            "Graph_KG.kg_NodeEmbeddings_optimized",
            "Graph_KG.kg_EdgeEmbeddings",
            "Graph_KG.docs",
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
    def get_embedding_dimension(
        cursor, table_name: str = "Graph_KG.kg_NodeEmbeddings"
    ) -> Optional[int]:
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
                if "LEN," in params:
                    # Parse 'LEN,768' from params string
                    parts = params.split(",")
                    for i, p in enumerate(parts):
                        if p == "LEN" and i + 1 < len(parts):
                            return int(parts[i + 1])
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
                    [s_name, t_name],
                )
                result = cursor.fetchone()
                if result:
                    break

            if result:
                for val in result:
                    if not val:
                        continue
                    val_str = str(val)
                    import re

                    matches = re.findall(r"(\d+)\s*\)", val_str)
                    if matches:
                        return int(matches[-1])
                    digits = "".join(ch for ch in val_str if ch.isdigit())
                    if digits:
                        return int(digits)
        except Exception:
            pass

        return None

    @staticmethod
    def get_procedures_sql_list(
        table_schema: str = "Graph_KG",
        embedding_dimension: int = 1000,
    ) -> List[str]:
        """
        Get a list of SQL statements to install retrieval stored procedures.

        Args:
            table_schema: SQL schema containing the data tables (e.g. "Graph_KG").
            embedding_dimension: Vector dimension for the DECLARE clause inside
                kg_KNN_VEC. Must match the emb column dimension in
                kg_NodeEmbeddings. Default 1000 for backward compatibility;
                internal callers (initialize_schema) MUST pass the real dimension.

        Returns:
            List of SQL DDL strings in execution order. Each is a complete
            statement suitable for cursor.execute().
        """
        return [
             "CREATE SCHEMA iris_vector_graph",
            """
CREATE OR REPLACE FUNCTION SQLUser.JSON_ARRAYLENGTH(j VARCHAR(32000)) RETURNS INTEGER LANGUAGE OBJECTSCRIPT { Set a = ##class(%Library.DynamicArray).%FromJSON(j) Quit a.%Size() }
""",
            """
CREATE OR REPLACE FUNCTION SQLUser.JSON_ARRAYGET(j VARCHAR(32000), i INTEGER) RETURNS VARCHAR(4000) LANGUAGE OBJECTSCRIPT { Set a = ##class(%Library.DynamicArray).%FromJSON(j) Quit a.%Get(i) _ "" }
""",
            """
CREATE OR REPLACE FUNCTION SQLUser.JSON_VALUE(j VARCHAR(32000), p VARCHAR(1000)) RETURNS VARCHAR(4000) LANGUAGE OBJECTSCRIPT { Set obj = ##class(%Library.DynamicObject).%FromJSON(j), key = $Extract(p, 3, *) Quit obj.%Get(key) _ "" }
""",
            """
CREATE OR REPLACE FUNCTION SQLUser.RAND() RETURNS DOUBLE LANGUAGE OBJECTSCRIPT { Quit $RANDOM(1000000) / 1000000.0 }
""",
            """
CREATE OR REPLACE FUNCTION SQLUser.NEWID() RETURNS VARCHAR(36) LANGUAGE OBJECTSCRIPT { Quit $SYSTEM.Util.CreateGUID() }
""",
            f"""
CREATE OR REPLACE PROCEDURE {table_schema}.kg_KNN_VEC(
  IN queryInput VARCHAR(32000),
  IN k INT,
  IN labelFilter VARCHAR(128),
  IN embeddingConfig VARCHAR(128)
)
LANGUAGE SQL
BEGIN
  SELECT TOP :k n.id, VECTOR_COSINE(n.emb, TO_VECTOR(:queryInput, DOUBLE)) AS score
  FROM {table_schema}.kg_NodeEmbeddings n
  LEFT JOIN {table_schema}.rdf_labels L ON L.s = n.id
  WHERE (:labelFilter IS NULL OR :labelFilter = '' OR L.label = :labelFilter)
  ORDER BY score DESC;
END
""",
            f"""
CREATE OR REPLACE PROCEDURE {table_schema}.kg_TXT(
  IN q VARCHAR(4000),
  IN k INT
)
LANGUAGE SQL
BEGIN
  SELECT TOP :k d.id, %FIND.Rank(d.text, :q) AS bm25
  FROM {table_schema}.docs d
  WHERE %FIND(d.text, :q) > 0
  ORDER BY bm25 DESC;
END
""",
            f"""
CREATE OR REPLACE PROCEDURE {table_schema}.kg_RRF_FUSE(
  IN k INT,
  IN k1 INT,
  IN k2 INT,
  IN c INT,
  IN queryVector VARCHAR(32000),
  IN qtext VARCHAR(4000)
)
LANGUAGE SQL
BEGIN
  WITH V AS (
    SELECT ROW_NUMBER() OVER (ORDER BY score DESC) AS r, id, score AS vs
    FROM {table_schema}.kg_KNN_VEC(:queryVector, :k1, NULL, NULL)
  ),
  K AS (
    SELECT ROW_NUMBER() OVER (ORDER BY bm25 DESC) AS r, id, bm25
    FROM {table_schema}.kg_TXT(:qtext, :k2)
  ),
  F AS (
    SELECT COALESCE(V.id, K.id) AS id,
           (1.0/(:c + COALESCE(V.r, 1000000000))) +
           (1.0/(:c + COALESCE(K.r, 1000000000))) AS rrf,
           V.vs, K.bm25
    FROM V FULL OUTER JOIN K ON V.id = K.id
  )
  SELECT TOP :k id, rrf, vs, bm25
  FROM F
  ORDER BY rrf DESC;
END
""",
            f"""
CREATE OR REPLACE FUNCTION {table_schema}.kg_PPR(
  seedEntities VARCHAR(32000),
  dampingFactor DOUBLE DEFAULT 0.85,
  maxIterations INT DEFAULT 100,
  bidirectional INT DEFAULT 0,
  reverseEdgeWeight DOUBLE DEFAULT 1.0
)
RETURNS VARCHAR(8000)
LANGUAGE OBJECTSCRIPT
{{
    set result = ##class(Graph.KG.PageRank).RunJson(seedEntities, dampingFactor, maxIterations, bidirectional, reverseEdgeWeight)
    quit result
}}
""",
        ]

    # ------------------------------------------------------------------
    # ObjectScript (.cls) deployment helpers
    # ------------------------------------------------------------------

    @staticmethod
    def check_objectscript_classes(cursor, conn=None) -> "IRISCapabilities":
        """
        Detect which ObjectScript classes are compiled in IRIS.

        Uses %Dictionary.ClassDefinition (fast); falls back to $CLASSMETHOD
        %Exists if the dictionary query fails (e.g. restricted namespace).

        Args:
            cursor: Active IRIS dbapi cursor (for SQL queries).
            conn:   Optional connection object for native API calls via
                    createIRIS(). When None, the cursor itself is tried.

        Returns:
            IRISCapabilities with objectscript_deployed, graphoperators_deployed,
            and kg_built flags set.
        """
        # Resolve the native-API handle: prefer explicit conn, else try cursor
        _native_target = conn if conn is not None else cursor

        caps = IRISCapabilities()

        # --- Graph.KG.PageRank (sentinel: reliably compiles; Edge conflicts with existing DDL table) ---
        try:
            cursor.execute(
                "SELECT COUNT(*) FROM %Dictionary.ClassDefinition "
                "WHERE Name='Graph.KG.PageRank'"
            )
            row = cursor.fetchone()
            caps.objectscript_deployed = bool(row and row[0])
        except Exception:
            try:
                result = _call_classmethod(
                    _native_target, "Graph.KG.PageRank", "%Exists", 1
                )
                caps.objectscript_deployed = bool(result)
            except Exception:
                caps.objectscript_deployed = False

        # --- iris.vector.graph.GraphOperators ---
        try:
            cursor.execute(
                "SELECT COUNT(*) FROM %Dictionary.ClassDefinition "
                "WHERE Name='iris.vector.graph.GraphOperators'"
            )
            row = cursor.fetchone()
            caps.graphoperators_deployed = bool(row and row[0])
        except Exception:
            try:
                result = _call_classmethod(
                    _native_target, "iris.vector.graph.GraphOperators", "%Exists", 1
                )
                caps.graphoperators_deployed = bool(result)
            except Exception:
                caps.graphoperators_deployed = False

        # --- ^KG bootstrap marker via native API ---
        try:
            result = _call_classmethod(
                _native_target, "Graph.KG.Meta", "IsSet", "kg_built"
            )
            caps.kg_built = bool(result)
        except Exception:
            caps.kg_built = False

        try:
            result = _call_classmethod(
                _native_target, "Graph.KG.Meta", "IsSet", "nkg_built"
            )
            caps.nkg_built = bool(result)
        except Exception:
            caps.nkg_built = False

        return caps

    @staticmethod
    def deploy_objectscript_classes(
        cursor, iris_src_path: Path, conn=None
    ) -> "IRISCapabilities":
        """
        Deploy ObjectScript .cls files to IRIS and return capability flags.

        Attempts to load .cls files via $system.OBJ.LoadDir through the native
        API bridge. If files are not accessible from the IRIS server filesystem,
        falls back to detection-only (check what's already compiled).

        Args:
            cursor:        Active IRIS dbapi cursor.
            iris_src_path: Path to iris_src/src/ directory containing .cls files.
            conn:          Connection for native API calls.

        Returns:
            IRISCapabilities reflecting what is compiled in IRIS after deployment.
        """
        _native = conn if conn is not None else cursor

        _SKIP_CLASSES = frozenset({"Graph.KG.Edge", "Graph.KG.TestEdge"})

        src_dir = str(iris_src_path / "src") if iris_src_path else ""
        deployed = False
        for candidate_dir in ["/tmp/src/", "/irisdev/app/iris_src/src/", src_dir]:
            if not candidate_dir:
                continue
            try:
                for cls_name in _SKIP_CLASSES:
                    file_path = f"{candidate_dir}/{cls_name.replace('.', '/')}.cls"
                    try:
                        _call_classmethod(_native, "%Library.File", "Delete", file_path)
                    except Exception:
                        pass
                _call_classmethod(
                    _native, "%SYSTEM.OBJ", "LoadDir", candidate_dir, "ck", "", 1
                )
                logger.debug("ObjectScript classes loaded from %s", candidate_dir)
                deployed = True
                break
            except Exception:
                continue
            try:
                result = _call_classmethod(
                    _native, "%Library.File", "FileExists", candidate_dir
                )
                if not result:
                    continue
                rs_result = _call_classmethod(
                    _native,
                    "%Library.File",
                    "FileSetExecute",
                    candidate_dir,
                    "*.cls",
                    "",
                    1,
                )
                files_loaded = 0
                for candidate_dir2 in [candidate_dir]:
                    import_result = _call_classmethod(
                        _native,
                        "%SYSTEM.OBJ",
                        "ImportDir",
                        candidate_dir,
                        "*.cls",
                        "ck",
                        "",
                        1,
                    )
                    files_loaded += 1
                if files_loaded > 0:
                    deployed = True
                    break
            except Exception:
                try:
                    _call_classmethod(
                        _native, "%SYSTEM.OBJ", "LoadDir", candidate_dir, "ck", "", 1
                    )
                    logger.debug("ObjectScript classes loaded from %s", candidate_dir)
                    deployed = True
                    break
                except Exception:
                    continue

        if not deployed:
            logger.warning(
                "Could not load .cls files from any known path. "
                "Classes may need to be pre-loaded via docker cp + $system.OBJ.LoadDir."
            )

        try:
            return GraphSchema.check_objectscript_classes(cursor, conn=conn)
        except Exception as exc:
            logger.warning("check_objectscript_classes failed: %s", exc)
            return IRISCapabilities()

    @staticmethod
    def bootstrap_kg_global(cursor, conn=None) -> bool:
        """
        Backfill the ^KG global from existing SQL edge data using BuildKG().

        Only runs once — records completion in Graph.KG.Meta so subsequent
        calls are no-ops.  Safe to call on an empty database (returns False).

        Args:
            cursor: Active IRIS dbapi cursor (for SQL queries).
            conn:   Optional connection for native API calls via createIRIS().
                    When None, the cursor itself is tried.

        Returns:
            True if BuildKG() was called, False if already done or no edges.
        """
        _native = conn if conn is not None else cursor

        # Already done?
        try:
            result = _call_classmethod(_native, "Graph.KG.Meta", "IsSet", "kg_built")
            if result:
                return False
        except Exception:
            return False

        # Any edges to backfill?
        try:
            cursor.execute("SELECT COUNT(*) FROM Graph_KG.rdf_edges")
            row = cursor.fetchone()
            if not row or not row[0]:
                return False
        except Exception:
            return False

        # Run BuildKG via native API
        try:
            _call_classmethod(_native, "Graph.KG.Traversal", "BuildKG")
        except Exception as exc:
            logger.warning("BuildKG() failed: %s", exc)
            return False

        # Record completion
        try:
            _call_classmethod(_native, "Graph.KG.Meta", "Set", "kg_built", "1")
            _call_classmethod(_native, "Graph.KG.Meta", "Set", "nkg_built", "1")
        except Exception as exc:
            logger.warning("Could not record kg_built in Graph.KG.Meta: %s", exc)

        logger.info("^KG and ^NKG globals bootstrapped from existing rdf_edges rows")
        return True
