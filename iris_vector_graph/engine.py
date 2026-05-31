#!/usr/bin/env python3
"""
IRIS Graph Core Engine - Domain-Agnostic Graph Operations

High-performance graph operations extracted from the biomedical implementation.
Provides vector search, text search, graph traversal, and hybrid fusion capabilities
that can be used across any domain.
"""

import json
from pathlib import Path
from typing import Callable, List, Tuple, Optional, Dict, Any
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
from iris_vector_graph.result import IVGResult
from iris_vector_graph._validate import (
    NodeIdInput, EdgeInput, CypherInput,
    IVFBuildInput, VectorSearchInput,
    BM25BuildInput, BM25SearchInput,
    KHop2Input, TemporalEdgeInput, VecSearchInput,
)
from iris_vector_graph._engine.temporal import TemporalMixin
from iris_vector_graph._engine.snapshot import SnapshotMixin
from iris_vector_graph._engine.fhir import FhirMixin
from iris_vector_graph._engine.admin import AdminMixin
from iris_vector_graph._engine.embeddings import EmbeddingsMixin
from iris_vector_graph._engine.schema import SchemaMixin
from iris_vector_graph._engine.nodes_edges import NodesEdgesMixin

logger = logging.getLogger(__name__)

_sentence_transformers = None
_torch = None
_BULK_CHUNK_SIZE = 1000


def _get_sentence_transformers():
    global _sentence_transformers
    if _sentence_transformers is None:
        import sentence_transformers as _st
        _sentence_transformers = _st
    return _sentence_transformers

def _get_torch():
    global _torch
    if _torch is None:
        import torch as _t
        _torch = _t
    return _torch

def _load_sentence_transformer(model_name: str):
    st = _get_sentence_transformers()
    import warnings as _w
    with _w.catch_warnings():
        _w.simplefilter("ignore")
        return st.SentenceTransformer(model_name, local_files_only=False)

def _is_sentence_transformer(obj) -> bool:
    try:
        st = _get_sentence_transformers()
        return isinstance(obj, st.SentenceTransformer)
    except ImportError:
        return False

def _bfs_stream_pages(conn, tag, page_size=500):
    import json as _j
    cursor_step = ""
    cursor_o = ""
    while True:
        raw = str(_call_classmethod(
            conn, "Graph.KG.Traversal", "ReadBFSPage",
            tag, cursor_step, cursor_o, page_size,
        ))
        if raw.startswith("SORTED:"):
            break
        page = _j.loads(raw)
        yield from page.get("items", [])
        if page.get("done"):
            break
        next_step = page.get("next_step", -1)
        if next_step == -1:
            break
        cursor_step = str(next_step)
        cursor_o = page.get("next_o", "")

