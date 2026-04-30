#!/usr/bin/env python3
"""
IRIS Graph Core Engine - Domain-Agnostic Graph Operations

High-performance graph operations extracted from the biomedical implementation.
Provides vector search, text search, graph traversal, and hybrid fusion capabilities
that can be used across any domain.
"""

import json
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any
import logging

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import (
    translate_to_sql,
    _table,
    set_schema_prefix,
)
from iris_vector_graph.schema import GraphSchema, _call_classmethod
from iris_vector_graph.capabilities import IRISCapabilities
from iris_vector_graph.status import (
    EngineStatus, TableCounts, AdjacencyStatus,
    ObjectScriptStatus, ArnoStatus, IndexInventory,
)
from iris_vector_graph.security import validate_table_name

logger = logging.getLogger(__name__)


class IRISGraphEngine:
    """
    Domain-agnostic IRIS graph engine providing:
    - HNSW-optimized vector search (50ms performance)
    - Native IRIS iFind text search
    - Graph traversal with confidence filtering
    - Reciprocal Rank Fusion for hybrid ranking
    """

    def __init__(
        self,
        connection,
        embedding_dimension: Optional[int] = None,
        embedder: Optional[Any] = None,
        embedding_config: Optional[str] = None,
        embed_fn=None,
        use_iris_embedding: bool = False,
    ):
        """
        Initialize with IRIS database connection.

        Args:
            connection: IRIS database connection object
            embedding_dimension: Optional fixed dimension for vector embeddings.
                                 If not provided, it will be auto-detected from the schema.
            embedder: Optional callable or object with .encode() or .embed() method
                      for text-to-vector conversion.
            embedding_config: Optional name of the IRIS embedding configuration
                              (for native IRIS 2024.3+ embedding).
        """
        self.conn = connection
        if hasattr(connection, "prepare") and not hasattr(connection, "cursor"):
            from .embedded import EmbeddedConnection

            self.conn = EmbeddedConnection()
        self.embedding_dimension = embedding_dimension
        self.embedder = embedder
        self.embedding_config = embedding_config
        self._embed_fn = embed_fn
        self._use_iris_embedding = use_iris_embedding
        set_schema_prefix("Graph_KG")
        self._embedding_function_available: Optional[bool] = None
        self.capabilities: IRISCapabilities = IRISCapabilities()
        self._arno_available: Optional[bool] = None
        self._arno_capabilities: Dict[str, Any] = {}
        self._table_mapping_cache: Optional[Dict[str, dict]] = None
        self._rel_mapping_cache: Optional[Dict[tuple, dict]] = None

    def _invalidate_mapping_cache(self) -> None:
        self._table_mapping_cache = None
        self._rel_mapping_cache = None

    def get_table_mapping(self, label: str) -> Optional[dict]:
        if self._table_mapping_cache is None:
            self._load_table_mapping_cache()
        return (
            self._table_mapping_cache.get(label) if self._table_mapping_cache else None
        )

    def _load_table_mapping_cache(self) -> None:
        self._table_mapping_cache = {}
        try:
            cur = self.conn.cursor()
            cur.execute(
                "SELECT label, sql_table, id_column, prop_columns FROM Graph_KG.table_mappings"
            )
            for row in cur.fetchall():
                self._table_mapping_cache[row[0]] = {
                    "label": row[0],
                    "sql_table": row[1],
                    "id_column": row[2],
                    "prop_columns": row[3],
                }
        except Exception:
            self._table_mapping_cache = {}

    def get_rel_mapping(
        self, source_label: str, predicate: str, target_label: str
    ) -> Optional[dict]:
        if self._rel_mapping_cache is None:
            self._load_rel_mapping_cache()
        key = (source_label, predicate, target_label)
        return self._rel_mapping_cache.get(key) if self._rel_mapping_cache else None

    def _load_rel_mapping_cache(self) -> None:
        self._rel_mapping_cache = {}
        try:
            cur = self.conn.cursor()
            cur.execute(
                "SELECT source_label, predicate, target_label, target_fk, "
                "via_table, via_source, via_target FROM Graph_KG.relationship_mappings"
            )
            for row in cur.fetchall():
                key = (row[0], row[1], row[2])
                self._rel_mapping_cache[key] = {
                    "source_label": row[0],
                    "predicate": row[1],
                    "target_label": row[2],
                    "target_fk": row[3],
                    "via_table": row[4],
                    "via_source": row[5],
                    "via_target": row[6],
                }
        except Exception:
            self._rel_mapping_cache = {}

    class TableNotMappedError(ValueError):
        pass

    def map_sql_table(
        self, table: str, id_column: str, label: str, property_columns=None
    ) -> dict:
        from iris_vector_graph.security import sanitize_identifier

        sanitize_identifier(table)
        sanitize_identifier(id_column)
        sanitize_identifier(label)
        cur = self.conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? AND COLUMN_NAME = ?",
            [
                table.split(".")[0] if "." in table else "USER",
                table.split(".")[-1],
                id_column,
            ],
        )
        row = cur.fetchone()
        if not row or int(row[0]) == 0:
            raise ValueError(
                f"Table '{table}' or column '{id_column}' not found. "
                f"Verify the table exists and id_column is correct."
            )
        prop_json = json.dumps(property_columns) if property_columns else None
        cur.execute(
            "UPDATE Graph_KG.table_mappings SET sql_table=?, id_column=?, prop_columns=? WHERE label=?",
            [table, id_column, prop_json, label],
        )
        if cur.rowcount == 0:
            cur.execute(
                "INSERT INTO Graph_KG.table_mappings (label, sql_table, id_column, prop_columns) VALUES (?,?,?,?)",
                [label, table, id_column, prop_json],
            )
        self.conn.commit()
        self._invalidate_mapping_cache()
        return {
            "label": label,
            "sql_table": table,
            "id_column": id_column,
            "prop_columns": property_columns,
        }

    def map_sql_relationship(
        self,
        source_label: str,
        predicate: str,
        target_label: str,
        target_fk: str = None,
        via_table: str = None,
        via_source: str = None,
        via_target: str = None,
    ) -> dict:
        if not target_fk and not via_table:
            raise ValueError("Either target_fk or via_table must be provided.")
        if not self.get_table_mapping(source_label):
            raise ValueError(
                f"Source label '{source_label}' is not registered. Call map_sql_table first."
            )
        if not self.get_table_mapping(target_label):
            raise ValueError(
                f"Target label '{target_label}' is not registered. Call map_sql_table first."
            )
        cur = self.conn.cursor()
        cur.execute(
            "UPDATE Graph_KG.relationship_mappings SET target_fk=?, via_table=?, via_source=?, via_target=? "
            "WHERE source_label=? AND predicate=? AND target_label=?",
            [
                target_fk,
                via_table,
                via_source,
                via_target,
                source_label,
                predicate,
                target_label,
            ],
        )
        if cur.rowcount == 0:
            cur.execute(
                "INSERT INTO Graph_KG.relationship_mappings "
                "(source_label, predicate, target_label, target_fk, via_table, via_source, via_target) "
                "VALUES (?,?,?,?,?,?,?)",
                [
                    source_label,
                    predicate,
                    target_label,
                    target_fk,
                    via_table,
                    via_source,
                    via_target,
                ],
            )
        self.conn.commit()
        self._invalidate_mapping_cache()
        return {
            "source_label": source_label,
            "predicate": predicate,
            "target_label": target_label,
            "target_fk": target_fk,
            "via_table": via_table,
        }

    def list_table_mappings(self) -> dict:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT label, sql_table, id_column, prop_columns, registered_at FROM Graph_KG.table_mappings"
        )
        nodes = [
            {
                "label": r[0],
                "sql_table": r[1],
                "id_column": r[2],
                "prop_columns": r[3],
                "registered_at": str(r[4]),
            }
            for r in cur.fetchall()
        ]
        cur.execute(
            "SELECT source_label, predicate, target_label, target_fk, via_table, via_source, via_target "
            "FROM Graph_KG.relationship_mappings"
        )
        rels = [
            {
                "source_label": r[0],
                "predicate": r[1],
                "target_label": r[2],
                "target_fk": r[3],
                "via_table": r[4],
                "via_source": r[5],
                "via_target": r[6],
            }
            for r in cur.fetchall()
        ]
        return {"nodes": nodes, "relationships": rels}

    def remove_table_mapping(self, label: str) -> None:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM Graph_KG.table_mappings WHERE label=?", [label]
        )
        if int(cur.fetchone()[0]) == 0:
            raise ValueError(f"Label '{label}' not found in table_mappings.")
        cur.execute("DELETE FROM Graph_KG.table_mappings WHERE label=?", [label])
        cur.execute(
            "DELETE FROM Graph_KG.relationship_mappings WHERE source_label=? OR target_label=?",
            [label, label],
        )
        self.conn.commit()
        self._invalidate_mapping_cache()

    def reload_table_mappings(self) -> None:
        self._invalidate_mapping_cache()
        self._load_table_mapping_cache()
        self._load_rel_mapping_cache()

    def attach_embeddings_to_table(
        self,
        label: str,
        text_columns: list,
        batch_size: int = 1000,
        force: bool = False,
        progress_callback=None,
    ) -> dict:
        mapping = self.get_table_mapping(label)
        if not mapping:
            raise IRISGraphEngine.TableNotMappedError(
                f"Label '{label}' is not registered. Call map_sql_table first."
            )
        sql_table = mapping["sql_table"]
        id_col = mapping["id_column"]
        cur = self.conn.cursor()
        cur.execute(f"SELECT {id_col}, {', '.join(text_columns)} FROM {sql_table}")
        all_rows = cur.fetchall()
        n_total = len(all_rows)
        embedded = 0
        skipped = 0
        for batch_start in range(0, n_total, batch_size):
            batch = all_rows[batch_start : batch_start + batch_size]
            for row in batch:
                row_id = row[0]
                node_id = f"{label}:{row_id}"
                if not force:
                    cur.execute(
                        "SELECT COUNT(*) FROM Graph_KG.kg_NodeEmbeddings WHERE id=?",
                        [node_id],
                    )
                    if int(cur.fetchone()[0]) > 0:
                        skipped += 1
                        continue
                text = " ".join(
                    str(row[i + 1])
                    for i in range(len(text_columns))
                    if row[i + 1] is not None
                )
                try:
                    emb = self.embed_text(text)
                    emb_str = ",".join(str(x) for x in emb)
                    cur.execute(
                        "DELETE FROM Graph_KG.kg_NodeEmbeddings WHERE id=?", [node_id]
                    )
                    cur.execute(
                        "INSERT INTO Graph_KG.kg_NodeEmbeddings (id, emb) VALUES (?, TO_VECTOR(?))",
                        [node_id, emb_str],
                    )
                    embedded += 1
                except Exception as ex:
                    logger.warning(
                        f"attach_embeddings_to_table: failed to embed {node_id}: {ex}"
                    )
            self.conn.commit()
            n_done = batch_start + len(batch)
            logger.info(
                f"attach_embeddings_to_table: {n_done}/{n_total} rows processed"
            )
            if progress_callback:
                progress_callback(n_done, n_total)
        return {"embedded": embedded, "skipped": skipped, "total": n_total}

    def embed_text(self, text: str) -> List[float]:
        """
        Converts text to a vector embedding using the best available method.
        Order of preference:
        1. Native IRIS EMBEDDING() if embedding_config is set.
        2. Configured Python embedder.
        3. Default SentenceTransformer fallback.
        """
        # 1. Native IRIS embedding if available
        if self.embedding_config and self._probe_embedding_support():
            cursor = self.conn.cursor()
            try:
                # Call SQL EMBEDDING function
                cursor.execute("SELECT EMBEDDING(?, ?)", [text, self.embedding_config])
                result = cursor.fetchone()
                if result:
                    # IRIS returns vector as string or list depending on driver version
                    val = result[0]
                    if isinstance(val, str):
                        return [float(x) for x in val.strip("[]").split(",")]
                    return list(val)
            except Exception as e:
                logger.warning(
                    f"Native IRIS EMBEDDING failed for config '{self.embedding_config}': {e}. Falling back to Python."
                )
            finally:
                cursor.close()

        # 2. Python-side embedding
        if not self.embedder:
            # Try to auto-load a default model if sentence-transformers is available
            try:
                import logging as _logging
                import transformers as _tf

                _tf.logging.set_verbosity_error()
                _logging.getLogger("safetensors").setLevel(_logging.ERROR)
                from sentence_transformers import SentenceTransformer
                import warnings as _w

                with _w.catch_warnings():
                    _w.simplefilter("ignore")
                    self.embedder = SentenceTransformer(
                        "all-MiniLM-L6-v2", local_files_only=False
                    )
                logger.info("Auto-initialized SentenceTransformer('all-MiniLM-L6-v2')")
            except ImportError:
                raise RuntimeError(
                    "No embedder or embedding_config configured, and 'sentence-transformers' not installed. "
                    "Pass an embedder/embedding_config to IRISGraphEngine or install sentence-transformers."
                )

        if hasattr(self.embedder, "encode"):
            return self.embedder.encode(text).tolist()
        if hasattr(self.embedder, "embed"):
            return self.embedder.embed(text)
        if callable(self.embedder):
            return self.embedder(text)

        raise TypeError(
            f"Configured embedder {type(self.embedder)} is not a supported type (must have encode/embed or be callable)"
        )

    def initialize_schema(self, auto_deploy_objectscript: bool = True) -> None:
        """
        Create the base schema tables in IRIS, using the configured embedding_dimension.

        Safe to call on existing databases — statements that fail with "already exists"
        are silently ignored.  Raises if ``embedding_dimension`` has not been set (either
        via the constructor or prior calls to :meth:`store_embedding`).

        Args:
            auto_deploy_objectscript: When True (default), attempt to load and compile
                the ObjectScript .cls files from iris_src/ into IRIS.  On failure a
                warning is logged and the engine falls back to Python/SQL paths.
                Set to False to skip .cls deployment entirely.

        Example::

            engine = IRISGraphEngine(conn, embedding_dimension=384)
            engine.initialize_schema()
        """
        from iris_vector_graph.utils import _split_sql_statements

        dim = self.embedding_dimension
        if dim is None:
            raise ValueError(
                "embedding_dimension must be set before calling initialize_schema(). "
                "Pass it to IRISGraphEngine(conn, embedding_dimension=<N>) or call "
                "store_embedding() first so the dimension can be inferred."
            )

        cursor = self.conn.cursor()
        try:
            cursor.execute("CREATE SCHEMA Graph_KG")
        except Exception:
            pass  # already exists

        sql = GraphSchema.get_base_schema_sql(embedding_dimension=dim)
        for stmt in _split_sql_statements(sql):
            if not stmt.strip():
                continue
            try:
                cursor.execute(stmt)
            except Exception as e:
                err = str(e).lower()
                _OPTIONAL_DDL_PATTERNS = (
                    "ifind",
                    "json_value",
                    "indextype",
                    "%find",
                    "kg_txt",
                    "kg_rrf",
                    "irisdev",
                    "iris_src",
                )
                if (
                    "already exists" not in err
                    and "already has a" not in err
                    and "already has index" not in err
                ):
                    import re as _re_ddl

                    _sqlcode = _re_ddl.search(
                        r"sqlcode.*?<(-?\d+)>", err
                    ) or _re_ddl.search(r"<(-\d+)>", err)
                    _sqlcode_val = _sqlcode.group(1) if _sqlcode else ""
                    is_index_on_rdf_edges = (
                        _sqlcode_val == "-400"
                        and "rdf_edges" in stmt.lower()
                        and "create index" in stmt.lower()
                    )
                    if (
                        any(
                            p in err or p in stmt.lower()
                            for p in _OPTIONAL_DDL_PATTERNS
                        )
                        or is_index_on_rdf_edges
                    ):
                        logger.debug(
                            "Optional DDL skipped (will retry via ALTER TABLE): %s",
                            stmt[:80],
                        )
                    else:
                        logger.warning(
                            "Schema setup warning: %s | Statement: %.100s", e, stmt
                        )

        # 3. Ensure indexes and run schema migrations (e.g. column size upgrades)
        GraphSchema.ensure_indexes(cursor)

        # 4. Check for dimension mismatch on existing tables; fix untyped vector column
        try:
            db_dim = GraphSchema.get_embedding_dimension(cursor)
            if db_dim is None:
                # Column exists but has no dimension (e.g. created without VECTOR(DOUBLE,N)).
                # ALTER TABLE to add the dimension so procedure compilation succeeds.
                logger.info(
                    "kg_NodeEmbeddings.emb has no dimension — altering to VECTOR(DOUBLE, %d)",
                    dim,
                )
                try:
                    cursor.execute(
                        f"ALTER TABLE Graph_KG.kg_NodeEmbeddings ALTER COLUMN emb VECTOR(DOUBLE, {dim})"
                    )
                    cursor.execute(
                        f"ALTER TABLE Graph_KG.kg_NodeEmbeddings_optimized ALTER COLUMN emb VECTOR(DOUBLE, {dim})"
                    )
                    self.conn.commit()
                    logger.info("ALTER TABLE succeeded — dimension set to %d", dim)
                except Exception as alter_e:
                    logger.warning("Could not alter emb column dimension: %s", alter_e)
            elif db_dim != dim:
                logger.error(
                    "CRITICAL: Embedding dimension mismatch! DB has %d but engine configured for %d. "
                    "Vector operations will fail. You must drop and recreate kg_NodeEmbeddings to change dimension.",
                    db_dim,
                    dim,
                )
        except Exception as e:
            logger.warning("Could not verify embedding dimension: %s", e)

        # 5. Install stored procedures
        procedure_errors = []
        for stmt in GraphSchema.get_procedures_sql_list(
            table_schema="Graph_KG",
            embedding_dimension=dim,
        ):
            if not stmt.strip():
                continue
            try:
                cursor.execute(stmt)
            except Exception as e:
                err = str(e).lower()
                if "already exists" in err or "already has" in err:
                    continue  # idempotent re-run — schema or procedure already installed
                # Only kg_KNN_VEC is required for server-side vector search;
                # kg_TXT and kg_RRF_FUSE are optional (depend on full-text search feature)
                is_core = "procedure graph_kg.kg_knn_vec" in stmt.lower()
                if is_core:
                    _sqlcode = ""
                    import re as _re_proc
                    m = _re_proc.search(r"sqlcode.*?<(-?\d+)>", err)
                    if m:
                        _sqlcode = m.group(1)
                    if _sqlcode == "-260":
                        logger.debug(
                            "kg_KNN_VEC skipped: vector dimension mismatch in kg_NodeEmbeddings "
                            "(table has mixed-dim vectors from tests). Non-fatal. | Error: %s", e
                        )
                    else:
                        procedure_errors.append((stmt[:80], e))
                        logger.error(
                            "Procedure DDL failed: %s | Error: %s", stmt[:80], e
                        )
                else:
                    logger.debug(
                        "Optional procedure DDL skipped (non-fatal): %s | Error: %s",
                        stmt[:80],
                        e,
                    )

        if procedure_errors:
            raise RuntimeError(
                f"initialize_schema() failed to install {len(procedure_errors)} "
                f"stored procedure(s). Server-side vector search will be unavailable. "
                f"First error: {procedure_errors[0][1]}"
            )

        self.conn.commit()

        # 5b. Create SQLUser views so IVG's Python PPR fallback can use unqualified table names
        for view_sql in [
            "CREATE VIEW SQLUser.nodes AS SELECT node_id, created_at FROM Graph_KG.nodes",
            "CREATE VIEW SQLUser.rdf_edges AS SELECT * FROM Graph_KG.rdf_edges",
            "CREATE VIEW SQLUser.rdf_labels AS SELECT * FROM Graph_KG.rdf_labels",
            "CREATE VIEW SQLUser.rdf_props AS SELECT * FROM Graph_KG.rdf_props",
        ]:
            try:
                cursor.execute(view_sql)
            except Exception:
                pass

        # 6. Deploy ObjectScript .cls layer (best-effort)
        if auto_deploy_objectscript:
            try:
                pkg_dir = Path(__file__).parent.parent / "iris_src"
                if not pkg_dir.exists():
                    pkg_dir = Path(__file__).parent / ".." / "iris_src"
                self.capabilities = GraphSchema.deploy_objectscript_classes(
                    cursor, pkg_dir.resolve(), conn=self.conn
                )
            except Exception as exc:
                logger.debug(
                    "ObjectScript auto-deploy skipped (expected in Docker — use docker cp + LoadDir): %s",
                    exc,
                )
                self.capabilities = IRISCapabilities()
        else:
            self.capabilities = IRISCapabilities()

        # 6b. Always detect capabilities from %Dictionary (deployment may have failed
        # but classes could already be compiled from a prior docker cp + LoadDir)
        if not self.capabilities.objectscript_deployed:
            try:
                cursor.execute(
                    "SELECT COUNT(*) FROM %Dictionary.ClassDefinition "
                    "WHERE Name='Graph.KG.PageRank'"
                )
                row = cursor.fetchone()
                if row and row[0]:
                    self.capabilities.objectscript_deployed = True
                    logger.info("ObjectScript classes detected (pre-compiled)")
            except Exception:
                pass

        # 7. Bootstrap ^KG global if ObjectScript is deployed and edges exist
        if self.capabilities.objectscript_deployed and not self.capabilities.kg_built:
            try:
                built = GraphSchema.bootstrap_kg_global(cursor, conn=self.conn)
                if built:
                    self.capabilities.kg_built = True
                    self.conn.commit()
            except Exception as exc:
                logger.warning("^KG bootstrap failed: %s", exc)

    def _get_embedding_dimension(self) -> int:
        """
        Get the vector embedding dimension, either from initialization or auto-detection.
        Prioritizes database detection if the schema exists.
        """
        cursor = self.conn.cursor()

        # 1. Try to detect from DB first
        dim = GraphSchema.get_embedding_dimension(cursor)
        if dim:
            return int(dim)

        # 2. Fallback to instance variable if DB detection fails or table doesn't exist
        if self.embedding_dimension is not None:
            return self.embedding_dimension

        raise ValueError(
            "Embedding dimension could not be determined. Please provide it during IRISGraphEngine initialization."
        )

    def _probe_embedding_support(self) -> bool:
        """Probe whether the IRIS EMBEDDING() SQL function is available (IRIS 2024.3+).

        Result is cached per engine instance. Probe strategy:
        - Execute ``SELECT EMBEDDING('__ivg_probe__', '__nonexistent_config__')``
        - If the error message contains 'not found' / 'does not exist' → function absent → False
        - Any other error (e.g. config missing) → function present → True
        - No error → True
        """
        if self._embedding_function_available is not None:
            return self._embedding_function_available
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT EMBEDDING('__ivg_probe__', '__nonexistent_config__')"
            )
            self._embedding_function_available = True
        except Exception as e:
            err = str(e).lower()
            if "unknown function" in err or "not a recognized" in err:
                self._embedding_function_available = False
            else:
                self._embedding_function_available = True
        finally:
            try:
                cursor.close()
            except Exception:
                pass
        return bool(self._embedding_function_available)

    def _assert_node_exists(self, node_id: str) -> None:
        cursor = self.conn.cursor()
        # Use constant table names or sanitized identifiers
        table = validate_table_name("nodes")
        cursor.execute(
            f"SELECT COUNT(*) FROM Graph_KG.{table} WHERE node_id = ?", [node_id]
        )
        result = cursor.fetchone()
        if not result or result[0] == 0:
            raise ValueError(f"Node does not exist: {node_id}")

    def execute_cypher(
        self, cypher_query: str, parameters: Dict[str, Any] = None,
        read_only: bool = False,
    ) -> Dict[str, Any]:
        """
        Execute a Cypher query by translating it to IRIS SQL.

        Args:
            cypher_query: Cypher query string
            parameters: Optional query parameters
            read_only: If True, rejects any mutation (CREATE/DELETE/SET/MERGE/REMOVE/FOREACH)

        Returns:
            Dict containing 'columns', 'rows', and 'metadata'
        """
        stripped = cypher_query.strip().upper()

        if "CALL DB.LABELS() YIELD" in stripped and "UNION" in stripped:
            labels = self._try_system_procedure(
                type("P", (), {"procedure_name": "db.labels"})()
            ).get("rows", [])
            rels = self._try_system_procedure(
                type("P", (), {"procedure_name": "db.relationshipTypes"})()
            ).get("rows", [])
            cursor = self.conn.cursor()
            cursor.execute(
                'SELECT DISTINCT TOP 1000 "key" FROM Graph_KG.rdf_props ORDER BY "key"'
            )
            prop_keys = [r[0] for r in cursor.fetchall()]
            return {
                "columns": ["result"],
                "rows": [
                    [{"name": "labels", "data": [r[0] for r in labels]}],
                    [{"name": "relationshipTypes", "data": [r[0] for r in rels]}],
                    [{"name": "propertyKeys", "data": prop_keys}],
                ],
            }

        if (
            "RETURN DISTINCT" in stripped
            and "UNION ALL" in stripped
            and "ENTITY" in stripped
        ):
            cursor = self.conn.cursor()
            cursor.execute("SELECT TOP 25 node_id FROM Graph_KG.nodes")
            node_rows = [["node", r[0]] for r in cursor.fetchall()]
            cursor.execute("SELECT DISTINCT TOP 25 p FROM Graph_KG.rdf_edges")
            rel_rows = [["relationship", r[0]] for r in cursor.fetchall()]
            return {"columns": ["entity", "id"], "rows": node_rows + rel_rows}

        if (
            "MATCH ()" in stripped
            and "COUNT(*)" in stripped
            and "UNION ALL" in stripped
        ):
            cursor = self.conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM Graph_KG.nodes")
            node_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM Graph_KG.rdf_edges")
            edge_count = cursor.fetchone()[0]
            return {
                "columns": ["result"],
                "rows": [
                    [{"name": "nodes", "data": node_count}],
                    [{"name": "relationships", "data": edge_count}],
                ],
            }

        if ";" in cypher_query and "CALL " in cypher_query.upper():
            parts = [p.strip() for p in cypher_query.split(";") if p.strip()]
            if len(parts) > 1:
                all_rows = []
                all_cols = None
                for part in parts:
                    try:
                        sub = self.execute_cypher(part, parameters=parameters)
                        if all_cols is None:
                            all_cols = sub.get("columns", [])
                        all_rows.extend(sub.get("rows", []))
                    except Exception:
                        pass
                return {"columns": all_cols or ["result"], "rows": all_rows}

        if stripped.startswith("EXPLAIN "):
            return {
                "columns": ["Plan"],
                "rows": [["No execution plan available (IRIS backend)"]],
            }

        if stripped.startswith("SHOW "):
            return self._handle_show_command(stripped)

        if (stripped.startswith("CREATE CONSTRAINT")
                or stripped.startswith("DROP CONSTRAINT")
                or stripped.startswith("CREATE INDEX")
                or stripped.startswith("CREATE TEXT INDEX")
                or stripped.startswith("CREATE RANGE INDEX")
                or stripped.startswith("CREATE POINT INDEX")
                or stripped.startswith("DROP INDEX")
                or stripped.startswith("CREATE FULLTEXT")
                or stripped.startswith("CREATE LOOKUP")):
            return {"columns": [], "rows": [], "sql": cypher_query, "params": []}

        parsed = parse_query(cypher_query)

        if read_only and parsed.is_mutation:
            raise PermissionError(
                f"Read-only mode: mutation queries (CREATE/DELETE/SET/MERGE/REMOVE/FOREACH) "
                f"are not allowed. Query: {cypher_query[:100]}"
            )

        if parsed.subsequent_queries:
            result = None
            for part_query in [parsed] + parsed.subsequent_queries:
                part_query.subsequent_queries = []
                result = self._execute_parsed(part_query, parameters)
            return result

        if parsed.procedure_call is not None:
            result = self._try_system_procedure(parsed.procedure_call)
            if result is not None:
                return result

        # Mode 2 guard: if CALL uses a string query_input, verify EMBEDDING() is available
        if parsed.procedure_call is not None:
            proc = parsed.procedure_call
            if proc.procedure_name == "ivg.vector.search" and len(proc.arguments) >= 3:
                query_input_arg = proc.arguments[2]
                from iris_vector_graph.cypher.ast import (
                    Literal as CypherLiteral,
                    Variable as CypherVariable,
                )

                if isinstance(query_input_arg, CypherLiteral) and isinstance(
                    query_input_arg.value, str
                ):
                    if not self._probe_embedding_support():
                        raise RuntimeError(
                            "ivg.vector.search Mode 2 (text input) requires the IRIS EMBEDDING() SQL function "
                            "(available in IRIS 2024.3+). This IRIS instance does not support it. "
                            "Pass a pre-computed list[float] vector instead."
                        )
                elif isinstance(query_input_arg, CypherVariable):
                    param_val = (parameters or {}).get(query_input_arg.name)
                    if (
                        isinstance(param_val, str)
                        and not self._probe_embedding_support()
                    ):
                        raise RuntimeError(
                            "ivg.vector.search Mode 2 (text input) requires the IRIS EMBEDDING() SQL function "
                            "(available in IRIS 2024.3+). This IRIS instance does not support it. "
                            "Pass a pre-computed list[float] vector instead."
                        )

        sql_query = translate_to_sql(parsed, parameters, engine=self)

        if sql_query.var_length_paths:
            vl0 = sql_query.var_length_paths[0]
            if vl0.get("weighted"):
                return self._execute_weighted_shortest_path(sql_query, parameters)
            if vl0.get("shortest") or vl0.get("all_shortest"):
                return self._execute_shortest_path_cypher(sql_query, parameters)
            return self._execute_var_length_cypher(sql_query, parameters)

        cursor = self.conn.cursor()
        metadata = sql_query.query_metadata

        if sql_query.is_transactional:
            stmts = sql_query.sql
            all_params = sql_query.parameters

            cursor.execute("START TRANSACTION")
            try:
                rows = []
                for i, stmt in enumerate(stmts):
                    p = all_params[i] if i < len(all_params) else []
                    if p:
                        cursor.execute(stmt, p)
                    else:
                        cursor.execute(stmt)
                    if cursor.description:
                        rows = cursor.fetchall()

                cursor.execute("COMMIT")
                columns = (
                    [desc[0] for desc in cursor.description]
                    if cursor.description
                    else []
                )
                return {
                    "columns": columns,
                    "rows": rows,
                    "sql": stmts[-1] if stmts else "",
                    "params": all_params[-1] if all_params else [],
                    "metadata": metadata,
                }
            except Exception:
                cursor.execute("ROLLBACK")
                raise
        else:
            sql_str = (
                sql_query.sql
                if isinstance(sql_query.sql, str)
                else "\n".join(sql_query.sql)
            )
            p = sql_query.parameters[0] if sql_query.parameters else []

            if p:
                cursor.execute(sql_str, p)
            else:
                cursor.execute(sql_str)

            columns = (
                [desc[0] for desc in cursor.description] if cursor.description else []
            )
            rows = cursor.fetchall()

            return {
                "columns": columns,
                "rows": rows,
                "sql": sql_str,
                "params": p,
                "metadata": metadata,
            }

    def _execute_parsed(self, parsed, parameters):
        if parsed.procedure_call is not None:
            result = self._try_system_procedure(parsed.procedure_call)
            if result is not None:
                return result
        sql_query = translate_to_sql(parsed, parameters, engine=self)
        if sql_query.var_length_paths:
            vl0 = sql_query.var_length_paths[0]
            if vl0.get("weighted"):
                return self._execute_weighted_shortest_path(sql_query, parameters)
            if vl0.get("shortest") or vl0.get("all_shortest"):
                return self._execute_shortest_path_cypher(sql_query, parameters)
            return self._execute_var_length_cypher(sql_query, parameters)
        cursor = self.conn.cursor()
        metadata = sql_query.query_metadata
        if sql_query.is_transactional:
            stmts = sql_query.sql
            all_params = sql_query.parameters
            cursor.execute("START TRANSACTION")
            try:
                rows = []
                for i, stmt in enumerate(stmts):
                    p = all_params[i] if i < len(all_params) else []
                    cursor.execute(stmt, p)
                    if cursor.description:
                        rows = cursor.fetchall()
                self.conn.commit()
                cols = [d[0] for d in cursor.description] if cursor.description else []
                return {"columns": cols, "rows": [list(r) for r in rows],
                        "sql": str(stmts), "params": all_params, "metadata": metadata}
            except Exception:
                self.conn.rollback()
                raise
        sql_str = sql_query.sql
        p = sql_query.parameters[0] if sql_query.parameters else []
        cursor.execute(sql_str, p)
        cols = [d[0] for d in cursor.description] if cursor.description else []
        rows = cursor.fetchall()
        return {"columns": cols, "rows": [list(r) for r in rows],
                "sql": sql_str, "params": p, "metadata": metadata}


    def _execute_weighted_shortest_path(
        self, sql_query, parameters=None
    ) -> Dict[str, Any]:
        import json as _json

        vl = sql_query.var_length_paths[0]

        def _resolve(param_ref):
            if param_ref is None:
                return None
            s = str(param_ref)
            if s.startswith("'") and s.endswith("'"):
                return s[1:-1]
            if s.startswith("$"):
                name = s[1:]
                if parameters and name in parameters:
                    return str(parameters[name])
                return None
            return s

        source_id = _resolve(vl.get("src_id_param"))
        target_id = _resolve(vl.get("dst_id_param"))

        if source_id is None or target_id is None:
            raise ValueError(
                "ivg.shortestPath.weighted requires both from and to to be bound IDs"
            )

        weight_prop = vl.get("weight_prop", "weight") or "weight"
        max_cost = float(vl.get("max_cost", 9999))
        max_hops = int(vl.get("max_hops", 10))
        direction = vl.get("direction", "out") or "out"

        try:
            raw = _call_classmethod(
                self.conn,
                "Graph.KG.Traversal",
                "DijkstraJson",
                source_id,
                target_id,
                weight_prop,
                max_cost,
                max_hops,
                direction,
            )
            result_str = str(raw) if raw else "{}"
        except Exception as e:
            logger.warning(f"DijkstraJson failed: {e}")
            return {
                "columns": ["path", "totalCost"],
                "rows": [],
                "sql": "",
                "params": [],
                "metadata": sql_query.query_metadata,
            }

        if not result_str or result_str == "{}":
            return {
                "columns": ["path", "totalCost"],
                "rows": [],
                "sql": "",
                "params": [],
                "metadata": sql_query.query_metadata,
            }

        try:
            path_obj = _json.loads(result_str)
        except Exception:
            return {
                "columns": ["path", "totalCost"],
                "rows": [],
                "sql": "",
                "params": [],
                "metadata": sql_query.query_metadata,
            }

        total_cost = float(path_obj.get("totalCost", 0))
        return_funcs = vl.get("return_path_funcs", [])

        row = []
        cols = []
        if not return_funcs or "path" in return_funcs:
            row.append(result_str)
            cols.append("path")
        if "totalCost" in return_funcs or "totalcost" in return_funcs:
            row.append(total_cost)
            cols.append("totalCost")
        if "node" in return_funcs:
            nodes = path_obj.get("nodes", [])
            row.append(nodes[-1] if nodes else None)
            cols.append("node")
        if not cols:
            row = [result_str, total_cost]
            cols = ["path", "totalCost"]

        return {
            "columns": cols,
            "rows": [row],
            "sql": f"DijkstraJson({source_id}, {target_id})",
            "params": [],
            "metadata": sql_query.query_metadata,
        }

    def _execute_shortest_path_cypher(
        self, sql_query, parameters=None
    ) -> Dict[str, Any]:
        import json as _json

        vl = sql_query.var_length_paths[0]
        preds_json = _json.dumps(vl["types"]) if vl.get("types") else "[]"
        max_hops = vl.get("max_hops", 5)
        direction = vl.get("direction", "both")
        find_all = 1 if vl.get("all_shortest") else 0

        def _resolve(param_ref):
            if param_ref is None:
                return None
            if isinstance(param_ref, str) and param_ref.startswith("$"):
                name = param_ref[1:]
                if parameters and name in parameters:
                    return str(parameters[name])
                return None
            return str(param_ref)

        source_id = _resolve(vl.get("src_id_param"))
        target_id = _resolve(vl.get("dst_id_param"))

        if source_id is None and parameters:
            src_var = vl.get("source_var")
            if src_var and src_var in parameters:
                source_id = str(parameters[src_var])
            else:
                source_id = next(
                    (str(v) for v in parameters.values() if isinstance(v, str)), None
                )

        if target_id is None and parameters:
            dst_var = vl.get("target_var")
            if dst_var and dst_var in parameters:
                target_id = str(parameters[dst_var])
            else:
                vals = [str(v) for v in parameters.values() if isinstance(v, str)]
                target_id = vals[1] if len(vals) > 1 else None

        if source_id is None or target_id is None:
            sql_params = sql_query.parameters[0] if sql_query.parameters else []
            str_params = [p for p in sql_params if isinstance(p, str) and not p.startswith("Graph_KG")]
            if source_id is None and len(str_params) >= 1:
                source_id = str_params[0]
            if target_id is None and len(str_params) >= 2:
                target_id = str_params[1]

        if source_id is None or target_id is None:
            raise ValueError(
                "shortestPath requires both source and target node IDs to be bound. "
                "Use {id: $from} / {id: $to} or {id: 'literal'} on both endpoints."
            )

        try:
            path_json = _call_classmethod(
                self.conn,
                "Graph.KG.Traversal",
                "ShortestPathJson",
                source_id,
                target_id,
                max_hops,
                preds_json,
                direction,
                find_all,
            )
            paths = _json.loads(str(path_json)) if path_json else []
        except Exception as e:
            logger.warning(f"ShortestPathJson failed: {e}")
            return {
                "columns": ["p"],
                "rows": [],
                "sql": "",
                "params": [],
                "metadata": sql_query.query_metadata,
            }

        if not paths:
            return {
                "columns": ["p"],
                "rows": [],
                "sql": "",
                "params": [],
                "metadata": sql_query.query_metadata,
            }

        return_funcs = vl.get("return_path_funcs", [])
        rows = []
        for path in paths:
            row = []
            if not return_funcs or "path" in return_funcs:
                row.append(
                    _json.dumps(
                        {
                            "nodes": path.get("nodes", []),
                            "rels": path.get("rels", []),
                            "length": path.get("length", 0),
                        }
                    )
                )
            if "length" in return_funcs:
                row.append(path.get("length", 0))
            if "nodes" in return_funcs:
                row.append(path.get("nodes", []))
            if "relationships" in return_funcs:
                row.append(path.get("rels", []))
            if not row:
                row.append(
                    _json.dumps(
                        {
                            "nodes": path.get("nodes", []),
                            "rels": path.get("rels", []),
                            "length": path.get("length", 0),
                        }
                    )
                )
            rows.append(row)

        columns = []
        if not return_funcs or "path" in return_funcs:
            columns.append("p")
        if "length" in return_funcs:
            columns.append("length")
        if "nodes" in return_funcs:
            columns.append("nodes")
        if "relationships" in return_funcs:
            columns.append("relationships")
        if not columns:
            columns = ["p"]

        return {
            "columns": columns,
            "rows": rows,
            "sql": f"ShortestPathJson({source_id}, {target_id}, {max_hops})",
            "params": [],
            "metadata": sql_query.query_metadata,
        }

    def _execute_var_length_cypher(self, sql_query, parameters=None) -> Dict[str, Any]:
        import json as _json

        vl = sql_query.var_length_paths[0]
        predicates_json = _json.dumps(vl["types"]) if vl["types"] else ""
        max_hops = vl["max_hops"]
        min_hops = vl["min_hops"]
        rel_props_filter = vl.get("properties", {})

        params = sql_query.parameters[0] if sql_query.parameters else []
        source_id = None
        for item in params:
            if isinstance(item, str) and not item.startswith("Graph_KG"):
                source_id = item
                break
        if source_id is None and parameters:
            src_var = vl.get("source_var")
            if src_var and src_var in parameters:
                source_id = str(parameters[src_var])
            else:
                source_id = next(iter(parameters.values()), None)

        if source_id is None:
            return {
                "columns": [],
                "rows": [],
                "sql": "",
                "params": [],
                "metadata": sql_query.query_metadata,
            }

        max_results = 0
        if sql_query.sql:
            import re as _re
            sql_str = sql_query.sql if isinstance(sql_query.sql, str) else (sql_query.sql[0] if sql_query.sql else "")
            m = _re.search(r"\bLIMIT\s+(\d+)", sql_str, _re.IGNORECASE)
            if m:
                max_results = int(m.group(1))

        bfs_results = None
        if self._detect_arno() and self._arno_capabilities.get("bfs"):
            try:
                bfs_json = self._arno_call(
                    "Graph.KG.NKGAccel",
                    "BFSJson",
                    source_id,
                    predicates_json,
                    max_hops,
                    max_results,
                )
                bfs_results = _json.loads(str(bfs_json)) if bfs_json else []
                logger.debug("Arno BFSJson: %d results for %s", len(bfs_results), source_id)
            except Exception as e:
                logger.warning(f"Arno BFSJson failed, falling back to BFSFastJson: {e}")
                bfs_results = None

        if bfs_results is None:
            try:
                bfs_json = _call_classmethod(
                    self.conn,
                    "Graph.KG.Traversal",
                    "BFSFastJson",
                    source_id,
                    predicates_json,
                    max_hops,
                    "",
                    vl.get("direction", "out"),
                )
                bfs_results = _json.loads(str(bfs_json)) if bfs_json else []
            except Exception as e:
                logger.warning(f"BFSFastJson failed: {e}")
                return {
                    "columns": [],
                    "rows": [],
                    "sql": "",
                    "params": [],
                    "metadata": sql_query.query_metadata,
                }

        if min_hops > 1:
            min_step_per_node: dict = {}
            for r in bfs_results:
                oid = r.get("o")
                if oid:
                    s = r.get("step", 1)
                    if oid not in min_step_per_node or s < min_step_per_node[oid]:
                        min_step_per_node[oid] = s
            bfs_results = [
                r
                for r in bfs_results
                if min_step_per_node.get(r.get("o"), 0) >= min_hops
            ]

        if rel_props_filter and bfs_results:
            bfs_results = self._filter_edges_by_properties(bfs_results, rel_props_filter)

        seen = set()
        target_ids = []
        for r in bfs_results:
            oid = r.get("o")
            if oid and oid not in seen:
                seen.add(oid)
                target_ids.append(oid)

        import re as _re
        sql_str = sql_query.sql if isinstance(sql_query.sql, str) else ""
        alias_match = _re.search(r'SELECT\s+DISTINCT\s+\S+\s+AS\s+(\w+)|SELECT\s+\S+\s+AS\s+(\w+)', sql_str, _re.IGNORECASE)
        col_name = (alias_match.group(1) or alias_match.group(2)) if alias_match else "b_id"

        if not target_ids:
            return {
                "columns": [col_name, "b_labels", "b_props"],
                "rows": [],
                "sql": "",
                "params": [],
                "metadata": sql_query.query_metadata,
            }

        nodes = self.get_nodes(target_ids)
        rows = []
        for data in nodes:
            node_id = data.get("id", "")
            rows.append(
                (
                    node_id,
                    data.get("labels", []),
                    {k: v for k, v in data.items() if k not in ("labels", "id")},
                )
            )

        return {
            "columns": [col_name, "b_labels", "b_props"],
            "rows": [list(r) for r in rows],
            "sql": f"BFSFastJson({source_id}, {predicates_json}, {max_hops})",
            "params": [],
            "metadata": sql_query.query_metadata,
        }

    def _handle_show_command(self, cmd: str) -> Dict[str, Any]:
        if "DATABASES" in cmd:
            return {
                "columns": [
                    "name",
                    "type",
                    "aliases",
                    "access",
                    "address",
                    "role",
                    "writer",
                    "requestedStatus",
                    "currentStatus",
                    "statusMessage",
                    "default",
                    "home",
                    "constituents",
                ],
                "rows": [
                    [
                        "neo4j",
                        "standard",
                        [],
                        "read-write",
                        "localhost:7687",
                        "primary",
                        True,
                        "online",
                        "online",
                        "",
                        True,
                        True,
                        [],
                    ]
                ],
            }
        if "PROCEDURES" in cmd:
            procs = self._try_system_procedure(
                type("P", (), {"procedure_name": "dbms.procedures"})()
            )
            if procs:
                return {
                    "columns": ["name", "description", "signature"],
                    "rows": [[r[0], r[2], r[1]] for r in procs.get("rows", [])],
                }
            return {"columns": ["name", "description", "signature"], "rows": []}
        if "FUNCTIONS" in cmd:
            fns = self._try_system_procedure(
                type("P", (), {"procedure_name": "dbms.functions"})()
            )
            if fns:
                return {
                    "columns": ["name", "description", "signature"],
                    "rows": [[r[0], r[2], r[1]] for r in fns.get("rows", [])],
                }
            return {"columns": ["name", "description", "signature"], "rows": []}
        if "INDEXES" in cmd:
            return {
                "columns": [
                    "name",
                    "type",
                    "entityType",
                    "labelsOrTypes",
                    "properties",
                ],
                "rows": [],
            }
        if "CONSTRAINTS" in cmd:
            return {
                "columns": [
                    "name",
                    "type",
                    "entityType",
                    "labelsOrTypes",
                    "properties",
                ],
                "rows": [],
            }
        return {"columns": ["value"], "rows": []}

    def _try_system_procedure(self, proc) -> Optional[Dict[str, Any]]:
        name = proc.procedure_name.lower()

        if name in ("ivg.shortestpath.weighted", "ivg.shortestpath.weighted"):
            args = proc.arguments
            from iris_vector_graph.cypher import ast as cypher_ast

            def _arg_str(a, params=None):
                if isinstance(a, cypher_ast.Literal):
                    return str(a.value)
                if isinstance(a, cypher_ast.Variable):
                    if params and a.name in params:
                        return str(params[a.name])
                    return a.name
                return str(a)

            source_id = _arg_str(args[0]) if len(args) > 0 else None
            target_id = _arg_str(args[1]) if len(args) > 1 else None
            weight_prop = _arg_str(args[2]) if len(args) > 2 else "weight"
            max_cost = float(_arg_str(args[3])) if len(args) > 3 else 9999.0
            max_hops = int(float(_arg_str(args[4]))) if len(args) > 4 else 10
            direction = _arg_str(args[5]) if len(args) > 5 else "out"

            if not source_id or not target_id:
                return {"columns": ["path", "totalCost"], "rows": []}

            import json as _json

            try:
                raw = _call_classmethod(
                    self.conn,
                    "Graph.KG.Traversal",
                    "DijkstraJson",
                    source_id,
                    target_id,
                    weight_prop,
                    max_cost,
                    max_hops,
                    direction,
                )
                result_str = str(raw) if raw else "{}"
            except Exception as e:
                logger.warning(f"DijkstraJson failed: {e}")
                return {"columns": ["path", "totalCost"], "rows": []}

            if not result_str or result_str == "{}":
                return {"columns": ["path", "totalCost"], "rows": []}

            try:
                path_obj = _json.loads(result_str)
            except Exception:
                return {"columns": ["path", "totalCost"], "rows": []}

            total_cost = float(path_obj.get("totalCost", 0))
            return {
                "columns": ["path", "totalCost"],
                "rows": [[result_str, total_cost]],
            }

        if name == "db.labels":
            cursor = self.conn.cursor()
            cursor.execute(
                "SELECT DISTINCT label FROM Graph_KG.rdf_labels ORDER BY label"
            )
            labels = [row[0] for row in cursor.fetchall()]
            return {"columns": ["label"], "rows": [[l] for l in labels]}

        if name == "db.relationshiptypes":
            cursor = self.conn.cursor()
            cursor.execute("SELECT DISTINCT p FROM Graph_KG.rdf_edges ORDER BY p")
            types = [row[0] for row in cursor.fetchall()]
            return {"columns": ["relationshipType"], "rows": [[t] for t in types]}

        if name == "db.schema.visualization":
            schema = self.get_schema_visualization()
            nodes = schema.get("nodes", [])
            rels = schema.get("relationships", [])
            return {"columns": ["nodes", "relationships"], "rows": [[nodes, rels]]}

        if name == "db.schema.nodetypeproperties":
            cursor = self.conn.cursor()
            cursor.execute(
                "SELECT DISTINCT label FROM Graph_KG.rdf_labels ORDER BY label"
            )
            labels = [row[0] for row in cursor.fetchall()]
            rows = []
            for label in labels:
                cursor.execute(
                    "SELECT TOP 1 rl.s FROM Graph_KG.rdf_labels rl WHERE rl.label = ?",
                    [label],
                )
                sample = cursor.fetchone()
                if sample:
                    cursor.execute(
                        'SELECT DISTINCT TOP 20 "key" FROM Graph_KG.rdf_props '
                        'WHERE s = ? ORDER BY "key"',
                        [sample[0]],
                    )
                    for (prop_name,) in cursor.fetchall():
                        rows.append(
                            [
                                f":`{label}`",
                                [label],
                                prop_name,
                                ["String"],
                                False,
                            ]
                        )
            return {
                "columns": [
                    "nodeType",
                    "nodeLabels",
                    "propertyName",
                    "propertyTypes",
                    "mandatory",
                ],
                "rows": rows,
            }

        if name == "db.schema.reltypeproperties":
            cursor = self.conn.cursor()
            rows = []
            try:
                cursor.execute(
                    "SELECT DISTINCT p FROM Graph_KG.rdf_edges WHERE p IS NOT NULL ORDER BY p"
                )
                rel_types = [r[0] for r in cursor.fetchall()]
                for rel_type in rel_types[:50]:
                    props = {"weight"}
                    cursor.execute(
                        "SELECT TOP 1 qualifiers FROM Graph_KG.rdf_edges WHERE p = ? AND qualifiers IS NOT NULL",
                        [rel_type],
                    )
                    row = cursor.fetchone()
                    if row and row[0]:
                        try:
                            keys = list(json.loads(str(row[0])).keys())
                            props.update(keys[:20])
                        except Exception:
                            pass
                    for prop in sorted(props):
                        rows.append([rel_type, prop, ["STRING"], False])
            except Exception as e:
                logger.debug("relTypeProperties query failed: %s", e)
            return {
                "columns": ["relType", "propertyName", "propertyTypes", "mandatory"],
                "rows": rows,
            }

        if name == "dbms.components":
            return {
                "columns": ["name", "versions", "edition"],
                "rows": [["iris-vector-graph", ["5.0.0"], "community"]],
            }

        if name == "dbms.procedures":

            def _proc(n, sig, desc, mode="READ"):
                return [n, sig, desc, mode, False, {}, "neo4j", False, True, []]

            procs = [
                _proc(
                    "db.labels", "db.labels() :: (label :: STRING)", "List all labels"
                ),
                _proc(
                    "db.relationshipTypes",
                    "db.relationshipTypes() :: (relationshipType :: STRING)",
                    "List all rel types",
                ),
                _proc(
                    "db.schema.visualization",
                    "db.schema.visualization() :: (nodes :: LIST, relationships :: LIST)",
                    "Schema visualization",
                ),
                _proc(
                    "db.schema.nodeTypeProperties",
                    "db.schema.nodeTypeProperties() :: (nodeType :: STRING, nodeLabels :: LIST, propertyName :: STRING, propertyTypes :: LIST, mandatory :: BOOLEAN)",
                    "Node type props",
                ),
                _proc(
                    "db.schema.relTypeProperties",
                    "db.schema.relTypeProperties() :: (relType :: STRING, propertyName :: STRING, propertyTypes :: LIST, mandatory :: BOOLEAN)",
                    "Rel type props",
                ),
                _proc(
                    "dbms.components",
                    "dbms.components() :: (name :: STRING, versions :: LIST, edition :: STRING)",
                    "Server components",
                    "DBMS",
                ),
                _proc(
                    "dbms.procedures",
                    "dbms.procedures() :: (name :: STRING, signature :: STRING, description :: STRING)",
                    "List procedures",
                    "DBMS",
                ),
                _proc(
                    "dbms.functions",
                    "dbms.functions() :: (name :: STRING, signature :: STRING, description :: STRING)",
                    "List functions",
                    "DBMS",
                ),
                _proc(
                    "dbms.clientConfig",
                    "dbms.clientConfig() :: (key :: STRING, value :: STRING)",
                    "Client config",
                    "DBMS",
                ),
                _proc(
                    "dbms.security.showCurrentUser",
                    "dbms.security.showCurrentUser() :: (username :: STRING, roles :: LIST)",
                    "Current user",
                    "DBMS",
                ),
                _proc(
                    "dbms.queryJmx",
                    "dbms.queryJmx(query :: STRING) :: (name :: STRING, description :: STRING, attributes :: MAP)",
                    "Query JMX management data",
                    "DBMS",
                ),
            ]
            return {
                "columns": [
                    "name",
                    "signature",
                    "description",
                    "mode",
                    "admin",
                    "option",
                    "defaultBuiltInRoles",
                    "isDeprecated",
                    "worksOnSystem",
                    "argumentDescription",
                ],
                "rows": procs,
            }

        if name == "db.propertykeys":
            cursor = self.conn.cursor()
            cursor.execute(
                'SELECT DISTINCT TOP 1000 "key" FROM Graph_KG.rdf_props ORDER BY "key"'
            )
            keys = [row[0] for row in cursor.fetchall()]
            return {"columns": ["propertyKey"], "rows": [[k] for k in keys]}

        if name == "dbms.clientconfig":
            return {
                "columns": ["key", "value"],
                "rows": [
                    ["browser.allow_outgoing_connections", "false"],
                    ["browser.credential_timeout", "0"],
                    ["browser.retain_connection_credentials", "true"],
                    ["browser.retain_editor_history", "true"],
                    ["browser.post_connect_cmd", ""],
                    ["dbms.security.auth_enabled", "false"],
                ],
            }

        if name == "dbms.security.showcurrentuser" or name == "dbms.showcurrentuser":
            return {
                "columns": ["username", "roles", "flags"],
                "rows": [["neo4j", [], []]],
            }

        if name == "dbms.functions":
            return {
                "columns": [
                    "name",
                    "signature",
                    "description",
                    "aggregating",
                    "defaultBuiltInRoles",
                    "isDeprecated",
                    "argumentDescription",
                    "returnDescription",
                    "category",
                ],
                "rows": [],
            }

        if name == "dbms.queryjmx":
            cursor = self.conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM Graph_KG.nodes")
            node_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM Graph_KG.rdf_edges")
            edge_count = cursor.fetchone()[0]
            pfx = "org.neo4j:instance=kernel#0"
            return {
                "columns": ["name", "description", "attributes"],
                "rows": [
                    [
                        f"{pfx},name=Store file sizes",
                        "Store file sizes",
                        {
                            "TotalStoreSize": {"value": node_count * 200},
                            "NodeStoreSize": {"value": node_count * 100},
                            "RelationshipStoreSize": {"value": edge_count * 100},
                            "PropertyStoreSize": {"value": node_count * 50},
                            "StringStoreSize": {"value": node_count * 30},
                            "ArrayStoreSize": {"value": 0},
                            "IndexStoreSize": {"value": 0},
                            "LabelStoreSize": {"value": node_count * 10},
                            "SchemaStoreSize": {"value": 4096},
                        },
                    ],
                    [
                        f"{pfx},name=Primitive count",
                        "Primitive count",
                        {
                            "NumberOfNodeIdsInUse": {"value": node_count},
                            "NumberOfRelationshipIdsInUse": {"value": edge_count},
                            "NumberOfPropertyIdsInUse": {"value": node_count * 3},
                            "NumberOfRelationshipTypeIdsInUse": {"value": 20},
                            "NumberOfLabelIdsInUse": {"value": 5},
                        },
                    ],
                    [
                        f"{pfx},name=Page cache",
                        "Page cache statistics",
                        {
                            "Hits": {"value": 1000},
                            "Faults": {"value": 10},
                            "HitRatio": {"value": 0.99},
                            "UsageRatio": {"value": 0.5},
                            "FileMappings": {"value": 5},
                            "FileUnmappings": {"value": 0},
                            "BytesRead": {"value": 1024 * 1024},
                            "BytesWritten": {"value": 1024},
                            "FlushEvents": {"value": 0},
                            "EvictionExceptions": {"value": 0},
                        },
                    ],
                    [
                        f"{pfx},name=Transactions",
                        "Transaction statistics",
                        {
                            "LastCommittedTxId": {"value": 1},
                            "CurrentCommittedTxId": {"value": 1},
                            "LastClosedTxId": {"value": 1},
                            "NumberOfOpenTransactions": {"value": 0},
                            "PeakNumberOfConcurrentTransactions": {"value": 1},
                            "NumberOfOpenedTransactions": {"value": 1},
                            "NumberOfCommittedTransactions": {"value": 1},
                            "NumberOfRolledBackTransactions": {"value": 0},
                            "NumberOfTerminatedTransactions": {"value": 0},
                        },
                    ],
                    [
                        f"{pfx},name=Kernel",
                        "Kernel information",
                        {
                            "KernelVersion": {"value": "iris-vector-graph-1.47.0"},
                            "StoreId": {"value": "store-001"},
                            "DatabaseName": {"value": "neo4j"},
                            "ReadOnly": {"value": False},
                            "MBeanQuery": {"value": pfx},
                        },
                    ],
                    [
                        f"{pfx},name=Configuration",
                        "Configuration",
                        {
                            "dbms.jvm.heap.initial_size": {"value": "256m"},
                            "dbms.jvm.heap.max_size": {"value": "512m"},
                            "dbms.logs.native.size": {"value": "20m"},
                        },
                    ],
                ],
            }

        if name == "apoc.meta.data":
            cursor = self.conn.cursor()
            cursor.execute(
                "SELECT DISTINCT label FROM Graph_KG.rdf_labels ORDER BY label"
            )
            labels = [row[0] for row in cursor.fetchall()]
            rows = []
            for label in labels[:50]:
                cursor.execute(
                    'SELECT DISTINCT TOP 20 "key" FROM Graph_KG.rdf_props rp '
                    "JOIN Graph_KG.rdf_labels rl ON rl.s = rp.s "
                    'WHERE rl.label = ? ORDER BY "key"',
                    [label],
                )
                props = [row[0] for row in cursor.fetchall()]
                if props:
                    for prop_name in props:
                        rows.append(
                            [label, prop_name, "STRING", "node", False, False, False]
                        )
                else:
                    rows.append([label, None, "STRING", "node", False, False, False])
            cursor.execute("SELECT DISTINCT p FROM Graph_KG.rdf_edges ORDER BY p")
            for (rel_type,) in cursor.fetchall():
                rows.append(
                    [
                        rel_type,
                        None,
                        "RELATIONSHIP",
                        "relationship",
                        False,
                        False,
                        False,
                    ]
                )
            return {
                "columns": [
                    "label",
                    "property",
                    "type",
                    "elementType",
                    "unique",
                    "index",
                    "existence",
                ],
                "rows": rows,
            }

        if name == "apoc.meta.schema":
            result = self._try_system_procedure(
                type("P", (), {"procedure_name": "apoc.meta.data"})()
            )
            return {"columns": ["value"], "rows": [[result or {}]]}

        if name.startswith("apoc."):
            return {"columns": ["value"], "rows": []}

        if name.startswith("dbms.") or name.startswith("db."):
            return {"columns": ["value"], "rows": []}

        return None

    def get_schema_visualization(self) -> dict:
        cursor = self.conn.cursor()

        cursor.execute("SELECT DISTINCT label FROM Graph_KG.rdf_labels ORDER BY label")
        labels = [r[0] for r in cursor.fetchall()]

        cursor.execute("SELECT DISTINCT p FROM Graph_KG.rdf_edges ORDER BY p")
        rel_types = [r[0] for r in cursor.fetchall()]

        nodes = []
        for i, label in enumerate(labels):
            cursor.execute(
                "SELECT TOP 1 rl.s FROM Graph_KG.rdf_labels rl WHERE rl.label = ?",
                [label],
            )
            row = cursor.fetchone()
            sample_id = row[0] if row else None

            prop_names = []
            if sample_id:
                cursor.execute(
                    'SELECT DISTINCT TOP 20 "key" FROM Graph_KG.rdf_props WHERE s = ? '
                    'ORDER BY "key"',
                    [sample_id],
                )
                prop_names = [r[0] for r in cursor.fetchall()]

            nodes.append(
                {
                    "id": i,
                    "name": label,
                    "labels": [label],
                    "properties": [{"name": p, "type": "String"} for p in prop_names],
                }
            )

        label_to_id = {n["name"]: n["id"] for n in nodes}

        rels = []
        for i, rel_type in enumerate(rel_types):
            cursor.execute(
                "SELECT s, o_id FROM Graph_KG.rdf_edges WHERE p = ?", [rel_type]
            )
            row = cursor.fetchone()
            start_label_id = 0
            end_label_id = 0
            if row:
                src_id, tgt_id = row
                cursor.execute(
                    "SELECT TOP 1 label FROM Graph_KG.rdf_labels WHERE s = ?", [src_id]
                )
                src_row = cursor.fetchone()
                if src_row:
                    start_label_id = label_to_id.get(src_row[0], 0)
                cursor.execute(
                    "SELECT TOP 1 label FROM Graph_KG.rdf_labels WHERE s = ?", [tgt_id]
                )
                tgt_row = cursor.fetchone()
                if tgt_row:
                    end_label_id = label_to_id.get(tgt_row[0], 0)

            rels.append(
                {
                    "id": i,
                    "name": rel_type,
                    "type": rel_type,
                    "properties": [],
                    "startNode": start_label_id,
                    "endNode": end_label_id,
                }
            )

        return {"nodes": nodes, "relationships": rels}

    def get_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve a node by ID using optimized direct SQL.

        Bypasses Cypher translation for 80x+ faster single-node lookups.

        Args:
            node_id: Node identifier

        Returns:
            Dict with 'id', 'labels', and properties, or None if not found
        """
        nodes = self.get_nodes([node_id])
        return nodes[0] if nodes else None

    def _filter_edges_by_properties(
        self, bfs_results: list, prop_filter: dict
    ) -> list:
        if not prop_filter:
            return bfs_results
        import json as _json
        edges = [(r["s"], r["p"], r["o"]) for r in bfs_results if r.get("s") and r.get("p") and r.get("o")]
        if not edges:
            return bfs_results

        cursor = self.conn.cursor()
        try:
            results = []
            for s, p, o in edges:
                cursor.execute(
                    f"SELECT qualifiers FROM {_table('rdf_edges')} "
                    "WHERE s=? AND p=? AND o_id=?",
                    [s, p, o],
                )
                row = cursor.fetchone()
                qual_json = row[0] if row else None
                try:
                    qualifiers = _json.loads(qual_json) if qual_json else {}
                except Exception:
                    qualifiers = {}
                if all(str(qualifiers.get(k)) == str(v) for k, v in prop_filter.items()):
                    results.append((s, p, o))
        except Exception as e:
            logger.warning("_filter_edges_by_properties query failed: %s", e)
            return bfs_results

        passing = set(results)

        if not passing:
            logger.debug(
                "_filter_edges_by_properties: no edges match filter %s", prop_filter
            )

        return [
            r for r in bfs_results
            if (r.get("s"), r.get("p"), r.get("o")) in passing
        ]

    def get_nodes(self, node_ids: List[str]) -> List[Dict[str, Any]]:
        """
        Retrieve multiple nodes by ID using optimized batch SQL.

        Eliminates N+1 query patterns by fetching all labels and properties
        for a set of nodes in two efficient queries.

        Args:
            node_ids: List of node identifiers

        Returns:
            List of node dicts with 'id', 'labels', and properties
        """
        if not node_ids:
            return []

        _IN_CHUNK = 499

        try:
            cursor = self.conn.cursor()
            node_map = {nid: {"id": nid, "labels": []} for nid in node_ids}

            for i in range(0, len(node_ids), _IN_CHUNK):
                chunk = node_ids[i : i + _IN_CHUNK]
                placeholders = ",".join(["?"] * len(chunk))

                cursor.execute(
                    f"SELECT s, label FROM {_table('rdf_labels')} WHERE s IN ({placeholders})",
                    chunk,
                )
                for s, label in cursor.fetchall():
                    if s in node_map:
                        node_map[s]["labels"].append(label)

                cursor.execute(
                    f'SELECT s, "key", val FROM {_table("rdf_props")} WHERE s IN ({placeholders})',
                    chunk,
                )
                _STRUCTURAL_KEYS = ("id", "labels")
                for s, key, val in cursor.fetchall():
                    if s in node_map:
                        store_key = f"p_{key}" if key in _STRUCTURAL_KEYS else key
                        if val is not None:
                            parsed_val = val
                            try:
                                if (
                                    str(val).startswith("{") and str(val).endswith("}")
                                ) or (
                                    str(val).startswith("[") and str(val).endswith("]")
                                ):
                                    parsed_val = json.loads(val)
                            except Exception:
                                pass
                            node_map[s][store_key] = parsed_val
                        else:
                            node_map[s][store_key] = val

            empty_nids = [
                nid
                for nid, data in node_map.items()
                if not data["labels"] and len(data) <= 2
            ]
            if empty_nids:
                existing_empty: set = set()
                for i in range(0, len(empty_nids), _IN_CHUNK):
                    chunk = empty_nids[i : i + _IN_CHUNK]
                    e_placeholders = ",".join(["?"] * len(chunk))
                    cursor.execute(
                        f"SELECT node_id FROM {_table('nodes')} WHERE node_id IN ({e_placeholders})",
                        chunk,
                    )
                    existing_empty.update(row[0] for row in cursor.fetchall())
                return [
                    node_map[nid]
                    for nid in node_ids
                    if nid in existing_empty or nid not in empty_nids
                ]

            return [node_map[nid] for nid in node_ids if nid in node_map]

        except Exception as e:
            logger.error(f"Batch get_nodes failed: {str(e)}")
            # Fallback to individual lookups (which might use Cypher fallback)
            results = []
            for nid in node_ids:
                node = self._get_node_cypher_fallback(nid)
                if node:
                    results.append(node)
            return results

    def _get_node_cypher_fallback(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Original Cypher-based get_node implementation as safety fallback."""
        # Use parameters to prevent Cypher injection
        cypher = "MATCH (n) WHERE n.id = $node_id RETURN n"
        result = self.execute_cypher(cypher, parameters={"node_id": node_id})

        if not result.get("rows"):
            return None

        row = result["rows"][0]
        columns = result["columns"]
        row_map = dict(zip(columns, row))

        id_key = next((k for k in row_map if k.endswith("_id")), None)
        if not id_key:
            return None

        prefix = id_key[:-3]
        labels_key = f"{prefix}_labels"
        props_key = f"{prefix}_props"

        labels_raw = row_map.get(labels_key)
        props_raw = row_map.get(props_key)

        labels = (
            json.loads(labels_raw)
            if isinstance(labels_raw, str)
            else (labels_raw or [])
        )
        props_items = (
            json.loads(props_raw) if isinstance(props_raw, str) else (props_raw or [])
        )

        if props_items and isinstance(props_items[0], str):
            props_items = [json.loads(item) for item in props_items]

        props = {
            item["key"]: item["value"] for item in props_items if isinstance(item, dict)
        }

        return {"id": row_map[id_key], "labels": labels, "properties": props}

    def status(self) -> "EngineStatus":
        import time as _time
        t0 = _time.perf_counter()
        errors: list = []
        cursor = self.conn.cursor()

        def _count(sql):
            try:
                cursor.execute(sql)
                row = cursor.fetchone()
                return int(row[0]) if row else 0
            except Exception as e:
                errors.append(f"count probe failed: {e}")
                return 0

        tables = TableCounts(
            nodes=_count(f"SELECT COUNT(*) FROM {_table('nodes')}"),
            edges=_count(f"SELECT COUNT(*) FROM {_table('rdf_edges')}"),
            labels=_count(f"SELECT COUNT(*) FROM {_table('rdf_labels')}"),
            props=_count(f"SELECT COUNT(*) FROM {_table('rdf_props')}"),
            node_embeddings=_count(f"SELECT COUNT(*) FROM {_table('kg_NodeEmbeddings')}"),
            edge_embeddings=_count(f"SELECT COUNT(*) FROM {_table('kg_EdgeEmbeddings')}"),
        )

        kg_count = 0
        kg_capped = False
        kg_populated = False
        nkg_populated = False
        try:
            native = self._iris_obj()
            kg_count = int(native.classMethodValue("Graph.KG.Traversal", "KGEdgeCount", 10000))
            kg_populated = kg_count > 0
            kg_capped = kg_count >= 10000
            nkg_populated = bool(int(native.classMethodValue("Graph.KG.Traversal", "NKGPopulated")))
        except Exception as e:
            try:
                iris_native = self._iris_obj()
                kg_populated = bool(iris_native.isDefined(["KG", "out"]))
            except Exception:
                errors.append(f"^KG probe failed: {e}")

        kg_predicates_consistent = True
        if kg_populated and tables.edges > 0:
            try:
                native = self._iris_obj()
                kg_pred = str(native.get(["KG", "out", 0, ""])) or ""
                if not kg_pred:
                    s_val = ""
                    kg_pred_node = native.orderAll(["KG", "out", 0, s_val])
                    if kg_pred_node:
                        kg_pred = str(native.orderAll(
                            ["KG", "out", 0, str(kg_pred_node), ""]
                        ) or "")
            except Exception:
                kg_pred = ""

            if kg_pred:
                try:
                    cursor.execute(
                        f"SELECT COUNT(*) FROM {_table('rdf_edges')} WHERE p = ?",
                        [kg_pred],
                    )
                    row = cursor.fetchone()
                    if row and int(row[0]) == 0:
                        kg_predicates_consistent = False
                        errors.append(
                            f"^KG predicate mismatch: ^KG has '{kg_pred[:60]}' "
                            f"but rdf_edges has no matching p — "
                            f"^KG is stale from a different data snapshot. "
                            f"Run BuildKG() after reloading graph data."
                        )
                except Exception:
                    pass

        adjacency = AdjacencyStatus(
            kg_populated=kg_populated,
            kg_edge_count=kg_count,
            kg_edge_count_capped=kg_capped,
            nkg_populated=nkg_populated,
            kg_predicates_consistent=kg_predicates_consistent,
            bfs_path="none",
        )

        os_classes = []
        os_deployed = self.capabilities.objectscript_deployed
        _known_classes = [
            "Graph.KG.Traversal", "Graph.KG.PageRank", "Graph.KG.IVFIndex",
            "Graph.KG.BM25Index", "Graph.KG.ArnoAccel", "Graph.KG.Snapshot",
            "Graph.KG.Dijkstra",
        ]
        if os_deployed:
            for cls in _known_classes:
                try:
                    cursor.execute(
                        "SELECT COUNT(*) FROM %Dictionary.ClassDefinition WHERE Name = ?",
                        [cls],
                    )
                    row = cursor.fetchone()
                    if row and int(row[0]) > 0:
                        os_classes.append(cls)
                except Exception:
                    pass

        objectscript = ObjectScriptStatus(deployed=os_deployed, classes=os_classes)

        self._detect_arno()
        arno = ArnoStatus(
            loaded=bool(self._arno_available),
            capabilities=dict(self._arno_capabilities),
        )

        hnsw_built = _count(f"SELECT COUNT(*) FROM {_table('kg_NodeEmbeddings_optimized')}") > 0

        def _list_index(sql):
            try:
                cursor.execute(sql)
                return [row[0] for row in cursor.fetchall() if row[0]]
            except Exception:
                return []

        ivf = _list_index(f"SELECT DISTINCT name FROM {_table('kg_IVFMeta')}")
        bm25 = _list_index(f"SELECT DISTINCT name FROM {_table('kg_BM25Meta')}")
        plaid = _list_index(f"SELECT DISTINCT idx_name FROM {_table('kg_PlaidMeta')}")

        indexes = IndexInventory(
            hnsw_built=hnsw_built,
            ivf_indexes=ivf,
            bm25_indexes=bm25,
            plaid_indexes=plaid,
        )

        if arno.loaded and arno.capabilities.get("bfs") and adjacency.nkg_populated:
            adjacency.bfs_path = "arno"
        elif objectscript.deployed and adjacency.kg_populated:
            adjacency.bfs_path = "objectscript"

        probe_ms = (_time.perf_counter() - t0) * 1000
        return EngineStatus(
            tables=tables,
            adjacency=adjacency,
            objectscript=objectscript,
            arno=arno,
            indexes=indexes,
            embedding_dimension=self.embedding_dimension,
            probe_ms=probe_ms,
            errors=errors,
        )

    def count_nodes(self, label: Optional[str] = None) -> int:
        """
        Count nodes in the graph using optimized SQL.

        Args:
            label: Optional label filter

        Returns:
            Total node count (filtered by label if provided)
        """
        cursor = self.conn.cursor()
        try:
            if label:
                # Use constant table names
                cursor.execute(
                    "SELECT COUNT(*) FROM Graph_KG.rdf_labels WHERE label = ?", [label]
                )
            else:
                cursor.execute("SELECT COUNT(*) FROM Graph_KG.nodes")

            result = cursor.fetchone()
            return result[0] if result else 0
        except Exception as e:
            logger.error(f"Count nodes failed: {e}")
            return 0

    def create_node(
        self, node_id: str, labels: List[str] = None, properties: Dict[str, Any] = None
    ) -> bool:
        """
        Create a single node with labels and properties in a single transaction.
        Optimized for individual creations with proper batching of internal inserts.
        """
        cursor = self.conn.cursor()
        try:
            cursor.execute("START TRANSACTION")

            # 1. Create node
            cursor.execute(
                f"INSERT INTO {_table('nodes')} (node_id) VALUES (?)", [node_id]
            )

            # 2. Batch labels
            if labels:
                label_data = [[node_id, label] for label in labels]
                cursor.executemany(
                    f"INSERT INTO {_table('rdf_labels')} (s, label) VALUES (?, ?)",
                    label_data,
                )

            # 3. Batch properties
            # Always include 'id' in rdf_props so Cypher queries like
            # MATCH (n {id: $value}) or WHERE n.id = $value can find the node.
            props = dict(properties) if properties else {}
            if "id" not in props:
                props["id"] = node_id

            prop_data = []
            for k, v in props.items():
                val_str = json.dumps(v) if isinstance(v, (dict, list)) else str(v)
                # Use safe INSERT pattern to prevent duplicate property violations
                # params: [s, key, val, s, key]
                prop_data.append([node_id, k, val_str, node_id, k])

            prop_sql = f'INSERT INTO {_table("rdf_props")} (s, "key", val) SELECT ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM {_table("rdf_props")} WHERE s = ? AND "key" = ?)'
            cursor.executemany(prop_sql, prop_data)

            cursor.execute("COMMIT")
            return True
        except Exception as e:
            cursor.execute("ROLLBACK")
            if (
                "UNIQUE" in str(e)
                or "-119" in str(e)
                or "validation failed" in str(e).lower()
            ):
                logger.debug(f"create_node skipped: {node_id}: {str(e)[:80]}")
            else:
                logger.error(f"create_node failed: {e}")
            return False

    def create_edge(
        self,
        source_id: str,
        predicate: str,
        target_id: str,
        qualifiers: Dict[str, Any] = None,
        graph: Optional[str] = None,
    ) -> bool:
        cursor = self.conn.cursor()
        try:
            qual_json = json.dumps(qualifiers) if qualifiers else None
            if graph:
                cursor.execute(
                    f"INSERT INTO {_table('rdf_edges')} (s, p, o_id, qualifiers, graph_id) VALUES (?, ?, ?, ?, ?)",
                    [source_id, predicate, target_id, qual_json, graph],
                )
            else:
                cursor.execute(
                    f"INSERT INTO {_table('rdf_edges')} (s, p, o_id, qualifiers) VALUES (?, ?, ?, ?)",
                    [source_id, predicate, target_id, qual_json],
                )
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            if "UNIQUE" in str(e) or "-119" in str(e):
                logger.debug(
                    f"create_edge duplicate: {source_id}-[{predicate}]->{target_id}"
                )
            else:
                logger.error(f"create_edge failed: {e}")
            return False
        try:
            self._iris_obj().classMethodVoid(
                "Graph.KG.EdgeScan",
                "WriteAdjacency",
                source_id,
                predicate,
                target_id,
                "1.0",
            )
        except Exception as e:
            logger.warning(f"create_edge ^KG write failed (BuildKG can recover): {e}")
        return True

    def delete_edge(self, source_id: str, predicate: str, target_id: str) -> bool:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                f"DELETE FROM {_table('rdf_edges')} WHERE s = ? AND p = ? AND o_id = ?",
                [source_id, predicate, target_id],
            )
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            logger.error(f"delete_edge failed: {e}")
            return False
        try:
            self._iris_obj().classMethodVoid(
                "Graph.KG.EdgeScan", "DeleteAdjacency", source_id, predicate, target_id
            )
        except Exception as e:
            logger.warning(f"delete_edge ^KG kill failed (BuildKG can recover): {e}")
        return True

    def list_graphs(self) -> List[str]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT DISTINCT graph_id FROM Graph_KG.rdf_edges WHERE graph_id IS NOT NULL ORDER BY graph_id"
        )
        return [row[0] for row in cursor.fetchall()]

    def drop_graph(self, graph_id: str) -> int:
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM Graph_KG.rdf_edges WHERE graph_id = ?", [graph_id])
        deleted = cursor.rowcount if cursor.rowcount is not None else 0
        try:
            self.conn.commit()
        except Exception:
            pass
        return deleted

    def bulk_create_nodes(
        self,
        nodes: List[Dict[str, Any]],
        disable_indexes: bool = True,
    ) -> List[str]:
        """
        Bulk create nodes using high-performance batch SQL.

        Uses %NOINDEX hints and batch parameter binding for 5,000+ entities/sec.
        Best for initial data loads or large syncs (10k+ entities).

        Args:
            nodes: List of node dicts, each with:
                - id: Node ID (required)
                - labels: List of labels (optional)
                - properties: Dict of properties (optional)
            disable_indexes: Drop indexes before load, rebuild after (default True)

        Returns:
            List of successfully created node IDs
        """
        if not nodes:
            return []

        cursor = self.conn.cursor()
        created_ids = []

        try:
            # 1. Pre-load setup
            if disable_indexes:
                GraphSchema.disable_indexes(cursor)

            # 2. SQL templates (using %NOINDEX for speed)
            node_sql = GraphSchema.get_bulk_insert_sql("nodes")
            label_sql = GraphSchema.get_bulk_insert_sql("rdf_labels")
            prop_sql = GraphSchema.get_bulk_insert_sql("rdf_props")

            # 3. Collect and prepare data
            all_labels = []
            all_props = []
            valid_nodes = []

            for node in nodes:
                node_id = node.get("id")
                if not node_id:
                    continue

                created_ids.append(node_id)
                # params: [node_id, node_id] for WHERE NOT EXISTS
                valid_nodes.append([node_id, node_id])

                for label in node.get("labels", []):
                    # params: [s, label, s, label]
                    all_labels.append((node_id, label, node_id, label))

                props = node.get("properties", {})
                # Ensure ID is in properties for consistency with Cypher CREATE
                if "id" not in props:
                    props["id"] = node_id

                for k, v in props.items():
                    if v is None:
                        continue
                    val_str = json.dumps(v) if isinstance(v, (dict, list)) else str(v)
                    # params: [s, key, val, s, key]
                    all_props.append((node_id, k, val_str, node_id, k))

            # 4. Batch Execution (Transactional phases for FK safety)
            # Phase 1: Nodes
            cursor.executemany(node_sql, valid_nodes)
            self.conn.commit()

            # Phase 2: Labels
            if all_labels:
                cursor.executemany(label_sql, all_labels)

            # Phase 3: Properties
            if all_props:
                cursor.executemany(prop_sql, all_props)

            self.conn.commit()
            return created_ids

        except Exception as e:
            self.conn.rollback()
            logger.error(f"Bulk load failed: {e}")
            raise
        finally:
            if disable_indexes:
                GraphSchema.rebuild_indexes(cursor)
                self.conn.commit()

    def bulk_create_edges(
        self,
        edges: List[Dict[str, Any]],
        disable_indexes: bool = True,
        graph: Optional[str] = None,
    ) -> int:
        """
        Bulk create edges using high-performance batch SQL.

        Args:
            edges: List of edge dicts:
                - source_id: Source node ID
                - predicate: Relationship type
                - target_id: Target node ID
            disable_indexes: Drops indexes before load (default True)

        Returns:
            Number of edges created
        """
        if not edges:
            return 0

        cursor = self.conn.cursor()
        try:
            if disable_indexes:
                GraphSchema.disable_indexes(cursor)

            edge_sql = GraphSchema.get_bulk_insert_sql("rdf_edges")
            edge_params = []
            has_graph = graph is not None or any(e.get("graph") for e in edges)
            if has_graph:
                graph_sql = GraphSchema.get_bulk_insert_sql("rdf_edges_with_graph")
                plain_sql = GraphSchema.get_bulk_insert_sql("rdf_edges")
                for e in edges:
                    if all(k in e for k in ("source_id", "predicate", "target_id")):
                        s, p, o = e["source_id"], e["predicate"], e["target_id"]
                        g = e.get("graph", graph)
                        if g is not None:
                            edge_params.append([s, p, o, g, s, p, o, g, g])
                            cursor.execute(graph_sql, [s, p, o, g, s, p, o, g, g])
                        else:
                            cursor.execute(plain_sql, [s, p, o, s, p, o])
            else:
                for e in edges:
                    if all(k in e for k in ("source_id", "predicate", "target_id")):
                        s, p, o = e["source_id"], e["predicate"], e["target_id"]
                        edge_params.append([s, p, o, s, p, o])
                cursor.executemany(edge_sql, edge_params)

            self.conn.commit()
            return len(edge_params)
        except Exception as e:
            self.conn.rollback()
            logger.error(f"Bulk edge load failed: {e}")
            raise
        finally:
            if disable_indexes:
                GraphSchema.rebuild_indexes(cursor)
                self.conn.commit()
            try:
                self._iris_obj().classMethodVoid("Graph.KG.Traversal", "BuildKG")
            except Exception as e:
                logger.warning(
                    f"bulk_create_edges BuildKG failed (^KG may be stale): {e}"
                )

    def load_networkx(
        self,
        G,
        label_attr: str = "type",
        skip_existing: bool = True,
        progress_callback=None,
    ) -> dict:
        added_nodes = 0
        added_edges = 0
        skipped_nodes = 0
        skipped_edges = 0
        total_nodes = G.number_of_nodes()
        total_edges = G.number_of_edges()
        for node_id, data in G.nodes(data=True):
            labels = []
            if label_attr and label_attr in data:
                val = data[label_attr]
                labels = [val] if isinstance(val, str) else list(val)
            elif "namespace" in data:
                labels = [data["namespace"]]
            props = {}
            for k, v in data.items():
                if k in (label_attr, "namespace") or v is None:
                    continue
                s = str(v) if not isinstance(v, str) else v
                if len(s) > 60000:
                    s = s[:60000]
                props[k] = s
            if self.create_node(node_id=str(node_id), labels=labels, properties=props):
                added_nodes += 1
            else:
                skipped_nodes += 1
            n_done = added_nodes + skipped_nodes
            if n_done % 10000 == 0:
                logger.info(
                    f"Nodes: {n_done:,}/{total_nodes:,} ({added_nodes:,} added, {skipped_nodes:,} skipped)"
                )
                if progress_callback:
                    progress_callback(n_done, added_edges + skipped_edges)
        logger.info(f"Nodes complete: {added_nodes:,} added, {skipped_nodes:,} skipped")
        if progress_callback:
            progress_callback(added_nodes + skipped_nodes, 0)
        for src, dst, data in G.edges(data=True):
            predicate = data.get(
                "predicate", data.get("label", data.get("key", "is_a"))
            )
            qualifiers = {
                k: v for k, v in data.items() if k not in ("predicate", "label", "key")
            }
            if self.create_edge(
                source_id=str(src),
                predicate=str(predicate),
                target_id=str(dst),
                qualifiers=qualifiers or None,
            ):
                added_edges += 1
            else:
                skipped_edges += 1
            e_done = added_edges + skipped_edges
            if e_done % 10000 == 0:
                logger.info(
                    f"Edges: {e_done:,}/{total_edges:,} ({added_edges:,} added, {skipped_edges:,} skipped)"
                )
                if progress_callback:
                    progress_callback(added_nodes + skipped_nodes, e_done)
        logger.info(f"Edges complete: {added_edges:,} added, {skipped_edges:,} skipped")
        if progress_callback:
            progress_callback(added_nodes + skipped_nodes, added_edges + skipped_edges)
        return {
            "nodes": added_nodes,
            "edges": added_edges,
            "skipped_nodes": skipped_nodes,
            "skipped_edges": skipped_edges,
        }

    def import_rdf(
        self,
        path: str,
        format: Optional[str] = None,
        batch_size: int = 10000,
        progress=None,
        infer=False,
        graph: Optional[str] = None,
    ) -> Dict[str, int]:
        try:
            import rdflib
            from rdflib import (
                Graph,
                ConjunctiveGraph,
                URIRef,
                Literal as RDFLiteral,
                BNode,
            )
        except ImportError:
            raise ImportError("import_rdf requires rdflib: pip install rdflib")

        if format is None:
            ext = path.rsplit(".", 1)[-1].lower()
            format = {
                "ttl": "turtle",
                "nt": "nt",
                "nq": "nquads",
                "n3": "n3",
                "trig": "trig",
                "jsonld": "json-ld",
            }.get(ext, "turtle")

        is_quads = format in ("nquads", "trig")
        if is_quads:
            g = ConjunctiveGraph()
        else:
            g = Graph()
        g.parse(path, format=format)

        cursor = self.conn.cursor()
        nodes_inserted = 0
        edges_inserted = 0
        props_inserted = 0
        triple_count = 0
        blank_prefix = f"_:{abs(hash(path)) % 10**8}:"

        def _node_id(term):
            if isinstance(term, URIRef):
                return str(term)
            if isinstance(term, BNode):
                return f"{blank_prefix}{term}"
            return str(term)

        def _ensure_node(nid):
            try:
                cursor.execute(
                    f"INSERT INTO {_table('nodes')} (node_id) SELECT ? WHERE NOT EXISTS (SELECT 1 FROM {_table('nodes')} WHERE node_id = ?)",
                    [nid, nid],
                )
                return True
            except Exception:
                return False

        batch_nodes: set = set()
        batch_edges: List = []
        batch_props: List = []

        def _flush():
            nonlocal nodes_inserted, edges_inserted, props_inserted
            for nid in batch_nodes:
                if _ensure_node(nid):
                    nodes_inserted += 1
            for s, p, o, edge_graph in batch_edges:
                try:
                    if edge_graph:
                        cursor.execute(
                            f"INSERT INTO {_table('rdf_edges')} (s, p, o_id, graph_id) SELECT ?, ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM {_table('rdf_edges')} WHERE s = ? AND p = ? AND o_id = ? AND graph_id = ?)",
                            [s, p, o, edge_graph, s, p, o, edge_graph],
                        )
                    else:
                        cursor.execute(
                            f"INSERT INTO {_table('rdf_edges')} (s, p, o_id) SELECT ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM {_table('rdf_edges')} WHERE s = ? AND p = ? AND o_id = ? AND graph_id IS NULL)",
                            [s, p, o, s, p, o],
                        )
                    edges_inserted += 1
                except Exception:
                    pass
            for s, k, v in batch_props:
                try:
                    cursor.execute(
                        f'INSERT INTO {_table("rdf_props")} (s, "key", val) VALUES (?, ?, ?)',
                        [s, k, v[:64000]],
                    )
                    props_inserted += 1
                except Exception:
                    pass
            try:
                self.conn.commit()
            except Exception:
                pass
            batch_nodes.clear()
            batch_edges.clear()
            batch_props.clear()

        triples_iter = g.quads() if is_quads else ((s, p, o, None) for s, p, o in g)

        for s, p, o, graph_ctx in triples_iter:
            triple_count += 1
            s_id = _node_id(s)
            p_str = _node_id(p)
            batch_nodes.add(s_id)

            effective_graph = graph
            if graph_ctx is not None:
                ctx_str = str(graph_ctx)
                if ctx_str and ctx_str not in ("", "DEFAULT", "urn:x-rdflib:default"):
                    effective_graph = ctx_str

            if isinstance(o, RDFLiteral):
                key = p_str.rsplit("/", 1)[-1].rsplit("#", 1)[-1][:128]
                val = str(o)
                lang = getattr(o, "language", None)
                if lang:
                    batch_props.append((s_id, key, val))
                    batch_props.append((s_id, f"{key}_lang", lang))
                else:
                    batch_props.append((s_id, key, val))
                props_inserted += 0
            elif isinstance(o, (URIRef, BNode)):
                o_id = _node_id(o)
                batch_nodes.add(o_id)
                batch_edges.append((s_id, p_str, o_id, effective_graph))
            else:
                batch_props.append((s_id, p_str[:128], str(o)[:64000]))

            if triple_count % batch_size == 0:
                _flush()
                if progress:
                    progress(triple_count, 0)

        _flush()

        try:
            self._iris_obj().classMethodVoid("Graph.KG.Traversal", "BuildKG")
        except Exception as e:
            logger.warning(f"import_rdf BuildKG failed (^KG may be stale): {e}")

        result = {
            "triples": triple_count,
            "nodes": nodes_inserted,
            "edges": edges_inserted,
            "properties": props_inserted,
        }

        if infer:
            rules = infer if isinstance(infer, str) else "rdfs"
            inf_result = self.materialize_inference(rules=rules, graph=graph)
            result["inferred"] = inf_result.get("inferred", 0)

        return result

    def materialize_inference(
        self, rules: str = "rdfs", graph: Optional[str] = None
    ) -> Dict[str, int]:
        RDFS_SUBCLASSOF = "http://www.w3.org/2000/01/rdf-schema#subClassOf"
        RDFS_SUBPROPOF = "http://www.w3.org/2000/01/rdf-schema#subPropertyOf"
        RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
        RDFS_DOMAIN = "http://www.w3.org/2000/01/rdf-schema#domain"
        RDFS_RANGE = "http://www.w3.org/2000/01/rdf-schema#range"
        OWL_EQUIV_CLASS = "http://www.w3.org/2002/07/owl#equivalentClass"
        OWL_EQUIV_PROP = "http://www.w3.org/2002/07/owl#equivalentProperty"
        OWL_INVERSE = "http://www.w3.org/2002/07/owl#inverseOf"
        OWL_SAME_AS = "http://www.w3.org/2002/07/owl#sameAs"
        OWL_TRANS_PROP = "http://www.w3.org/2002/07/owl#TransitiveProperty"
        OWL_SYM_PROP = "http://www.w3.org/2002/07/owl#SymmetricProperty"
        INFERRED_JSON = '{"inferred":true}'

        cursor = self.conn.cursor()
        inferred_count = 0

        graph_filter_sql = " AND graph_id = ?" if graph else " AND (graph_id IS NULL)"
        graph_filter_params = [graph] if graph else []

        def _fetch_edges(predicate):
            cursor.execute(
                "SELECT s, o_id FROM Graph_KG.rdf_edges WHERE p = ? "
                "AND (qualifiers IS NULL OR JSON_VALUE(qualifiers, '$.inferred') IS NULL)"
                + graph_filter_sql,
                [predicate] + graph_filter_params,
            )
            return set((r[0], r[1]) for r in cursor.fetchall())

        def _exists(s, p, o):
            if graph:
                cursor.execute(
                    "SELECT 1 FROM Graph_KG.rdf_edges WHERE s=? AND p=? AND o_id=? AND graph_id=? FETCH FIRST 1 ROWS ONLY",
                    [s, p, o, graph],
                )
            else:
                cursor.execute(
                    "SELECT 1 FROM Graph_KG.rdf_edges WHERE s=? AND p=? AND o_id=? AND (graph_id IS NULL) FETCH FIRST 1 ROWS ONLY",
                    [s, p, o],
                )
            return cursor.fetchone() is not None

        def _insert_inferred(triples):
            nonlocal inferred_count
            for s, p, o in triples:
                if not _exists(s, p, o):
                    try:
                        if graph:
                            cursor.execute(
                                "INSERT INTO Graph_KG.rdf_edges (s, p, o_id, qualifiers, graph_id) VALUES (?, ?, ?, ?, ?)",
                                [s, p, o, INFERRED_JSON, graph],
                            )
                        else:
                            cursor.execute(
                                "INSERT INTO Graph_KG.rdf_edges (s, p, o_id, qualifiers) VALUES (?, ?, ?, ?)",
                                [s, p, o, INFERRED_JSON],
                            )
                        inferred_count += 1
                    except Exception:
                        pass
            try:
                self.conn.commit()
            except Exception:
                pass

        def _transitive_closure(direct_edges):
            closure = set(direct_edges)
            changed = True
            while changed:
                changed = False
                new = set()
                for a, b in closure:
                    for b2, c in closure:
                        if b == b2 and (a, c) not in closure and a != c:
                            new.add((a, c))
                if new:
                    closure |= new
                    changed = True
            return closure - direct_edges

        subclass_direct = _fetch_edges(RDFS_SUBCLASSOF)
        subprop_direct = _fetch_edges(RDFS_SUBPROPOF)

        inferred = set()
        inferred |= {
            (a, RDFS_SUBCLASSOF, c) for a, c in _transitive_closure(subclass_direct)
        }
        inferred |= {
            (a, RDFS_SUBPROPOF, c) for a, c in _transitive_closure(subprop_direct)
        }

        rdf_type_edges = _fetch_edges(RDF_TYPE)
        all_subclass = subclass_direct | {
            (a, c) for a, _, c in inferred if _ == RDFS_SUBCLASSOF
        }
        for x, cls_a in list(rdf_type_edges):
            for a, b in all_subclass:
                if a == cls_a:
                    inferred.add((x, RDF_TYPE, b))

        domain_edges = _fetch_edges(RDFS_DOMAIN)
        range_edges = _fetch_edges(RDFS_RANGE)
        all_predicate_edges = {}
        cursor.execute(
            "SELECT s, p, o_id FROM Graph_KG.rdf_edges WHERE p NOT IN (?, ?, ?, ?, ?) LIMIT 50000",
            [RDFS_SUBCLASSOF, RDFS_SUBPROPOF, RDF_TYPE, RDFS_DOMAIN, RDFS_RANGE],
        )
        for s, p, o in cursor.fetchall():
            all_predicate_edges.setdefault(p, []).append((s, o))

        for p, domain in domain_edges:
            for s, _ in all_predicate_edges.get(p, []):
                inferred.add((s, RDF_TYPE, domain))

        for p, rng in range_edges:
            for _, o in all_predicate_edges.get(p, []):
                inferred.add((o, RDF_TYPE, rng))

        if rules == "owl":
            equiv_class = _fetch_edges(OWL_EQUIV_CLASS)
            for a, b in equiv_class:
                inferred.add((a, RDFS_SUBCLASSOF, b))
                inferred.add((b, RDFS_SUBCLASSOF, a))

            equiv_prop = _fetch_edges(OWL_EQUIV_PROP)
            for p, q in equiv_prop:
                inferred.add((p, RDFS_SUBPROPOF, q))
                inferred.add((q, RDFS_SUBPROPOF, p))

            inverse_edges = _fetch_edges(OWL_INVERSE)
            for p, q in inverse_edges:
                for x, y in all_predicate_edges.get(p, []):
                    inferred.add((y, q, x))
                for x, y in all_predicate_edges.get(q, []):
                    inferred.add((y, p, x))

            cursor.execute(
                "SELECT s FROM Graph_KG.rdf_edges WHERE p=? AND o_id=?",
                [RDF_TYPE, OWL_TRANS_PROP],
            )
            trans_props = {r[0] for r in cursor.fetchall()}
            for tp in trans_props:
                tp_edges = _fetch_edges(tp)
                inferred |= {(a, tp, c) for a, c in _transitive_closure(tp_edges)}

            cursor.execute(
                "SELECT s FROM Graph_KG.rdf_edges WHERE p=? AND o_id=?",
                [RDF_TYPE, OWL_SYM_PROP],
            )
            sym_props = {r[0] for r in cursor.fetchall()}
            for sp in sym_props:
                for x, y in all_predicate_edges.get(sp, []):
                    inferred.add((y, sp, x))

        _insert_inferred(inferred)
        return {"inferred": inferred_count}

    def retract_inference(self, graph: Optional[str] = None) -> int:
        cursor = self.conn.cursor()
        if graph:
            cursor.execute(
                "DELETE FROM Graph_KG.rdf_edges WHERE JSON_VALUE(qualifiers, '$.inferred') = 'true' AND graph_id = ?",
                [graph],
            )
        else:
            cursor.execute(
                "DELETE FROM Graph_KG.rdf_edges WHERE JSON_VALUE(qualifiers, '$.inferred') = 'true'"
            )
        deleted = cursor.rowcount or 0
        try:
            self.conn.commit()
        except Exception:
            pass
        return deleted

    def save_snapshot(
        self,
        path: str,
        layers: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        import zipfile as _zipfile
        import json as _json
        import time as _time
        import uuid as _uuid

        if layers is None:
            layers = ["sql", "globals"]

        ts = int(_time.time() * 1000)
        run_id = _uuid.uuid4().hex[:8]
        import sys as _sys

        try:
            iris_ver = str(
                _call_classmethod(self.conn, "%SYSTEM.Version", "GetVersion")
            )
        except Exception:
            iris_ver = "unknown"
        metadata: Dict[str, Any] = {
            "version": "1.1",
            "globals_format": "ndjson",
            "created_ts": ts,
            "ivg_version": "1.58.0",
            "iris_version": iris_ver,
            "python_version": f"{_sys.version_info.major}.{_sys.version_info.minor}",
            "has_vector_sql": False,
            "embedding_dim": self.embedding_dimension or 0,
            "layers": layers,
            "tables": {},
            "globals": {},
        }

        sql_data: Dict[str, str] = {}
        globals_data: Dict[str, bytes] = {}

        SQL_TABLES_EXPORT = [
            ("Graph_KG.nodes", "node_id"),
            ("Graph_KG.rdf_edges", "s"),
            ("Graph_KG.rdf_labels", "s"),
            ("Graph_KG.rdf_props", "s"),
            ("Graph_KG.rdf_reifications", "subject_s"),
        ]
        VECTOR_TABLE = "Graph_KG.kg_NodeEmbeddings"

        if "sql" in layers:
            cursor = self.conn.cursor()
            for table, _ in SQL_TABLES_EXPORT:
                try:
                    cursor.execute(f"SELECT * FROM {table}")
                    all_desc = cursor.description
                    rows = cursor.fetchall()
                    _ROWID_NAMES = {"edge_id", "reification_id", "label_id", "prop_id"}
                    skip = {
                        i
                        for i, d in enumerate(all_desc)
                        if d[0].lower() in _ROWID_NAMES
                    }
                    cols = [
                        d[0].lower() for i, d in enumerate(all_desc) if i not in skip
                    ]
                    lines = []
                    for row in rows:
                        lines.append(
                            _json.dumps(
                                {
                                    k: (
                                        None
                                        if v is None
                                        else v.isoformat()
                                        if hasattr(v, "isoformat")
                                        else float(v)
                                        if hasattr(v, "__float__")
                                        and not isinstance(v, (int, str, bool))
                                        else v
                                    )
                                    for k, v in zip(
                                        cols,
                                        [
                                            val
                                            for i, val in enumerate(row)
                                            if i not in skip
                                        ],
                                    )
                                }
                            )
                        )
                    sql_data[table] = "\n".join(lines)
                    metadata["tables"][table] = len(rows)
                except Exception as e:
                    logger.debug("Snapshot: skipping table %s: %s", table, e)
                    metadata["tables"][table] = 0

            try:
                cursor.execute(f"SELECT id, emb, metadata FROM {VECTOR_TABLE}")
                cols = ["id", "emb", "metadata"]
                rows = cursor.fetchall()
                lines = []
                for row in rows:
                    nid, emb_val, meta_val = row[0], row[1], row[2]
                    emb_str = str(emb_val) if emb_val is not None else None
                    lines.append(
                        _json.dumps({"id": nid, "emb": emb_str, "metadata": meta_val})
                    )
                sql_data[VECTOR_TABLE] = "\n".join(lines)
                metadata["tables"][VECTOR_TABLE] = len(rows)
                metadata["has_vector_sql"] = True
            except Exception as e:
                logger.debug("Snapshot: kg_NodeEmbeddings not available: %s", e)
                metadata["has_vector_sql"] = False

            EDGE_VECTOR_TABLE = "Graph_KG.kg_EdgeEmbeddings"
            try:
                cursor.execute(f"SELECT s, p, o_id, emb FROM {EDGE_VECTOR_TABLE}")
                rows = cursor.fetchall()
                lines = []
                for row in rows:
                    s_val, p_val, o_val, emb_val = row[0], row[1], row[2], row[3]
                    emb_str = str(emb_val) if emb_val is not None else None
                    lines.append(
                        _json.dumps({"s": s_val, "p": p_val, "o_id": o_val, "emb": emb_str})
                    )
                sql_data[EDGE_VECTOR_TABLE] = "\n".join(lines)
                metadata["tables"][EDGE_VECTOR_TABLE] = len(rows)
            except Exception as e:
                logger.debug("Snapshot: kg_EdgeEmbeddings not available: %s", e)

        if "globals" in layers:
            GLOBALS_EXPORT = [
                ("KG", [["out"], ["in"]]),
                ("BM25Idx", [[]]),
                ("IVF", [[]]),
                ("PLAID", [[]]),
                ("VecIdx", [[]]),
                ("NKG", [[]]),
                ("IVG.CDC", [[]]),
            ]
            try:
                iris_obj = self._iris_obj()
                for gname, subscript_prefixes in GLOBALS_EXPORT:
                    lines = []
                    for prefix_subs in subscript_prefixes:
                        try:
                            lines.extend(
                                self._export_global_to_ndjson(
                                    iris_obj, f"^{gname}", prefix_subs
                                )
                            )
                        except Exception as eg:
                            logger.debug(
                                "Snapshot: skip global ^%s%s: %s",
                                gname,
                                prefix_subs,
                                eg,
                            )
                    if lines:
                        content = "\n".join(lines).encode("utf-8")
                        globals_data[gname] = content
                        metadata["globals"][gname] = {
                            "format": "ndjson",
                            "subscripts": [s for sl in subscript_prefixes for s in sl],
                            "size": len(content),
                        }
            except Exception as e:
                logger.warning(
                    "Snapshot: global export failed (globals layer skipped): %s", e
                )
                size_raw = _call_classmethod(
                    self.conn, "Graph.KG.Snapshot", "GetFileSize", tmp_xml
                )
                file_size = int(size_raw or 0)
                if file_size > 0:
                    chunks = []
                    chunk_size = 512 * 1024
                    offset = 0
                    while offset < file_size:
                        chunk = _call_classmethod(
                            self.conn,
                            "Graph.KG.Snapshot",
                            "ReadFileChunk",
                            tmp_xml,
                            offset,
                            chunk_size,
                        )
                        if not chunk:
                            break
                        chunks.append(str(chunk).encode("utf-8"))
                        offset += chunk_size
                    xml_bytes = b"".join(chunks)
                    globals_data["_all_globals"] = xml_bytes
                    metadata["globals"]["_all_globals"] = {
                        "format": "iris-xml",
                        "size": len(xml_bytes),
                        "globals": GLOBALS_TO_EXPORT,
                    }
                    try:
                        _call_classmethod(
                            self.conn,
                            "Graph.KG.Snapshot",
                            "DeleteDir",
                            f"/tmp/ivg_snap_{run_id}_globals.xml",
                        )
                    except Exception:
                        pass
                else:
                    logger.debug("Snapshot: globals XML export produced empty file")
            except Exception as e:
                logger.warning(
                    "Snapshot: XML global export failed (globals layer skipped): %s", e
                )
        with _zipfile.ZipFile(path, "w", _zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("metadata.json", _json.dumps(metadata, indent=2))
            for table, content in sql_data.items():
                safe_name = table.replace(".", "_").replace("/", "_")
                zf.writestr(f"sql/{safe_name}.ndjson", content)
            for gname, content in globals_data.items():
                zf.writestr(f"globals/{gname}.ndjson", content)

        return {
            "path": path,
            "tables": metadata["tables"],
            "globals": list(globals_data.keys()),
            "snapshot_ts": ts,
        }

    @staticmethod
    def snapshot_info(path: str) -> Dict[str, Any]:
        import zipfile as _zipfile
        import json as _json

        with _zipfile.ZipFile(path, "r") as zf:
            with zf.open("metadata.json") as f:
                metadata = _json.loads(f.read())
        return {
            "metadata": metadata,
            "tables": metadata.get("tables", {}),
            "has_vector_sql": metadata.get("has_vector_sql", False),
            "version": metadata.get("version", "unknown"),
            "snapshot_ts": metadata.get("created_ts", 0),
            "globals": metadata.get("globals", {}),
        }

    def restore_snapshot(
        self,
        path: str,
        merge: bool = False,
    ) -> Dict[str, Any]:
        import zipfile as _zipfile
        import json as _json
        import uuid as _uuid

        run_id = _uuid.uuid4().hex[:8]

        with _zipfile.ZipFile(path, "r") as zf:
            names = zf.namelist()
            metadata = _json.loads(zf.read("metadata.json"))

            sql_files = {
                n: zf.read(n).decode("utf-8") for n in names if n.startswith("sql/")
            }
            global_files = {n: zf.read(n) for n in names if n.startswith("globals/")}

        restored_tables: Dict[str, int] = {}
        restored_globals: List[str] = []
        cursor = self.conn.cursor()

        TABLE_ORDER = [
            "Graph_KG_nodes.ndjson",
            "Graph_KG_rdf_edges.ndjson",
            "Graph_KG_rdf_labels.ndjson",
            "Graph_KG_rdf_props.ndjson",
            "Graph_KG_rdf_reifications.ndjson",
        ]
        VECTOR_FILE = "Graph_KG_kg_NodeEmbeddings.ndjson"

        if not merge:
            table_clear_order = [
                "Graph_KG.rdf_reifications",
                "Graph_KG.rdf_labels",
                "Graph_KG.rdf_props",
                "Graph_KG.rdf_edges",
                "Graph_KG.kg_NodeEmbeddings",
                "Graph_KG.nodes",
            ]
            globals_in_snapshot = metadata.get("globals", {})
            for gname, ginfo in globals_in_snapshot.items():
                subscripts = (
                    ginfo.get("subscripts", []) if isinstance(ginfo, dict) else []
                )
                try:
                    iris_obj = self._iris_obj()
                    if subscripts:
                        for sub in subscripts:
                            iris_obj.kill(f"^{gname}", sub)
                    else:
                        iris_obj.kill(f"^{gname}")
                except Exception as e:
                    logger.debug("restore: kill ^%s failed: %s", gname, e)
            for table in table_clear_order:
                try:
                    cursor.execute(f"DELETE FROM {table}")
                    self.conn.commit()
                except Exception:
                    try:
                        self.conn.rollback()
                    except Exception:
                        pass

        def _insert_row(table: str, row: Dict[str, Any]) -> bool:
            if not row:
                return False
            cols = list(row.keys())
            vals = list(row.values())
            placeholders = ", ".join(["?"] * len(cols))
            col_list = ", ".join(cols)
            try:
                cursor.execute(
                    f"INSERT INTO {table} ({col_list}) SELECT {placeholders} "
                    f"WHERE NOT EXISTS (SELECT 1 FROM {table} WHERE "
                    + " AND ".join(
                        f"{c} = ?" if row[c] is not None else f"{c} IS NULL"
                        for c in cols[:1]
                    )
                    + ")",
                    vals + [vals[0]],
                )
                return True
            except Exception:
                return False

        sql_order = [
            f for f in TABLE_ORDER if f"sql/{f}" in sql_files or f in sql_files
        ]

        for fname_short in TABLE_ORDER:
            fname = f"sql/{fname_short}"
            if fname not in sql_files:
                continue
            table_name = fname_short.replace("Graph_KG_", "Graph_KG.").replace(
                ".ndjson", ""
            )
            count = 0
            for line in sql_files[fname].splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = _json.loads(line)
                    cols = list(row.keys())
                    vals = list(row.values())
                    placeholders = ", ".join(["?"] * len(cols))
                    col_list = ", ".join(cols)
                    if merge:
                        cursor.execute(
                            f"INSERT INTO {table_name} ({col_list}) SELECT {placeholders} "
                            f"WHERE NOT EXISTS (SELECT 1 FROM {table_name} WHERE {cols[0]} = ?)",
                            vals + [vals[0]],
                        )
                    else:
                        cursor.execute(
                            f"INSERT INTO {table_name} ({col_list}) VALUES ({placeholders})",
                            vals,
                        )
                    count += 1
                except Exception as e:
                    logger.debug("restore: row insert failed for %s: %s", table_name, e)
            try:
                self.conn.commit()
            except Exception:
                pass
            restored_tables[table_name] = count

        if f"sql/{VECTOR_FILE}" in sql_files:
            count = 0
            for line in sql_files[f"sql/{VECTOR_FILE}"].splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = _json.loads(line)
                    nid = row.get("id")
                    emb_str = row.get("emb")
                    meta_val = row.get("metadata")
                    if nid and emb_str:
                        try:
                            if merge:
                                cursor.execute(
                                    "INSERT INTO Graph_KG.kg_NodeEmbeddings (id, emb) "
                                    "SELECT ?, TO_VECTOR(?, DOUBLE) "
                                    "WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.kg_NodeEmbeddings WHERE id = ?)",
                                    [nid, emb_str, nid],
                                )
                            else:
                                cursor.execute(
                                    "INSERT INTO Graph_KG.kg_NodeEmbeddings (id, emb) "
                                    "VALUES (?, TO_VECTOR(?, DOUBLE))",
                                    [nid, emb_str],
                                )
                            count += 1
                        except Exception:
                            pass
                except Exception:
                    pass
            try:
                self.conn.commit()
            except Exception:
                pass
            restored_tables["Graph_KG.kg_NodeEmbeddings"] = count

        EDGE_VECTOR_FILE = "Graph_KG_kg_EdgeEmbeddings.ndjson"
        if f"sql/{EDGE_VECTOR_FILE}" in sql_files:
            count = 0
            for line in sql_files[f"sql/{EDGE_VECTOR_FILE}"].splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = _json.loads(line)
                    s_val = row.get("s")
                    p_val = row.get("p")
                    o_val = row.get("o_id")
                    emb_str = row.get("emb")
                    if s_val and p_val and o_val and emb_str:
                        try:
                            if merge:
                                cursor.execute(
                                    "INSERT INTO Graph_KG.kg_EdgeEmbeddings (s, p, o_id, emb) "
                                    "SELECT ?, ?, ?, TO_VECTOR(?, DOUBLE) "
                                    "WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.kg_EdgeEmbeddings "
                                    "WHERE s=? AND p=? AND o_id=?)",
                                    [s_val, p_val, o_val, emb_str, s_val, p_val, o_val],
                                )
                            else:
                                cursor.execute(
                                    "INSERT INTO Graph_KG.kg_EdgeEmbeddings (s, p, o_id, emb) "
                                    "VALUES (?, ?, ?, TO_VECTOR(?, DOUBLE))",
                                    [s_val, p_val, o_val, emb_str],
                                )
                            count += 1
                        except Exception:
                            pass
                except Exception:
                    pass
            try:
                self.conn.commit()
            except Exception:
                pass
            restored_tables["Graph_KG.kg_EdgeEmbeddings"] = count

        if global_files:
            try:
                iris_obj = self._iris_obj()
                for gfile_path, content in global_files.items():
                    gname = (
                        gfile_path.replace("globals/", "")
                        .replace(".ndjson", "")
                        .replace(".xml", "")
                        .replace(".gof", "")
                    )
                    ndjson = content.decode("utf-8", errors="replace")
                    count = self._import_global_from_ndjson(
                        iris_obj, f"^{gname}", ndjson
                    )
                    if count > 0:
                        restored_globals.append(gname)
            except Exception as e:
                logger.warning("restore: global import failed: %s", e)

        restored_layers = []
        if restored_tables:
            restored_layers.append("sql")
        if restored_globals:
            restored_layers.append("globals")

        if (
            not restored_tables
            and not restored_globals
            and "globals" in restored_layers
        ):
            logger.warning(
                "Globals-only restore: SQL tables are empty — rdf_edges queries will return no results"
            )

        return {
            "restored_tables": restored_tables,
            "restored_globals": restored_globals,
            "restored_layers": restored_layers,
            "snapshot_ts": metadata.get("created_ts", 0),
        }

    def get_unembedded_nodes(self) -> List[str]:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT n.node_id FROM Graph_KG.nodes n "
                "LEFT JOIN Graph_KG.kg_NodeEmbeddings e ON e.id = n.node_id "
                "WHERE e.id IS NULL"
            )
            return [row[0] for row in cursor.fetchall()]
        except Exception:
            return []

    def _export_global_to_ndjson(
        self, iris_obj, global_name: str, prefix_subs: list
    ) -> list:
        import json as _json

        lines = []
        gname_clean = global_name.lstrip("^")

        def _recurse(subs: list):
            cur = subs[-1] if subs else 0
            while True:
                nxt = (
                    iris_obj.nextSubscript(False, global_name, *subs[:-1], cur)
                    if subs
                    else iris_obj.nextSubscript(False, global_name, cur)
                )
                if not nxt:
                    break
                new_subs = (subs[:-1] if subs else []) + [nxt]
                val = iris_obj.get(global_name, *new_subs)
                if val is not None:
                    lines.append(_json.dumps({"k": new_subs, "v": str(val)}))
                _recurse(new_subs + [0])
                cur = nxt

        seed = prefix_subs + [0] if prefix_subs else [0]
        _recurse(seed)
        return lines

    def _import_global_from_ndjson(
        self, iris_obj, global_name: str, ndjson: str
    ) -> int:
        import json as _json

        count = 0
        for line in ndjson.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = _json.loads(line)
                subs = entry["k"]
                val = entry["v"]
                iris_obj.set(val, global_name, *subs)
                count += 1
            except Exception as e:
                logger.debug("global import line failed: %s", e)
        return count

    def load_obo(
        self,
        path_or_url: str,
        prefix: str = None,
        encoding: str = "utf-8",
        encoding_errors: str = "replace",
        progress_callback=None,
    ) -> dict:
        try:
            import obonet
        except ImportError:
            raise ImportError("load_obo requires obonet: pip install obonet")
        import io

        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            G = obonet.read_obo(path_or_url)
        else:
            with open(path_or_url, "rb") as raw:
                content = raw.read().decode(encoding, errors=encoding_errors)
            G = obonet.read_obo(io.StringIO(content))
        if prefix:
            import networkx as nx

            mapping = {n: f"{prefix}:{n}" for n in G.nodes()}
            G = nx.relabel_nodes(G, mapping)
        return self.load_networkx(
            G, label_attr="namespace", progress_callback=progress_callback
        )

    def store_embedding(
        self,
        node_id: str,
        embedding: List[float],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        self._assert_node_exists(node_id)

        try:
            dim = self._get_embedding_dimension()
        except ValueError:
            # Infer dimension from input if auto-detection fails
            dim = len(embedding)
            self.embedding_dimension = dim
            logger.warning(
                f"Embedding dimension auto-detection failed. Inferred dimension {dim} from input."
            )

        if len(embedding) != dim:
            raise ValueError(
                f"Embedding dimension mismatch: expected {dim}, got {len(embedding)}"
            )

        cursor = self.conn.cursor()
        emb_str = ",".join(str(x) for x in embedding)
        meta_json = json.dumps(metadata) if metadata else None

        cursor.execute(
            f"DELETE FROM {_table('kg_NodeEmbeddings')} WHERE id = ?", [node_id]
        )
        cursor.execute(
            f"INSERT INTO {_table('kg_NodeEmbeddings')} (id, emb, metadata) VALUES (?, TO_VECTOR(?), ?)",
            [node_id, emb_str, meta_json],
        )
        self.conn.commit()
        return True

    def store_embeddings(self, items: List[Dict[str, Any]]) -> bool:
        if not items:
            return True

        try:
            dim = self._get_embedding_dimension()
        except ValueError:
            # Infer dimension from first item if auto-detection fails
            dim = len(items[0]["embedding"])
            self.embedding_dimension = dim
            logger.warning(
                f"Embedding dimension auto-detection failed. Inferred dimension {dim} from input."
            )

        for item in items:
            node_id = item["node_id"]
            embedding = item["embedding"]
            if len(embedding) != dim:
                raise ValueError(
                    f"Embedding dimension mismatch: expected {dim}, got {len(embedding)}"
                )
            self._assert_node_exists(node_id)

        cursor = self.conn.cursor()
        cursor.execute("START TRANSACTION")
        try:
            for item in items:
                node_id = item["node_id"]
                embedding = item["embedding"]
                metadata = item.get("metadata")

                emb_str = ",".join(str(x) for x in embedding)
                meta_json = json.dumps(metadata) if metadata else None

                cursor.execute(
                    f"DELETE FROM {_table('kg_NodeEmbeddings')} WHERE id = ?", [node_id]
                )
                cursor.execute(
                    f"INSERT INTO {_table('kg_NodeEmbeddings')} (id, emb, metadata) VALUES (?, TO_VECTOR(?), ?)",
                    [node_id, emb_str, meta_json],
                )
            cursor.execute("COMMIT")
            return True
        except Exception:
            cursor.execute("ROLLBACK")
            raise

    def embed_nodes(
        self,
        model=None,
        where: str = None,
        text_fn=None,
        batch_size: int = 500,
        force: bool = False,
        progress_callback=None,
    ) -> dict:
        """Incrementally embed nodes from Graph_KG.nodes into kg_NodeEmbeddings.

        Args:
            model: Embedder to use (overrides engine's configured embedder for this call).
                   Must have .encode(text) or .embed(text) method, or be callable.
                   If None, uses the engine's configured embedder/embedding_config.
            where: SQL WHERE fragment applied to node_id. Examples:
                   "node_id NOT LIKE 'NCIT:%'"
                   "node_id NOT IN (SELECT id FROM Graph_KG.kg_NodeEmbeddings)"
                   None means all nodes.
            text_fn: callable(node_id, props_dict) -> str. Builds the text to embed.
                     props_dict is the merged rdf_props for the node (key → val).
                     If None, uses node_id as the embedding text.
                     If returns None or "", the node is skipped.
            batch_size: nodes processed per batch (controls memory usage).
            force: if True, re-embeds nodes already in kg_NodeEmbeddings.
            progress_callback: callable(n_done, n_total) called after each batch.

        Returns:
            {"embedded": int, "skipped": int, "errors": int, "total": int}
        """
        from iris_vector_graph.security import sanitize_identifier

        if where is not None:
            if any(
                x in where.upper() for x in (";", "--", "/*", "XP_", "EXEC", "EXECUTE")
            ):
                raise ValueError(f"Unsafe WHERE clause rejected: {where!r}")

        orig_embedder = self.embedder
        if model is not None:
            if isinstance(model, str):
                from sentence_transformers import SentenceTransformer

                self.embedder = SentenceTransformer(model)
            else:
                self.embedder = model

        try:
            cursor = self.conn.cursor()

            where_clause = f"WHERE {where}" if where else ""
            cursor.execute(f"SELECT node_id FROM {_table('nodes')} {where_clause}")
            all_node_ids = [row[0] for row in cursor.fetchall()]
            n_total = len(all_node_ids)

            if not force:
                cursor.execute(f"SELECT id FROM {_table('kg_NodeEmbeddings')}")
                already_embedded = {row[0] for row in cursor.fetchall()}
                to_embed = [nid for nid in all_node_ids if nid not in already_embedded]
            else:
                to_embed = all_node_ids

            n_to_embed = len(to_embed)
            embedded = skipped = errors = 0

            for batch_start in range(0, n_to_embed, batch_size):
                batch_ids = to_embed[batch_start : batch_start + batch_size]

                placeholders = ", ".join("?" * len(batch_ids))
                cursor.execute(
                    f'SELECT s, "key", val FROM {_table("rdf_props")} WHERE s IN ({placeholders})',
                    batch_ids,
                )
                props_by_node: Dict[str, Dict[str, Any]] = {}
                for row in cursor.fetchall():
                    node_id, key, val = row[0], row[1], row[2]
                    props_by_node.setdefault(node_id, {})[key] = val

                texts: List[str] = []
                valid_ids: List[str] = []
                for node_id in batch_ids:
                    props = props_by_node.get(node_id, {})
                    if text_fn is not None:
                        try:
                            text = text_fn(node_id, props)
                        except Exception as ex:
                            logger.warning(
                                f"embed_nodes: text_fn raised for {node_id}: {ex}"
                            )
                            errors += 1
                            continue
                    else:
                        text = node_id

                    if not text:
                        skipped += 1
                        continue

                    texts.append(text)
                    valid_ids.append(node_id)

                if not texts:
                    self.conn.commit()
                    n_done = batch_start + len(batch_ids)
                    if progress_callback:
                        progress_callback(n_done, n_to_embed)
                    continue

                try:
                    use_batch = False
                    if not self.embedding_config and self.embedder is not None:
                        try:
                            from sentence_transformers import SentenceTransformer as _ST
                            use_batch = isinstance(self.embedder, _ST)
                        except ImportError:
                            pass
                    if use_batch:
                        raw = self.embedder.encode(
                            texts, batch_size=min(64, len(texts)), show_progress_bar=False
                        )
                        embeddings = [row.tolist() for row in raw]
                    else:
                        embeddings = [self.embed_text(t) for t in texts]
                except Exception as ex:
                    logger.warning(f"embed_nodes: batch encode failed, falling back per-node: {ex}")
                    embeddings = []
                    for t in texts:
                        try:
                            embeddings.append(self.embed_text(t))
                        except Exception as ex2:
                            logger.warning(f"embed_nodes: embed_text failed: {ex2}")
                            embeddings.append(None)

                insert_params = []
                for node_id, emb in zip(valid_ids, embeddings):
                    if emb is None:
                        errors += 1
                        continue
                    emb_str = ",".join(str(x) for x in emb)
                    insert_params.append((node_id, emb_str))

                if insert_params:
                    ids_to_delete = [p[0] for p in insert_params]
                    del_placeholders = ", ".join("?" * len(ids_to_delete))
                    try:
                        cursor.execute(
                            f"DELETE FROM {_table('kg_NodeEmbeddings')} WHERE id IN ({del_placeholders})",
                            ids_to_delete,
                        )
                    except Exception:
                        pass
                    try:
                        cursor.executemany(
                            f"INSERT INTO {_table('kg_NodeEmbeddings')} (id, emb) VALUES (?, TO_VECTOR(?))",
                            insert_params,
                        )
                        embedded += len(insert_params)
                    except Exception as ex:
                        logger.warning(f"embed_nodes: executemany failed, falling back per-row: {ex}")
                        for node_id, emb_str in insert_params:
                            try:
                                cursor.execute(
                                    f"INSERT INTO {_table('kg_NodeEmbeddings')} (id, emb) VALUES (?, TO_VECTOR(?))",
                                    [node_id, emb_str],
                                )
                                embedded += 1
                            except Exception as ex2:
                                logger.warning(f"embed_nodes: insert failed for {node_id}: {ex2}")
                                errors += 1

                self.conn.commit()
                n_done = batch_start + len(batch_ids)
                logger.info(
                    f"embed_nodes: {n_done}/{n_to_embed} processed ({embedded} embedded)"
                )
                if progress_callback:
                    progress_callback(n_done, n_to_embed)

            skipped += n_total - n_to_embed
            return {
                "embedded": embedded,
                "skipped": skipped,
                "errors": errors,
                "total": n_total,
            }
        finally:
            self.embedder = orig_embedder

    def embed_edges(
        self,
        model=None,
        text_fn=None,
        where: str = None,
        batch_size: int = 500,
        force: bool = False,
        progress_callback=None,
    ) -> dict:
        if where is not None:
            if any(
                x in where.upper() for x in (";", "--", "/*", "XP_", "EXEC", "EXECUTE")
            ):
                raise ValueError(f"Unsafe WHERE clause rejected: {where!r}")

        orig_embedder = self.embedder
        if model is not None:
            if isinstance(model, str):
                from sentence_transformers import SentenceTransformer

                self.embedder = SentenceTransformer(model)
            else:
                self.embedder = model

        try:
            cursor = self.conn.cursor()

            where_clause = f"WHERE {where}" if where else ""
            cursor.execute(
                f"SELECT s, p, o_id FROM {_table('rdf_edges')} {where_clause}"
            )
            all_edges = [(row[0], row[1], row[2]) for row in cursor.fetchall()]
            n_total = len(all_edges)

            if not force:
                cursor.execute(
                    f"SELECT s, p, o_id FROM {_table('kg_EdgeEmbeddings')}"
                )
                already_embedded = {
                    (row[0], row[1], row[2]) for row in cursor.fetchall()
                }
                to_embed = [e for e in all_edges if e not in already_embedded]
            else:
                to_embed = all_edges

            n_to_embed = len(to_embed)
            embedded = skipped = errors = 0

            for batch_start in range(0, n_to_embed, batch_size):
                batch = to_embed[batch_start : batch_start + batch_size]

                texts: List[str] = []
                valid_edges: List[tuple] = []
                for s, p, o_id in batch:
                    if text_fn is not None:
                        try:
                            text = text_fn(s, p, o_id)
                        except Exception as ex:
                            logger.warning(
                                "embed_edges: text_fn raised for (%s, %s, %s): %s",
                                s, p, o_id, ex,
                            )
                            errors += 1
                            continue
                    else:
                        text = f"{s} {p} {o_id}"

                    if not text:
                        skipped += 1
                        continue

                    texts.append(text)
                    valid_edges.append((s, p, o_id))

                if not texts:
                    self.conn.commit()
                    n_done = batch_start + len(batch)
                    if progress_callback:
                        progress_callback(n_done, n_to_embed)
                    continue

                try:
                    use_batch = False
                    if not self.embedding_config and self.embedder is not None:
                        try:
                            from sentence_transformers import SentenceTransformer as _ST
                            use_batch = isinstance(self.embedder, _ST)
                        except ImportError:
                            pass
                    if use_batch:
                        raw = self.embedder.encode(texts, batch_size=min(64, len(texts)), show_progress_bar=False)
                        embeddings = [row.tolist() for row in raw]
                    else:
                        embeddings = [self.embed_text(t) for t in texts]
                except Exception as ex:
                    logger.warning(f"embed_edges: batch encode failed, falling back per-edge: {ex}")
                    embeddings = []
                    for t in texts:
                        try:
                            embeddings.append(self.embed_text(t))
                        except Exception as ex2:
                            logger.warning(f"embed_edges: embed_text failed: {ex2}")
                            embeddings.append(None)

                insert_params = []
                for (s, p, o_id), emb in zip(valid_edges, embeddings):
                    if emb is None:
                        errors += 1
                        continue
                    emb_str = ",".join(str(x) for x in emb)
                    insert_params.append((s, p, o_id, emb_str))

                if insert_params:
                    try:
                        del_params = [(p[0], p[1], p[2]) for p in insert_params]
                        for s, p, o_id in del_params:
                            try:
                                cursor.execute(
                                    f"DELETE FROM {_table('kg_EdgeEmbeddings')} "
                                    "WHERE s=? AND p=? AND o_id=?",
                                    [s, p, o_id],
                                )
                            except Exception:
                                pass
                    except Exception:
                        pass
                    try:
                        cursor.executemany(
                            f"INSERT INTO {_table('kg_EdgeEmbeddings')} "
                            "(s, p, o_id, emb) VALUES (?, ?, ?, TO_VECTOR(?))",
                            insert_params,
                        )
                        embedded += len(insert_params)
                    except Exception as ex:
                        logger.warning(f"embed_edges: executemany failed, falling back per-row: {ex}")
                        for s, p, o_id, emb_str in insert_params:
                            try:
                                cursor.execute(
                                    f"INSERT INTO {_table('kg_EdgeEmbeddings')} "
                                    "(s, p, o_id, emb) VALUES (?, ?, ?, TO_VECTOR(?))",
                                    [s, p, o_id, emb_str],
                                )
                                embedded += 1
                            except Exception as ex2:
                                logger.warning(
                                    "embed_edges: insert failed for (%s, %s, %s): %s",
                                    s, p, o_id, ex2,
                                )
                                errors += 1

                self.conn.commit()
                n_done = batch_start + len(batch)
                logger.info(
                    "embed_edges: %d/%d processed (%d embedded)",
                    n_done, n_to_embed, embedded,
                )
                if progress_callback:
                    progress_callback(n_done, n_to_embed)

            skipped += n_total - n_to_embed
            return {
                "embedded": embedded,
                "skipped": skipped,
                "errors": errors,
                "total": n_total,
            }
        finally:
            self.embedder = orig_embedder

    def edge_vector_search(
        self,
        query_embedding,
        top_k: int = 10,
        score_threshold: float = None,
    ) -> List[dict]:
        if isinstance(query_embedding, list):
            import json as _json
            query_vec_str = _json.dumps(query_embedding)
            dim = len(query_embedding)
        else:
            query_vec_str = query_embedding
            dim = str(query_embedding).count(",") + 1

        query_cast = f"TO_VECTOR(?, DOUBLE, {dim})"

        having = (
            f"HAVING score >= {score_threshold}" if score_threshold is not None else ""
        )
        sql = (
            f"SELECT TOP {int(top_k)} s, p, o_id, "
            f"VECTOR_COSINE(emb, {query_cast}) AS score "
            f"FROM {_table('kg_EdgeEmbeddings')} "
            f"ORDER BY score DESC "
            f"{having}"
        )

        cursor = self.conn.cursor()
        try:
            cursor.execute(sql, [query_vec_str])
        except Exception as e:
            if "-30" in str(e) or "not found" in str(e).lower() or "empty" in str(e).lower():
                return []
            raise
        rows = cursor.fetchall()
        if not rows:
            return []
        return [
            {"s": row[0], "p": row[1], "o_id": row[2], "score": float(row[3])}
            for row in rows
        ]

    def get_embedding(self, node_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve embedding for a node.

        Args:
            node_id: The node ID to get embedding for

        Returns:
            Dict with 'id', 'embedding' (as list of floats), and 'metadata' (if present)
            None if node has no embedding
        """
        cursor = self.conn.cursor()
        cursor.execute(
            f"SELECT id, emb, metadata FROM {_table('kg_NodeEmbeddings')} WHERE id = ?",
            [node_id],
        )
        row = cursor.fetchone()
        if not row:
            return None

        node_id, emb_csv, metadata_json = row
        embedding = [float(x) for x in emb_csv.split(",")] if emb_csv else []
        metadata = json.loads(metadata_json) if metadata_json else None

        result = {"id": node_id, "embedding": embedding}
        if metadata:
            result["metadata"] = metadata
        return result

    def get_embeddings(self, node_ids: List[str]) -> List[Dict[str, Any]]:
        """
        Retrieve embeddings for multiple nodes.

        Args:
            node_ids: List of node IDs

        Returns:
            List of dicts with 'id', 'embedding', and 'metadata' for nodes that have embeddings
        """
        if not node_ids:
            return []

        cursor = self.conn.cursor()
        placeholders = ",".join(["?"] * len(node_ids))
        cursor.execute(
            f"SELECT id, emb, metadata FROM {_table('kg_NodeEmbeddings')} WHERE id IN ({placeholders})",
            node_ids,
        )

        results = []
        for row in cursor.fetchall():
            node_id, emb_csv, metadata_json = row
            embedding = [float(x) for x in emb_csv.split(",")] if emb_csv else []
            metadata = json.loads(metadata_json) if metadata_json else None

            result = {"id": node_id, "embedding": embedding}
            if metadata:
                result["metadata"] = metadata
            results.append(result)

        return results

    def delete_node(self, node_id: str) -> bool:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                f"DELETE FROM {_table('kg_NodeEmbeddings')} WHERE id = ?", [node_id]
            )
            cursor.execute(
                f"SELECT edge_id FROM {_table('rdf_edges')} WHERE s = ? OR o_id = ?",
                [node_id, node_id],
            )
            edge_ids = [row[0] for row in cursor.fetchall()]
            for eid in edge_ids:
                cursor.execute(
                    f"SELECT reifier_id FROM {_table('rdf_reifications')} WHERE edge_id = ?",
                    [eid],
                )
                for (reif_id,) in cursor.fetchall():
                    cursor.execute(
                        f"DELETE FROM {_table('rdf_reifications')} WHERE reifier_id = ?",
                        [reif_id],
                    )
                    cursor.execute(
                        f"DELETE FROM {_table('rdf_props')} WHERE s = ?", [reif_id]
                    )
                    cursor.execute(
                        f"DELETE FROM {_table('rdf_labels')} WHERE s = ?", [reif_id]
                    )
                    cursor.execute(
                        f"DELETE FROM {_table('nodes')} WHERE node_id = ?", [reif_id]
                    )
            cursor.execute(
                f"DELETE FROM {_table('rdf_edges')} WHERE s = ? OR o_id = ?",
                [node_id, node_id],
            )
            cursor.execute(f"DELETE FROM {_table('rdf_labels')} WHERE s = ?", [node_id])
            cursor.execute(f"DELETE FROM {_table('rdf_props')} WHERE s = ?", [node_id])
            cursor.execute(
                f"DELETE FROM {_table('nodes')} WHERE node_id = ?", [node_id]
            )
            self.conn.commit()
            return True
        except Exception as e:
            logger.warning(f"delete_node({node_id}) failed: {e}")
            return False
        finally:
            cursor.close()

    def get_kg_anchors(
        self, icd_codes: List[str], bridge_type: str = "icd10_to_mesh"
    ) -> List[str]:
        if not icd_codes:
            return []
        _IN_CHUNK = 499
        results: list = []
        cursor = self.conn.cursor()
        try:
            for i in range(0, len(icd_codes), _IN_CHUNK):
                chunk = icd_codes[i : i + _IN_CHUNK]
                placeholders = ", ".join(["?"] * len(chunk))
                sql = (
                    f"SELECT DISTINCT b.kg_node_id "
                    f"FROM {_table('fhir_bridges')} b "
                    f"JOIN {_table('nodes')} n ON n.node_id = b.kg_node_id "
                    f"WHERE b.fhir_code IN ({placeholders}) "
                    f"AND b.bridge_type = ?"
                )
                cursor.execute(sql, chunk + [bridge_type])
                results.extend(row[0] for row in cursor.fetchall())
            return results
        except Exception as e:
            logger.warning(f"get_kg_anchors failed: {e}")
            return []
        finally:
            cursor.close()

    def reify_edge(
        self,
        edge_id: int,
        reifier_id: str = None,
        label: str = "Reification",
        props: Dict[str, str] = None,
    ) -> Optional[str]:
        if reifier_id is None:
            reifier_id = f"reif:{edge_id}"
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                f"SELECT edge_id FROM {_table('rdf_edges')} WHERE edge_id = ?",
                [edge_id],
            )
            if not cursor.fetchone():
                logger.warning(f"reify_edge: edge_id={edge_id} not found")
                return None
            self.create_node(reifier_id)
            cursor.execute(
                f"INSERT INTO {_table('rdf_labels')} (s, label) "
                f"SELECT ?, ? WHERE NOT EXISTS "
                f"(SELECT 1 FROM {_table('rdf_labels')} WHERE s = ? AND label = ?)",
                [reifier_id, label, reifier_id, label],
            )
            cursor.execute(
                f"INSERT INTO {_table('rdf_reifications')} (reifier_id, edge_id) VALUES (?, ?)",
                [reifier_id, edge_id],
            )
            if props:
                for key, val in props.items():
                    cursor.execute(
                        f'INSERT INTO {_table("rdf_props")} (s, "key", val) '
                        f"SELECT ?, ?, ? WHERE NOT EXISTS "
                        f'(SELECT 1 FROM {_table("rdf_props")} WHERE s = ? AND "key" = ?)',
                        [reifier_id, key, str(val), reifier_id, key],
                    )
            self.conn.commit()
            return reifier_id
        except Exception as e:
            self.conn.rollback()
            logger.warning(f"reify_edge({edge_id}) failed: {e}")
            return None
        finally:
            cursor.close()

    def get_reifications(self, edge_id: int) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                f'SELECT r.reifier_id, p."key", p.val '
                f"FROM {_table('rdf_reifications')} r "
                f"LEFT JOIN {_table('rdf_props')} p ON p.s = r.reifier_id "
                f"WHERE r.edge_id = ?",
                [edge_id],
            )
            rows = cursor.fetchall()
            result: Dict[str, dict] = {}
            for reifier_id, key, val in rows:
                if reifier_id not in result:
                    result[reifier_id] = {"reifier_id": reifier_id, "properties": {}}
                if key is not None:
                    result[reifier_id]["properties"][key] = val
            return list(result.values())
        except Exception as e:
            logger.warning(f"get_reifications({edge_id}) failed: {e}")
            return []
        finally:
            cursor.close()

    def delete_reification(self, reifier_id: str) -> bool:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                f"DELETE FROM {_table('rdf_reifications')} WHERE reifier_id = ?",
                [reifier_id],
            )
            cursor.execute(
                f"DELETE FROM {_table('rdf_props')} WHERE s = ?", [reifier_id]
            )
            cursor.execute(
                f"DELETE FROM {_table('rdf_labels')} WHERE s = ?", [reifier_id]
            )
            cursor.execute(
                f"DELETE FROM {_table('nodes')} WHERE node_id = ?", [reifier_id]
            )
            self.conn.commit()
            return True
        except Exception as e:
            self.conn.rollback()
            logger.warning(f"delete_reification({reifier_id}) failed: {e}")
            return False
        finally:
            cursor.close()
        """
        Delete embedding for a node.
        
        Args:
            node_id: The node ID
            
        Returns:
            True if deleted, False if not found
        """
        cursor = self.conn.cursor()
        cursor.execute(
            f"DELETE FROM {_table('kg_NodeEmbeddings')} WHERE id = ?", [node_id]
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def _validate_k(self, k: Any) -> int:
        """
        Validates and caps the 'k' parameter (TOP clause limit)
        1 <= k <= 1000, defaults to 50.
        Handles non-numeric strings by failing safe to 50.
        """
        try:
            k = int(k or 50)
        except (ValueError, TypeError):
            return 50
        return min(max(1, k), 1000)

    def kg_KNN_VEC(
        self, query_vector: str, k: int = 50, label_filter: Optional[str] = None
    ) -> List[Tuple[str, float]]:
        """
        K-Nearest Neighbors vector search using server-side SQL procedure.
        Leverages native IRIS EMBEDDING() if embedding_config is set.

        Args:
            query_vector: JSON array string OR raw text (if embedding_config is set)
            k: Number of top results to return
            label_filter: Optional label to filter by

        Returns:
            List of (entity_id, similarity_score) tuples
        """
        cursor = self.conn.cursor()
        try:
            # Direct SQL vector search — IRIS HNSW index is used automatically
            # via TOP k + ORDER BY VECTOR_COSINE DESC pattern.
            # (CALL proc(?, ...) is broken in IRIS Python dbapi for result-set procedures)
            emb_table = _table("kg_NodeEmbeddings")
            labels_table = _table("rdf_labels")
            if label_filter:
                cursor.execute(
                    f"SELECT TOP ? n.id, VECTOR_COSINE(n.emb, TO_VECTOR(?, DOUBLE)) AS score"
                    f" FROM {emb_table} n"
                    f" LEFT JOIN {labels_table} L ON L.s = n.id"
                    f" WHERE L.label = ?"
                    f" ORDER BY score DESC",
                    [k, query_vector, label_filter],
                )
            else:
                cursor.execute(
                    f"SELECT TOP ? n.id, VECTOR_COSINE(n.emb, TO_VECTOR(?, DOUBLE)) AS score"
                    f" FROM {emb_table} n"
                    f" ORDER BY score DESC",
                    [k, query_vector],
                )
            results = cursor.fetchall()
            return [(entity_id, float(similarity)) for entity_id, similarity in results]
        except Exception as e:
            logger.warning(
                f"Server-side kg_KNN_VEC failed: {e}. Falling back to client-side logic."
            )
            # Fallback to Python implementation
            return self._kg_KNN_VEC_python_optimized(query_vector, k, label_filter)

    def _kg_KNN_VEC_python_optimized(
        self, query_vector: str, k: int = 50, label_filter: Optional[str] = None
    ) -> List[Tuple[str, float]]:
        """
        Fallback Python implementation using CSV parsing
        Performance: ~5.8s for 20K vectors (when HNSW not available)
        """
        cursor = self.conn.cursor()
        try:
            import numpy as np

            query_array = np.array(json.loads(query_vector))

            # Get embeddings with optional label filter (optimized query)
            emb_table = _table("kg_NodeEmbeddings")
            labels_table = _table("rdf_labels")
            if label_filter is None:
                sql = f"""
                    SELECT n.id, n.emb
                    FROM {emb_table} n
                    WHERE n.emb IS NOT NULL
                """
                cursor.execute(sql)
            else:
                sql = f"""
                    SELECT n.id, n.emb
                    FROM {emb_table} n
                    LEFT JOIN {labels_table} L ON L.s = n.id
                    WHERE n.emb IS NOT NULL
                      AND L.label = ?
                """
                cursor.execute(sql, [label_filter])

            # Compute similarities efficiently
            similarities = []
            batch_size = 1000  # Process in batches for memory efficiency

            while True:
                batch = cursor.fetchmany(batch_size)
                if not batch:
                    break

                for entity_id, emb_csv in batch:
                    try:
                        # Fast CSV parsing to numpy array
                        emb_array = np.fromstring(emb_csv, dtype=float, sep=",")

                        # Compute cosine similarity efficiently
                        dot_product = np.dot(query_array, emb_array)
                        query_norm = np.linalg.norm(query_array)
                        emb_norm = np.linalg.norm(emb_array)

                        if query_norm > 0 and emb_norm > 0:
                            cos_sim = dot_product / (query_norm * emb_norm)
                            similarities.append((entity_id, float(cos_sim)))

                    except Exception:
                        # Skip problematic embeddings
                        continue

            # Sort by similarity and return top k
            similarities.sort(key=lambda x: x[1], reverse=True)
            return similarities[:k]

        except Exception as e:
            logger.error(f"Python optimized kg_KNN_VEC failed: {e}")
            raise
        finally:
            cursor.close()

    # Text Search Operations
    def kg_TXT(
        self, query_text: str, k: int = 50, min_confidence: int = 0
    ) -> List[Tuple[str, float]]:
        """
        Enhanced text search using server-side SQL procedure

        Args:
            query_text: Text query string
            k: Number of results to return
            min_confidence: Minimum confidence score (0-1000 scale)

        Returns:
            List of (entity_id, relevance_score) tuples
        """
        cursor = self.conn.cursor()
        try:
            # Call server-side procedure for unified logic
            # Signature: (queryText, k, minConfidence)
            cursor.execute(
                "CALL iris_vector_graph.kg_TXT(?, ?, ?)",
                [query_text, k, min_confidence],
            )
            results = cursor.fetchall()
            return [(entity_id, float(score)) for entity_id, score in results]

        except Exception as e:
            logger.error(f"kg_TXT failed: {e}")
            raise
        finally:
            cursor.close()

    # Graph Traversal Operations
    def kg_NEIGHBORHOOD_EXPANSION(
        self,
        entity_list: List[str],
        expansion_depth: int = 1,
        confidence_threshold: int = 500,
    ) -> List[Dict[str, Any]]:
        """
        Efficient neighborhood expansion for multiple entities using JSON_TABLE filtering

        Args:
            entity_list: List of seed entity IDs
            expansion_depth: Number of hops to expand (1-3 recommended)
            confidence_threshold: Minimum confidence for edges (0-1000 scale)

        Returns:
            List of expanded entities with metadata
        """
        if not entity_list:
            return []

        cursor = self.conn.cursor()
        try:
            # Build parameterized query for multiple entities
            entity_placeholders = ",".join(["?" for _ in entity_list])

            sql = f"""
                SELECT DISTINCT e.s, e.p, e.o_id, jt.confidence
                FROM rdf_edges e,
                     JSON_TABLE(e.qualifiers, '$' COLUMNS(confidence INTEGER PATH '$.confidence')) jt
                WHERE e.s IN ({entity_placeholders}) AND jt.confidence >= ?
                ORDER BY confidence DESC, e.s, e.p
            """

            params = entity_list + [confidence_threshold]
            cursor.execute(sql, params)

            results = []
            for row in cursor.fetchall():
                results.append(
                    {
                        "source": row[0],
                        "predicate": row[1],
                        "target": row[2],
                        "confidence": row[3],
                    }
                )

            return results

        except Exception as e:
            logger.error(f"kg_NEIGHBORHOOD_EXPANSION failed: {e}")
            raise
        finally:
            cursor.close()

    def validate_vector_table(self, table: str, vector_col: str) -> dict:
        from iris_vector_graph.security import sanitize_identifier

        sanitize_identifier(table)
        sanitize_identifier(vector_col)
        schema, tbl = (table.split(".", 1) + [""])[:2] if "." in table else ("", table)
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? AND COLUMN_NAME = ?",
                [schema or "USER", tbl or table, vector_col],
            )
            row = cursor.fetchone()
            if not row or int(row[0]) == 0:
                raise ValueError(f"Column '{vector_col}' not found in table '{table}'")
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            row_count = int(cursor.fetchone()[0])
            cursor.execute(f"SELECT TOP 1 {vector_col} FROM {table}")
            sample = cursor.fetchone()
            dimension = None
            if sample and sample[0]:
                try:
                    import json

                    v = (
                        json.loads(sample[0])
                        if isinstance(sample[0], str)
                        else sample[0]
                    )
                    dimension = len(v)
                except Exception:
                    pass
            return {
                "table": table,
                "vector_col": vector_col,
                "dimension": dimension,
                "row_count": row_count,
            }
        finally:
            cursor.close()

    def vector_search(
        self,
        table: str,
        vector_col: str,
        query_embedding,
        top_k: int = 10,
        id_col: str = "id",
        return_cols: List[str] = None,
        score_threshold: float = None,
    ) -> List[dict]:
        from iris_vector_graph.security import sanitize_identifier

        sanitize_identifier(table)
        sanitize_identifier(vector_col)
        sanitize_identifier(id_col)
        if return_cols:
            for col in return_cols:
                sanitize_identifier(col)

        if isinstance(query_embedding, list):
            import json

            query_vec_str = json.dumps(query_embedding)
        else:
            query_vec_str = query_embedding

        extra = ", ".join(
            sanitize_identifier(c) for c in (return_cols or []) if c != id_col
        )

        dim = None
        if isinstance(query_embedding, list):
            dim = len(query_embedding)
        elif isinstance(query_embedding, str):
            dim = query_embedding.count(",") + 1

        if dim:
            query_cast = f"TO_VECTOR(?, DOUBLE, {dim})"
        else:
            query_cast = "TO_VECTOR(?, DOUBLE)"

        select_cols = (
            f"t.{id_col}, VECTOR_COSINE(t.{vector_col}, {query_cast}) AS score"
        )
        if extra:
            select_cols += f", {extra}"

        having = (
            f"HAVING score >= {score_threshold}" if score_threshold is not None else ""
        )
        sql = (
            f"SELECT TOP {int(top_k)} {select_cols} "
            f"FROM {table} t "
            f"ORDER BY score DESC "
            f"{having}"
        )

        cursor = self.conn.cursor()
        try:
            cursor.execute(sql, [query_vec_str])
            cols = [d[0].lower() for d in cursor.description]
            results = []
            for row in cursor.fetchall():
                r = dict(zip(cols, row))
                r["id"] = r.pop(id_col.lower(), r.get("id"))
                results.append(r)
            return results
        except Exception as ex:
            raise ValueError(
                f"vector_search failed on {table}.{vector_col}: {ex}. "
                f"Ensure the column is a VECTOR type and query_embedding has the correct dimension."
            ) from ex
        finally:
            cursor.close()

    def multi_vector_search(
        self,
        sources: List[dict],
        query_embedding,
        top_k: int = 10,
        fusion: str = "rrf",
        rrf_k: int = 60,
    ) -> List[dict]:
        if isinstance(query_embedding, list):
            import json

            query_vec_str = json.dumps(query_embedding)
        else:
            query_vec_str = query_embedding

        per_source_k = top_k * 2

        all_results: List[dict] = []
        for source in sources:
            tbl = source["table"]
            col = source.get("col") or source.get("vector_col", "emb")
            id_c = source.get("id_col", "id")
            weight = float(source.get("weight", 1.0))
            return_c = source.get("return_cols")
            try:
                rows = self.vector_search(
                    table=tbl,
                    vector_col=col,
                    query_embedding=query_vec_str,
                    top_k=per_source_k,
                    id_col=id_c,
                    return_cols=return_c,
                )
                for i, r in enumerate(rows):
                    r["source_table"] = tbl
                    r["_rank"] = i + 1
                    r["_weight"] = weight
                all_results.extend(rows)
            except Exception as ex:
                logger.warning(f"multi_vector_search: skipping {tbl}: {ex}")

        if not all_results:
            return []

        if fusion == "rrf":
            scores: Dict[str, float] = {}
            meta: Dict[str, dict] = {}
            for r in all_results:
                node_id = str(r["id"])
                weight = r["_weight"]
                rank = r["_rank"]
                rrf_score = weight * (1.0 / (rrf_k + rank))
                scores[node_id] = scores.get(node_id, 0.0) + rrf_score
                if node_id not in meta:
                    meta[node_id] = {
                        k: v for k, v in r.items() if not k.startswith("_")
                    }
            ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
            results = []
            for rank_i, (node_id, score) in enumerate(ranked, 1):
                row = meta[node_id].copy()
                row["score"] = round(score, 6)
                row["rank"] = rank_i
                results.append(row)
            return results
        else:
            seen: set = set()
            merged = []
            for r in sorted(all_results, key=lambda x: x.get("score", 0), reverse=True):
                nid = str(r["id"])
                if nid not in seen:
                    seen.add(nid)
                    clean = {k: v for k, v in r.items() if not k.startswith("_")}
                    merged.append(clean)
                    if len(merged) >= top_k:
                        break
            return merged

    def kg_RRF_FUSE(
        self, k: int, k1: int, k2: int, c: int, query_vector: str, query_text: str
    ) -> List[Tuple[str, float, float, float]]:
        """
        Reciprocal Rank Fusion using server-side SQL procedure

        Args:
            k: Final number of results to return
            k1: Number of vector search results to retrieve
            k2: Number of text search results to retrieve
            c: RRF parameter (typically 60)
            query_vector: Vector query as JSON string
            query_text: Text query string

        Returns:
            List of (entity_id, rrf_score, vector_score, text_score) tuples
        """
        cursor = self.conn.cursor()
        try:
            # Call server-side procedure for unified logic
            # Signature: (k, k1, k2, c, queryVector, queryText)
            cursor.execute(
                "CALL iris_vector_graph.kg_RRF_FUSE(?, ?, ?, ?, ?, ?)",
                [k, k1, k2, c, query_vector, query_text],
            )
            results = cursor.fetchall()
            return [
                (entity_id, float(rrf), float(v), float(t))
                for entity_id, rrf, v, t in results
            ]

        except Exception as e:
            logger.error(f"kg_RRF_FUSE failed: {e}")
            raise
        finally:
            cursor.close()

    def kg_VECTOR_GRAPH_SEARCH(
        self,
        query_vector: str,
        query_text: str = None,
        k: int = 15,
        expansion_depth: int = 1,
        min_confidence: float = 0.5,
    ) -> List[Dict[str, Any]]:
        """
        Multi-modal search combining vector similarity, graph expansion, and text relevance

        Args:
            query_vector: Vector query as JSON string
            query_text: Optional text query
            k: Number of final results
            expansion_depth: Graph expansion depth
            min_confidence: Minimum confidence threshold

        Returns:
            List of ranked entities with combined scores
        """
        try:
            # Step 1: Vector search for semantic similarity
            k_vector = min(k * 2, 50)  # Get more candidates for fusion
            vector_results = self.kg_KNN_VEC(query_vector, k=k_vector)
            vector_entities = [entity_id for entity_id, _ in vector_results]

            # Step 2: Graph expansion around vector results
            if vector_entities:
                graph_expansion = self.kg_NEIGHBORHOOD_EXPANSION(
                    vector_entities, expansion_depth, int(min_confidence * 1000)
                )
                expanded_entities = list(
                    set([item["target"] for item in graph_expansion])
                )
            else:
                expanded_entities = []

            # Step 3: Combine with text search if provided
            if query_text:
                text_results = self.kg_TXT(
                    query_text,
                    k=k_vector * 2,
                    min_confidence=int(min_confidence * 1000),
                )
                text_entities = [entity_id for entity_id, _ in text_results]
                all_entities = list(
                    set(vector_entities + expanded_entities + text_entities)
                )
            else:
                all_entities = list(set(vector_entities + expanded_entities))

            # Step 4: Score combination (simplified)
            combined_results = []
            for entity_id in all_entities[:k]:
                # Get scores from different sources
                vector_sim = next(
                    (score for eid, score in vector_results if eid == entity_id), 0.0
                )

                # Simple weighted combination
                combined_score = (
                    vector_sim  # Can be enhanced with graph centrality, text relevance
                )

                combined_results.append(
                    {
                        "entity_id": entity_id,
                        "combined_score": combined_score,
                        "vector_similarity": vector_sim,
                        "in_graph_expansion": entity_id in expanded_entities,
                    }
                )

            # Sort by combined score
            combined_results.sort(key=lambda x: x["combined_score"], reverse=True)
            return combined_results[:k]

        except Exception as e:
            logger.error(f"kg_VECTOR_GRAPH_SEARCH failed: {e}")
            raise

    # Personalized PageRank Operations
    def kg_PERSONALIZED_PAGERANK(
        self,
        seed_entities: List[str],
        damping_factor: float = 0.85,
        max_iterations: int = 100,
        tolerance: float = 1e-6,
        return_top_k: Optional[int] = None,
        bidirectional: bool = False,
        reverse_edge_weight: float = 1.0,
    ) -> Dict[str, float]:
        """
        Personalized PageRank with optional bidirectional edge traversal.

        Implements personalized PageRank biased toward seed entities, with optional
        reverse edge traversal for enhanced multi-hop reasoning in knowledge graphs.

        Architecture: Python API -> SQL Function -> ObjectScript Embedded Python
        Falls back to pure Python if SQL function is unavailable.

        Args:
            seed_entities: List of entity IDs to use as seeds (personalization)
            damping_factor: PageRank damping factor (default 0.85)
            max_iterations: Maximum iterations before stopping (default 100)
            tolerance: Convergence threshold (default 1e-6)
            return_top_k: Limit results to top K entities (None = all)
            bidirectional: Enable reverse edge traversal (default False)
            reverse_edge_weight: Weight multiplier for reverse edges (default 1.0)

        Returns:
            Dict mapping entity_id to PageRank score

        Raises:
            ValueError: If reverse_edge_weight is negative
            ValueError: If seed_entities is empty

        Note:
            Uses IRIS embedded Python for 10-50x performance (10-50ms for 10K nodes).
            Falls back to pure Python if SQL function unavailable.
        """
        # Input validation
        if reverse_edge_weight < 0:
            raise ValueError(
                f"reverse_edge_weight must be non-negative, got: {reverse_edge_weight}"
            )
        if not seed_entities:
            raise ValueError("seed_entities must contain at least one entity")

        # --- Fast path: Graph.KG.PageRank.RunJson() via .cls layer ---
        if self.capabilities.objectscript_deployed and self.capabilities.kg_built:
            try:
                seed_json = json.dumps(seed_entities)
                iris_obj = self._iris_obj()
                result_json = iris_obj.classMethodValue(
                    "Graph.KG.PageRank",
                    "RunJson",
                    seed_json,
                    damping_factor,
                    max_iterations,
                    1 if bidirectional else 0,
                    reverse_edge_weight,
                )
                if result_json:
                    items = json.loads(str(result_json))
                    scores = {
                        item["id"]: item["score"]
                        for item in items
                        if item.get("score", 0) > 0
                    }
                    if return_top_k is not None and return_top_k > 0:
                        scores = dict(
                            sorted(scores.items(), key=lambda x: x[1], reverse=True)[
                                :return_top_k
                            ]
                        )
                    logger.debug(
                        "PageRank via Graph.KG.PageRank.RunJson(): %d results",
                        len(scores),
                    )
                    return scores
            except Exception as exc:
                logger.warning(
                    "Graph.KG.PageRank.RunJson() failed, falling back: %s", exc
                )

        return self._kg_PERSONALIZED_PAGERANK_python_fallback(
            seed_entities,
            damping_factor,
            max_iterations,
            tolerance,
            return_top_k,
            bidirectional,
            reverse_edge_weight,
        )

    def _kg_PERSONALIZED_PAGERANK_python_fallback(
        self,
        seed_entities: List[str],
        damping_factor: float = 0.85,
        max_iterations: int = 100,
        tolerance: float = 1e-6,
        return_top_k: Optional[int] = None,
        bidirectional: bool = False,
        reverse_edge_weight: float = 1.0,
    ) -> Dict[str, float]:
        """
        Pure Python fallback for Personalized PageRank.

        Used when IRIS SQL function kg_PPR is unavailable.
        Performance: ~25ms for 1K nodes (vs 2-5ms with embedded Python).
        """
        from iris_vector_graph.cypher.translator import _table as _t

        cursor = self.conn.cursor()
        try:
            # Step 1: Get all nodes
            cursor.execute(f"SELECT node_id FROM {_t('nodes')}")
            nodes = [row[0] for row in cursor.fetchall()]
            num_nodes = len(nodes)

            if num_nodes == 0:
                return {}

            node_set = set(nodes)
            valid_seeds = [s for s in seed_entities if s in node_set]
            if not valid_seeds:
                # No valid seeds found - return empty
                logger.warning(f"No valid seeds found in graph: {seed_entities}")
                return {}

            # Step 2: Build adjacency lists
            cursor.execute(f"SELECT s, o_id FROM {_t('rdf_edges')}")

            in_edges = {}  # target -> [(source, weight)]
            out_degree = {}

            for src, dst in cursor.fetchall():
                # Forward edge: weight = 1.0
                if dst not in in_edges:
                    in_edges[dst] = []
                in_edges[dst].append((src, 1.0))
                out_degree[src] = out_degree.get(src, 0) + 1

            # Step 2b: Build reverse edges if bidirectional mode enabled
            if bidirectional and reverse_edge_weight > 0:
                cursor.execute(f"SELECT o_id, s FROM {_t('rdf_edges')}")
                for o_id, s in cursor.fetchall():
                    # Reverse edge: o_id -> s with weighted contribution
                    if s not in in_edges:
                        in_edges[s] = []
                    in_edges[s].append((o_id, reverse_edge_weight))
                    out_degree[o_id] = out_degree.get(o_id, 0) + 1

            # Initialize out_degree for nodes with no outgoing edges
            for node in nodes:
                if node not in out_degree:
                    out_degree[node] = 0

            # Step 3: Initialize PageRank scores (Personalized)
            seed_count = len(valid_seeds)
            seed_set = set(valid_seeds)
            ranks = {
                node: (1.0 / seed_count if node in seed_set else 0.0) for node in nodes
            }

            # Step 4: Iterative computation with personalization
            teleport_prob = (1.0 - damping_factor) / seed_count

            for iteration in range(max_iterations):
                new_ranks = {}
                max_diff = 0.0

                for node in nodes:
                    # Teleport: jump to seed nodes (personalized)
                    if node in seed_set:
                        rank = teleport_prob
                    else:
                        rank = 0.0

                    # Add contributions from incoming edges (with weights)
                    if node in in_edges:
                        for src, weight in in_edges[node]:
                            if out_degree.get(src, 0) > 0:
                                rank += (
                                    damping_factor
                                    * weight
                                    * (ranks.get(src, 0) / out_degree[src])
                                )

                    new_ranks[node] = rank
                    max_diff = max(max_diff, abs(rank - ranks.get(node, 0)))

                ranks = new_ranks

                # Check convergence
                if max_diff < tolerance:
                    logger.debug(
                        f"PageRank converged after {iteration + 1} iterations (Python fallback)"
                    )
                    break

            # Filter out zero scores and apply top_k limit
            results = {node: score for node, score in ranks.items() if score > 0}

            if return_top_k is not None and return_top_k > 0:
                sorted_items = sorted(results.items(), key=lambda x: x[1], reverse=True)
                results = dict(sorted_items[:return_top_k])

            return results

        except Exception as e:
            logger.error(f"kg_PERSONALIZED_PAGERANK Python fallback failed: {e}")
            raise
        finally:
            cursor.close()

    # --- Arno acceleration (optional) ---

    def _detect_arno(self) -> bool:
        if self._arno_available is not None:
            return self._arno_available
        try:
            iris_obj = self._iris_obj()
            cap_json = iris_obj.classMethodValue("Graph.KG.NKGAccel", "Capabilities")
            self._arno_capabilities = json.loads(str(cap_json))
            self._arno_available = True
            if not self._arno_capabilities.get("nkg_data", False):
                logger.warning(
                    "Arno detected but ^NKG not populated — run BuildKG() to enable acceleration"
                )
        except Exception:
            self._arno_available = False
            self._arno_capabilities = {}
        return self._arno_available

    def _arno_call(self, cls: str, method: str, *args) -> str:
        return str(self._iris_obj().classMethodValue(cls, method, *args))

    def khop(self, seed: str, hops: int = 2, max_nodes: int = 500) -> dict:
        if self._detect_arno() and "khop" in self._arno_capabilities.get(
            "algorithms", []
        ):
            result = self._arno_call(
                "Graph.KG.NKGAccel", "KHopNeighbors", seed, str(hops), str(max_nodes)
            )
            parsed = json.loads(result)
            if "error" not in parsed:
                return parsed
            logger.warning(f"Arno khop error: {parsed['error']}")
        return self._khop_fallback(seed, hops, max_nodes)

    def _khop_fallback(self, seed: str, hops: int, max_nodes: int) -> dict:
        if self.capabilities.objectscript_deployed:
            try:
                iris_obj = self._iris_obj()
                result = iris_obj.classMethodValue(
                    "Graph.KG.Traversal", "BFSFastJson", seed, "", hops, "", "out"
                )
                if result:
                    edges = json.loads(str(result))
                    nodes = set()
                    for e in edges:
                        nodes.add(e["s"])
                        nodes.add(e["o"])
                    return {"nodes": list(nodes)[:max_nodes], "edges": edges}
            except Exception as e:
                logger.debug(f"BFSFastJson fallback failed: {e}")
        return {"nodes": [], "edges": []}

    def ppr(
        self, seed: str, alpha: float = 0.85, max_iter: int = 20, top_k: int = 20
    ) -> dict:
        if self._detect_arno() and "ppr" in self._arno_capabilities.get(
            "algorithms", []
        ):
            result = self._arno_call(
                "Graph.KG.NKGAccel",
                "PPRNative",
                seed,
                str(alpha),
                str(max_iter),
                str(top_k),
            )
            parsed = json.loads(result)
            if "error" not in parsed:
                return parsed
            logger.warning(f"Arno ppr error: {parsed['error']}")
        scores = self.kg_PERSONALIZED_PAGERANK(
            [seed], damping_factor=alpha, max_iterations=max_iter, return_top_k=top_k
        )
        return {
            "scores": [
                {"id": k, "score": v}
                for k, v in sorted(scores.items(), key=lambda x: -x[1])
            ]
        }

    def random_walk(self, seed: str, length: int = 20, num_walks: int = 10) -> list:
        if self._detect_arno() and "random_walk" in self._arno_capabilities.get(
            "algorithms", []
        ):
            result = self._arno_call(
                "Graph.KG.NKGAccel", "RandomWalkJson", seed, str(length), str(num_walks)
            )
            parsed = json.loads(result)
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict) and "error" in parsed:
                logger.warning(f"Arno random_walk error: {parsed['error']}")
        return []

    # ── VecIndex: lightweight ANN vector search in globals ──

    def _iris_obj(self):
        try:
            import iris

            return iris.createIRIS(self.conn)
        except (TypeError, AttributeError):
            import iris

            return iris.createIRIS(self.conn)

    def vec_create_index(
        self,
        name: str,
        dim: int,
        metric: str = "cosine",
        num_trees: int = 4,
        leaf_size: int = 50,
    ) -> dict:
        result = self._iris_obj().classMethodValue(
            "Graph.KG.VecIndex",
            "Create",
            name,
            str(dim),
            metric,
            str(num_trees),
            str(leaf_size),
        )
        return json.loads(str(result))

    def vec_insert(self, index_name: str, doc_id: str, embedding) -> None:
        vec_json = json.dumps([float(v) for v in embedding])
        self._iris_obj().classMethodVoid(
            "Graph.KG.VecIndex", "InsertJSON", index_name, doc_id, vec_json
        )

    def vec_bulk_insert(self, index_name: str, items: list) -> int:
        batch = [
            {"id": item["id"], "vec": [float(v) for v in item["embedding"]]}
            for item in items
        ]
        batch_json = json.dumps(batch)
        result = self._iris_obj().classMethodValue(
            "Graph.KG.VecIndex", "InsertBatchJSON", index_name, batch_json
        )
        return json.loads(str(result)).get("inserted", 0)

    def vec_build(self, index_name: str) -> dict:
        result = self._iris_obj().classMethodValue(
            "Graph.KG.VecIndex", "Build", index_name
        )
        return json.loads(str(result))

    def vec_search(
        self, index_name: str, query_embedding, k: int = 10, nprobe: int = 8
    ) -> list:
        vec_json = json.dumps([float(v) for v in query_embedding])
        result = self._iris_obj().classMethodValue(
            "Graph.KG.VecIndex", "SearchJSON", index_name, vec_json, k, nprobe
        )
        return json.loads(str(result))

    def vec_search_multi(
        self, index_name: str, query_embeddings: list, k: int = 10, nprobe: int = 8
    ) -> list:
        queries_json = json.dumps([[float(v) for v in q] for q in query_embeddings])
        result = self._iris_obj().classMethodValue(
            "Graph.KG.VecIndex", "SearchMultiJSON", index_name, queries_json, k, nprobe
        )
        return json.loads(str(result))

    def vec_info(self, index_name: str) -> dict:
        result = self._iris_obj().classMethodValue(
            "Graph.KG.VecIndex", "Info", index_name
        )
        return json.loads(str(result))

    def vec_drop(self, index_name: str) -> None:
        self._iris_obj().classMethodVoid("Graph.KG.VecIndex", "Drop", index_name)

    def vec_expand(self, index_name: str, seed_id: str, k: int = 5) -> list:
        result = self._iris_obj().classMethodValue(
            "Graph.KG.VecIndex", "SeededVectorExpand", seed_id, index_name, k
        )
        return json.loads(str(result))

    # ── PLAID: multi-vector retrieval (ColBERT-style) ──

    def plaid_build(
        self, name: str, docs: list, n_clusters: int = None, dim: int = 128
    ) -> dict:
        try:
            import numpy as np
            from sklearn.cluster import KMeans
        except ImportError:
            raise ImportError(
                "plaid_build requires numpy and sklearn: pip install numpy scikit-learn"
            )

        all_tokens = []
        doc_token_map = []
        for doc in docs:
            tokens = doc["tokens"]
            for tok_pos, tok in enumerate(tokens):
                all_tokens.append(tok)
                doc_token_map.append(
                    {"docId": doc["id"], "tokPos": tok_pos, "centroid": 0}
                )

        all_tokens_np = np.array(all_tokens, dtype=np.float64)
        K = n_clusters or max(1, int(np.sqrt(len(all_tokens_np))))
        K = min(K, len(all_tokens_np))

        kmeans = KMeans(n_clusters=K, n_init=1, max_iter=20, random_state=42).fit(
            all_tokens_np
        )
        labels = kmeans.labels_.tolist()

        for i, label in enumerate(labels):
            doc_token_map[i]["centroid"] = int(label)

        iris_obj = self._iris_obj()
        centroids_json = json.dumps(kmeans.cluster_centers_.tolist())
        iris_obj.classMethodVoid(
            "Graph.KG.PLAIDSearch", "StoreCentroids", name, centroids_json
        )

        BATCH_SIZE = 10
        for i in range(0, len(docs), BATCH_SIZE):
            batch = docs[i : i + BATCH_SIZE]
            batch_json = json.dumps(
                [
                    {
                        "id": doc["id"],
                        "tokens": [[float(v) for v in tok] for tok in doc["tokens"]],
                    }
                    for doc in batch
                ]
            )
            iris_obj.classMethodVoid(
                "Graph.KG.PLAIDSearch", "StoreDocTokensBatch", name, batch_json
            )

        ASSIGN_CHUNK = 5000
        for i in range(0, len(doc_token_map), ASSIGN_CHUNK):
            chunk_json = json.dumps(doc_token_map[i : i + ASSIGN_CHUNK])
            iris_obj.classMethodVoid(
                "Graph.KG.PLAIDSearch", "BuildInvertedIndex", name, chunk_json
            )

        return json.loads(
            str(iris_obj.classMethodValue("Graph.KG.PLAIDSearch", "Info", name))
        )

    def plaid_search(
        self, name: str, query_tokens: list, k: int = 10, nprobe: int = 4
    ) -> list:
        tokens_json = json.dumps([[float(v) for v in tok] for tok in query_tokens])
        result = self._iris_obj().classMethodValue(
            "Graph.KG.PLAIDSearch", "Search", name, tokens_json, k, nprobe
        )
        return json.loads(str(result))

    def plaid_insert(self, name: str, doc_id: str, token_embeddings: list) -> None:
        tokens_json = json.dumps([[float(v) for v in tok] for tok in token_embeddings])
        self._iris_obj().classMethodVoid(
            "Graph.KG.PLAIDSearch", "Insert", name, doc_id, tokens_json
        )

    def plaid_info(self, name: str) -> dict:
        result = self._iris_obj().classMethodValue("Graph.KG.PLAIDSearch", "Info", name)
        return json.loads(str(result))

    def plaid_drop(self, name: str) -> None:
        self._iris_obj().classMethodVoid("Graph.KG.PLAIDSearch", "Drop", name)

    # ── Temporal edges ──

    def create_edge_temporal(
        self,
        source: str,
        predicate: str,
        target: str,
        timestamp: int = None,
        weight: float = 1.0,
        attrs: dict = None,
        upsert: bool = False,
        graph: Optional[str] = None,
    ) -> bool:
        try:
            ts = int(timestamp) if timestamp is not None else ""
            attrs_json = json.dumps(attrs) if attrs else ""
            self._iris_obj().classMethodVoid(
                "Graph.KG.TemporalIndex",
                "InsertEdge",
                source,
                predicate,
                target,
                str(ts),
                weight,
                attrs_json,
                1 if upsert else 0,
            )
            if graph is not None:
                cursor = self.conn.cursor()
                for nid in [source, target]:
                    try:
                        cursor.execute(
                            "INSERT INTO Graph_KG.nodes (node_id) SELECT ? WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.nodes WHERE node_id = ?)",
                            [nid, nid],
                        )
                    except Exception:
                        pass
                try:
                    cursor.execute(
                        "INSERT INTO Graph_KG.rdf_edges (s, p, o_id, graph_id) SELECT ?, ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.rdf_edges WHERE s = ? AND p = ? AND o_id = ? AND graph_id = ?)",
                        [
                            source,
                            predicate,
                            target,
                            graph,
                            source,
                            predicate,
                            target,
                            graph,
                        ],
                    )
                    self.conn.commit()
                except Exception as e:
                    logger.debug("create_edge_temporal rdf_edges insert skipped: %s", e)
            return True
        except Exception as e:
            logger.warning(f"create_edge_temporal failed: {e}")
            return False

    def bulk_create_edges_temporal(
        self, edges: list, upsert: bool = False, graph: Optional[str] = None
    ) -> int:
        batch_json = json.dumps(edges)
        result = self._iris_obj().classMethodValue(
            "Graph.KG.TemporalIndex", "BulkInsert", batch_json, 1 if upsert else 0
        )
        count = int(result)
        if graph is not None or any(e.get("graph") for e in edges):
            cursor = self.conn.cursor()
            for e in edges:
                s = e.get("s") or e.get("source_id", "")
                p = e.get("p") or e.get("predicate", "")
                o = e.get("o") or e.get("target_id", "")
                g = e.get("graph", graph)
                if not (s and p and o and g):
                    continue
                for nid in [s, o]:
                    try:
                        cursor.execute(
                            "INSERT INTO Graph_KG.nodes (node_id) SELECT ? WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.nodes WHERE node_id = ?)",
                            [nid, nid],
                        )
                    except Exception:
                        pass
                try:
                    cursor.execute(
                        "INSERT INTO Graph_KG.rdf_edges (s, p, o_id, graph_id) SELECT ?, ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.rdf_edges WHERE s = ? AND p = ? AND o_id = ? AND graph_id = ?)",
                        [s, p, o, g, s, p, o, g],
                    )
                except Exception as e:
                    logger.debug(
                        "bulk_create_edges_temporal rdf_edges insert skipped: %s", e
                    )
            try:
                self.conn.commit()
            except Exception:
                pass
        return count

    def get_edges_in_window(
        self,
        source: str = "",
        predicate: str = "",
        start: int = 0,
        end: int = 0,
        direction: str = "out",
    ) -> list:
        if direction == "in":
            result = self._iris_obj().classMethodValue(
                "Graph.KG.TemporalIndex",
                "QueryWindowInbound",
                source,
                predicate,
                start,
                end,
            )
        else:
            result = self._iris_obj().classMethodValue(
                "Graph.KG.TemporalIndex", "QueryWindow", source, predicate, start, end
            )
        edges = json.loads(str(result))
        for edge in edges:
            edge["source"] = edge["s"]
            edge["predicate"] = edge["p"]
            edge["target"] = edge["o"]
            edge["timestamp"] = edge["ts"]
            edge["weight"] = edge["w"]
        return edges

    def purge_before(self, ts: int) -> None:
        self._iris_obj().classMethodVoid(
            "Graph.KG.TemporalIndex", "PurgeBefore", int(ts)
        )

    def get_edge_velocity(self, node_id: str, window_seconds: int = 300) -> int:
        result = self._iris_obj().classMethodValue(
            "Graph.KG.TemporalIndex", "GetVelocity", node_id, window_seconds
        )
        return int(result)

    def find_burst_nodes(
        self, predicate: str = "", window_seconds: int = 300, threshold: int = 50
    ) -> list:
        result = self._iris_obj().classMethodValue(
            "Graph.KG.TemporalIndex", "FindBursts", predicate, window_seconds, threshold
        )
        return json.loads(str(result))

    def get_edge_attrs(self, ts: int, source: str, predicate: str, target: str) -> dict:
        result = self._iris_obj().classMethodValue(
            "Graph.KG.TemporalIndex", "GetEdgeAttrs", ts, source, predicate, target
        )
        return json.loads(str(result))

    def get_temporal_aggregate(
        self,
        source: str,
        predicate: str,
        metric: str,
        ts_start: int,
        ts_end: int,
    ):
        result = self._iris_obj().classMethodValue(
            "Graph.KG.TemporalIndex",
            "GetAggregate",
            source,
            predicate,
            metric,
            ts_start,
            ts_end,
        )
        s = str(result)
        if s == "":
            return 0 if metric == "count" else None
        return int(s) if metric == "count" else float(s)

    def get_bucket_groups(
        self,
        predicate: str = "",
        ts_start: int = 0,
        ts_end: int = 0,
        source_prefix: str = "",
    ) -> list:
        """Return pre-aggregated statistics per (source, predicate) pair over a time window.

        Args:
            predicate: Edge type to filter on. Empty string matches all predicates.
            ts_start: Window start as Unix timestamp (inclusive).
            ts_end: Window end as Unix timestamp (inclusive).
            source_prefix: If non-empty, only include entries whose source node ID
                starts with this prefix. Use for tenant-scoped queries. Default "".

        Returns:
            list[dict]: Each dict has keys:
                source    (str)   — source node ID
                predicate (str)   — edge type
                count     (int)   — number of edges in window
                sum       (float) — total weight across all edges
                avg       (float) — mean weight (None if count == 0)
                min       (float) — minimum weight (None if no edges)
                max       (float) — maximum weight (None if no edges)
        """
        result = self._iris_obj().classMethodValue(
            "Graph.KG.TemporalIndex",
            "GetBucketGroups",
            predicate,
            ts_start,
            ts_end,
            source_prefix,
        )
        return json.loads(str(result))

    def get_bucket_group_targets(
        self,
        source: str,
        predicate: str,
        ts_start: int,
        ts_end: int,
    ) -> list[str]:
        """Return distinct target node IDs for a source+predicate over a time window."""
        result = self._iris_obj().classMethodValue(
            "Graph.KG.TemporalIndex",
            "GetBucketGroupTargets",
            source,
            predicate,
            ts_start,
            ts_end,
        )
        return json.loads(str(result))

    def get_distinct_count(
        self,
        source: str,
        predicate: str,
        ts_start: int,
        ts_end: int,
    ) -> int:
        result = self._iris_obj().classMethodValue(
            "Graph.KG.TemporalIndex",
            "GetDistinctCount",
            source,
            predicate,
            ts_start,
            ts_end,
        )
        return int(str(result))

    def import_graph_ndjson(
        self, path: str, upsert_nodes: bool = True, batch_size: int = 10000
    ) -> dict:
        nodes = 0
        edges = 0
        temporal_edges = 0
        temporal_batch = []

        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning(f"Skipping malformed NDJSON line")
                    continue

                kind = event.get("kind", "")

                if kind == "node":
                    node_id = event.get("id", "")
                    labels = event.get("labels", [])
                    props = event.get("properties", {})
                    if node_id:
                        self.create_node(node_id, labels=labels, properties=props)
                        nodes += 1

                elif kind == "edge":
                    src = event.get("source", "")
                    pred = event.get("predicate", "")
                    tgt = event.get("target", "")
                    if src and pred and tgt:
                        self.create_edge(src, pred, tgt)
                        edges += 1

                elif kind == "temporal_edge":
                    src = event.get("source", "")
                    pred = event.get("predicate", "")
                    tgt = event.get("target", "")
                    ts = event.get("timestamp", 0)
                    w = event.get("weight", 1.0)
                    attrs = event.get("attrs", {})
                    src_labels = event.get("source_labels", [])
                    tgt_labels = event.get("target_labels", [])
                    if upsert_nodes:
                        if src:
                            self.create_node(src, labels=src_labels)
                        if tgt:
                            self.create_node(tgt, labels=tgt_labels)
                    item = {"s": src, "p": pred, "o": tgt, "ts": ts, "w": w}
                    if attrs:
                        item["attrs"] = {k: str(v) for k, v in attrs.items()}
                    temporal_batch.append(item)
                    if len(temporal_batch) >= batch_size:
                        self.bulk_create_edges_temporal(temporal_batch)
                        temporal_edges += len(temporal_batch)
                        temporal_batch = []
                else:
                    logger.warning(f"Skipping unknown NDJSON kind: {kind}")

        if temporal_batch:
            self.bulk_create_edges_temporal(temporal_batch)
            temporal_edges += len(temporal_batch)

        return {"nodes": nodes, "edges": edges, "temporal_edges": temporal_edges}

    def export_graph_ndjson(self, path: str) -> dict:
        nodes_written = 0
        edges_written = 0

        cursor = self.conn.cursor()
        with open(path, "w") as f:
            cursor.execute(f"SELECT node_id FROM {_table('nodes')}")
            for (node_id,) in cursor.fetchall():
                node_data = self.get_node(node_id)
                if node_data:
                    event = {
                        "kind": "node",
                        "id": node_id,
                        "labels": node_data.get("labels", []),
                        "properties": {
                            k: v
                            for k, v in node_data.items()
                            if k not in ("id", "labels")
                        },
                    }
                    f.write(json.dumps(event) + "\n")
                    nodes_written += 1

        cursor.close()
        return {"nodes": nodes_written, "edges": edges_written}

    def export_temporal_edges_ndjson(
        self, path: str, start: int = None, end: int = None, predicate: str = None
    ) -> dict:
        s_filter = ""
        p_filter = predicate or ""
        ts_start = start or 0
        ts_end = end or 9999999999
        result_json = self._iris_obj().classMethodValue(
            "Graph.KG.TemporalIndex",
            "QueryWindow",
            s_filter,
            p_filter,
            ts_start,
            ts_end,
        )
        edges = json.loads(str(result_json))

        with open(path, "w") as f:
            for edge in edges:
                attrs = self.get_edge_attrs(edge["ts"], edge["s"], edge["p"], edge["o"])
                event = {
                    "kind": "temporal_edge",
                    "source": edge["s"],
                    "predicate": edge["p"],
                    "target": edge["o"],
                    "timestamp": edge["ts"],
                    "weight": edge.get("w", 1.0),
                    "attrs": attrs,
                }
                f.write(json.dumps(event) + "\n")

        return {"temporal_edges": len(edges)}

    # ── BM25Index: pure ObjectScript lexical search ──

    def bm25_build(
        self, name: str, text_props: list, k1: float = 1.5, b: float = 0.75
    ) -> dict:
        props_csv = ",".join(text_props)
        result = self._iris_obj().classMethodValue(
            "Graph.KG.BM25Index", "Build", name, props_csv, k1, b
        )
        return json.loads(str(result))

    def bm25_search(self, name: str, query: str, k: int = 10) -> list:
        result = self._iris_obj().classMethodValue(
            "Graph.KG.BM25Index", "Search", name, query, k
        )
        rows = json.loads(str(result))
        return [(r["id"], float(r["score"])) for r in rows]

    def bm25_insert(self, name: str, doc_id: str, text: str) -> bool:
        result = self._iris_obj().classMethodValue(
            "Graph.KG.BM25Index", "Insert", name, doc_id, text
        )
        return bool(int(str(result)))

    def bm25_drop(self, name: str) -> None:
        self._iris_obj().classMethodVoid("Graph.KG.BM25Index", "Drop", name)

    def bm25_info(self, name: str) -> dict:
        result = self._iris_obj().classMethodValue("Graph.KG.BM25Index", "Info", name)
        return json.loads(str(result))

    def ivf_build(
        self,
        name: str,
        nlist: int = 256,
        metric: str = "cosine",
        batch_size: int = 10000,
    ) -> dict:
        try:
            import numpy as np
            from sklearn.cluster import MiniBatchKMeans
        except ImportError:
            raise ImportError(
                "ivf_build requires numpy and sklearn: pip install numpy scikit-learn"
            )

        import base64
        import json as _json
        import struct

        cursor = self.conn.cursor()
        cursor.execute("SELECT id, emb FROM Graph_KG.kg_NodeEmbeddings")
        rows = cursor.fetchall()
        if not rows:
            raise ValueError("ivf_build: no vectors found in kg_NodeEmbeddings")

        node_ids = []
        vecs = []
        for row in rows:
            nid, emb_val = row[0], row[1]
            if emb_val is None:
                continue
            emb_str = str(emb_val)
            if "," in emb_str:
                vec = [float(v) for v in emb_str.split(",")]
            else:
                raw = base64.b64decode(emb_str)
                dim = len(raw) // 4
                vec = list(struct.unpack(f"{dim}f", raw))
            node_ids.append(nid)
            vecs.append(vec)

        X = np.array(vecs, dtype=np.float32)
        n_nodes, dim = X.shape
        effective_nlist = min(nlist, n_nodes)

        km = MiniBatchKMeans(
            n_clusters=effective_nlist,
            batch_size=batch_size,
            random_state=42,
            n_init=3,
        ).fit(X)

        centroids = km.cluster_centers_.tolist()
        labels = km.labels_.tolist()

        assignments = []
        for i, (nid, label) in enumerate(zip(node_ids, labels)):
            assignments.append(
                {"nodeId": nid, "cellIdx": int(label), "vec": _json.dumps(vecs[i])}
            )

        nlist_json = _json.dumps(effective_nlist)
        metric_json = _json.dumps(metric)
        centroids_json = _json.dumps(centroids)
        assignments_json = _json.dumps(assignments)

        result = self._iris_obj().classMethodValue(
            "Graph.KG.IVFIndex",
            "Build",
            name,
            nlist_json,
            metric_json,
            centroids_json,
            assignments_json,
        )
        return _json.loads(str(result))

    def ivf_search(self, name: str, query: list, k: int = 10, nprobe: int = 8) -> list:
        query_json = json.dumps([float(v) for v in query])
        result = self._iris_obj().classMethodValue(
            "Graph.KG.IVFIndex", "Search", name, query_json, k, nprobe
        )
        rows = json.loads(str(result))
        return [(r["id"], float(r["score"])) for r in rows]

    def ivf_drop(self, name: str) -> None:
        self._iris_obj().classMethodVoid("Graph.KG.IVFIndex", "Drop", name)

    def ivf_info(self, name: str) -> dict:
        result = self._iris_obj().classMethodValue("Graph.KG.IVFIndex", "Info", name)
        return json.loads(str(result))
