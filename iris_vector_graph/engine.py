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

class IRISGraphEngine(TemporalMixin, SnapshotMixin, FhirMixin, AdminMixin, EmbeddingsMixin, SchemaMixin, NodesEdgesMixin, QueryMixin, AlgorithmsMixin):
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