class IRISGraphEngine(TemporalMixin, SnapshotMixin, FhirMixin, AdminMixin, EmbeddingsMixin, SchemaMixin, NodesEdgesMixin):
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
        vector_dtype: str = "DOUBLE",
        store=None,
    ):
        self.conn = connection
        if hasattr(connection, "prepare") and not hasattr(connection, "cursor"):
            from .embedded import EmbeddedConnection

            self.conn = EmbeddedConnection()
        self.embedding_dimension = embedding_dimension
        self.embedder = embedder
        self.embedding_config = embedding_config
        self._embed_fn = embed_fn
        self._use_iris_embedding = use_iris_embedding
        self.vector_dtype = vector_dtype.upper()
        set_schema_prefix("Graph_KG")
        self._embedding_function_available: Optional[bool] = None
        self._native_vec_available: Optional[bool] = None
        self.capabilities: IRISCapabilities = IRISCapabilities()
        self._arno_available: Optional[bool] = None
        self._arno_capabilities: Dict[str, Any] = {}
        self._table_mapping_cache: Optional[Dict[str, dict]] = None
        self._rel_mapping_cache: Optional[Dict[tuple, dict]] = None
        self._connection_params: Optional[Dict[str, Any]] = None
        self._nkg_dirty: bool = False
        self._index_registry: Dict[str, str] = self._build_index_registry()
        self._pending_index_config: Dict[str, Any] = {}
        if vector_dtype == "DOUBLE":
            self.vector_dtype = self._detect_stored_vector_dtype()
        if store is None:
            from iris_vector_graph.stores.iris_sql_store import IRISGraphStore
            self._store = IRISGraphStore(self.conn)
        else:
            self._store = store
        self._store_capabilities = self._store.capabilities()
        logger.debug("IRISGraphEngine initialized (dim=%s dtype=%s)",
                     embedding_dimension or "auto", self.vector_dtype)

    @classmethod
    def from_connect(
        cls,
        hostname: str,
        port: int = 1972,
        namespace: str = "USER",
        username: str = "_SYSTEM",
        password: str = "SYS",
        embedding_dimension: Optional[int] = None,
        **kwargs,
    ) -> "IRISGraphEngine":
        import iris as _iris
        conn_params = dict(hostname=hostname, port=port, namespace=namespace, username=username, password=password)
        conn = _iris.connect(**conn_params)
        engine = cls(conn, embedding_dimension=embedding_dimension, **kwargs)
        engine._connection_params = conn_params
        return engine

    @classmethod
    def from_wrapper(
        cls,
        hostname: str = None,
        port: int = 1972,
        namespace: str = "USER",
        username: str = "_SYSTEM",
        password: str = "SYS",
        embedding_dimension: Optional[int] = None,
        **kwargs,
    ) -> "IRISGraphEngine":
        try:
            import iris as _iris_wrapper
            state = _iris_wrapper.runtime.state
        except ImportError:
            raise ImportError(
                "iris-embedded-python-wrapper not installed. "
                "Run: pip install iris-embedded-python-wrapper"
            )
        if hostname:
            conn = _iris_wrapper.dbapi.connect(
                hostname=hostname, port=port, namespace=namespace,
                username=username, password=password,
            )
        elif state.startswith("embedded"):
            conn = _iris_wrapper.dbapi.connect(mode="embedded", namespace=namespace)
        else:
            raise RuntimeError(
                "No IRIS connection available via wrapper. Provide hostname= or run inside IRIS."
            )
        return cls(conn, embedding_dimension=embedding_dimension, **kwargs)

    def _reconnect_if_stale(self) -> None:
        try:
            cur = self.conn.cursor()
            cur.execute("SELECT 1")
        except Exception as probe_err:
            err_str = str(probe_err).lower()
            if any(x in err_str for x in ("epipe", "broken pipe", "connection reset", "closed")):
                if self._connection_params:
                    import iris as _iris
                    self.conn = _iris.connect(**self._connection_params)
                    logger.info("IRIS connection re-established after EPIPE")
                else:
                    raise RuntimeError(
                        "IRIS connection is stale (EPIPE/BrokenPipe) and cannot auto-reconnect "
                        "because connection params were not stored. "
                        "Create the engine with a fresh iris.connect() call."
                    ) from probe_err

    def _invalidate_mapping_cache(self) -> None:
        self._table_mapping_cache = None
        self._rel_mapping_cache = None

    @staticmethod
    def _is_conn_drop(exc: Exception) -> bool:
        s = str(exc).lower()
        if isinstance(exc, (BrokenPipeError, ConnectionError)):
            return True
        return any(
            x in s
            for x in (
                "communication link",
                "epipe",
                "broken pipe",
                "connection reset",
                "operationalerror",
            )
        )

    def _with_reconnect(self, fn, *args, max_retries: int = 3, **kwargs):
        import time as _time

        delay = 0.5
        for attempt in range(max_retries + 1):
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                if (not self._is_conn_drop(e)) or attempt == max_retries:
                    raise
                logger.warning(
                    "bulk op hit connection drop (attempt %d/%d): %s — reconnecting",
                    attempt + 1, max_retries, str(e)[:120],
                )
                self._reconnect_if_stale()
                _time.sleep(delay)
                delay *= 2

    def execute_aql(
        self,
        aql: str,
        bind_vars: Optional[Dict[str, Any]] = None,
    ) -> "IVGResult":
        from iris_vector_graph.cypher.aql import translate_aql
        cypher_query, params = translate_aql(aql, bind_vars or {})
        return self.execute_cypher(cypher_query, parameters=params)

    def execute_cypher(
        self, cypher_query: str, parameters: Dict[str, Any] = None,
        read_only: bool = False,
    ) -> "IVGResult":
        """
        Execute a Cypher query by translating it to IRIS SQL.

        Args:
            cypher_query: Cypher query string
            parameters: Optional query parameters
            read_only: If True, rejects any mutation (CREATE/DELETE/SET/MERGE/REMOVE/FOREACH)

        Returns:
            Dict containing 'columns', 'rows', and 'metadata'
        """
        CypherInput(cypher_query=cypher_query)
        import re as _re_ec
        _APPROX_RE = _re_ec.compile(
            r'\bapprox_count_distinct\s*\(\s*(\w+)\s*\)\s+AS\s+(\w+)',
            _re_ec.IGNORECASE,
        )
        _approx_m = _APPROX_RE.search(cypher_query)
        if _approx_m:
            return self._execute_approx_count_distinct(cypher_query, parameters, _approx_m)

        _fast = self._try_khop_fast_path(cypher_query, parameters)
        if _fast is not None:
            return _fast

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
            return IVGResult(                columns= ["result"],
                rows= [
                    [{"name": "labels", "data": [r[0] for r in labels]}],
                    [{"name": "relationshipTypes", "data": [r[0] for r in rels]}],
                    [{"name": "propertyKeys", "data": prop_keys}],
                ]
            )

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
            return IVGResult(columns=["entity", "id"], rows=node_rows + rel_rows)

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
            return IVGResult(                columns= ["result"],
                rows= [
                    [{"name": "nodes", "data": node_count}],
                    [{"name": "relationships", "data": edge_count}],
                ]
            )

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
                return IVGResult(columns=all_cols or ["result"], rows=all_rows)

        if stripped.startswith("EXPLAIN "):
            return IVGResult(                columns= ["Plan"],
                rows= [["No execution plan available (IRIS backend)"]]
            )

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
            return IVGResult(columns=[], rows=[], sql=cypher_query, params=[])

        parsed = parse_query(cypher_query)

        self._reconnect_if_stale()

        if read_only and parsed.is_mutation:
            raise PermissionError(
                f"Read-only mode: mutation queries (CREATE/DELETE/SET/MERGE/REMOVE/FOREACH) "
                f"are not allowed. Query: {cypher_query[:100]}"
            )

        if parsed.subsequent_queries:
            result = None
            current_params = dict(parameters) if parameters else {}
            for part_query in [parsed] + parsed.subsequent_queries:
                part_query.subsequent_queries = []
                result = self._execute_parsed(part_query, current_params)
                if result and result.get("rows") and result.get("columns"):
                    first_row = result["rows"][0] if result["rows"] else []
                    for col, val in zip(result["columns"], first_row):
                        if isinstance(val, (str, int, float, bool, type(None))):
                            current_params[col] = val
            return result

        return self._execute_parsed(parsed, parameters)

    def _execute_parsed(self, parsed, parameters):
        if parsed.procedure_call is not None:
            result = self._try_system_procedure(parsed.procedure_call)
            if result is not None:
                return result
        sql_query = translate_to_sql(parsed, parameters, engine=self)
        if sql_query.var_length_paths:
            return self._route_var_length(sql_query, parameters)
        metadata = sql_query.query_metadata
        if sql_query.is_transactional:
            result = self._store.execute_transaction(sql_query.sql, sql_query.parameters)
            result.metadata = metadata
            return result
        if self._store_capabilities.get("native_sql", True):
            sql_str = sql_query.sql
            p = sql_query.parameters[0] if sql_query.parameters else []
            result = self._store.execute_sql(sql_str, p)
            result.metadata = metadata
            return result
        traversal = self._extract_traversal(parsed, parameters)
        if traversal is not None:
            return self._execute_traversal(traversal, sql_query, parsed, parameters)
        label_filter = None
        return_props = None
        limit = 0
        try:
            if parsed.query_parts:
                clause = parsed.query_parts[0].clauses[0]
                if hasattr(clause, "patterns") and clause.patterns:
                    node = clause.patterns[0].nodes[0] if clause.patterns[0].nodes else None
                    if node and node.labels:
                        label_filter = node.labels[0]
            if parsed.return_clause:
                return_props = [
                    item.expression.property_name
                    for item in parsed.return_clause.items
                    if hasattr(item.expression, "property_name")
                ]
            if parsed.limit:
                limit = int(parsed.limit)
        except Exception:
            pass
        return self._store.query_nodes(
            label_filter=label_filter,
            property_filters=None,
            return_properties=return_props,
            limit=limit,
        )

    def _extract_traversal(self, parsed, parameters):
        from iris_vector_graph.cypher.ast import Direction
        try:
            clause = parsed.query_parts[0].clauses[0]
            if not (hasattr(clause, "patterns") and clause.patterns):
                return None
            pat = clause.patterns[0]
            if len(pat.nodes) < 2 or len(pat.relationships) < 1:
                return None
            rel = pat.relationships[0]
            if rel.variable_length is not None:
                return None
            src_node = pat.nodes[0]
            src_id = None
            if src_node.properties:
                for k, v in src_node.properties.items():
                    if k == "id":
                        if isinstance(v, str) and v.startswith("$"):
                            src_id = parameters.get(v[1:])
                        elif hasattr(v, 'name'):
                            src_id = parameters.get(v.name)
                        elif isinstance(v, str):
                            src_id = v
                        else:
                            src_id = str(v)
                        break
            if src_id is None:
                return None
            direction_map = {Direction.OUTGOING: "out", Direction.INCOMING: "in", Direction.BOTH: "both"}
            is_count = bool(
                parsed.return_clause and
                any(hasattr(item.expression, "function_name") and
                    item.expression.function_name.upper() == "COUNT"
                    for item in parsed.return_clause.items)
            )
            return {
                "source_id": str(src_id),
                "predicates": rel.types or [],
                "direction": direction_map.get(rel.direction, "out"),
                "is_count": is_count,
                "return_col": (
                    (parsed.return_clause.items[0].alias or "count") if is_count
                    else (parsed.return_clause.items[0].alias or "id") if (parsed.return_clause and parsed.return_clause.items)
                    else "id"
                ),
            }
        except Exception:
            return None

    def _execute_traversal(self, traversal, sql_query, parsed, parameters):
        raw = self._store.execute_bfs(
            traversal["source_id"],
            traversal["predicates"],
            1,
            traversal["direction"],
            0,
        )
        if isinstance(raw, list):
            rows = [[r.get("node_id", r.get("id", "")), r.get("hops", 1)] for r in raw]
        else:
            rows = raw.rows if not raw.error else []
        if traversal["is_count"]:
            return IVGResult(columns=[traversal["return_col"]], rows=[[len(rows)]], metadata=sql_query.query_metadata)
        return IVGResult(columns=[traversal["return_col"]], rows=[[r[0]] for r in rows], metadata=sql_query.query_metadata)

    def _route_var_length(self, sql_query, parameters):
        if self._nkg_dirty:
            from iris_vector_graph.errors import IndexNotSyncedError
            raise IndexNotSyncedError()
        vl0 = sql_query.var_length_paths[0]
        if vl0.get("weighted"):
            return self._execute_weighted_shortest_path(sql_query, parameters)
        if vl0.get("shortest") or vl0.get("all_shortest"):
            return self._execute_shortest_path_cypher(sql_query, parameters)

        import re as _re
        sql_str = sql_query.sql if isinstance(sql_query.sql, str) else (sql_query.sql[0] if sql_query.sql else "")
        count_match = _re.search(r'SELECT\s+COUNT\s*\(\s*DISTINCT\s+.*?\)\s+AS\s+(\w+)', sql_str, _re.IGNORECASE)

        params = sql_query.parameters[0] if sql_query.parameters else []
        source_id = None
        for item in params:
            if isinstance(item, str) and not item.startswith("Graph_KG"):
                source_id = item
                break
        if source_id is None and parameters:
            src_var = vl0.get("source_var")
            if src_var and src_var in parameters:
                source_id = str(parameters[src_var])
            else:
                source_id = next(iter(parameters.values()), None)

        if source_id is None:
            return IVGResult(columns=[], rows=[], sql="", params=[], metadata=sql_query.query_metadata)

        predicates = vl0.get("types", [])
        max_hops = vl0.get("max_hops", 5)
        direction = vl0.get("direction", "out")
        max_results = 0
        if sql_str:
            m = _re.search(r"\bLIMIT\s+(\d+)", sql_str, _re.IGNORECASE)
            if m:
                max_results = int(m.group(1))

        if count_match:
            col_name = count_match.group(1)
            bfs_result = self._store.execute_bfs(source_id, predicates, max_hops, direction, 0)
            cnt = len(bfs_result.rows) if not bfs_result.error else 0
            return IVGResult(columns=[col_name], rows=[[cnt]], metadata=sql_query.query_metadata)
        max_results = 0
        if sql_str:
            m = _re.search(r"\bLIMIT\s+(\d+)", sql_str, _re.IGNORECASE)
            if m:
                max_results = int(m.group(1))

        direction = vl0.get("direction", "out")
        predicates = vl0.get("types", [])
        max_hops = vl0.get("max_hops", 5)

        if vl0.get("temporal_window"):
            ts_start = vl0.get("ts_start", 0)
            ts_end = vl0.get("ts_end", 9999999999)
            result = self._store.execute_temporal_cypher(
                source_id, predicates, ts_start, ts_end, direction, max_hops
            )
        else:
            result = self._store.execute_bfs(source_id, predicates, max_hops, direction, max_results)

        return_properties = getattr(sql_query.query_metadata, "return_properties", None)
        if return_properties and result.rows:
            node_ids = [row[0] for row in result.rows if row]
            if node_ids:
                props_result = self._store.get_nodes(node_ids, return_properties)
                props_by_id = {r[0]: r[2:] for r in props_result.rows}
                enriched = [[r[0], r[1]] + list(props_by_id.get(r[0], [None] * len(return_properties))) for r in result.rows]
                result = IVGResult(
                    columns=result.columns + return_properties,
                    rows=enriched,
                    metadata=result.metadata,
                )
        return result

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

        weight_prop = vl.get("weight_property", "weight")
        max_hops = int(vl.get("max_hops", 10))
        return self._store.execute_weighted_shortest_path(source_id, target_id, weight_prop, max_hops)

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

        predicates = vl.get("types", [])
        return self._store.execute_shortest_path(
            source_id, target_id, predicates, max_hops, direction, bool(find_all)
        )

        if not paths:
            return IVGResult(                columns= ["p"],
                rows= [],
                sql= "",
                params= [],
                metadata= sql_query.query_metadata
            )

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

        return IVGResult(            columns= columns,
            rows= rows,
            sql= f"ShortestPathJson({source_id}, {target_id}, {max_hops})",
            params= [],
            metadata= sql_query.query_metadata
        )

    def _execute_var_length_cypher(self, sql_query, parameters=None) -> Dict[str, Any]:
        import json as _json
        import warnings as _warnings

        if self._nkg_dirty:
            from iris_vector_graph.errors import IndexNotSyncedError
            raise IndexNotSyncedError()

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
            return IVGResult(                columns= [],
                rows= [],
                sql= "",
                params= [],
                metadata= sql_query.query_metadata
            )

        max_results = 0
        import re as _re
        sql_str = sql_query.sql if isinstance(sql_query.sql, str) else (sql_query.sql[0] if sql_query.sql else "")
        if sql_query.sql:
            m = _re.search(r"\bLIMIT\s+(\d+)", sql_str, _re.IGNORECASE)
            if m:
                max_results = int(m.group(1))

        count_match = _re.search(r'SELECT\s+COUNT\s*\(\s*DISTINCT\s+.*?\)\s+AS\s+(\w+)', sql_str, _re.IGNORECASE)
        if count_match:
            col_name = count_match.group(1)
            try:
                cnt = int(str(_call_classmethod(
                    self.conn, "Graph.KG.Traversal", "BFSFastCountDistinct",
                    source_id, predicates_json, max_hops, "", vl.get("direction", "out"),
                )))
            except Exception:
                cnt = 0
            return IVGResult(                columns= [col_name],
                rows= [[cnt]],
                sql= f"BFSFastCountDistinct({source_id}, {predicates_json}, {max_hops})",
                params= [],
                metadata= sql_query.query_metadata
            )

        bfs_results = None
        direction = vl.get("direction", "out")
        arno_usable = (
            self._detect_arno()
            and self._arno_capabilities.get("bfs")
            and self._arno_capabilities.get("rust_callout")
            and direction == "out"
        )
        if arno_usable:
            try:
                bfs_json = self._arno_call(
                    "Graph.KG.NKGAccel",
                    "BFSJson",
                    source_id,
                    predicates_json,
                    max_hops,
                    max_results,
                )
                bfs_str = str(bfs_json) if bfs_json else ""
                if bfs_str.startswith("SORTED:") and bfs_str != "SORTED:0":
                    tag = bfs_str.split(":")[1]
                    if max_results == 0:
                        bfs_results = list(_bfs_stream_pages(self.conn, tag))
                    else:
                        try:
                            results_str = str(_call_classmethod(
                                self.conn, "Graph.KG.Traversal", "ReadBFSResults", tag
                            ))
                            bfs_results = _json.loads(results_str)
                        except Exception:
                            bfs_results = list(_bfs_stream_pages(self.conn, tag))
                elif bfs_str:
                    bfs_results = _json.loads(bfs_str)
                else:
                    bfs_results = []
                logger.debug("Arno BFSJson: %d results for %s", len(bfs_results), source_id)
            except Exception as e:
                logger.warning(f"Arno BFSJson failed, falling back to BFSFastJsonSorted: {e}")
                bfs_results = None

        if bfs_results is None:
            direction = vl.get("direction", "out")
            try:
                resp = str(_call_classmethod(
                    self.conn, "Graph.KG.Traversal", "BFSFastJsonSorted",
                    source_id, predicates_json, max_hops, "", direction, max_results,
                ))
                if resp.startswith("SORTED:") and resp != "SORTED:0":
                    tag = resp.split(":", 2)[1]
                    if max_results == 0:
                        bfs_results = list(_bfs_stream_pages(self.conn, tag))
                    else:
                        try:
                            results_str = str(_call_classmethod(
                                self.conn, "Graph.KG.Traversal", "ReadBFSResults", tag
                            ))
                            bfs_results = _json.loads(results_str)
                        except Exception:
                            bfs_results = list(_bfs_stream_pages(self.conn, tag))
                else:
                    bfs_results = []
            except Exception as e:
                logger.warning(f"BFSFastJsonSorted failed: {e}")
                return IVGResult(columns=[], rows=[], sql="", params=[], metadata=sql_query.query_metadata)

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

        sql_str = sql_query.sql if isinstance(sql_query.sql, str) else ""

        # Fast path: if query only needs node IDs (RETURN DISTINCT b.node_id or RETURN b.node_id),
        # skip get_nodes() entirely — BFS already has the IDs.
        id_only_match = _re.search(
            r'SELECT\s+(?:DISTINCT\s+)?(?:\S+\.node_id|\S+\.id)\s+AS\s+(\w+)',
            sql_str, _re.IGNORECASE
        )
        # Count path: COUNT(DISTINCT ...) — just return the count
        count_match = _re.search(
            r'SELECT\s+COUNT\s*\(\s*DISTINCT\s+.*?\)\s+AS\s+(\w+)',
            sql_str, _re.IGNORECASE
        )

        if count_match:
            col_name = count_match.group(1)
            return IVGResult(                columns= [col_name],
                rows= [[len(target_ids)]],
                sql= f"BFSFastJson({source_id}, {predicates_json}, {max_hops})",
                params= [],
                metadata= sql_query.query_metadata
            )

        if id_only_match:
            col_name = id_only_match.group(1)
            # Apply LIMIT from SQL if present
            limit_match = _re.search(r'\bLIMIT\s+(\d+)', sql_str, _re.IGNORECASE)
            limit = int(limit_match.group(1)) if limit_match else None
            result_ids = target_ids[:limit] if limit else target_ids
            return IVGResult(                columns= [col_name],
                rows= [[nid] for nid in result_ids],
                sql= f"BFSFastJson({source_id}, {predicates_json}, {max_hops})",
                params= [],
                metadata= sql_query.query_metadata
            )

        # Full path: caller wants labels/props — fall through to get_nodes()
        alias_match = _re.search(r'SELECT\s+DISTINCT\s+\S+\s+AS\s+(\w+)|SELECT\s+\S+\s+AS\s+(\w+)', sql_str, _re.IGNORECASE)
        col_name = (alias_match.group(1) or alias_match.group(2)) if alias_match else "b_id"

        if not target_ids:
            return IVGResult(                columns= [col_name, "b_labels", "b_props"],
                rows= [],
                sql= "",
                params= [],
                metadata= sql_query.query_metadata
            )

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

        return IVGResult(            columns= [col_name, "b_labels", "b_props"],
            rows= [list(r) for r in rows],
            sql= f"BFSFastJson({source_id}, {predicates_json}, {max_hops})",
            params= [],
            metadata= sql_query.query_metadata
        )

    def _try_khop_fast_path(self, cypher_query: str, parameters) -> Optional[Dict[str, Any]]:
        import re as _re

        _1HOP_COUNT_RE = _re.compile(
            r'''^\s*MATCH\s*\(\s*\w+\s*\{\s*node_id\s*:\s*\$(\w+)\s*\}\s*\)
                \s*-\s*\[\s*:\s*(\w+)\s*\]\s*->\s*\(\s*(\w+)\s*\)
                \s*RETURN\s+count\s*\(\s*\3\s*\)\s+AS\s+(\w+)\s*$''',
            _re.IGNORECASE | _re.VERBOSE,
        )
        _1HOP_IDS_RE = _re.compile(
            r'''^\s*MATCH\s*\(\s*\w+\s*\{\s*node_id\s*:\s*\$(\w+)\s*\}\s*\)
                \s*-\s*\[\s*:\s*(\w+)\s*\]\s*->\s*\(\s*(\w+)\s*\)
                \s*RETURN\s+\3\.node_id(?:\s+AS\s+(\w+))?\s*$''',
            _re.IGNORECASE | _re.VERBOSE,
        )
        _2HOP_COUNT_RE = _re.compile(
            r'''^\s*MATCH\s*\(\s*\w+\s*\{\s*node_id\s*:\s*\$(\w+)\s*\}\s*\)
                \s*-\s*\[\s*:\s*(\w+)\s*\*2\s*\]\s*->\s*\(\s*(\w+)\s*\)
                \s*RETURN\s+count\s*\(\s*\3\s*\)\s+AS\s+(\w+)\s*$''',
            _re.IGNORECASE | _re.VERBOSE,
        )
        _2HOP_IDS_RE = _re.compile(
            r'''^\s*MATCH\s*\(\s*\w+\s*\{\s*node_id\s*:\s*\$(\w+)\s*\}\s*\)
                \s*-\s*\[\s*:\s*(\w+)\s*\*2\s*\]\s*->\s*\(\s*(\w+)\s*\)
                \s*RETURN\s+\3\.node_id(?:\s+AS\s+(\w+))?(?:\s+LIMIT\s+(\d+))?\s*$''',
            _re.IGNORECASE | _re.VERBOSE,
        )

        params = parameters or {}

        m = _1HOP_COUNT_RE.match(cypher_query)
        if m:
            src_param, pred, _nvar, col = m.group(1), m.group(2), m.group(3), m.group(4)
            src_id = params.get(src_param)
            if src_id is None:
                return None
            try:
                cnt = int(self._iris_obj().classMethodValue(
                    "Graph.KG.Traversal", "KHopCount", str(src_id), pred
                ))
                return IVGResult(columns=[col], rows=[(cnt,)])
            except Exception:
                return None

        m = _1HOP_IDS_RE.match(cypher_query)
        if m:
            src_param, pred, _nvar, alias = m.group(1), m.group(2), m.group(3), m.group(4)
            src_id = params.get(src_param)
            if src_id is None:
                return None
            try:
                raw = str(self._iris_obj().classMethodValue(
                    "Graph.KG.Traversal", "KHopNeighborIds", str(src_id), pred
                ))
                ids = [x for x in raw.split("\n") if x]
                col = alias or "node_id"
                return IVGResult(columns=[col], rows=[(nid,) for nid in ids])
            except Exception:
                return None

        m = _2HOP_COUNT_RE.match(cypher_query)
        if m:
            src_param, pred, _nvar, col = m.group(1), m.group(2), m.group(3), m.group(4)
            src_id = params.get(src_param)
            if src_id is None:
                return None
            try:
                cnt = int(self._iris_obj().classMethodValue(
                    "Graph.KG.Traversal", "KHop2CountExact", str(src_id), pred
                ))
                return IVGResult(columns=[col], rows=[(cnt,)])
            except Exception:
                return None

        m = _2HOP_IDS_RE.match(cypher_query)
        if m:
            src_param, pred, _nvar, alias, limit_str = (
                m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
            )
            src_id = params.get(src_param)
            if src_id is None:
                return None
            limit = int(limit_str) if limit_str else 0
            try:
                raw = str(self._iris_obj().classMethodValue(
                    "Graph.KG.Traversal", "KHop2NeighborIds", str(src_id), pred, limit
                ))
                ids = [x for x in raw.split("\n") if x]
                col = alias or "node_id"
                return IVGResult(columns=[col], rows=[(nid,) for nid in ids])
            except Exception:
                return None

        return None

        return None

    def _execute_approx_count_distinct(self, cypher_query: str, parameters, match) -> Dict[str, Any]:
        import json as _json
        import re as _re
        from .schema import _call_classmethod

        col_name = match.group(2)

        from .cypher.parser import parse_query
        from .cypher.translator import translate_to_sql
        try:
            q = parse_query(cypher_query)
            sql_query = translate_to_sql(q, params=parameters or {})
        except Exception:
            return IVGResult(columns=[col_name], rows=[[0]], sql="", params=[])

        if not sql_query.var_length_paths:
            return IVGResult(columns=[col_name], rows=[[0]], sql="", params=[])

        vl = sql_query.var_length_paths[0]
        predicates_json = _json.dumps(vl["types"]) if vl["types"] else ""
        max_hops = vl["max_hops"]
        direction = vl.get("direction", "both")

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
                source_id = next(iter(parameters.values()), None) if parameters else None

        if not source_id:
            return IVGResult(columns=[col_name], rows=[[0]], sql="", params=[])

        try:
            raw = str(_call_classmethod(
                self.conn, "Graph.KG.NKGAccel", "CountDistinctKHop",
                source_id, predicates_json, max_hops, direction,
            ))
            result = _json.loads(raw)
            estimate = result.get("estimate", 0)
            registers = result.get("registers", 256)
            std_error = result.get("std_error", 0.065)
        except Exception as e:
            logger.warning(f"CountDistinctKHop failed: {e}")
            estimate = 0
            registers = 256
            std_error = 0.065

        from .cypher.translator import QueryMetadata
        meta = QueryMetadata(
            warnings=[
                f"approx_count_distinct: HLL-{registers}, "
                f"std_error={std_error*100:.1f}%, registers={registers}"
            ]
        )
        return IVGResult(            columns= [col_name],
            rows= [[estimate]],
            sql= f"CountDistinctKHop({source_id}, {predicates_json}, {max_hops})",
            params= [],
            metadata= meta
        )

    def _try_system_procedure(self, proc) -> Optional[Dict[str, Any]]:
        name = proc.procedure_name.lower()

        if name == "ivg.vector.search":
            from iris_vector_graph.cypher.ast import Literal as CypherLiteral, Variable as CypherVariable
            args = proc.arguments
            label_filter = str(args[0].value) if args and isinstance(args[0], CypherLiteral) else None
            k = int(args[3].value) if len(args) > 3 and isinstance(args[3], CypherLiteral) else 10
            vec_arg = args[2] if len(args) > 2 else None
            query_vector = None
            if isinstance(vec_arg, CypherLiteral) and isinstance(vec_arg.value, list):
                query_vector = vec_arg.value
            return self._store.execute_knn_vec(query_vector or [], k, label_filter)

        if name == "ivg.shortestpath.weighted":
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
                return IVGResult(columns=["path", "totalCost"], rows=[])

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
                return IVGResult(columns=["path", "totalCost"], rows=[])

            if not result_str or result_str == "{}":
                return IVGResult(columns=["path", "totalCost"], rows=[])

            try:
                path_obj = _json.loads(result_str)
            except Exception:
                return IVGResult(columns=["path", "totalCost"], rows=[])

            total_cost = float(path_obj.get("totalCost", 0))
            return IVGResult(                columns= ["path", "totalCost"],
                rows= [[result_str, total_cost]]
            )

        if name == "db.labels":
            cursor = self.conn.cursor()
            cursor.execute(
                "SELECT DISTINCT label FROM Graph_KG.rdf_labels ORDER BY label"
            )
            labels = [row[0] for row in cursor.fetchall()]
            return IVGResult(columns=["label"], rows=[[l] for l in labels])

        if name == "db.relationshiptypes":
            cursor = self.conn.cursor()
            cursor.execute("SELECT DISTINCT p FROM Graph_KG.rdf_edges ORDER BY p")
            types = [row[0] for row in cursor.fetchall()]
            return IVGResult(columns=["relationshipType"], rows=[[t] for t in types])

        if name == "db.schema.visualization":
            schema = self.get_schema_visualization()
            nodes = schema.get("nodes", [])
            rels = schema.get("relationships", [])
            return IVGResult(columns=["nodes", "relationships"], rows=[[nodes, rels]])

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
            return IVGResult(                columns= [
                    "nodeType",
                    "nodeLabels",
                    "propertyName",
                    "propertyTypes",
                    "mandatory",
                ],
                rows= rows
            )

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
            return IVGResult(                columns= ["relType", "propertyName", "propertyTypes", "mandatory"],
                rows= rows
            )

        if name == "dbms.components":
            return IVGResult(                columns= ["name", "versions", "edition"],
                rows= [["iris-vector-graph", ["5.0.0"], "community"]]
            )

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
            return IVGResult(                columns= [
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
                rows= procs
            )

        if name == "db.propertykeys":
            cursor = self.conn.cursor()
            cursor.execute(
                'SELECT DISTINCT TOP 1000 "key" FROM Graph_KG.rdf_props ORDER BY "key"'
            )
            keys = [row[0] for row in cursor.fetchall()]
            return IVGResult(columns=["propertyKey"], rows=[[k] for k in keys])

        if name == "dbms.clientconfig":
            return IVGResult(                columns= ["key", "value"],
                rows= [
                    ["browser.allow_outgoing_connections", "false"],
                    ["browser.credential_timeout", "0"],
                    ["browser.retain_connection_credentials", "true"],
                    ["browser.retain_editor_history", "true"],
                    ["browser.post_connect_cmd", ""],
                    ["dbms.security.auth_enabled", "false"],
                ]
            )

        if name == "dbms.security.showcurrentuser" or name == "dbms.showcurrentuser":
            return IVGResult(                columns= ["username", "roles", "flags"],
                rows= [["neo4j", [], []]]
            )

        if name == "dbms.functions":
            return IVGResult(                columns= [
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
                rows= []
            )

        if name == "dbms.queryjmx":
            cursor = self.conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM Graph_KG.nodes")
            node_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM Graph_KG.rdf_edges")
            edge_count = cursor.fetchone()[0]
            pfx = "org.neo4j:instance=kernel#0"
            return IVGResult(                columns= ["name", "description", "attributes"],
                rows= [
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
                ]
            )

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
            return IVGResult(                columns= [
                    "label",
                    "property",
                    "type",
                    "elementType",
                    "unique",
                    "index",
                    "existence",
                ],
                rows= rows
            )

        if name == "apoc.meta.schema":
            result = self._try_system_procedure(
                type("P", (), {"procedure_name": "apoc.meta.data"})()
            )
            return IVGResult(columns=["value"], rows=[[result or {}]])

        if name.startswith("apoc."):
            return IVGResult(columns=["value"], rows=[])

        if name.startswith("dbms.") or name.startswith("db."):
            return IVGResult(columns=["value"], rows=[])

        return None

    def _detect_stored_vector_dtype(self) -> str:
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                f"SELECT TOP 1 emb FROM {_table('kg_NodeEmbeddings')} WHERE emb IS NOT NULL"
            )
            row = cursor.fetchone()
            cursor.close()
            if row is None:
                return "DOUBLE"
            emb_csv = str(row[0])
            sample = ",".join(emb_csv.split(",")[:2])
            for dtype in ("FLOAT", "DOUBLE"):
                try:
                    c2 = self.conn.cursor()
                    c2.execute(
                        f"SELECT VECTOR_COSINE(emb, TO_VECTOR(?, {dtype})) FROM {_table('kg_NodeEmbeddings')} WHERE emb IS NOT NULL LIMIT 1",
                        [sample],
                    )
                    c2.fetchone()
                    c2.close()
                    logger.info("Auto-detected stored vector dtype: %s", dtype)
                    return dtype
                except Exception:
                    pass
        except Exception:
            pass
        return "DOUBLE"

    def _build_index_registry(self) -> Dict[str, str]:
        registry: Dict[str, str] = {}
        try:
            import iris as _iris_pkg
            if not callable(getattr(_iris_pkg, "gref", None)):
                raise AttributeError("iris.gref not available")
            for global_name, type_str in (
                ("^IVF",      "ivf"),
                ("^VecIdx",   "vec"),
                ("^BM25Idx",  "bm25"),
                ("^PLAID",    "plaid"),
            ):
                gref = _iris_pkg.gref(global_name)
                name = ""
                for _ in range(10000):
                    name = gref.order([name])
                    if not isinstance(name, str) or name == "":
                        break
                    registry[name] = type_str
        except Exception:
            pass
        if not registry:
            try:
                from iris_vector_graph.schema import _call_classmethod
                for cls_name, type_str in (
                    ("Graph.KG.IVFIndex",   "ivf"),
                    ("Graph.KG.BM25Index",  "bm25"),
                    ("Graph.KG.PLAIDSearch", "plaid"),
                ):
                    raw = str(_call_classmethod(self.conn, cls_name, "List"))
                    for name in (n.strip() for n in raw.split(",") if n.strip()):
                        registry[name] = type_str
            except Exception:
                pass
                for sql_query, type_str in (
                    ("SELECT DISTINCT name FROM Graph_KG.ivf_indexes", "ivf"),
                    ("SELECT DISTINCT name FROM Graph_KG.bm25_indexes", "bm25"),
                    ("SELECT DISTINCT name FROM Graph_KG.plaid_indexes", "plaid"),
                ):
                    try:
                        cur.execute(sql_query)
                        for row in cur.fetchall():
                            registry[str(row[0])] = type_str
                    except Exception:
                        pass
            except Exception:
                pass
        if self._probe_native_vec():
            registry["hnsw"] = "hnsw"
        return registry

    _LEGACY_TO_CONCEPT = {
        "ivf": "vector", "vec": "vector", "bm25": "fulltext",
        "plaid": "multivector", "hnsw": "hnsw",
        "neighborhood_vector": "neighborhood_vector",
    }

    def index(self, name: str) -> "Index":
        from iris_vector_graph.index_protocol import Index
        from iris_vector_graph.errors import IndexNotFoundError
        if name not in self._index_registry:
            raise IndexNotFoundError(name, known=list(self._index_registry))
        concept = self._LEGACY_TO_CONCEPT.get(
            self._index_registry[name], self._index_registry[name]
        )
        return Index(name=name, type=concept, engine=self)

    def create_index(self, config, replace: bool = False) -> "Index":
        from iris_vector_graph.index_protocol import Index
        if config.name in self._index_registry:
            if not replace:
                raise ValueError(
                    f"Index '{config.name}' already exists; pass replace=True to recreate."
                )
            self.index(config.name).drop()
        self._pending_index_config[config.name] = config
        self._index_registry[config.name] = config.type
        return Index(name=config.name, type=config.type, engine=self)

    def list_indexes(self) -> "List[Index]":
        return [self.index(n) for n in sorted(self._index_registry)]

    def _index_config(self, name: str):
        return self._pending_index_config.get(name)

    def _build_vector_index(self, name: str, **kw) -> dict:
        cfg = self._index_config(name)
        if cfg is not None and getattr(cfg, "method", "ivf") == "vec":
            self.vec_create_index(name, dim=kw.get("dim") or cfg.dim, metric=cfg.metric)
            return self.vec_build(name)
        nlist = kw.get("nlist", getattr(cfg, "nlist", 256))
        metric = kw.get("metric", getattr(cfg, "metric", "cosine"))
        return self.ivf_build(name, nlist=nlist, metric=metric, node_ids=kw.get("node_ids"))

    def _search_vector_index(self, name: str, q, k: int = 10, **kw) -> list:
        cfg = self._index_config(name)
        if cfg is not None and getattr(cfg, "method", "ivf") == "vec":
            return self.vec_search(name, q, k, **kw)
        return self.ivf_search(name, q, k, **kw)

    def _vector_index_insert(self, name: str, id_: str, vec) -> None:
        cfg = self._index_config(name)
        if cfg is not None and getattr(cfg, "method", "ivf") == "vec":
            self.vec_insert(name, id_, vec)
        else:
            self.ivf_insert(name, id_, vec)

    def _vector_index_drop(self, name: str) -> None:
        cfg = self._index_config(name)
        if cfg is not None and getattr(cfg, "method", "ivf") == "vec":
            self.vec_drop(name)
        else:
            self.ivf_drop(name)

    def _vector_index_info(self, name: str) -> dict:
        cfg = self._index_config(name)
        if cfg is not None and getattr(cfg, "method", "ivf") == "vec":
            return self.vec_info(name)
        return self.ivf_info(name)

    def _build_fulltext_index(self, name: str, **kw) -> dict:
        cfg = self._index_config(name)
        props = kw.get("properties") or (cfg.properties if cfg else ["name"])
        k1 = kw.get("k1", getattr(cfg, "k1", 1.5))
        b = kw.get("b", getattr(cfg, "b", 0.75))
        info = self.bm25_build(name, props, k1=k1, b=b)
        from iris_vector_graph.index_protocol import _rows_of
        from iris_vector_graph.errors import IndexNotBuiltError
        if _rows_of(info or {}) == 0:
            raise IndexNotBuiltError(name, rows=0)
        return info

    def _build_multivector_index(self, name: str, **kw) -> dict:
        docs = kw.get("docs")
        if not docs:
            from iris_vector_graph.errors import IndexNotBuiltError
            raise IndexNotBuiltError(name, rows=0)
        cfg = self._index_config(name)
        return self.plaid_build(
            name, docs,
            n_clusters=kw.get("n_clusters", getattr(cfg, "n_clusters", None)),
            dim=kw.get("dim", getattr(cfg, "dim", 128)),
        )

    def _build_neighborhood_index(self, name: str, **kw) -> dict:
        raise NotImplementedError(
            "neighborhood_vector index build lands in spec 181; "
            "config registered but build not yet wired."
        )

    def _search_neighborhood_index(self, name: str, q, k: int = 10, **kw) -> list:
        raise NotImplementedError("neighborhood_vector search lands in spec 181.")

    def _neighborhood_index_drop(self, name: str) -> None:
        self._iris_obj().kill("^NKG", "q")

    def _neighborhood_index_info(self, name: str) -> dict:
        return {"type": "neighborhood_vector", "rows": 0}

    def khop2_count_fast(self, node_id: str, predicate: str = "") -> int:
        KHop2Input(node_id=node_id)
        result = self._iris_obj().classMethodValue(
            "Graph.KG.Traversal", "KHop2CountFast", node_id, predicate
        )
        return int(result)

    def khop2_count_exact(self, node_id: str, predicate: str = "") -> int:
        KHop2Input(node_id=node_id)
        result = self._iris_obj().classMethodValue(
            "Graph.KG.Traversal", "KHop2CountExact", node_id, predicate
        )
        return int(result)

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

        query_cast = f"TO_VECTOR(?, {self.vector_dtype}, {dim})"

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
        self, query_vector: str, k: int = 50, label_filter: Optional[str] = None,
        dtype: Optional[str] = None,
    ) -> List[Tuple[str, float]]:
        _dtype = (dtype or self.vector_dtype).upper()
        cursor = self.conn.cursor()
        try:
            emb_table = _table("kg_NodeEmbeddings")
            labels_table = _table("rdf_labels")

            qv = query_vector.strip() if isinstance(query_vector, str) else query_vector
            exclude_id: Optional[str] = None
            if isinstance(qv, str) and not qv.startswith("["):
                exclude_id = qv
                cursor.execute(
                    f"SELECT emb FROM {emb_table} WHERE id = ?", [exclude_id]
                )
                row = cursor.fetchone()
                if not row:
                    return []
                query_vector = f"[{str(row[0])}]"

            if label_filter and exclude_id:
                cursor.execute(
                    f"SELECT TOP ? n.id, VECTOR_COSINE(n.emb, TO_VECTOR(?, {_dtype})) AS score"
                    f" FROM {emb_table} n"
                    f" LEFT JOIN {labels_table} L ON L.s = n.id"
                    f" WHERE L.label = ? AND n.id != ?"
                    f" ORDER BY score DESC",
                    [k, query_vector, label_filter, exclude_id],
                )
            elif label_filter:
                cursor.execute(
                    f"SELECT TOP ? n.id, VECTOR_COSINE(n.emb, TO_VECTOR(?, {_dtype})) AS score"
                    f" FROM {emb_table} n"
                    f" LEFT JOIN {labels_table} L ON L.s = n.id"
                    f" WHERE L.label = ?"
                    f" ORDER BY score DESC",
                    [k, query_vector, label_filter],
                )
            elif exclude_id:
                cursor.execute(
                    f"SELECT TOP ? n.id, VECTOR_COSINE(n.emb, TO_VECTOR(?, {_dtype})) AS score"
                    f" FROM {emb_table} n"
                    f" WHERE n.id != ?"
                    f" ORDER BY score DESC",
                    [k, query_vector, exclude_id],
                )
            else:
                cursor.execute(
                    f"SELECT TOP ? n.id, VECTOR_COSINE(n.emb, TO_VECTOR(?, {_dtype})) AS score"
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
            return self._kg_KNN_VEC_python_optimized(query_vector, k, label_filter)

    def search_nodes_by_vector(
        self,
        query: "Union[List[float], str]",
        k: int = 10,
        label_filter: Optional[str] = None,
        ivf_name: Optional[str] = None,
        nprobe: int = 8,
    ) -> List[Tuple[str, float]]:
        if not isinstance(query, str):
            VecSearchInput(query=list(query), k=k, nprobe=nprobe)
        if self._probe_native_vec():
            query_json = json.dumps([float(v) for v in query]) if not isinstance(query, str) else query
            return self.kg_KNN_VEC(query_json, k=k, label_filter=label_filter)
        if ivf_name is not None:
            query_list = json.loads(query) if isinstance(query, str) else query
            return self.ivf_search(ivf_name, query_list, k=k, nprobe=nprobe)
        query_list = json.loads(query) if isinstance(query, str) else query
        return self.ivf_search("default", query_list, k=k, nprobe=nprobe)

    def _kg_KNN_VEC_python_optimized(
        self, query_vector: str, k: int = 50, label_filter: Optional[str] = None
    ) -> List[Tuple[str, float]]:
        _dtype = getattr(self, 'vector_dtype', 'DOUBLE')
        emb_table = _table("kg_NodeEmbeddings")
        labels_table = _table("rdf_labels")

        if label_filter:
            sql = (
                f"SELECT TOP {int(k)} n.id, VECTOR_COSINE(n.emb, TO_VECTOR(?, {_dtype})) AS score"
                f" FROM {emb_table} n"
                f" LEFT JOIN {labels_table} L ON L.s = n.id"
                f" WHERE L.label = ?"
                f" ORDER BY score DESC"
            )
            params = [query_vector, label_filter]
        else:
            sql = (
                f"SELECT TOP {int(k)} n.id, VECTOR_COSINE(n.emb, TO_VECTOR(?, {_dtype})) AS score"
                f" FROM {emb_table} n"
                f" ORDER BY score DESC"
            )
            params = [query_vector]

        try:
            from iris_vector_graph.embedded import _sql_statement_execute, _is_ddtab_error
            rs = _sql_statement_execute(sql, params)
            results = [(row[0], float(row[1])) for row in rs if row[0] is not None]
            return results
        except Exception:
            pass

        try:
            cursor = self.conn.cursor()
            cursor.execute(sql, params)
            results = [(row[0], float(row[1])) for row in cursor.fetchall()]
            cursor.close()
            return results
        except Exception:
            pass

        return self._kg_KNN_VEC_client_side(query_vector, k, label_filter)

    def _kg_KNN_VEC_client_side(
        self, query_vector: str, k: int = 50, label_filter: Optional[str] = None
    ) -> List[Tuple[str, float]]:
        cursor = self.conn.cursor()
        try:
            import numpy as np

            query_array = np.array(json.loads(query_vector))

            emb_table = _table("kg_NodeEmbeddings")
            labels_table = _table("rdf_labels")
            if label_filter is None:
                cursor.execute(f"SELECT n.id, n.emb FROM {emb_table} n WHERE n.emb IS NOT NULL")
            else:
                cursor.execute(
                    f"SELECT n.id, n.emb FROM {emb_table} n"
                    f" LEFT JOIN {labels_table} L ON L.s = n.id"
                    f" WHERE n.emb IS NOT NULL AND L.label = ?",
                    [label_filter],
                )

            similarities = []
            while True:
                batch = cursor.fetchmany(1000)
                if not batch:
                    break
                for entity_id, emb_csv in batch:
                    try:
                        emb_array = np.fromstring(str(emb_csv), dtype=float, sep=",")
                        dot_product = np.dot(query_array, emb_array)
                        query_norm = np.linalg.norm(query_array)
                        emb_norm = np.linalg.norm(emb_array)
                        if query_norm > 0 and emb_norm > 0:
                            cos_sim = dot_product / (query_norm * emb_norm)
                            similarities.append((entity_id, float(cos_sim)))
                    except Exception:
                        continue

            similarities.sort(key=lambda x: x[1], reverse=True)
            return similarities[:k]

        except Exception as e:
            logger.error(f"Client-side kg_KNN_VEC failed: {e}")
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
            query_cast = f"TO_VECTOR(?, {self.vector_dtype}, {dim})"
        else:
            query_cast = f"TO_VECTOR(?, {self.vector_dtype})"

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
        vec_results: List[Tuple[str, float]] = []
        txt_results: List[Tuple[str, float]] = []

        import json as _json
        vec_list = _json.loads(query_vector) if isinstance(query_vector, str) else query_vector

        try:
            for idx_name in self._index_registry:
                if self._index_registry[idx_name] == "ivf":
                    raw = self.ivf_search(idx_name, vec_list, k=k1)
                    vec_results = [(r["id"], float(r.get("score", 0))) for r in raw]
                    break
            for idx_name in self._index_registry:
                if self._index_registry[idx_name] == "bm25":
                    txt_results = self.bm25_search(idx_name, query_text, k=k2)
                    break
        except Exception as e:
            logger.error(f"kg_RRF_FUSE index search failed: {e}")

        vec_rank = {nid: i + 1 for i, (nid, _) in enumerate(vec_results)}
        txt_rank = {nid: i + 1 for i, (nid, _) in enumerate(txt_results)}
        all_ids = set(vec_rank) | set(txt_rank)

        fused = []
        for nid in all_ids:
            v_r = vec_rank.get(nid, len(vec_results) + c)
            t_r = txt_rank.get(nid, len(txt_results) + c)
            rrf = 1.0 / (c + v_r) + 1.0 / (c + t_r)
            v_score = dict(vec_results).get(nid, 0.0)
            t_score = dict(txt_results).get(nid, 0.0)
            fused.append((nid, rrf, v_score, t_score))

        fused.sort(key=lambda x: -x[1])
        return fused[:k]

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

        if self._store_capabilities.get("ppr", True):
            result = self._store.execute_ppr(seed_entities, damping_factor, max_iterations)
            if not result.error:
                top_k = return_top_k
                rows = result.rows
                if top_k:
                    rows = rows[:top_k]
                return {r[0]: float(r[1]) for r in rows if len(r) >= 2}

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
            try:
                iris_obj.classMethodValue("Graph.KG.ArnoAccel", "Load")
            except Exception:
                pass
            try:
                iris_obj.classMethodValue("Graph.KG.NKGAccel", "Load")
            except Exception:
                pass
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
        iris_obj = self._iris_obj()
        raw = str(iris_obj.classMethodValue(cls, method, *args))
        if not raw.startswith("CHUNKED:"):
            return raw
        _, tag, n_str = raw.split(":", 2)
        n = int(n_str)
        return "".join(
            str(iris_obj.classMethodValue(cls, "ReadLargeOutChunk", tag, i))
            for i in range(1, n + 1)
        )

    def khop(self, seed: str, hops: int = 2, max_nodes: int = 500) -> dict:
        if hops > 1 and self._detect_arno() and "khop" in self._arno_capabilities.get(
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
        info = json.loads(str(result))
        self._index_registry[name] = "vec"
        return info

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
        info = json.loads(str(result))
        info.setdefault("type", "vec")
        return info

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

        centroids_json = json.dumps(kmeans.cluster_centers_.tolist())
        docs_json = json.dumps([
            {
                "id": doc["id"],
                "tokens": [[float(v) for v in tok] for tok in doc["tokens"]],
            }
            for doc in docs
        ])
        assignments_json = json.dumps(doc_token_map)

        result = self._iris_obj().classMethodValue(
            "Graph.KG.PLAIDSearch", "Build", name,
            centroids_json, docs_json, assignments_json
        )
        info = json.loads(str(result))
        self._index_registry[name] = "plaid"
        return info

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

    def bm25_build(
        self, name: str, text_props: list, k1: float = 1.5, b: float = 0.75
    ) -> dict:
        BM25BuildInput(name=name, text_props=text_props, k1=k1, b=b)
        props_csv = ",".join(text_props)
        result = self._iris_obj().classMethodValue(
            "Graph.KG.BM25Index", "Build", name, props_csv, k1, b
        )
        info = json.loads(str(result))
        self._index_registry[name] = "bm25"
        return info

    def bm25_search(self, name: str, query: str, k: int = 10) -> list:
        BM25SearchInput(name=name, query=query, k=k)
        result = self._iris_obj().classMethodValue(
            "Graph.KG.BM25Index", "Search", name, query, k
        )
        import re as _re
        raw = str(result)
        raw = _re.sub(r'(?<=[:\[,])(\.\d)', r'0\1', raw)
        rows = json.loads(raw)
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
        info = json.loads(str(result))
        info.setdefault("type", "bm25")
        return info

    def ivf_build(
        self,
        name: str,
        nlist: int = 256,
        metric: str = "cosine",
        batch_size: int = 10000,
        build_batch_size: int = 500,
        node_ids: Optional[List[str]] = None,
    ) -> dict:
        IVFBuildInput(name=name, nlist=nlist, metric=metric, batch_size=batch_size, build_batch_size=build_batch_size)
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
        if node_ids is not None:
            if not node_ids:
                raise ValueError("ivf_build: node_ids list is empty")
            placeholders = ",".join(["?"] * len(node_ids))
            cursor.execute(
                f"SELECT id, emb FROM Graph_KG.kg_NodeEmbeddings WHERE id IN ({placeholders})",
                node_ids,
            )
        else:
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

        iris_obj = self._iris_obj()

        result = iris_obj.classMethodValue(
            "Graph.KG.IVFIndex",
            "Build",
            name,
            _json.dumps(effective_nlist),
            _json.dumps(metric),
            _json.dumps(centroids),
            "[]",
        )

        for batch_start in range(0, n_nodes, build_batch_size):
            batch = []
            for i in range(batch_start, min(batch_start + build_batch_size, n_nodes)):
                batch.append(
                    {"nodeId": node_ids[i], "cellIdx": int(labels[i]), "vec": vecs[i]}
                )
            iris_obj.classMethodValue(
                "Graph.KG.IVFIndex", "AddBatch", name, _json.dumps(batch)
            )

        iris_obj.classMethodValue("Graph.KG.IVFIndex", "FinalizeIndex", name)
        info = iris_obj.classMethodValue("Graph.KG.IVFIndex", "Info", name)
        result = _json.loads(str(info))
        self._index_registry[name] = "ivf"
        return result

    def ivf_search(self, name: str, query: list, k: int = 10, nprobe: int = 8) -> list:
        VectorSearchInput(name=name, query=query, k=k, nprobe=nprobe)
        query_json = json.dumps([float(v) for v in query])
        result = self._iris_obj().classMethodValue(
            "Graph.KG.IVFIndex", "Search", name, query_json, k, nprobe
        )
        rows = json.loads(str(result))
        return [(r["id"], float(r["score"])) for r in rows]

    def ivf_insert(self, name: str, node_id: str, vector: list) -> int:
        vec_json = json.dumps([float(v) for v in vector])
        cell = int(self._iris_obj().classMethodValue(
            "Graph.KG.IVFIndex", "Insert", name, node_id, vec_json
        ))
        if cell < 0:
            raise ValueError(f"ivf_insert: index '{name}' not found — call ivf_build first")
        return cell

    def ivf_delete(self, name: str, node_id: str) -> bool:
        removed = int(self._iris_obj().classMethodValue(
            "Graph.KG.IVFIndex", "Delete", name, node_id
        ))
        return bool(removed)

    def ivf_drop(self, name: str) -> None:
        self._iris_obj().classMethodVoid("Graph.KG.IVFIndex", "Drop", name)

    def ivf_info(self, name: str) -> dict:
        result = self._iris_obj().classMethodValue("Graph.KG.IVFIndex", "Info", name)
        info = json.loads(str(result))
        if info:
            info.setdefault("type", "ivf")
        return info

    def kg_GRAPH_PATH(self, src_id: str, pred1: str, pred2: str, max_hops: int = 2):
        result = self.execute_cypher(
            "MATCH (a {node_id: $src})-[r1]->(b)-[r2]->(c) "
            "WHERE type(r1) = $p1 AND type(r2) = $p2 "
            "RETURN 1 AS path_id, 1 AS step, a.node_id, type(r1), b.node_id "
            "UNION ALL "
            "MATCH (a {node_id: $src})-[r1]->(b)-[r2]->(c) "
            "WHERE type(r1) = $p1 AND type(r2) = $p2 "
            "RETURN 1 AS path_id, 2 AS step, b.node_id, type(r2), c.node_id",
            {"src": src_id, "p1": pred1, "p2": pred2},
        )
        return [(int(r[0]), int(r[1]), r[2], r[3], r[4]) for r in (result.get("rows") or [])]

    def kg_GRAPH_WALK(self, start_entity: str, max_depth: int = 3,
                      edge_types: Optional[List[str]] = None,
                      max_results: int = 100):
        preds_json = json.dumps(edge_types) if edge_types else "[]"
        from iris_vector_graph.schema import _call_classmethod, _call_classmethod_large
        raw = str(_call_classmethod(
            self.conn, "Graph.KG.Traversal", "BFSFastJsonSorted",
            start_entity, preds_json, max_depth, "", "out", max_results
        ))
        if raw.startswith("SORTED:") and raw != "SORTED:0":
            tag = raw.split(":")[1]
            json_str = str(_call_classmethod_large(
                self._iris_obj(), "Graph.KG.Traversal", "ReadBFSResults", tag))
            rows = json.loads(json_str) if json_str else []
            return [(r.get("s", ""), r.get("p", ""), r.get("o", ""), r.get("step", 1))
                    for r in rows]
        return []

    def kg_GRAPH_WALK_TVF(self, start_entity: str, max_depth: int = 3,
                           edge_types: Optional[List[str]] = None,
                           max_results: int = 100):
        return self.kg_GRAPH_WALK(start_entity, max_depth, edge_types, max_results)

    def kg_PAGERANK(self, seed_entities: Optional[List[str]] = None,
                    damping: float = 0.85, max_iterations: int = 20,
                    bidirectional: bool = False, reverse_weight: float = 1.0):
        if seed_entities is not None:
            return self.kg_PERSONALIZED_PAGERANK(
                seed_entities, damping_factor=damping, max_iterations=max_iterations,
            )
        from iris_vector_graph.schema import _call_classmethod
        raw = str(_call_classmethod(self.conn, "Graph.KG.PageRank", "PageRankGlobalJson",
                                    damping, max_iterations))
        parsed = json.loads(raw) if raw else []
        return [(item["id"], float(item["score"])) for item in parsed]

    def kg_WCC(self, max_iterations: int = 100) -> Dict[str, Any]:
        if self._store_capabilities.get("wcc", True):
            result = self._store.execute_wcc()
            if not result.error:
                return {r[0]: r[1] for r in result.rows if len(r) >= 2}
        from iris_vector_graph.schema import _call_classmethod
        raw = str(_call_classmethod(self.conn, "Graph.KG.Algorithms", "WCCJson", max_iterations))
        return json.loads(raw) if raw else {}

    def kg_CDLP(self, max_iterations: int = 10) -> Dict[str, Any]:
        if self._store_capabilities.get("cdlp", True):
            result = self._store.execute_cdlp(max_iterations)
            if not result.error:
                return {r[0]: r[1] for r in result.rows if len(r) >= 2}
        from iris_vector_graph.schema import _call_classmethod
        raw = str(_call_classmethod(self.conn, "Graph.KG.Algorithms", "CDLPJson", max_iterations))
        return json.loads(raw) if raw else {}

    def kg_SUBGRAPH(self, seed_ids: List[str], k_hops: int = 2,
                    edge_types: Optional[List[str]] = None,
                    include_properties: bool = True,
                    include_embeddings: bool = False,
                    max_nodes: int = 10000):
        from iris_vector_graph.models import SubgraphData
        if not seed_ids:
            return SubgraphData(seed_ids=list(seed_ids))
        if self._store_capabilities.get("subgraph", True):
            result = self._store.execute_subgraph(seed_ids, k_hops, edge_types or [], max_nodes)
            if result.error is None:
                import json as _j
                if result.rows:
                    nodes = _j.loads(result.rows[0][0]) if result.rows[0][0] else []
                    edges = _j.loads(result.rows[0][1]) if result.rows[0][1] else []
                else:
                    nodes, edges = [], []
                return SubgraphData(nodes=nodes, edges=edges, seed_ids=list(seed_ids))
        from iris_vector_graph.schema import _call_classmethod
        seed_json = json.dumps(seed_ids)
        edge_types_json = json.dumps(edge_types) if edge_types else ""
        raw = str(_call_classmethod(self.conn, "Graph.KG.Subgraph", "SubgraphJson",
                                    seed_json, k_hops, edge_types_json, max_nodes))
        if raw:
            parsed = json.loads(raw)
            nodes = parsed.get("nodes", [])
            edges = [(e["s"], e["p"], e["o"]) for e in parsed.get("edges", [])]
            node_properties = parsed.get("properties", {})
            node_labels = parsed.get("labels", {})
            node_embeddings: Dict[str, Any] = {}
            if include_embeddings and nodes:
                emb_table = _table("kg_NodeEmbeddings")
                cursor = self.conn.cursor()
                phs = ",".join(["?"] * len(nodes))
                cursor.execute(
                    f"SELECT id, emb FROM {emb_table} WHERE id IN ({phs})", nodes
                )
                for row in cursor.fetchall():
                    nid, emb_csv = row[0], str(row[1])
                    try:
                        import numpy as _np
                        node_embeddings[nid] = list(_np.fromstring(emb_csv, dtype=float, sep=","))
                    except Exception:
                        pass
                cursor.close()
            return SubgraphData(
                seed_ids=seed_ids, nodes=nodes, edges=edges,
                node_properties=node_properties, node_labels=node_labels,
                node_embeddings=node_embeddings,
            )
        return SubgraphData(seed_ids=seed_ids)

    def kg_PPR_GUIDED_SUBGRAPH(self, seed_ids: List[str], ppr_top_k: int = 50,
                                k_hops: int = 1, damping: float = 0.85,
                                max_iterations: int = 10,
                                edge_types: Optional[List[str]] = None,
                                max_nodes: int = 5000):
        from iris_vector_graph.models import PprGuidedSubgraphData
        if not seed_ids:
            return PprGuidedSubgraphData(seed_ids=[], nodes=[], edges=[], ppr_scores=[])
        ppr_scores = self.kg_PERSONALIZED_PAGERANK(seed_ids, damping_factor=damping,
                                                     max_iterations=max_iterations)
        if isinstance(ppr_scores, dict):
            sorted_scores = sorted(ppr_scores.items(), key=lambda x: -x[1])
            top_ids = [k for k, _ in sorted_scores[:ppr_top_k]]
        else:
            sorted_scores = sorted(ppr_scores, key=lambda x: -x[1])
            top_ids = [item[0] for item in sorted_scores[:ppr_top_k]]
        all_seeds = list(dict.fromkeys(seed_ids + top_ids))
        subgraph = self.kg_SUBGRAPH(all_seeds, k_hops=k_hops, edge_types=edge_types,
                                    max_nodes=min(max_nodes, ppr_top_k))
        return PprGuidedSubgraphData(
            seed_ids=seed_ids,
            nodes=subgraph.nodes,
            edges=[{"src": e[0], "dst": e[2], "type": e[1]} for e in subgraph.edges if isinstance(e, (list, tuple)) and len(e) == 3]
                  if subgraph.edges and isinstance(subgraph.edges[0], (list, tuple))
                  else subgraph.edges,
            ppr_scores=sorted_scores[:ppr_top_k],
            nodes_before_pruning=len(subgraph.nodes),
            nodes_after_pruning=len(subgraph.nodes),
        )

    def kg_NEIGHBORS(self, source_ids: List[str], predicate: Optional[str] = None,
                     direction: str = "out", distinct: bool = True,
                     chunk_size: int = 500) -> List[str]:
        if not source_ids:
            return []
        if direction not in ("out", "in", "both"):
            raise ValueError(f"direction must be 'out', 'in', or 'both', got {direction!r}")
        all_targets: List[str] = []
        seen: set = set()
        for i in range(0, len(source_ids), chunk_size):
            chunk = source_ids[i:i + chunk_size]
            for src in chunk:
                dirs = ["out", "in"] if direction == "both" else [direction]
                for d in dirs:
                    if d == "out":
                        q = ("MATCH (s {node_id: $id})-[r]->(t) " +
                             ("WHERE type(r)=$p " if predicate else "") +
                             "RETURN t.node_id")
                    else:
                        q = ("MATCH (t)-[r]->(s {node_id: $id}) " +
                             ("WHERE type(r)=$p " if predicate else "") +
                             "RETURN t.node_id")
                    params: Dict[str, Any] = {"id": src}
                    if predicate:
                        params["p"] = predicate
                    r = self.execute_cypher(q, params)
                    for row in (r.get("rows") or []):
                        t = row[0]
                        if t and (not distinct or t not in seen):
                            all_targets.append(t)
                            seen.add(t)
        return all_targets

    def kg_MENTIONS(self, source_ids: List[str], predicate: str = "MENTIONS",
                    direction: str = "out") -> List[str]:
        return self.kg_NEIGHBORS(source_ids, predicate=predicate, direction=direction)

    def kg_PPR(self, seed_entities: List[str], damping: float = 0.85,
               max_iterations: int = 20) -> List[Tuple[str, float]]:
        if not seed_entities:
            return []
        result = self.kg_PERSONALIZED_PAGERANK(seed_entities, damping_factor=damping,
                                                max_iterations=max_iterations)
        if isinstance(result, dict):
            return sorted(result.items(), key=lambda x: -x[1])
        return result

    def kg_RERANK(self, top_n: int, query_vector: str, query_text: str):
        return self.kg_RRF_FUSE(k=top_n, k1=top_n * 2, k2=top_n * 2, c=60,
                                 query_vector=query_vector, query_text=query_text)

    def degree_centrality(
        self,
        direction: str = "out",
        predicate: Optional[str] = None,
        top_k: int = 10000,
    ) -> List[Dict[str, Any]]:
        """Degree centrality — node connectivity.

        Measures how many edges connect to each node (in/out/bidirectional). Useful for 
        identifying hubs and peripheral nodes. Normalized to (n-1).

        Args:
            direction: Edge direction — "out" (outbound), "in" (inbound), or "both" (undirected). Default "out".
            predicate: Optional relationship type to filter by (e.g., "DEPENDS_ON"). None = all predicates.
            top_k: Maximum results to return. 0 = all nodes (with warning if > 100K).

        Returns:
            List of dicts sorted by score descending, each containing:
                - id (str): Node identifier.
                - score (float): Normalized degree (value / (n-1)).
                - degree (int): Raw edge count.

        Example:
            >>> scores = engine.degree_centrality(direction="out", top_k=20)
            >>> print(scores[0])  # {"id": "hub-node", "score": 0.847, "degree": 12}

        Note:
            Performance tier: ObjectScript parallel (8× workers) when `^NKG` built, 
            otherwise Python LazyKG. See docs/performance/GRAPH_ALGORITHMS.md.
        """
        from iris_vector_graph._validate import DegreeCentralityInput
        validated = DegreeCentralityInput(
            direction=direction,
            predicate=predicate,
            top_k=top_k,
        )

        if not getattr(self, "_store", None) or not self._store.capabilities().get("degree_centrality", False):
            raise NotImplementedError(
                f"Centrality.degree_centrality not supported by store "
                f"{type(self._store).__name__ if getattr(self, '_store', None) else 'None'}"
            )

        if validated.top_k == 0:
            try:
                count_result = self._store.get_node_count()
                node_count = int(count_result.rows[0][0]) if count_result.rows else 0
                if node_count > 100_000:
                    import warnings
                    warnings.warn(
                        f"degree_centrality(top_k=0) on {node_count}-node graph may produce large JSON",
                        RuntimeWarning,
                        stacklevel=2,
                    )
            except Exception:
                pass

        result = self._store.execute_degree_centrality(
            validated.direction,
            validated.predicate or "",
            validated.top_k,
        )
        if result.error:
            return []
        return [
            {"id": row[0], "score": row[1], "degree": row[2]}
            for row in result.rows
        ]

    def betweenness_centrality(
        self,
        sample_size: int = 0,
        direction: str = "out",
        max_hops: int = 0,
        top_k: int = 10000,
        mem_budget_mb: int = 256,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> List[Dict[str, Any]]:
        """Betweenness centrality via Brandes (2001) algorithm.

        Measures how often a node appears on shortest paths between other nodes.
        Uses three-tier dispatch: Rust accelerator (fastest) → ObjectScript parallel 
        (8× workers) → Python LazyKG (always works).

        Args:
            sample_size: Number of source nodes for Brandes-Pich approximation.
                0 uses the maxSources cap (default 200). Set equal to node count
                for exact full Brandes.
            direction: Edge direction — "out", "in", or "both". Default "out".
            max_hops: Maximum BFS depth per source. 0 = unbounded.
            top_k: Maximum results to return. 0 = all nodes.
            mem_budget_mb: Memory budget in MB for predecessor accumulator.
            progress_callback: Optional callable(completed, total) for progress reporting.

        Returns:
            List of dicts sorted by score descending, each containing:
                - id (str): Node identifier.
                - score (float): Raw betweenness score (sum of dependency values
                  scaled by n/sample_size if sampled).

        Example:
            >>> scores = engine.betweenness_centrality(sample_size=200, top_k=20)
            >>> print(scores[0])  # {"id": "hub-node", "score": 4821.3}

        Note:
            Performance tiers require `^NKG` to be built (`engine.rebuild_nkg()`).
            Without the accelerator library, falls back to ObjectScript parallel
            (~500ms on ER(2000)). See docs/performance/GRAPH_ALGORITHMS.md.
        """
        from iris_vector_graph._validate import BetweennessInput
        validated = BetweennessInput(
            sample_size=sample_size,
            direction=direction,
            max_hops=max_hops,
            top_k=top_k,
            mem_budget_mb=mem_budget_mb,
        )

        if not getattr(self, "_store", None) or not self._store.capabilities().get("betweenness", False):
            raise NotImplementedError(
                f"Centrality.betweenness not supported by store "
                f"{type(self._store).__name__ if getattr(self, '_store', None) else 'None'}"
            )

        if validated.top_k == 0:
            try:
                count_result = self._store.get_node_count()
                node_count = int(count_result.rows[0][0]) if count_result.rows else 0
                if node_count > 100_000:
                    import warnings
                    warnings.warn(
                        f"betweenness_centrality(top_k=0) on {node_count}-node graph may produce large JSON",
                        RuntimeWarning,
                        stacklevel=2,
                    )
            except Exception:
                pass

        result = self._store.execute_betweenness(
            validated.sample_size,
            validated.direction,
            validated.max_hops,
            validated.top_k,
            validated.mem_budget_mb,
            progress_callback,
        )
        if result.error:
            return []

        out: List[Dict[str, Any]] = []
        for row in result.rows:
            if len(row) == 2 and row[0] == "_meta" and isinstance(row[1], dict):
                out.append(row[1])
            else:
                out.append({"id": row[0], "score": row[1]})
        return out

    def betweenness_centrality_neighborhood(
        self,
        seed: str,
        hops: int = 2,
        sample_size: int = 200,
        top_k: int = 20,
    ) -> List[Dict[str, Any]]:
        """Betweenness centrality within a node's neighborhood.

        Extracts a k-hop neighborhood around a seed node and computes Brandes 
        betweenness only on that subgraph. Scales to biomedical KGs: performance depends 
        on neighborhood size, not total graph size.

        Args:
            seed: Seed node ID (e.g., "MESH:D009101" for Multiple Myeloma).
            hops: Neighborhood radius in hops (default 2). Typical biomedical: 2–3 hops = 500–5K nodes.
            sample_size: Number of source nodes for Brandes approximation (default 200).
            top_k: Maximum results to return (default 20).

        Returns:
            List of dicts sorted by score descending, each containing:
                - id (str): Node identifier.
                - score (float): Betweenness within the neighborhood subgraph.
                - hops (int): Distance from seed node.

        Example:
            >>> scores = engine.betweenness_centrality_neighborhood(
            ...     seed="MESH:D009101", hops=2, top_k=20
            ... )
            >>> print(scores[0])  # {"id": "TP53", "score": 1234.5, "hops": 1}

        Note:
            Use this for disease-gene bottleneck analysis. Rust accelerator extracts 
            subgraph via process-static adjacency cache (~microseconds), then runs 
            rayon parallel Brandes on the subgraph only.
        """
        if not getattr(self, "_store", None):
            raise NotImplementedError("No store configured")
        result = self._store.execute_betweenness_neighborhood(seed, hops, sample_size, top_k)
        if result.error:
            return []
        return [{"id": r[0], "score": r[1]} for r in result.rows]

    def bfs_vector_rerank(
        self,
        seed: str,
        query_vec: List[float],
        hops: int = 2,
        top_k: int = 10,
        max_buckets: int = 32,
    ) -> List[Dict[str, Any]]:
        """Graph-filtered semantic search: fused BFS + vector reranking.

        Finds nodes that are BOTH reachable from a seed within `hops` BFS steps
        AND semantically similar to `query_vec`. Graph topology defines the
        candidate scope; vector similarity defines relevance. This is the
        biomedical drug-discovery pattern — "which genes are connected to this
        disease AND similar to my target gene?"

        Uses the NICHE quantized bucket index (`^NKG("q",...)`): the BFS frontier
        is pruned to nodes in the query's nearest IVF buckets before full-precision
        cosine reranking.

        Args:
            seed: Seed node ID to start the BFS from (e.g., "Gene::7157" for TP53).
            query_vec: Query embedding vector (same dimension as node embeddings).
            hops: BFS radius (default 2). Larger neighborhoods cost more.
            top_k: Maximum results to return (default 10).
            max_buckets: Number of nearest IVF buckets to scan (default 32).
                Higher = better recall, slower. 32 gives recall@10 ≈ 0.90 on
                400-dim TransE embeddings.

        Returns:
            List of dicts sorted by score descending, each containing:
                - id (str): Node identifier.
                - score (float): Cosine similarity to query_vec.
                - hops (int): BFS distance from seed.

        Example:
            >>> tp53_vec = engine.get_node_embedding("Gene::7157")
            >>> hits = engine.bfs_vector_rerank(
            ...     seed="Gene::7157", query_vec=tp53_vec, hops=1, top_k=10
            ... )
            >>> print(hits[0])  # {"id": "Gene::8626", "score": 0.63, "hops": 1}

        Note:
            Requires the NICHE bucket index to be built (see scripts/niche/).
            Returns [] if the bucket index is absent or the seed is not found.
            Performance: ObjectScript path ~28ms for hops=1 on a 18K-node graph.
            The sub-millisecond Rust accelerator path is planned for v2.1.x.
        """
        if not getattr(self, "_store", None):
            raise NotImplementedError("No store configured")
        result = self._store.execute_bfs_vector_rerank(seed, query_vec, hops, top_k, max_buckets)
        if result.error:
            return []
        return [{"id": r[0], "score": r[1], "hops": r[2]} for r in result.rows]

    def closeness_centrality(
        self,
        formula: str = "harmonic",
        direction: str = "out",
        max_hops: int = 0,
        top_k: int = 10000,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> List[Dict[str, Any]]:
        """Closeness centrality — how close a node is to all other nodes.

        Measures how quickly a node can reach other nodes via shortest paths.
        Uses either the classical formula (undefined for disconnected graphs) or 
        the harmonic formula (robust for disconnected graphs).

        Args:
            formula: "harmonic" (default, Beauchamp 1965, works on disconnected) 
                or "classical" (standard Bavelas-Freeman, undefined for disconnected).
            direction: Edge direction — "out", "in", or "both". Default "out".
            max_hops: Maximum BFS depth. 0 = unbounded (full graph).
            top_k: Maximum results to return. 0 = all nodes.
            progress_callback: Optional callable(completed, total) for progress reporting.

        Returns:
            List of dicts sorted by score descending, each containing:
                - id (str): Node identifier.
                - score (float): Closeness score (depends on formula choice).

        Example:
            >>> scores = engine.closeness_centrality(formula="harmonic", top_k=20)
            >>> print(scores[0])  # {"id": "central-node", "score": 0.823}

        Note:
            Harmonic formula = 1 / (average shortest-path distance), so it works 
            on disconnected components. Classical formula is undefined when any node 
            is unreachable.
        """
        from iris_vector_graph._validate import ClosenessInput
        validated = ClosenessInput(
            formula=formula,
            direction=direction,
            max_hops=max_hops,
            top_k=top_k,
        )

        if not getattr(self, "_store", None) or not self._store.capabilities().get("closeness", False):
            raise NotImplementedError(
                f"Centrality.closeness not supported by store "
                f"{type(self._store).__name__ if getattr(self, '_store', None) else 'None'}"
            )

        if validated.top_k == 0:
            try:
                count_result = self._store.get_node_count()
                node_count = int(count_result.rows[0][0]) if count_result.rows else 0
                if node_count > 100_000:
                    import warnings
                    warnings.warn(
                        f"closeness_centrality(top_k=0) on {node_count}-node graph may produce large JSON",
                        RuntimeWarning,
                        stacklevel=2,
                    )
            except Exception:
                pass

        result = self._store.execute_closeness(
            validated.formula,
            validated.direction,
            validated.max_hops,
            validated.top_k,
            progress_callback,
        )
        if result.error:
            return []
        return [{"id": row[0], "score": row[1]} for row in result.rows]

    def eigenvector_centrality(
        self,
        max_iter: int = 30,
        tol: float = 1e-6,
        top_k: int = 10000,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> List[Dict[str, Any]]:
        """Eigenvector centrality — influence by neighbor influence.

        Iterative power method over the raw adjacency matrix A (not the transition 
        matrix). Measures influence: a node is important if it's connected to other 
        important nodes. L2-normalized output. Matches `networkx.eigenvector_centrality_numpy`.

        Args:
            max_iter: Maximum power iterations (default 30). Typical convergence: 5–15 iters.
            tol: Convergence tolerance for L2 norm change (default 1e-6).
            top_k: Maximum results to return. 0 = all nodes.
            progress_callback: Optional callable(completed, total) for progress reporting.

        Returns:
            List of dicts sorted by score descending, each containing:
                - id (str): Node identifier.
                - score (float): L2-normalized eigenvector component (range 0–1).

        Example:
            >>> scores = engine.eigenvector_centrality(max_iter=30, top_k=20)
            >>> print(scores[0])  # {"id": "influential-node", "score": 0.894}

        Note:
            Convergence requires the largest eigenvalue to be unique (no symmetry).
            Falls back to Python LazyKG if ObjectScript or Rust path unavailable.
        """
        from iris_vector_graph._validate import EigenvectorInput
        validated = EigenvectorInput(
            max_iter=max_iter,
            tol=tol,
            top_k=top_k,
        )

        if not getattr(self, "_store", None) or not self._store.capabilities().get("eigenvector", False):
            raise NotImplementedError(
                f"Centrality.eigenvector not supported by store "
                f"{type(self._store).__name__ if getattr(self, '_store', None) else 'None'}"
            )

        if validated.top_k == 0:
            try:
                count_result = self._store.get_node_count()
                node_count = int(count_result.rows[0][0]) if count_result.rows else 0
                if node_count > 100_000:
                    import warnings
                    warnings.warn(
                        f"eigenvector_centrality(top_k=0) on {node_count}-node graph may produce large JSON",
                        RuntimeWarning,
                        stacklevel=2,
                    )
            except Exception:
                pass

        result = self._store.execute_eigenvector(
            validated.max_iter,
            validated.tol,
            validated.top_k,
            progress_callback,
        )
        if result.error:
            return []
        return [{"id": row[0], "score": row[1]} for row in result.rows]

    def leiden_communities(
        self,
        max_levels: int = 10,
        gamma: float = 1.0,
        tol: float = 1e-4,
        top_k: int = 10000,
        mem_budget_mb: int = 256,
        random_seed: Optional[int] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> List[Dict[str, Any]]:
        """Leiden community detection (Traag et al. 2019).

        Partitions the graph into densely-connected communities using the Leiden
        algorithm, which fixes the "badly connected community" problem in Louvain.
        Supports modularity (gamma=1.0) and CPM (resolution parameter) quality functions.

        Args:
            max_levels: Maximum number of aggregation levels (default 10).
            gamma: Resolution parameter. 1.0 = ModularityVertexPartition (default,
                canonical Leiden). Values < 1.0 produce fewer, larger communities;
                values > 1.0 produce more, smaller communities.
            tol: Convergence tolerance (default 1e-4).
            top_k: Maximum results to return. 0 = all nodes.
            mem_budget_mb: Memory budget in MB for community tracking.
            random_seed: Seed for reproducibility. None = stochastic.
            progress_callback: Optional callable(completed, total).

        Returns:
            List of dicts sorted by community ascending, each containing:
                - id (str): Node identifier.
                - community (int): Community index (0 = largest, 1 = second-largest, ...).
                - size (int): Number of nodes in this community.

        Example:
            >>> communities = engine.leiden_communities(gamma=1.0, top_k=100)
            >>> print(communities[0])  # {"id": "node-a", "community": 0, "size": 23}

        Note:
            Uses Rust accelerator (leiden-rs) when libarno_callout.so is deployed.
            Falls back to Python leidenalg or networkx Louvain. Quality matches 
            leidenalg reference (ARI=1.0 on karate club).
        """
        from iris_vector_graph._validate import LeidenInput
        validated = LeidenInput(
            max_levels=max_levels, gamma=gamma, tol=tol, top_k=top_k,
            mem_budget_mb=mem_budget_mb, random_seed=random_seed,
        )

        if not getattr(self, "_store", None) or not self._store.capabilities().get("leiden", False):
            raise NotImplementedError(
                f"Communities.leiden not supported by store "
                f"{type(self._store).__name__ if getattr(self, '_store', None) else 'None'}"
            )

        if validated.top_k == 0:
            try:
                count_result = self._store.get_node_count()
                node_count = int(count_result.rows[0][0]) if count_result.rows else 0
                if node_count > 100_000:
                    import warnings
                    warnings.warn(
                        f"leiden_communities(top_k=0) on {node_count}-node graph may produce large JSON",
                        RuntimeWarning, stacklevel=2,
                    )
            except Exception:
                pass

        result = self._store.execute_leiden(
            validated.max_levels, validated.gamma, validated.tol,
            validated.top_k, validated.mem_budget_mb,
            validated.random_seed, progress_callback,
        )
        if result.error:
            return []

        out: List[Dict[str, Any]] = []
        for row in result.rows:
            if len(row) >= 1 and row[0] == "_meta":
                import json as _json
                meta = _json.loads(row[1]) if isinstance(row[1], str) else row[1]
                out.append(meta if isinstance(meta, dict) else {"_meta": row[1]})
            else:
                out.append({"id": row[0], "community": row[1], "size": row[2]})
        return out

    def triangle_count(
        self,
        top_k: int = 10000,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> List[Dict[str, Any]]:
        """Triangle count and local clustering coefficient.

        Counts triangles incident to each node and computes the local clustering 
        coefficient (LCC) — fraction of a node's neighbors that are also connected 
        to each other. High LCC indicates tightly-knit local neighborhoods.

        Args:
            top_k: Maximum results to return (default 10000). 0 = all nodes.
            progress_callback: Optional callable(completed, total) for progress reporting.

        Returns:
            List of dicts sorted by triangle count descending, each containing:
                - id (str): Node identifier.
                - triangles (int): Number of triangles involving this node.
                - lcc (float): Local clustering coefficient (0–1).

        Example:
            >>> results = engine.triangle_count(top_k=50)
            >>> print(results[0])  # {"id": "hub-node", "triangles": 45, "lcc": 0.73}

        Note:
            Uses symmetrized adjacency (treats graph as undirected for deduplication).
            LCC = 2 * triangles / (k * (k-1)) where k is node degree.
        """
        from iris_vector_graph._validate import TriangleCountInput
        validated = TriangleCountInput(top_k=top_k)

        if not getattr(self, "_store", None) or not self._store.capabilities().get("triangle_count", False):
            raise NotImplementedError(
                f"Communities.triangle_count not supported by store "
                f"{type(self._store).__name__ if getattr(self, '_store', None) else 'None'}"
            )

        result = self._store.execute_triangle_count(validated.top_k, progress_callback)
        if result.error:
            return []
        return [{"id": row[0], "triangles": row[1], "lcc": row[2]} for row in result.rows]

    def strongly_connected_components(
        self,
        top_k: int = 10000,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> List[Dict[str, Any]]:
        """Strongly connected components (Tarjan 1972, iterative).

        Partitions directed graph into SCCs — maximal sets of nodes where every node 
        is reachable from every other node. Detects feedback loops and cycles in workflows.

        Args:
            top_k: Maximum results to return (default 10000). 0 = all nodes.
            progress_callback: Optional callable(completed, total) for progress reporting.

        Returns:
            List of dicts sorted by component ascending, each containing:
                - id (str): Node identifier.
                - component (int): Component index (0 = first SCC, etc.).
                - size (int): Number of nodes in this SCC.

        Example:
            >>> sccs = engine.strongly_connected_components(top_k=100)
            >>> print(sccs[0])  # {"id": "node-a", "component": 0, "size": 8}

        Note:
            Iterative Tarjan (1972) with explicit DFS stack to avoid Python recursion limits.
            Matches `networkx.strongly_connected_components` exactly.
        """
        from iris_vector_graph._validate import SCCInput
        validated = SCCInput(top_k=top_k)

        if not getattr(self, "_store", None) or not self._store.capabilities().get("scc", False):
            raise NotImplementedError(
                f"Communities.scc not supported by store "
                f"{type(self._store).__name__ if getattr(self, '_store', None) else 'None'}"
            )

        result = self._store.execute_scc(validated.top_k, progress_callback)
        if result.error:
            return []
        return [{"id": row[0], "component": row[1], "size": row[2]} for row in result.rows]

    def k_core(
        self,
        top_k: int = 10000,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> List[Dict[str, Any]]:
        """K-core decomposition (Batagelj-Zaversnik 2003, O(V+E)).

        Recursively removes nodes with degree < k, iteratively increasing k. The k-core 
        is the maximal subgraph where all nodes have degree ≥ k. High coreness nodes form 
        the network's structural core; low coreness nodes are peripheral.

        Args:
            top_k: Maximum results to return (default 10000). 0 = all nodes.
            progress_callback: Optional callable(completed, total) for progress reporting.

        Returns:
            List of dicts sorted by coreness descending, each containing:
                - id (str): Node identifier.
                - coreness (int): K-core index (higher = more central/core).

        Example:
            >>> cores = engine.k_core(top_k=100)
            >>> print(cores[0])  # {"id": "core-hub", "coreness": 5}

        Note:
            Uses bucket-sort O(V+E) algorithm (Batagelj-Zaversnik 2003) over symmetrized 
            adjacency. Matches `networkx.core_number` per-node values exactly.
        """
        from iris_vector_graph._validate import KCoreInput
        validated = KCoreInput(top_k=top_k)

        if not getattr(self, "_store", None) or not self._store.capabilities().get("k_core", False):
            raise NotImplementedError(
                f"Communities.k_core not supported by store "
                f"{type(self._store).__name__ if getattr(self, '_store', None) else 'None'}"
            )

        result = self._store.execute_k_core(validated.top_k, progress_callback)
        if result.error:
            return []
        return [{"id": row[0], "coreness": row[1]} for row in result.rows]

