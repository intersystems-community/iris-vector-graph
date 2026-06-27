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
from iris_vector_graph._engine.query import QueryMixin
from iris_vector_graph._engine.algorithms import AlgorithmsMixin
from iris_vector_graph._engine.vector import VectorMixin
from iris_vector_graph._engine.snapshot import SnapshotMixin
from iris_vector_graph._engine.fhir import FhirMixin
from iris_vector_graph._engine.admin import AdminMixin
from iris_vector_graph._engine.embeddings import EmbeddingsMixin
from iris_vector_graph._engine.schema import SchemaMixin
from iris_vector_graph._engine.nodes_edges import NodesEdgesMixin, _BulkLoadSession
from iris_vector_graph._engine.rdf_export import RdfExportMixin
from iris_vector_graph._engine.shacl import ShaclMixin
from iris_vector_graph._engine.prov import ProvMixin

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

class IRISGraphEngine(RdfExportMixin, ShaclMixin, ProvMixin, TemporalMixin, SnapshotMixin, FhirMixin, AdminMixin, EmbeddingsMixin, SchemaMixin, NodesEdgesMixin, QueryMixin, AlgorithmsMixin, VectorMixin):
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
        self._native_conn = None  # dedicated connection for iris.createIRIS — never used for cursor DDL
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
        # Standard connection seam: iris-embedded-python-wrapper's dbapi.connect
        # (drop-in for iris.connect with DB-API exception semantics).
        conn = _iris.dbapi.connect(**conn_params)
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
                    self.conn = _iris.dbapi.connect(**self._connection_params)
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












    _SYSTEM_PROCEDURES = {
        "ivg.vector.search": "_proc_ivg_vector_search",
        "ivg.shortestpath.weighted": "_proc_ivg_shortestpath_weighted",
        "db.labels": "_proc_db_labels",
        "db.relationshiptypes": "_proc_db_relationshiptypes",
        "db.schema.visualization": "_proc_db_schema_visualization",
        "db.schema.nodetypeproperties": "_proc_db_schema_nodetypeproperties",
        "db.schema.reltypeproperties": "_proc_db_schema_reltypeproperties",
        "dbms.components": "_proc_dbms_components",
        "dbms.procedures": "_proc_dbms_procedures",
        "db.propertykeys": "_proc_db_propertykeys",
        "dbms.clientconfig": "_proc_dbms_clientconfig",
        "dbms.security.showcurrentuser": "_proc_dbms_security_showcurrentuser",
        "dbms.showcurrentuser": "_proc_dbms_security_showcurrentuser",
        "dbms.functions": "_proc_dbms_functions",
        "dbms.queryjmx": "_proc_dbms_queryjmx",
        "apoc.meta.data": "_proc_apoc_meta_data",
        "apoc.meta.schema": "_proc_apoc_meta_schema",
    }

    def _proc_ivg_vector_search(self, proc) -> Optional[Dict[str, Any]]:
        from iris_vector_graph.cypher.ast import Literal as CypherLiteral, Variable as CypherVariable
        args = proc.arguments
        label_filter = str(args[0].value) if args and isinstance(args[0], CypherLiteral) else None
        k = int(args[3].value) if len(args) > 3 and isinstance(args[3], CypherLiteral) else 10
        vec_arg = args[2] if len(args) > 2 else None
        query_vector = None
        if isinstance(vec_arg, CypherLiteral) and isinstance(vec_arg.value, list):
            query_vector = vec_arg.value
        return self._store.execute_knn_vec(query_vector or [], k, label_filter)

    def _proc_ivg_shortestpath_weighted(self, proc) -> Optional[Dict[str, Any]]:
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
        return IVGResult(columns=["path", "totalCost"], rows=[[result_str, total_cost]])

    def _proc_db_labels(self, proc) -> Optional[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT DISTINCT label FROM Graph_KG.rdf_labels ORDER BY label"
        )
        labels = [row[0] for row in cursor.fetchall()]
        return IVGResult(columns=["label"], rows=[[l] for l in labels])

    def _proc_db_relationshiptypes(self, proc) -> Optional[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT DISTINCT p FROM Graph_KG.rdf_edges ORDER BY p")
        types = [row[0] for row in cursor.fetchall()]
        return IVGResult(columns=["relationshipType"], rows=[[t] for t in types])

    def _proc_db_schema_visualization(self, proc) -> Optional[Dict[str, Any]]:
        schema = self.get_schema_visualization()
        nodes = schema.get("nodes", [])
        rels = schema.get("relationships", [])
        return IVGResult(columns=["nodes", "relationships"], rows=[[nodes, rels]])

    def _proc_db_schema_nodetypeproperties(self, proc) -> Optional[Dict[str, Any]]:
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
        return IVGResult(
            columns=[
                "nodeType",
                "nodeLabels",
                "propertyName",
                "propertyTypes",
                "mandatory",
            ],
            rows=rows
        )

    def _proc_db_schema_reltypeproperties(self, proc) -> Optional[Dict[str, Any]]:
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
        return IVGResult(
            columns=["relType", "propertyName", "propertyTypes", "mandatory"],
            rows=rows
        )

    def _proc_dbms_components(self, proc) -> Optional[Dict[str, Any]]:
        return IVGResult(
            columns=["name", "versions", "edition"],
            rows=[["iris-vector-graph", ["5.0.0"], "community"]]
        )

    def _proc_dbms_procedures(self, proc) -> Optional[Dict[str, Any]]:
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
        return IVGResult(
            columns=[
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
            rows=procs
        )

    def _proc_db_propertykeys(self, proc) -> Optional[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            'SELECT DISTINCT TOP 1000 "key" FROM Graph_KG.rdf_props ORDER BY "key"'
        )
        keys = [row[0] for row in cursor.fetchall()]
        return IVGResult(columns=["propertyKey"], rows=[[k] for k in keys])

    def _proc_dbms_clientconfig(self, proc) -> Optional[Dict[str, Any]]:
        return IVGResult(
            columns=["key", "value"],
            rows=[
                ["browser.allow_outgoing_connections", "false"],
                ["browser.credential_timeout", "0"],
                ["browser.retain_connection_credentials", "true"],
                ["browser.retain_editor_history", "true"],
                ["browser.post_connect_cmd", ""],
                ["dbms.security.auth_enabled", "false"],
            ]
        )

    def _proc_dbms_security_showcurrentuser(self, proc) -> Optional[Dict[str, Any]]:
        return IVGResult(
            columns=["username", "roles", "flags"],
            rows=[["neo4j", [], []]]
        )

    def _proc_dbms_functions(self, proc) -> Optional[Dict[str, Any]]:
        return IVGResult(
            columns=[
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
            rows=[]
        )

    def _proc_dbms_queryjmx(self, proc) -> Optional[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM Graph_KG.nodes")
        node_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM Graph_KG.rdf_edges")
        edge_count = cursor.fetchone()[0]
        pfx = "org.neo4j:instance=kernel#0"
        return IVGResult(
            columns=["name", "description", "attributes"],
            rows=[
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

    def _proc_apoc_meta_data(self, proc) -> Optional[Dict[str, Any]]:
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
        return IVGResult(
            columns=[
                "label",
                "property",
                "type",
                "elementType",
                "unique",
                "index",
                "existence",
            ],
            rows=rows
        )

    def _proc_apoc_meta_schema(self, proc) -> Optional[Dict[str, Any]]:
        result = self._try_system_procedure(
            type("P", (), {"procedure_name": "apoc.meta.data"})()
        )
        return IVGResult(columns=["value"], rows=[[result or {}]])

    def _try_system_procedure(self, proc) -> Optional[Dict[str, Any]]:
        name = proc.procedure_name.lower()

        # GDS → ivg shim: intercept gds.* calls before normal dispatch.
        if name.startswith("gds."):
            from iris_vector_graph._engine.query import _handle_gds_shim
            gds_result = _handle_gds_shim(proc)
            if gds_result is not None:
                if isinstance(gds_result, tuple):
                    # (shimmed_proc, None) sentinel — re-dispatch with ivg procedure name.
                    return self._try_system_procedure(gds_result[0])
                # IVGResult with error — return as-is.
                return gds_result

        handler_method_name = self._SYSTEM_PROCEDURES.get(name)
        if handler_method_name is not None:
            handler = getattr(self, handler_method_name)
            return handler(proc)

        if name.startswith("apoc."):
            return IVGResult(columns=["value"], rows=[])

        if name.startswith("dbms.") or name.startswith("db."):
            return IVGResult(columns=["value"], rows=[])

        return None



    _LEGACY_TO_CONCEPT = {
        "ivf": "vector", "vec": "vector", "bm25": "fulltext",
        "plaid": "multivector", "hnsw": "hnsw",
        "neighborhood_vector": "neighborhood_vector",
    }
























    # Text Search Operations

    # Graph Traversal Operations






    # Personalized PageRank Operations


    # --- Arno acceleration (optional) ---

    def _detect_arno(self) -> bool:
        if self._arno_available is not None:
            return self._arno_available
        detector = getattr(self._store, "_detect_arno", None)
        if detector is None:
            self._arno_available = False
            self._arno_capabilities = {}
            return False
        self._arno_available = detector()
        self._arno_capabilities = dict(getattr(self._store, "_arno_capabilities", {}) or {})
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





    # ── VecIndex: lightweight ANN vector search in globals ──

    def _iris_obj(self):
        # Use a dedicated connection so that iris.createIRIS() never touches self.conn.
        # Mixing createIRIS() and cursor DDL (DROP/CREATE INDEX) on the same connection
        # permanently corrupts the IRIS Python driver's parameter binding state.
        #
        # In test contexts the pytest conftest monkeypatches iris.createIRIS to redirect
        # createIRIS(self.conn) to a shared session-level native connection, avoiding
        # redundant open connections under Community Edition's 5-connection limit.
        # We detect the monkeypatch by checking the function name: the real createIRIS
        # is a C extension; the monkeypatch is a Python closure named "_safe_createIRIS".
        import iris
        _create = iris.createIRIS
        if getattr(_create, "__name__", "") == "_safe_createIRIS":
            # Test context: monkeypatch handles isolation — call directly.
            return _create(self.conn)
        # Production path: open a dedicated native connection (never used for cursor DDL).
        if self._native_conn is None or getattr(self._native_conn, "isClosed", lambda: True)():
            try:
                hostname = getattr(self.conn, "hostname", None)
                port = getattr(self.conn, "port", None)
                namespace = getattr(self.conn, "namespace", None)
                if hostname and port and namespace:
                    import iris.dbapi as _dbapi
                    self._native_conn = _dbapi.connect(
                        hostname=hostname,
                        port=port,
                        namespace=namespace,
                        username="_SYSTEM",
                        password="SYS",
                    )
                else:
                    return _create(self.conn)
            except Exception:
                return _create(self.conn)
        return _create(self._native_conn)










    # ── PLAID: multi-vector retrieval (ColBERT-style) ──







































