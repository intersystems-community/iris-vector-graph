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
from iris_vector_graph.cypher.translator import translate_to_sql, _table, set_schema_prefix
from iris_vector_graph.schema import GraphSchema, _call_classmethod
from iris_vector_graph.capabilities import IRISCapabilities
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
        embedding_config: Optional[str] = None
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
        if hasattr(connection, 'prepare') and not hasattr(connection, 'cursor'):
            from .embedded import EmbeddedConnection
            self.conn = EmbeddedConnection()
        self.embedding_dimension = embedding_dimension
        self.embedder = embedder
        self.embedding_config = embedding_config
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
        return self._table_mapping_cache.get(label) if self._table_mapping_cache else None

    def _load_table_mapping_cache(self) -> None:
        self._table_mapping_cache = {}
        try:
            cur = self.conn.cursor()
            cur.execute(
                "SELECT label, sql_table, id_column, prop_columns FROM Graph_KG.table_mappings"
            )
            for row in cur.fetchall():
                self._table_mapping_cache[row[0]] = {
                    "label": row[0], "sql_table": row[1],
                    "id_column": row[2], "prop_columns": row[3],
                }
        except Exception:
            self._table_mapping_cache = {}

    def get_rel_mapping(self, source_label: str, predicate: str, target_label: str) -> Optional[dict]:
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
                    "source_label": row[0], "predicate": row[1], "target_label": row[2],
                    "target_fk": row[3], "via_table": row[4],
                    "via_source": row[5], "via_target": row[6],
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
            [table.split(".")[0] if "." in table else "USER",
             table.split(".")[-1], id_column]
        )
        row = cur.fetchone()
        if not row or int(row[0]) == 0:
            raise ValueError(
                f"Table '{table}' or column '{id_column}' not found. "
                f"Verify the table exists and id_column is correct."
            )
        prop_json = json.dumps(property_columns) if property_columns else None
        cur.execute("UPDATE Graph_KG.table_mappings SET sql_table=?, id_column=?, prop_columns=? WHERE label=?",
                    [table, id_column, prop_json, label])
        if cur.rowcount == 0:
            cur.execute(
                "INSERT INTO Graph_KG.table_mappings (label, sql_table, id_column, prop_columns) VALUES (?,?,?,?)",
                [label, table, id_column, prop_json]
            )
        self.conn.commit()
        self._invalidate_mapping_cache()
        return {"label": label, "sql_table": table, "id_column": id_column, "prop_columns": property_columns}

    def map_sql_relationship(
        self, source_label: str, predicate: str, target_label: str,
        target_fk: str = None, via_table: str = None,
        via_source: str = None, via_target: str = None
    ) -> dict:
        if not target_fk and not via_table:
            raise ValueError("Either target_fk or via_table must be provided.")
        if not self.get_table_mapping(source_label):
            raise ValueError(f"Source label '{source_label}' is not registered. Call map_sql_table first.")
        if not self.get_table_mapping(target_label):
            raise ValueError(f"Target label '{target_label}' is not registered. Call map_sql_table first.")
        cur = self.conn.cursor()
        cur.execute(
            "UPDATE Graph_KG.relationship_mappings SET target_fk=?, via_table=?, via_source=?, via_target=? "
            "WHERE source_label=? AND predicate=? AND target_label=?",
            [target_fk, via_table, via_source, via_target, source_label, predicate, target_label]
        )
        if cur.rowcount == 0:
            cur.execute(
                "INSERT INTO Graph_KG.relationship_mappings "
                "(source_label, predicate, target_label, target_fk, via_table, via_source, via_target) "
                "VALUES (?,?,?,?,?,?,?)",
                [source_label, predicate, target_label, target_fk, via_table, via_source, via_target]
            )
        self.conn.commit()
        self._invalidate_mapping_cache()
        return {"source_label": source_label, "predicate": predicate, "target_label": target_label,
                "target_fk": target_fk, "via_table": via_table}

    def list_table_mappings(self) -> dict:
        cur = self.conn.cursor()
        cur.execute("SELECT label, sql_table, id_column, prop_columns, registered_at FROM Graph_KG.table_mappings")
        nodes = [{"label": r[0], "sql_table": r[1], "id_column": r[2], "prop_columns": r[3], "registered_at": str(r[4])}
                 for r in cur.fetchall()]
        cur.execute("SELECT source_label, predicate, target_label, target_fk, via_table, via_source, via_target "
                    "FROM Graph_KG.relationship_mappings")
        rels = [{"source_label": r[0], "predicate": r[1], "target_label": r[2],
                 "target_fk": r[3], "via_table": r[4], "via_source": r[5], "via_target": r[6]}
                for r in cur.fetchall()]
        return {"nodes": nodes, "relationships": rels}

    def remove_table_mapping(self, label: str) -> None:
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM Graph_KG.table_mappings WHERE label=?", [label])
        if int(cur.fetchone()[0]) == 0:
            raise ValueError(f"Label '{label}' not found in table_mappings.")
        cur.execute("DELETE FROM Graph_KG.table_mappings WHERE label=?", [label])
        cur.execute("DELETE FROM Graph_KG.relationship_mappings WHERE source_label=? OR target_label=?", [label, label])
        self.conn.commit()
        self._invalidate_mapping_cache()

    def reload_table_mappings(self) -> None:
        self._invalidate_mapping_cache()
        self._load_table_mapping_cache()
        self._load_rel_mapping_cache()

    def attach_embeddings_to_table(
        self, label: str, text_columns: list,
        batch_size: int = 1000, force: bool = False,
        progress_callback=None
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
            batch = all_rows[batch_start:batch_start + batch_size]
            for row in batch:
                row_id = row[0]
                node_id = f"{label}:{row_id}"
                if not force:
                    cur.execute("SELECT COUNT(*) FROM Graph_KG.kg_NodeEmbeddings WHERE id=?", [node_id])
                    if int(cur.fetchone()[0]) > 0:
                        skipped += 1
                        continue
                text = " ".join(str(row[i+1]) for i in range(len(text_columns)) if row[i+1] is not None)
                try:
                    emb = self.embed_text(text)
                    emb_str = ",".join(str(x) for x in emb)
                    cur.execute("DELETE FROM Graph_KG.kg_NodeEmbeddings WHERE id=?", [node_id])
                    cur.execute(
                        "INSERT INTO Graph_KG.kg_NodeEmbeddings (id, emb) VALUES (?, TO_VECTOR(?))",
                        [node_id, emb_str]
                    )
                    embedded += 1
                except Exception as ex:
                    logger.warning(f"attach_embeddings_to_table: failed to embed {node_id}: {ex}")
            self.conn.commit()
            n_done = batch_start + len(batch)
            logger.info(f"attach_embeddings_to_table: {n_done}/{n_total} rows processed")
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
                        return [float(x) for x in val.strip('[]').split(',')]
                    return list(val)
            except Exception as e:
                logger.warning(f"Native IRIS EMBEDDING failed for config '{self.embedding_config}': {e}. Falling back to Python.")
            finally:
                cursor.close()

        # 2. Python-side embedding
        if not self.embedder:
            # Try to auto-load a default model if sentence-transformers is available
            try:
                from sentence_transformers import SentenceTransformer
                self.embedder = SentenceTransformer('all-MiniLM-L6-v2')
                logger.info("Auto-initialized SentenceTransformer('all-MiniLM-L6-v2')")
            except ImportError:
                raise RuntimeError(
                    "No embedder or embedding_config configured, and 'sentence-transformers' not installed. "
                    "Pass an embedder/embedding_config to IRISGraphEngine or install sentence-transformers."
                )

        if hasattr(self.embedder, 'encode'):
            return self.embedder.encode(text).tolist()
        if hasattr(self.embedder, 'embed'):
            return self.embedder.embed(text)
        if callable(self.embedder):
            return self.embedder(text)
            
        raise TypeError(f"Configured embedder {type(self.embedder)} is not a supported type (must have encode/embed or be callable)")


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
                if "already exists" not in err and "already has a" not in err:
                    logger.warning("Schema setup warning: %s | Statement: %.100s", e, stmt)

        # 3. Ensure indexes and run schema migrations (e.g. column size upgrades)
        GraphSchema.ensure_indexes(cursor)

        # 4. Check for dimension mismatch on existing tables
        try:
            db_dim = self._get_embedding_dimension()
            if db_dim != dim:
                logger.error(
                    "CRITICAL: Embedding dimension mismatch! DB has %d but engine configured for %d. "
                    "Vector operations will fail. You must drop and recreate kg_NodeEmbeddings to change dimension.",
                    db_dim, dim
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
                    procedure_errors.append((stmt[:80], e))
                    logger.error(
                        "Procedure DDL failed: %s | Error: %s",
                        stmt[:80],
                        e,
                    )
                else:
                    logger.warning(
                        "Optional procedure DDL failed (non-fatal): %s | Error: %s",
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

        # 6. Deploy ObjectScript .cls layer (best-effort)
        if auto_deploy_objectscript:
            # Locate iris_src/ relative to the installed package
            try:
                pkg_dir = Path(__file__).parent.parent / "iris_src"
                if not pkg_dir.exists():
                    # Installed wheel: iris_src shipped as package data alongside iris_vector_graph
                    pkg_dir = Path(__file__).parent / ".." / "iris_src"
                self.capabilities = GraphSchema.deploy_objectscript_classes(cursor, pkg_dir.resolve(), conn=self.conn)
            except Exception as exc:
                logger.warning("ObjectScript deploy step failed: %s", exc)
                self.capabilities = IRISCapabilities()
        else:
            self.capabilities = IRISCapabilities()

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

        raise ValueError("Embedding dimension could not be determined. Please provide it during IRISGraphEngine initialization.")

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
            cursor.execute("SELECT EMBEDDING('__ivg_probe__', '__nonexistent_config__')")
            self._embedding_function_available = True
        except Exception as e:
            err = str(e).lower()
            if "not found" in err or "does not exist" in err or "unknown function" in err:
                self._embedding_function_available = False
            else:
                # Function exists but config is missing — that's expected for the probe
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
        cursor.execute(f"SELECT COUNT(*) FROM Graph_KG.{table} WHERE node_id = ?", [node_id])
        result = cursor.fetchone()
        if not result or result[0] == 0:
            raise ValueError(f"Node does not exist: {node_id}")

    def execute_cypher(self, cypher_query: str, parameters: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Execute a Cypher query by translating it to IRIS SQL.
        
        Args:
            cypher_query: Cypher query string
            parameters: Optional query parameters
            
        Returns:
            Dict containing 'columns', 'rows', and 'metadata'
        """
        parsed = parse_query(cypher_query)

        if parsed.procedure_call is not None:
            result = self._try_system_procedure(parsed.procedure_call)
            if result is not None:
                return result

        # Mode 2 guard: if CALL uses a string query_input, verify EMBEDDING() is available
        if parsed.procedure_call is not None:
            proc = parsed.procedure_call
            if proc.procedure_name == "ivg.vector.search" and len(proc.arguments) >= 3:
                query_input_arg = proc.arguments[2]
                from iris_vector_graph.cypher.ast import Literal as CypherLiteral, Variable as CypherVariable
                if isinstance(query_input_arg, CypherLiteral) and isinstance(query_input_arg.value, str):
                    if not self._probe_embedding_support():
                        raise RuntimeError(
                            "ivg.vector.search Mode 2 (text input) requires the IRIS EMBEDDING() SQL function "
                            "(available in IRIS 2024.3+). This IRIS instance does not support it. "
                            "Pass a pre-computed list[float] vector instead."
                        )
                elif isinstance(query_input_arg, CypherVariable):
                    param_val = (parameters or {}).get(query_input_arg.name)
                    if isinstance(param_val, str) and not self._probe_embedding_support():
                        raise RuntimeError(
                            "ivg.vector.search Mode 2 (text input) requires the IRIS EMBEDDING() SQL function "
                            "(available in IRIS 2024.3+). This IRIS instance does not support it. "
                            "Pass a pre-computed list[float] vector instead."
                        )

        sql_query = translate_to_sql(parsed, parameters, engine=self)

        if sql_query.var_length_paths:
            return self._execute_var_length_cypher(sql_query, parameters)

        cursor = self.conn.cursor()
        metadata = sql_query.query_metadata
        
        if sql_query.is_transactional:
            stmts = sql_query.sql
            all_params = sql_query.parameters
            
            cursor.execute("START TRANSACTION")
            try:
                for i, stmt in enumerate(stmts):
                    p = all_params[i] if i < len(all_params) else []
                    if p:
                        cursor.execute(stmt, p)
                    else:
                        cursor.execute(stmt)
                    if cursor.description:
                        rows = cursor.fetchall()
                
                cursor.execute("COMMIT")
                columns = [desc[0] for desc in cursor.description] if cursor.description else []
                return {
                    "columns": columns,
                    "rows": rows,
                    "sql": stmts[-1],
                    "params": all_params[-1] if all_params else [],
                    "metadata": metadata
                }
            except Exception:
                cursor.execute("ROLLBACK")
                raise
        else:
            sql_str = sql_query.sql if isinstance(sql_query.sql, str) else "\n".join(sql_query.sql)
            p = sql_query.parameters[0] if sql_query.parameters else []
            
            if p:
                cursor.execute(sql_str, p)
            else:
                cursor.execute(sql_str)

            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            rows = cursor.fetchall()

            return {
                "columns": columns,
                "rows": rows,
                "sql": sql_str,
                "params": p,
                "metadata": metadata
            }

    def _execute_var_length_cypher(self, sql_query, parameters=None) -> Dict[str, Any]:
        import json as _json
        vl = sql_query.var_length_paths[0]
        predicates_json = _json.dumps(vl["types"]) if vl["types"] else ""
        max_hops = vl["max_hops"]
        min_hops = vl["min_hops"]

        params = sql_query.parameters[0] if sql_query.parameters else []
        source_id = None
        for item in params:
            if isinstance(item, str) and not item.startswith("Graph_KG"):
                source_id = item
                break
        if source_id is None and parameters:
            source_id = next(iter(parameters.values()), None)

        if source_id is None:
            return {"columns": [], "rows": [], "sql": "", "params": [], "metadata": sql_query.query_metadata}

        try:
            bfs_json = _call_classmethod(
                self.conn, "Graph.KG.Traversal", "BFSFastJson",
                source_id, predicates_json, max_hops, ""
            )
            bfs_results = _json.loads(str(bfs_json)) if bfs_json else []
        except Exception as e:
            logger.warning(f"BFSFastJson failed: {e}")
            return {"columns": [], "rows": [], "sql": "", "params": [], "metadata": sql_query.query_metadata}

        if min_hops > 1:
            min_step_per_node: dict = {}
            for r in bfs_results:
                oid = r.get("o")
                if oid:
                    s = r.get("step", 1)
                    if oid not in min_step_per_node or s < min_step_per_node[oid]:
                        min_step_per_node[oid] = s
            bfs_results = [r for r in bfs_results if min_step_per_node.get(r.get("o"), 0) >= min_hops]

        seen = set()
        target_ids = []
        for r in bfs_results:
            oid = r.get("o")
            if oid and oid not in seen:
                seen.add(oid)
                target_ids.append(oid)

        if not target_ids:
            return {"columns": ["b_id", "b_labels", "b_props"], "rows": [], "sql": "", "params": [], "metadata": sql_query.query_metadata}

        nodes = self.get_nodes(target_ids)
        rows = []
        for data in nodes:
            node_id = data.get("id", "")
            rows.append((node_id, data.get("labels", []), {k: v for k, v in data.items() if k not in ("labels", "id")}))

        return {
            "columns": ["b_id", "b_labels", "b_props"],
            "rows": [list(r) for r in rows],
            "sql": f"BFSFastJson({source_id}, {predicates_json}, {max_hops})",
            "params": [],
            "metadata": sql_query.query_metadata,
        }

    def _try_system_procedure(self, proc) -> Optional[Dict[str, Any]]:
        name = proc.procedure_name.lower()

        if name == "db.labels":
            cursor = self.conn.cursor()
            cursor.execute("SELECT DISTINCT label FROM Graph_KG.rdf_labels ORDER BY label")
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
            cursor.execute("SELECT DISTINCT label FROM Graph_KG.rdf_labels ORDER BY label")
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
                        "SELECT DISTINCT TOP 20 \"key\" FROM Graph_KG.rdf_props "
                        "WHERE s = ? ORDER BY \"key\"",
                        [sample[0]],
                    )
                    for (prop_name,) in cursor.fetchall():
                        rows.append([
                            f":`{label}`", [label], prop_name, ["String"], False,
                        ])
            return {
                "columns": ["nodeType", "nodeLabels", "propertyName", "propertyTypes", "mandatory"],
                "rows": rows,
            }

        if name == "db.schema.reltypeproperties":
            return {
                "columns": ["relType", "propertyName", "propertyTypes", "mandatory"],
                "rows": [],
            }

        if name == "dbms.components":
            return {
                "columns": ["name", "versions", "edition"],
                "rows": [["iris-vector-graph", ["1.47.0"], "community"]],
            }

        if name == "dbms.procedures":
            procs = [
                ["db.labels", "Returns all labels", "READ", True],
                ["db.relationshipTypes", "Returns all relationship types", "READ", True],
                ["db.schema.visualization", "Returns graph schema visualization", "READ", True],
                ["db.schema.nodeTypeProperties", "Returns node type properties", "READ", True],
                ["db.schema.relTypeProperties", "Returns rel type properties", "READ", True],
                ["dbms.components", "Returns server components", "DBMS", True],
                ["ivg.vector.search", "Vector similarity search", "READ", True],
                ["ivg.bm25.search", "BM25 lexical search", "READ", True],
                ["ivg.ppr", "Personalized PageRank", "READ", True],
                ["ivg.neighbors", "1-hop neighborhood", "READ", True],
            ]
            return {
                "columns": ["name", "description", "mode", "worksOnSystem"],
                "rows": procs,
            }

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
                "SELECT TOP 1 rl.s FROM Graph_KG.rdf_labels rl "
                "WHERE rl.label = ?",
                [label]
            )
            row = cursor.fetchone()
            sample_id = row[0] if row else None

            prop_names = []
            if sample_id:
                cursor.execute(
                    "SELECT DISTINCT TOP 20 \"key\" FROM Graph_KG.rdf_props WHERE s = ? "
                    "ORDER BY \"key\"",
                    [sample_id]
                )
                prop_names = [r[0] for r in cursor.fetchall()]

            nodes.append({
                "id": i,
                "name": label,
                "labels": [label],
                "properties": [{"name": p, "type": "String"} for p in prop_names],
            })

        label_to_id = {n["name"]: n["id"] for n in nodes}

        rels = []
        for i, rel_type in enumerate(rel_types):
            cursor.execute(
                "SELECT s, o_id FROM Graph_KG.rdf_edges WHERE p = ?",
                [rel_type]
            )
            row = cursor.fetchone()
            start_label_id = 0
            end_label_id = 0
            if row:
                src_id, tgt_id = row
                cursor.execute(
                    "SELECT TOP 1 label FROM Graph_KG.rdf_labels WHERE s = ?",
                    [src_id]
                )
                src_row = cursor.fetchone()
                if src_row:
                    start_label_id = label_to_id.get(src_row[0], 0)
                cursor.execute(
                    "SELECT TOP 1 label FROM Graph_KG.rdf_labels WHERE s = ?",
                    [tgt_id]
                )
                tgt_row = cursor.fetchone()
                if tgt_row:
                    end_label_id = label_to_id.get(tgt_row[0], 0)

            rels.append({
                "id": i,
                "name": rel_type,
                "type": rel_type,
                "properties": [],
                "startNode": start_label_id,
                "endNode": end_label_id,
            })

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
                chunk = node_ids[i:i + _IN_CHUNK]
                placeholders = ",".join(["?"] * len(chunk))

                cursor.execute(
                    f"SELECT s, label FROM {_table('rdf_labels')} WHERE s IN ({placeholders})",
                    chunk
                )
                for s, label in cursor.fetchall():
                    if s in node_map:
                        node_map[s]["labels"].append(label)

                cursor.execute(
                    f"SELECT s, \"key\", val FROM {_table('rdf_props')} WHERE s IN ({placeholders})",
                    chunk
                )
                _STRUCTURAL_KEYS = ("id", "labels")
                for s, key, val in cursor.fetchall():
                    if s in node_map:
                        store_key = f"p_{key}" if key in _STRUCTURAL_KEYS else key
                        if val is not None:
                            parsed_val = val
                            try:
                                if (str(val).startswith('{') and str(val).endswith('}')) or (str(val).startswith('[') and str(val).endswith(']')):
                                    parsed_val = json.loads(val)
                            except Exception:
                                pass
                            node_map[s][store_key] = parsed_val
                        else:
                            node_map[s][store_key] = val

            empty_nids = [nid for nid, data in node_map.items() if not data["labels"] and len(data) <= 2]
            if empty_nids:
                existing_empty: set = set()
                for i in range(0, len(empty_nids), _IN_CHUNK):
                    chunk = empty_nids[i:i + _IN_CHUNK]
                    e_placeholders = ",".join(["?"] * len(chunk))
                    cursor.execute(f"SELECT node_id FROM {_table('nodes')} WHERE node_id IN ({e_placeholders})", chunk)
                    existing_empty.update(row[0] for row in cursor.fetchall())
                return [node_map[nid] for nid in node_ids if nid in existing_empty or nid not in empty_nids]

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

        labels = json.loads(labels_raw) if isinstance(labels_raw, str) else (labels_raw or [])
        props_items = json.loads(props_raw) if isinstance(props_raw, str) else (props_raw or [])
        
        if props_items and isinstance(props_items[0], str):
            props_items = [json.loads(item) for item in props_items]
        
        props = {item["key"]: item["value"] for item in props_items if isinstance(item, dict)}

        return {"id": row_map[id_key], "labels": labels, "properties": props}

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
                cursor.execute("SELECT COUNT(*) FROM Graph_KG.rdf_labels WHERE label = ?", [label])
            else:
                cursor.execute("SELECT COUNT(*) FROM Graph_KG.nodes")
            
            result = cursor.fetchone()
            return result[0] if result else 0
        except Exception as e:
            logger.error(f"Count nodes failed: {e}")
            return 0

    def create_node(self, node_id: str, labels: List[str] = None, properties: Dict[str, Any] = None) -> bool:
        """
        Create a single node with labels and properties in a single transaction.
        Optimized for individual creations with proper batching of internal inserts.
        """
        cursor = self.conn.cursor()
        try:
            cursor.execute("START TRANSACTION")
            
            # 1. Create node
            cursor.execute(f"INSERT INTO {_table('nodes')} (node_id) VALUES (?)", [node_id])
            
            # 2. Batch labels
            if labels:
                label_data = [[node_id, label] for label in labels]
                cursor.executemany(f"INSERT INTO {_table('rdf_labels')} (s, label) VALUES (?, ?)", label_data)
                
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
            
            prop_sql = f"INSERT INTO {_table('rdf_props')} (s, \"key\", val) SELECT ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM {_table('rdf_props')} WHERE s = ? AND \"key\" = ?)"
            cursor.executemany(prop_sql, prop_data)
                
            cursor.execute("COMMIT")
            return True
        except Exception as e:
            cursor.execute("ROLLBACK")
            if "UNIQUE" in str(e) or "-119" in str(e) or "validation failed" in str(e).lower():
                logger.debug(f"create_node skipped: {node_id}: {str(e)[:80]}")
            else:
                logger.error(f"create_node failed: {e}")
            return False

    def create_edge(self, source_id: str, predicate: str, target_id: str, qualifiers: Dict[str, Any] = None) -> bool:
        """
        Create a directed edge between nodes with optional qualifiers.
        """
        cursor = self.conn.cursor()
        try:
            qual_json = json.dumps(qualifiers) if qualifiers else None
            cursor.execute(
                f"INSERT INTO {_table('rdf_edges')} (s, p, o_id, qualifiers) VALUES (?, ?, ?, ?)",
                [source_id, predicate, target_id, qual_json]
            )
            self.conn.commit()
            return True
        except Exception as e:
            self.conn.rollback()
            if "UNIQUE" in str(e) or "-119" in str(e):
                logger.debug(f"create_edge duplicate: {source_id}-[{predicate}]->{target_id}")
            else:
                logger.error(f"create_edge failed: {e}")
            return False

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
            node_sql = GraphSchema.get_bulk_insert_sql('nodes')
            label_sql = GraphSchema.get_bulk_insert_sql('rdf_labels')
            prop_sql = GraphSchema.get_bulk_insert_sql('rdf_props')

            # 3. Collect and prepare data
            all_labels = []
            all_props = []
            valid_nodes = []

            for node in nodes:
                node_id = node.get('id')
                if not node_id:
                    continue
                
                created_ids.append(node_id)
                # params: [node_id, node_id] for WHERE NOT EXISTS
                valid_nodes.append([node_id, node_id])
                
                for label in node.get('labels', []):
                    # params: [s, label, s, label]
                    all_labels.append((node_id, label, node_id, label))
                
                props = node.get('properties', {})
                # Ensure ID is in properties for consistency with Cypher CREATE
                if 'id' not in props:
                    props['id'] = node_id
                
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
            
            edge_sql = GraphSchema.get_bulk_insert_sql('rdf_edges')
            edge_params = []
            for e in edges:
                if all(k in e for k in ('source_id', 'predicate', 'target_id')):
                    # params: [s, p, o_id, s, p, o_id] for WHERE NOT EXISTS
                    s, p, o = e['source_id'], e['predicate'], e['target_id']
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

    def load_networkx(self, G, label_attr: str = "type", skip_existing: bool = True,
                      progress_callback=None) -> dict:
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
                logger.info(f"Nodes: {n_done:,}/{total_nodes:,} ({added_nodes:,} added, {skipped_nodes:,} skipped)")
                if progress_callback:
                    progress_callback(n_done, added_edges + skipped_edges)
        logger.info(f"Nodes complete: {added_nodes:,} added, {skipped_nodes:,} skipped")
        if progress_callback:
            progress_callback(added_nodes + skipped_nodes, 0)
        for src, dst, data in G.edges(data=True):
            predicate = data.get("predicate", data.get("label", data.get("key", "is_a")))
            qualifiers = {k: v for k, v in data.items() if k not in ("predicate", "label", "key")}
            if self.create_edge(source_id=str(src), predicate=str(predicate),
                                target_id=str(dst), qualifiers=qualifiers or None):
                added_edges += 1
            else:
                skipped_edges += 1
            e_done = added_edges + skipped_edges
            if e_done % 10000 == 0:
                logger.info(f"Edges: {e_done:,}/{total_edges:,} ({added_edges:,} added, {skipped_edges:,} skipped)")
                if progress_callback:
                    progress_callback(added_nodes + skipped_nodes, e_done)
        logger.info(f"Edges complete: {added_edges:,} added, {skipped_edges:,} skipped")
        if progress_callback:
            progress_callback(added_nodes + skipped_nodes, added_edges + skipped_edges)
        return {"nodes": added_nodes, "edges": added_edges, "skipped_nodes": skipped_nodes, "skipped_edges": skipped_edges}

    def load_obo(self, path_or_url: str, prefix: str = None,
                 encoding: str = "utf-8", encoding_errors: str = "replace",
                 progress_callback=None) -> dict:
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
        return self.load_networkx(G, label_attr="namespace", progress_callback=progress_callback)

    def store_embedding(
        self, node_id: str, embedding: List[float], metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        self._assert_node_exists(node_id)
        
        try:
            dim = self._get_embedding_dimension()
        except ValueError:
            # Infer dimension from input if auto-detection fails
            dim = len(embedding)
            self.embedding_dimension = dim
            logger.warning(f"Embedding dimension auto-detection failed. Inferred dimension {dim} from input.")

        if len(embedding) != dim:
            raise ValueError(f"Embedding dimension mismatch: expected {dim}, got {len(embedding)}")

        cursor = self.conn.cursor()
        emb_str = ",".join(str(x) for x in embedding)
        meta_json = json.dumps(metadata) if metadata else None

        cursor.execute(f"DELETE FROM {_table('kg_NodeEmbeddings')} WHERE id = ?", [node_id])
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
            logger.warning(f"Embedding dimension auto-detection failed. Inferred dimension {dim} from input.")

        for item in items:
            node_id = item["node_id"]
            embedding = item["embedding"]
            if len(embedding) != dim:
                raise ValueError(f"Embedding dimension mismatch: expected {dim}, got {len(embedding)}")
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

                cursor.execute(f"DELETE FROM {_table('kg_NodeEmbeddings')} WHERE id = ?", [node_id])
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
            if any(x in where.upper() for x in (";", "--", "/*", "XP_", "EXEC", "EXECUTE")):
                raise ValueError(f"Unsafe WHERE clause rejected: {where!r}")

        orig_embedder = self.embedder
        if model is not None:
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
                batch_ids = to_embed[batch_start:batch_start + batch_size]

                placeholders = ", ".join("?" * len(batch_ids))
                cursor.execute(
                    f"SELECT s, \"key\", val FROM {_table('rdf_props')} WHERE s IN ({placeholders})",
                    batch_ids,
                )
                props_by_node: Dict[str, Dict[str, Any]] = {}
                for row in cursor.fetchall():
                    node_id, key, val = row[0], row[1], row[2]
                    props_by_node.setdefault(node_id, {})[key] = val

                for node_id in batch_ids:
                    props = props_by_node.get(node_id, {})
                    if text_fn is not None:
                        try:
                            text = text_fn(node_id, props)
                        except Exception as ex:
                            logger.warning(f"embed_nodes: text_fn raised for {node_id}: {ex}")
                            errors += 1
                            continue
                    else:
                        text = node_id

                    if not text:
                        skipped += 1
                        continue

                    try:
                        emb = self.embed_text(text)
                        emb_str = ",".join(str(x) for x in emb)
                        try:
                            cursor.execute(f"DELETE FROM {_table('kg_NodeEmbeddings')} WHERE id = ?", [node_id])
                        except Exception:
                            pass
                        cursor.execute(
                            f"INSERT INTO {_table('kg_NodeEmbeddings')} (id, emb) VALUES (?, TO_VECTOR(?))",
                            [node_id, emb_str],
                        )
                        embedded += 1
                    except Exception as ex:
                        logger.warning(f"embed_nodes: failed to embed {node_id}: {ex}")
                        errors += 1

                self.conn.commit()
                n_done = batch_start + len(batch_ids)
                logger.info(f"embed_nodes: {n_done}/{n_to_embed} processed ({embedded} embedded)")
                if progress_callback:
                    progress_callback(n_done, n_to_embed)

            skipped += (n_total - n_to_embed)
            return {"embedded": embedded, "skipped": skipped, "errors": errors, "total": n_total}
        finally:
            self.embedder = orig_embedder

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
            [node_id]
        )
        row = cursor.fetchone()
        if not row:
            return None
        
        node_id, emb_csv, metadata_json = row
        embedding = [float(x) for x in emb_csv.split(',')] if emb_csv else []
        metadata = json.loads(metadata_json) if metadata_json else None
        
        result = {'id': node_id, 'embedding': embedding}
        if metadata:
            result['metadata'] = metadata
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
        placeholders = ','.join(['?'] * len(node_ids))
        cursor.execute(
            f"SELECT id, emb, metadata FROM {_table('kg_NodeEmbeddings')} WHERE id IN ({placeholders})",
            node_ids
        )
        
        results = []
        for row in cursor.fetchall():
            node_id, emb_csv, metadata_json = row
            embedding = [float(x) for x in emb_csv.split(',')] if emb_csv else []
            metadata = json.loads(metadata_json) if metadata_json else None
            
            result = {'id': node_id, 'embedding': embedding}
            if metadata:
                result['metadata'] = metadata
            results.append(result)
        
        return results

    def delete_node(self, node_id: str) -> bool:
        cursor = self.conn.cursor()
        try:
            cursor.execute(f"DELETE FROM {_table('kg_NodeEmbeddings')} WHERE id = ?", [node_id])
            cursor.execute(
                f"SELECT edge_id FROM {_table('rdf_edges')} WHERE s = ? OR o_id = ?",
                [node_id, node_id]
            )
            edge_ids = [row[0] for row in cursor.fetchall()]
            for eid in edge_ids:
                cursor.execute(
                    f"SELECT reifier_id FROM {_table('rdf_reifications')} WHERE edge_id = ?", [eid]
                )
                for (reif_id,) in cursor.fetchall():
                    cursor.execute(f"DELETE FROM {_table('rdf_reifications')} WHERE reifier_id = ?", [reif_id])
                    cursor.execute(f"DELETE FROM {_table('rdf_props')} WHERE s = ?", [reif_id])
                    cursor.execute(f"DELETE FROM {_table('rdf_labels')} WHERE s = ?", [reif_id])
                    cursor.execute(f"DELETE FROM {_table('nodes')} WHERE node_id = ?", [reif_id])
            cursor.execute(f"DELETE FROM {_table('rdf_edges')} WHERE s = ? OR o_id = ?", [node_id, node_id])
            cursor.execute(f"DELETE FROM {_table('rdf_labels')} WHERE s = ?", [node_id])
            cursor.execute(f"DELETE FROM {_table('rdf_props')} WHERE s = ?", [node_id])
            cursor.execute(f"DELETE FROM {_table('nodes')} WHERE node_id = ?", [node_id])
            self.conn.commit()
            return True
        except Exception as e:
            logger.warning(f"delete_node({node_id}) failed: {e}")
            return False
        finally:
            cursor.close()

    def get_kg_anchors(self, icd_codes: List[str], bridge_type: str = "icd10_to_mesh") -> List[str]:
        if not icd_codes:
            return []
        _IN_CHUNK = 499
        results: list = []
        cursor = self.conn.cursor()
        try:
            for i in range(0, len(icd_codes), _IN_CHUNK):
                chunk = icd_codes[i:i + _IN_CHUNK]
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

    def reify_edge(self, edge_id: int, reifier_id: str = None,
                   label: str = "Reification", props: Dict[str, str] = None) -> Optional[str]:
        if reifier_id is None:
            reifier_id = f"reif:{edge_id}"
        cursor = self.conn.cursor()
        try:
            cursor.execute(f"SELECT edge_id FROM {_table('rdf_edges')} WHERE edge_id = ?", [edge_id])
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
                        f"INSERT INTO {_table('rdf_props')} (s, \"key\", val) "
                        f"SELECT ?, ?, ? WHERE NOT EXISTS "
                        f"(SELECT 1 FROM {_table('rdf_props')} WHERE s = ? AND \"key\" = ?)",
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
                f"SELECT r.reifier_id, p.\"key\", p.val "
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
            cursor.execute(f"DELETE FROM {_table('rdf_reifications')} WHERE reifier_id = ?", [reifier_id])
            cursor.execute(f"DELETE FROM {_table('rdf_props')} WHERE s = ?", [reifier_id])
            cursor.execute(f"DELETE FROM {_table('rdf_labels')} WHERE s = ?", [reifier_id])
            cursor.execute(f"DELETE FROM {_table('nodes')} WHERE node_id = ?", [reifier_id])
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
        cursor.execute(f"DELETE FROM {_table('kg_NodeEmbeddings')} WHERE id = ?", [node_id])
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

    def kg_KNN_VEC(self, query_vector: str, k: int = 50, label_filter: Optional[str] = None) -> List[Tuple[str, float]]:
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
            emb_table = _table('kg_NodeEmbeddings')
            labels_table = _table('rdf_labels')
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
            logger.warning(f"Server-side kg_KNN_VEC failed: {e}. Falling back to client-side logic.")
            # Fallback to Python implementation
            return self._kg_KNN_VEC_python_optimized(query_vector, k, label_filter)

    def _kg_KNN_VEC_python_optimized(self, query_vector: str, k: int = 50, label_filter: Optional[str] = None) -> List[Tuple[str, float]]:
        """
        Fallback Python implementation using CSV parsing
        Performance: ~5.8s for 20K vectors (when HNSW not available)
        """
        cursor = self.conn.cursor()
        try:
            import numpy as np
            query_array = np.array(json.loads(query_vector))

            # Get embeddings with optional label filter (optimized query)
            emb_table = _table('kg_NodeEmbeddings')
            labels_table = _table('rdf_labels')
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
                        emb_array = np.fromstring(emb_csv, dtype=float, sep=',')

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
    def kg_TXT(self, query_text: str, k: int = 50, min_confidence: int = 0) -> List[Tuple[str, float]]:
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
            cursor.execute("CALL iris_vector_graph.kg_TXT(?, ?, ?)", [query_text, k, min_confidence])
            results = cursor.fetchall()
            return [(entity_id, float(score)) for entity_id, score in results]

        except Exception as e:
            logger.error(f"kg_TXT failed: {e}")
            raise
        finally:
            cursor.close()

    # Graph Traversal Operations
    def kg_NEIGHBORHOOD_EXPANSION(self, entity_list: List[str], expansion_depth: int = 1, confidence_threshold: int = 500) -> List[Dict[str, Any]]:
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
            entity_placeholders = ','.join(['?' for _ in entity_list])

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
                results.append({
                    'source': row[0],
                    'predicate': row[1],
                    'target': row[2],
                    'confidence': row[3]
                })

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
                    v = json.loads(sample[0]) if isinstance(sample[0], str) else sample[0]
                    dimension = len(v)
                except Exception:
                    pass
            return {"table": table, "vector_col": vector_col, "dimension": dimension, "row_count": row_count}
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

        extra = ", ".join(sanitize_identifier(c) for c in (return_cols or []) if c != id_col)

        dim = None
        if isinstance(query_embedding, list):
            dim = len(query_embedding)
        elif isinstance(query_embedding, str):
            dim = query_embedding.count(",") + 1

        if dim:
            query_cast = f"TO_VECTOR(?, DOUBLE, {dim})"
        else:
            query_cast = "TO_VECTOR(?, DOUBLE)"

        select_cols = f"t.{id_col}, VECTOR_COSINE(t.{vector_col}, {query_cast}) AS score"
        if extra:
            select_cols += f", {extra}"

        having = f"HAVING score >= {score_threshold}" if score_threshold is not None else ""
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
                    table=tbl, vector_col=col,
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
                    meta[node_id] = {k: v for k, v in r.items() if not k.startswith("_")}
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

    def kg_RRF_FUSE(self, k: int, k1: int, k2: int, c: int, query_vector: str, query_text: str) -> List[Tuple[str, float, float, float]]:
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
            cursor.execute("CALL iris_vector_graph.kg_RRF_FUSE(?, ?, ?, ?, ?, ?)", [k, k1, k2, c, query_vector, query_text])
            results = cursor.fetchall()
            return [(entity_id, float(rrf), float(v), float(t)) for entity_id, rrf, v, t in results]

        except Exception as e:
            logger.error(f"kg_RRF_FUSE failed: {e}")
            raise
        finally:
            cursor.close()

    def kg_VECTOR_GRAPH_SEARCH(self, query_vector: str, query_text: str = None, k: int = 15,
                             expansion_depth: int = 1, min_confidence: float = 0.5) -> List[Dict[str, Any]]:
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
                    vector_entities,
                    expansion_depth,
                    int(min_confidence * 1000)
                )
                expanded_entities = list(set([item['target'] for item in graph_expansion]))
            else:
                expanded_entities = []

            # Step 3: Combine with text search if provided
            if query_text:
                text_results = self.kg_TXT(query_text, k=k_vector * 2, min_confidence=int(min_confidence * 1000))
                text_entities = [entity_id for entity_id, _ in text_results]
                all_entities = list(set(vector_entities + expanded_entities + text_entities))
            else:
                all_entities = list(set(vector_entities + expanded_entities))

            # Step 4: Score combination (simplified)
            combined_results = []
            for entity_id in all_entities[:k]:
                # Get scores from different sources
                vector_sim = next((score for eid, score in vector_results if eid == entity_id), 0.0)

                # Simple weighted combination
                combined_score = vector_sim  # Can be enhanced with graph centrality, text relevance

                combined_results.append({
                    'entity_id': entity_id,
                    'combined_score': combined_score,
                    'vector_similarity': vector_sim,
                    'in_graph_expansion': entity_id in expanded_entities
                })

            # Sort by combined score
            combined_results.sort(key=lambda x: x['combined_score'], reverse=True)
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
            raise ValueError(f"reverse_edge_weight must be non-negative, got: {reverse_edge_weight}")
        if not seed_entities:
            raise ValueError("seed_entities must contain at least one entity")

        # --- Fast path: Graph.KG.PageRank.RunJson() via .cls layer ---
        if self.capabilities.objectscript_deployed and self.capabilities.kg_built:
            try:
                seed_json = json.dumps(seed_entities)
                iris_obj = self._iris_obj()
                result_json = iris_obj.classMethodValue(
                    'Graph.KG.PageRank', 'RunJson',
                    seed_json, damping_factor, max_iterations,
                    1 if bidirectional else 0, reverse_edge_weight,
                )
                if result_json:
                    items = json.loads(str(result_json))
                    scores = {item["id"]: item["score"] for item in items if item.get("score", 0) > 0}
                    if return_top_k is not None and return_top_k > 0:
                        scores = dict(sorted(scores.items(), key=lambda x: x[1], reverse=True)[:return_top_k])
                    logger.debug("PageRank via Graph.KG.PageRank.RunJson(): %d results", len(scores))
                    return scores
            except Exception as exc:
                logger.warning("Graph.KG.PageRank.RunJson() failed, falling back: %s", exc)

        return self._kg_PERSONALIZED_PAGERANK_python_fallback(
            seed_entities, damping_factor, max_iterations, tolerance,
            return_top_k, bidirectional, reverse_edge_weight
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
            ranks = {node: (1.0 / seed_count if node in seed_set else 0.0) for node in nodes}

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
                                rank += damping_factor * weight * (ranks.get(src, 0) / out_degree[src])

                    new_ranks[node] = rank
                    max_diff = max(max_diff, abs(rank - ranks.get(node, 0)))

                ranks = new_ranks

                # Check convergence
                if max_diff < tolerance:
                    logger.debug(f"PageRank converged after {iteration + 1} iterations (Python fallback)")
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
                logger.warning("Arno detected but ^NKG not populated — run BuildKG() to enable acceleration")
        except Exception:
            self._arno_available = False
            self._arno_capabilities = {}
        return self._arno_available

    def _arno_call(self, cls: str, method: str, *args) -> str:
        return str(self._iris_obj().classMethodValue(cls, method, *args))

    def khop(self, seed: str, hops: int = 2, max_nodes: int = 500) -> dict:
        if self._detect_arno() and "khop" in self._arno_capabilities.get("algorithms", []):
            result = self._arno_call("Graph.KG.NKGAccel", "KHopNeighbors", seed, str(hops), str(max_nodes))
            parsed = json.loads(result)
            if "error" not in parsed:
                return parsed
            logger.warning(f"Arno khop error: {parsed['error']}")
        return self._khop_fallback(seed, hops, max_nodes)

    def _khop_fallback(self, seed: str, hops: int, max_nodes: int) -> dict:
        if self.capabilities.objectscript_deployed:
            try:
                iris_obj = self._iris_obj()
                result = iris_obj.classMethodValue("Graph.KG.Traversal", "BFSFastJson", seed, "", hops, "")
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

    def ppr(self, seed: str, alpha: float = 0.85, max_iter: int = 20, top_k: int = 20) -> dict:
        if self._detect_arno() and "ppr" in self._arno_capabilities.get("algorithms", []):
            result = self._arno_call("Graph.KG.NKGAccel", "PPRNative", seed, str(alpha), str(max_iter), str(top_k))
            parsed = json.loads(result)
            if "error" not in parsed:
                return parsed
            logger.warning(f"Arno ppr error: {parsed['error']}")
        scores = self.kg_PERSONALIZED_PAGERANK([seed], damping_factor=alpha, max_iterations=max_iter, return_top_k=top_k)
        return {"scores": [{"id": k, "score": v} for k, v in sorted(scores.items(), key=lambda x: -x[1])]}

    def random_walk(self, seed: str, length: int = 20, num_walks: int = 10) -> list:
        if self._detect_arno() and "random_walk" in self._arno_capabilities.get("algorithms", []):
            result = self._arno_call("Graph.KG.NKGAccel", "RandomWalkJson", seed, str(length), str(num_walks))
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
            import intersystems_iris
            return intersystems_iris.createIRIS(self.conn)

    def vec_create_index(self, name: str, dim: int, metric: str = "cosine",
                         num_trees: int = 4, leaf_size: int = 50) -> dict:
        result = self._iris_obj().classMethodValue(
            "Graph.KG.VecIndex", "Create",
            name, str(dim), metric, str(num_trees), str(leaf_size))
        return json.loads(str(result))

    def vec_insert(self, index_name: str, doc_id: str, embedding) -> None:
        vec_json = json.dumps([float(v) for v in embedding])
        self._iris_obj().classMethodVoid(
            "Graph.KG.VecIndex", "InsertJSON", index_name, doc_id, vec_json)

    def vec_bulk_insert(self, index_name: str, items: list) -> int:
        batch = [{"id": item["id"], "vec": [float(v) for v in item["embedding"]]} for item in items]
        batch_json = json.dumps(batch)
        result = self._iris_obj().classMethodValue(
            "Graph.KG.VecIndex", "InsertBatchJSON", index_name, batch_json)
        return json.loads(str(result)).get("inserted", 0)

    def vec_build(self, index_name: str) -> dict:
        result = self._iris_obj().classMethodValue("Graph.KG.VecIndex", "Build", index_name)
        return json.loads(str(result))

    def vec_search(self, index_name: str, query_embedding, k: int = 10, nprobe: int = 8) -> list:
        vec_json = json.dumps([float(v) for v in query_embedding])
        result = self._iris_obj().classMethodValue(
            "Graph.KG.VecIndex", "SearchJSON", index_name, vec_json, k, nprobe)
        return json.loads(str(result))

    def vec_search_multi(self, index_name: str, query_embeddings: list, k: int = 10, nprobe: int = 8) -> list:
        queries_json = json.dumps([[float(v) for v in q] for q in query_embeddings])
        result = self._iris_obj().classMethodValue(
            "Graph.KG.VecIndex", "SearchMultiJSON", index_name, queries_json, k, nprobe)
        return json.loads(str(result))

    def vec_info(self, index_name: str) -> dict:
        result = self._iris_obj().classMethodValue("Graph.KG.VecIndex", "Info", index_name)
        return json.loads(str(result))

    def vec_drop(self, index_name: str) -> None:
        self._iris_obj().classMethodVoid("Graph.KG.VecIndex", "Drop", index_name)

    def vec_expand(self, index_name: str, seed_id: str, k: int = 5) -> list:
        result = self._iris_obj().classMethodValue(
            "Graph.KG.VecIndex", "SeededVectorExpand", seed_id, index_name, k)
        return json.loads(str(result))

    # ── PLAID: multi-vector retrieval (ColBERT-style) ──

    def plaid_build(self, name: str, docs: list, n_clusters: int = None, dim: int = 128) -> dict:
        try:
            import numpy as np
            from sklearn.cluster import KMeans
        except ImportError:
            raise ImportError("plaid_build requires numpy and sklearn: pip install numpy scikit-learn")

        all_tokens = []
        doc_token_map = []
        for doc in docs:
            tokens = doc["tokens"]
            for tok_pos, tok in enumerate(tokens):
                all_tokens.append(tok)
                doc_token_map.append({"docId": doc["id"], "tokPos": tok_pos, "centroid": 0})

        all_tokens_np = np.array(all_tokens, dtype=np.float64)
        K = n_clusters or max(1, int(np.sqrt(len(all_tokens_np))))
        K = min(K, len(all_tokens_np))

        kmeans = KMeans(n_clusters=K, n_init=1, max_iter=20, random_state=42).fit(all_tokens_np)
        labels = kmeans.labels_.tolist()

        for i, label in enumerate(labels):
            doc_token_map[i]["centroid"] = int(label)

        iris_obj = self._iris_obj()
        centroids_json = json.dumps(kmeans.cluster_centers_.tolist())
        iris_obj.classMethodVoid("Graph.KG.PLAIDSearch", "StoreCentroids", name, centroids_json)

        BATCH_SIZE = 10
        for i in range(0, len(docs), BATCH_SIZE):
            batch = docs[i : i + BATCH_SIZE]
            batch_json = json.dumps([
                {"id": doc["id"], "tokens": [[float(v) for v in tok] for tok in doc["tokens"]]}
                for doc in batch
            ])
            iris_obj.classMethodVoid("Graph.KG.PLAIDSearch", "StoreDocTokensBatch", name, batch_json)

        ASSIGN_CHUNK = 5000
        for i in range(0, len(doc_token_map), ASSIGN_CHUNK):
            chunk_json = json.dumps(doc_token_map[i : i + ASSIGN_CHUNK])
            iris_obj.classMethodVoid("Graph.KG.PLAIDSearch", "BuildInvertedIndex", name, chunk_json)

        return json.loads(str(iris_obj.classMethodValue("Graph.KG.PLAIDSearch", "Info", name)))

    def plaid_search(self, name: str, query_tokens: list, k: int = 10, nprobe: int = 4) -> list:
        tokens_json = json.dumps([[float(v) for v in tok] for tok in query_tokens])
        result = self._iris_obj().classMethodValue(
            "Graph.KG.PLAIDSearch", "Search", name, tokens_json, k, nprobe)
        return json.loads(str(result))

    def plaid_insert(self, name: str, doc_id: str, token_embeddings: list) -> None:
        tokens_json = json.dumps([[float(v) for v in tok] for tok in token_embeddings])
        self._iris_obj().classMethodVoid("Graph.KG.PLAIDSearch", "Insert", name, doc_id, tokens_json)

    def plaid_info(self, name: str) -> dict:
        result = self._iris_obj().classMethodValue("Graph.KG.PLAIDSearch", "Info", name)
        return json.loads(str(result))

    def plaid_drop(self, name: str) -> None:
        self._iris_obj().classMethodVoid("Graph.KG.PLAIDSearch", "Drop", name)

    # ── Temporal edges ──

    def create_edge_temporal(self, source: str, predicate: str, target: str,
                             timestamp: int = None, weight: float = 1.0,
                             attrs: dict = None, upsert: bool = False) -> bool:
        try:
            ts = int(timestamp) if timestamp is not None else ""
            attrs_json = json.dumps(attrs) if attrs else ""
            self._iris_obj().classMethodVoid(
                "Graph.KG.TemporalIndex", "InsertEdge",
                source, predicate, target, str(ts), weight, attrs_json, 1 if upsert else 0)
            return True
        except Exception as e:
            logger.warning(f"create_edge_temporal failed: {e}")
            return False

    def bulk_create_edges_temporal(self, edges: list, upsert: bool = False) -> int:
        batch_json = json.dumps(edges)
        result = self._iris_obj().classMethodValue(
            "Graph.KG.TemporalIndex", "BulkInsert", batch_json, 1 if upsert else 0)
        return int(result)

    def get_edges_in_window(self, source: str = "", predicate: str = "",
                            start: int = 0, end: int = 0,
                            direction: str = "out") -> list:
        if direction == "in":
            result = self._iris_obj().classMethodValue(
                "Graph.KG.TemporalIndex", "QueryWindowInbound", source, predicate, start, end)
        else:
            result = self._iris_obj().classMethodValue(
                "Graph.KG.TemporalIndex", "QueryWindow", source, predicate, start, end)
        edges = json.loads(str(result))
        for edge in edges:
            edge["source"]    = edge["s"]
            edge["predicate"] = edge["p"]
            edge["target"]    = edge["o"]
            edge["timestamp"] = edge["ts"]
            edge["weight"]    = edge["w"]
        return edges

    def purge_before(self, ts: int) -> None:
        self._iris_obj().classMethodVoid(
            "Graph.KG.TemporalIndex", "PurgeBefore", int(ts))

    def get_edge_velocity(self, node_id: str, window_seconds: int = 300) -> int:
        result = self._iris_obj().classMethodValue(
            "Graph.KG.TemporalIndex", "GetVelocity", node_id, window_seconds)
        return int(result)

    def find_burst_nodes(self, predicate: str = "", window_seconds: int = 300, threshold: int = 50) -> list:
        result = self._iris_obj().classMethodValue(
            "Graph.KG.TemporalIndex", "FindBursts", predicate, window_seconds, threshold)
        return json.loads(str(result))

    def get_edge_attrs(self, ts: int, source: str, predicate: str, target: str) -> dict:
        result = self._iris_obj().classMethodValue(
            "Graph.KG.TemporalIndex", "GetEdgeAttrs", ts, source, predicate, target)
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
            "Graph.KG.TemporalIndex", "GetAggregate",
            source, predicate, metric, ts_start, ts_end)
        s = str(result)
        if s == "":
            return 0 if metric == "count" else None
        return int(s) if metric == "count" else float(s)

    def get_bucket_groups(
        self,
        predicate: str = "",
        ts_start: int = 0,
        ts_end: int = 0,
    ) -> list:
        result = self._iris_obj().classMethodValue(
            "Graph.KG.TemporalIndex", "GetBucketGroups",
            predicate, ts_start, ts_end)
        return json.loads(str(result))

    def get_distinct_count(
        self,
        source: str,
        predicate: str,
        ts_start: int,
        ts_end: int,
    ) -> int:
        result = self._iris_obj().classMethodValue(
            "Graph.KG.TemporalIndex", "GetDistinctCount",
            source, predicate, ts_start, ts_end)
        return int(str(result))

    def import_graph_ndjson(self, path: str, upsert_nodes: bool = True, batch_size: int = 10000) -> dict:
        nodes = 0
        edges = 0
        temporal_edges = 0
        temporal_batch = []

        with open(path, 'r') as f:
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
        with open(path, 'w') as f:
            cursor.execute(f"SELECT node_id FROM {_table('nodes')}")
            for (node_id,) in cursor.fetchall():
                node_data = self.get_node(node_id)
                if node_data:
                    event = {"kind": "node", "id": node_id, "labels": node_data.get("labels", []),
                             "properties": {k: v for k, v in node_data.items() if k not in ("id", "labels")}}
                    f.write(json.dumps(event) + "\n")
                    nodes_written += 1

        cursor.close()
        return {"nodes": nodes_written, "edges": edges_written}

    def export_temporal_edges_ndjson(self, path: str, start: int = None, end: int = None,
                                     predicate: str = None) -> dict:
        s_filter = ""
        p_filter = predicate or ""
        ts_start = start or 0
        ts_end = end or 9999999999
        result_json = self._iris_obj().classMethodValue(
            "Graph.KG.TemporalIndex", "QueryWindow", s_filter, p_filter, ts_start, ts_end)
        edges = json.loads(str(result_json))

        with open(path, 'w') as f:
            for edge in edges:
                attrs = self.get_edge_attrs(edge["ts"], edge["s"], edge["p"], edge["o"])
                event = {"kind": "temporal_edge", "source": edge["s"], "predicate": edge["p"],
                         "target": edge["o"], "timestamp": edge["ts"], "weight": edge.get("w", 1.0),
                         "attrs": attrs}
                f.write(json.dumps(event) + "\n")

        return {"temporal_edges": len(edges)}

    # ── BM25Index: pure ObjectScript lexical search ──

    def bm25_build(self, name: str, text_props: list, k1: float = 1.5, b: float = 0.75) -> dict:
        props_csv = ",".join(text_props)
        result = self._iris_obj().classMethodValue(
            "Graph.KG.BM25Index", "Build", name, props_csv, k1, b)
        return json.loads(str(result))

    def bm25_search(self, name: str, query: str, k: int = 10) -> list:
        result = self._iris_obj().classMethodValue(
            "Graph.KG.BM25Index", "Search", name, query, k)
        rows = json.loads(str(result))
        return [(r["id"], float(r["score"])) for r in rows]

    def bm25_insert(self, name: str, doc_id: str, text: str) -> bool:
        result = self._iris_obj().classMethodValue(
            "Graph.KG.BM25Index", "Insert", name, doc_id, text)
        return bool(int(str(result)))

    def bm25_drop(self, name: str) -> None:
        self._iris_obj().classMethodVoid("Graph.KG.BM25Index", "Drop", name)

    def bm25_info(self, name: str) -> dict:
        result = self._iris_obj().classMethodValue(
            "Graph.KG.BM25Index", "Info", name)
        return json.loads(str(result))
