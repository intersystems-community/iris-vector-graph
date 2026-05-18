import json
import logging
from typing import Any, Dict, List, Optional

from iris_vector_graph.result import IVGResult

logger = logging.getLogger(__name__)

_FULL_CAPABILITIES = {
    "native_sql": True,
    "bfs": True,
    "shortest_path": True,
    "weighted_shortest_path": True,
    "ppr": True,
    "pagerank": True,
    "wcc": True,
    "cdlp": True,
    "subgraph": True,
    "knn_vec": True,
    "temporal_edges": True,
    "temporal_window_query": True,
    "temporal_cypher": True,
    "temporal_aggregate": True,
}


class IRISGraphStore:
    def __init__(self, conn):
        self.conn = conn
        self._arno_available: Optional[bool] = None
        self._arno_capabilities: Dict[str, Any] = {}

    # ── Internal helpers (moved from engine) ─────────────────────────────────

    def _iris_obj(self):
        import iris as _iris
        return _iris.createIRIS(self.conn)

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

    def _call_classmethod(self, cls: str, method: str, *args):
        iris_obj = self._iris_obj()
        return iris_obj.classMethodValue(cls, method, *args)

    def _kg_PERSONALIZED_PAGERANK_python_fallback(self, seed_ids, damping=0.85, max_iterations=20):
        return []

    def _kg_KNN_VEC_python_optimized(self, query_vector, k, label_filter=None):
        return IVGResult(columns=["id", "score"], rows=[])

    def _kg_KNN_VEC_client_side(self, query_vector, k, label_filter=None):
        return IVGResult(columns=["id", "score"], rows=[])

    def _khop_fallback(self, seed, hops, max_nodes):
        return {"nodes": [], "edges": []}

    # ── Node & Edge Reads ─────────────────────────────────────────────────────

    def get_nodes(self, node_ids: list, properties: Optional[list] = None) -> IVGResult:
        if not node_ids:
            return IVGResult(columns=["id", "labels"], rows=[])
        placeholders = ",".join("?" * len(node_ids))
        cursor = self.conn.cursor()
        cursor.execute(
            f"SELECT s, label FROM Graph_KG.rdf_labels WHERE s IN ({placeholders})",
            node_ids,
        )
        labels_by_id: Dict[str, List[str]] = {}
        for row in cursor.fetchall():
            labels_by_id.setdefault(row[0], []).append(row[1])

        props_by_id: Dict[str, Dict[str, Any]] = {}
        if properties:
            cursor.execute(
                f'SELECT s, "key", val FROM Graph_KG.rdf_props WHERE s IN ({placeholders})',
                node_ids,
            )
            for row in cursor.fetchall():
                nid, key, val = row[0], row[1], row[2]
                if key in properties:
                    try:
                        parsed = json.loads(val) if val else None
                    except (json.JSONDecodeError, TypeError):
                        parsed = val
                    props_by_id.setdefault(nid, {})[key] = parsed

        cols = ["id", "labels"] + (properties or [])
        rows = []
        for nid in node_ids:
            labels_json = json.dumps(labels_by_id.get(nid, []))
            props = props_by_id.get(nid, {})
            row = [nid, labels_json] + [props.get(p) for p in (properties or [])]
            rows.append(row)
        return IVGResult(columns=cols, rows=rows)

    def get_node_labels(self, node_ids: list) -> IVGResult:
        if not node_ids:
            return IVGResult(columns=["id", "labels"], rows=[])
        result = self.get_nodes(node_ids, properties=[])
        return IVGResult(columns=["id", "labels"], rows=[[r[0], r[1]] for r in result.rows])

    def query_nodes(
        self,
        label_filter: Optional[str] = None,
        property_filters: Optional[dict] = None,
        return_properties: Optional[list] = None,
        limit: int = 0,
    ) -> IVGResult:
        cursor = self.conn.cursor()
        if label_filter:
            cursor.execute(
                "SELECT DISTINCT s FROM Graph_KG.rdf_labels WHERE label = ?",
                [label_filter],
            )
            node_ids = [r[0] for r in cursor.fetchall()]
        else:
            cursor.execute("SELECT node_id FROM Graph_KG.nodes" + (" FETCH FIRST ? ROWS ONLY" if limit else ""),
                           [limit] if limit else [])
            node_ids = [r[0] for r in cursor.fetchall()]

        if property_filters:
            filtered = []
            for nid in node_ids:
                match = True
                for key, val in property_filters.items():
                    cursor.execute(
                        'SELECT val FROM Graph_KG.rdf_props WHERE s = ? AND "key" = ?',
                        [nid, key],
                    )
                    row = cursor.fetchone()
                    if row is None:
                        match = False
                        break
                    try:
                        stored = json.loads(row[0]) if row[0] else None
                    except (json.JSONDecodeError, TypeError):
                        stored = row[0]
                    if stored != val:
                        match = False
                        break
                if match:
                    filtered.append(nid)
            node_ids = filtered

        if limit and len(node_ids) > limit:
            node_ids = node_ids[:limit]

        return self.get_nodes(node_ids, return_properties)

    # ── Mutations ─────────────────────────────────────────────────────────────

    def write_nodes(self, nodes: list) -> IVGResult:
        cursor = self.conn.cursor()
        written = 0
        for node in nodes:
            nid = node.get("id") or node.get("node_id")
            if not nid:
                continue
            err_lower_check = lambda e: ("unique" in str(e).lower() or "-119" in str(e))
            try:
                cursor.execute(
                    "INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [nid]
                )
            except Exception as e:
                if not err_lower_check(e):
                    logger.warning("write_nodes insert failed: %s", e)
            for label in (node.get("labels") or []):
                try:
                    cursor.execute(
                        "INSERT INTO Graph_KG.rdf_labels (s, label) VALUES (?, ?)", [nid, label]
                    )
                except Exception as e:
                    if not err_lower_check(e):
                        logger.warning("write_nodes label failed: %s", e)
            for key, val in (node.get("properties") or {}).items():
                val_str = json.dumps(val) if not isinstance(val, str) else f'"{val}"'
                try:
                    cursor.execute(
                        'INSERT INTO Graph_KG.rdf_props (s, "key", val) VALUES (?, ?, ?)',
                        [nid, key, val_str],
                    )
                except Exception as e:
                    if not err_lower_check(e):
                        logger.warning("write_nodes prop failed: %s", e)
            written += 1
        self.conn.commit()
        return IVGResult(columns=["written"], rows=[[written]])

    def write_edges(self, edges: list) -> IVGResult:
        cursor = self.conn.cursor()
        written = 0
        err_lower_check = lambda e: ("unique" in str(e).lower() or "-119" in str(e))
        for edge in edges:
            s = edge.get("source") or edge.get("s")
            p = edge.get("predicate") or edge.get("p")
            o = edge.get("target") or edge.get("o")
            w = float(edge.get("weight", 1.0))
            qualifiers = edge.get("qualifiers") or {}
            if not (s and p and o):
                continue
            try:
                cursor.execute(
                    "INSERT INTO Graph_KG.rdf_edges (s, p, o_id, weight) VALUES (?, ?, ?, ?)",
                    [s, p, o, w],
                )
            except Exception as e:
                if not err_lower_check(e):
                    logger.warning("write_edges insert failed: %s", e)
            try:
                cursor.execute(
                    "INSERT INTO Graph_KG.rdf_edges_adjout (s, p, o_id) VALUES (?, ?, ?)",
                    [s, p, o],
                )
            except Exception:
                pass
            written += 1
        self.conn.commit()
        return IVGResult(columns=["written"], rows=[[written]])

    def delete_nodes(self, node_ids: list) -> IVGResult:
        if not node_ids:
            return IVGResult(columns=["deleted"], rows=[[0]])
        cursor = self.conn.cursor()
        placeholders = ",".join("?" * len(node_ids))
        cursor.execute(f"DELETE FROM Graph_KG.rdf_props WHERE s IN ({placeholders})", node_ids)
        cursor.execute(f"DELETE FROM Graph_KG.rdf_labels WHERE s IN ({placeholders})", node_ids)
        cursor.execute(
            f"DELETE FROM Graph_KG.rdf_edges WHERE s IN ({placeholders}) OR o_id IN ({placeholders})",
            node_ids + node_ids,
        )
        cursor.execute(f"DELETE FROM Graph_KG.nodes WHERE node_id IN ({placeholders})", node_ids)
        deleted = len(node_ids)
        self.conn.commit()
        return IVGResult(columns=["deleted"], rows=[[deleted]])

    def delete_edges(self, edges: list) -> IVGResult:
        if not edges:
            return IVGResult(columns=["deleted"], rows=[[0]])
        cursor = self.conn.cursor()
        deleted = 0
        for edge in edges:
            s, p, o = edge[0], edge[1], edge[2]
            cursor.execute(
                "DELETE FROM Graph_KG.rdf_edges WHERE s = ? AND p = ? AND o_id = ?",
                [s, p, o],
            )
            deleted += 1
        self.conn.commit()
        return IVGResult(columns=["deleted"], rows=[[deleted]])

    # ── SQL Passthrough ───────────────────────────────────────────────────────

    def execute_sql(self, sql: str, params: list, read_only: bool = True) -> IVGResult:
        cursor = self.conn.cursor()
        try:
            cursor.execute(sql, params)
            cols = [d[0] for d in cursor.description] if cursor.description else []
            rows = cursor.fetchall()
            return IVGResult(columns=cols, rows=[list(r) for r in rows], sql=sql, params=params)
        except Exception as e:
            err = str(e)[:200]
            logger.warning("execute_sql error: %s", err)
            try:
                self.conn.rollback()
            except Exception:
                pass
            return IVGResult(columns=[], rows=[], sql=sql, params=params, error=err)

    def execute_transaction(self, stmts: list, params_list: list) -> IVGResult:
        cursor = self.conn.cursor()
        cursor.execute("START TRANSACTION")
        try:
            rows = []
            for i, stmt in enumerate(stmts):
                p = params_list[i] if i < len(params_list) else []
                cursor.execute(stmt, p)
                if cursor.description:
                    rows = cursor.fetchall()
            self.conn.commit()
            cols = [d[0] for d in cursor.description] if cursor.description else []
            return IVGResult(columns=cols, rows=[list(r) for r in rows])
        except Exception as e:
            self.conn.rollback()
            raise

    # ── Traversal ─────────────────────────────────────────────────────────────

    def execute_bfs(
        self, source_id: str, predicates: list, max_hops: int,
        direction: str, max_results: int
    ) -> IVGResult:
        import json as _json
        predicates_json = _json.dumps(predicates) if predicates else ""

        if self._detect_arno() and self._arno_capabilities.get("bfs"):
            try:
                raw = self._arno_call(
                    "Graph.KG.NKGAccel", "BFSJson",
                    source_id, predicates_json, str(max_hops),
                    str(max_results), direction,
                )
                raw_val = raw if isinstance(raw, str) else str(raw)
                if raw_val.startswith("SORTED:"):
                    tag = raw_val.split(":", 2)[1]
                    iris_obj = self._iris_obj()
                    pages = []
                    i = 1
                    while True:
                        chunk = str(iris_obj.classMethodValue(
                            "Graph.KG.NKGAccel", "ReadBFSPage", tag, i
                        ))
                        if not chunk or chunk == "":
                            break
                        pages.append(chunk)
                        i += 1
                    raw_val = "".join(pages)
                results = _json.loads(raw_val) if raw_val else []
                if not isinstance(results, list):
                    results = []
                rows = [[r.get("id", r.get("node_id", "")), r.get("hops", 0), r.get("pred", "")] for r in results]
                return IVGResult(columns=["id", "hops", "pred"], rows=rows)
            except Exception as e:
                logger.warning("Arno BFS failed, falling back to ObjectScript: %s", e)

        try:
            bfs_json = str(self._call_classmethod(
                "Graph.KG.Traversal", "BFSFastJsonSorted",
                source_id, predicates_json, str(max_hops),
                str(max_results), direction,
            ))
        except Exception as e:
            logger.warning("BFS ObjectScript failed: %s", e)
            return IVGResult(columns=["id", "hops", "pred"], rows=[])

        val = bfs_json if isinstance(bfs_json, str) else str(bfs_json)
        if val.startswith("SORTED:"):
            from iris_vector_graph.engine import _bfs_stream_pages
            tag = val.split(":", 2)[1]
            results = list(_bfs_stream_pages(self.conn, tag))
            rows = [[r.get("o", r.get("id", "")), r.get("step", r.get("hops", 0)), r.get("pred", r.get("p", ""))] for r in results]
            return IVGResult(columns=["id", "hops", "pred"], rows=rows)

        try:
            results = _json.loads(val) if val else []
            if not isinstance(results, list):
                results = []
        except Exception:
            results = []

        rows = [[r.get("id", r.get("node_id", "")), r.get("hops", 0), r.get("pred", "")] for r in results]
        return IVGResult(columns=["id", "hops", "pred"], rows=rows)

    def execute_shortest_path(
        self, source_id: str, target_id: str, predicates: list,
        max_hops: int, direction: str, find_all: bool
    ) -> IVGResult:
        import json as _json
        predicates_json = _json.dumps(predicates) if predicates else ""
        try:
            path_json = str(self._call_classmethod(
                "Graph.KG.Traversal", "ShortestPathJson",
                source_id, target_id, str(max_hops),
                predicates_json, direction, str(int(find_all)),
            ))
            paths_raw = _json.loads(path_json) if path_json else []
            if isinstance(paths_raw, dict):
                paths = [paths_raw]
            else:
                paths = paths_raw
            rows = [[_json.dumps(p), p.get("length", len(p.get("nodes", [])) - 1)] for p in paths if p]
            return IVGResult(columns=["path", "length"], rows=rows)
        except Exception as e:
            logger.warning("ShortestPath failed: %s", e)
            return IVGResult(columns=["path", "length"], rows=[])

    def execute_weighted_shortest_path(
        self, source_id: str, target_id: str, weight_property: str, max_hops: int
    ) -> IVGResult:
        import json as _json
        try:
            result_json = str(self._call_classmethod(
                "Graph.KG.Traversal", "DijkstraJson",
                source_id, target_id, weight_property, str(max_hops),
            ))
            result = _json.loads(result_json) if result_json else {}
            if not result:
                return IVGResult(columns=["path", "totalCost"], rows=[])
            return IVGResult(
                columns=["path", "totalCost"],
                rows=[[_json.dumps(result), result.get("totalCost", 0.0)]],
            )
        except Exception as e:
            logger.warning("DijkstraPath failed: %s", e)
            return IVGResult(columns=["path", "totalCost"], rows=[])

    # ── Analytics ─────────────────────────────────────────────────────────────

    def execute_ppr(self, seed_ids: list, damping: float, max_iterations: int) -> IVGResult:
        import json as _json
        seeds_json = _json.dumps(seed_ids)
        try:
            if self._detect_arno() and "ppr" in self._arno_capabilities.get("algorithms", []):
                raw = self._arno_call("Graph.KG.NKGAccel", "PPRJson", seeds_json, str(damping), str(max_iterations))
            else:
                raw = str(self._call_classmethod("Graph.KG.PageRank", "PPRJson", seeds_json, str(damping), str(max_iterations)))
            results = _json.loads(raw) if raw else []
            rows = [[r.get("id", ""), float(r.get("score", 0))] for r in results]
            return IVGResult(columns=["id", "score"], rows=rows)
        except Exception as e:
            logger.warning("PPR failed: %s", e)
            return IVGResult(columns=["id", "score"], rows=[])

    def execute_pagerank(self, damping: float, max_iterations: int) -> IVGResult:
        import json as _json
        try:
            if self._detect_arno() and "pagerank" in self._arno_capabilities.get("algorithms", []):
                raw = self._arno_call("Graph.KG.NKGAccel", "PageRankJson", str(damping), str(max_iterations))
            else:
                raw = str(self._call_classmethod("Graph.KG.PageRank", "RunJson", str(damping), str(max_iterations)))
            results = _json.loads(raw) if raw else []
            rows = [[r.get("id", ""), float(r.get("score", 0))] for r in results]
            return IVGResult(columns=["id", "score"], rows=rows)
        except Exception as e:
            logger.warning("PageRank failed: %s", e)
            return IVGResult(columns=["id", "score"], rows=[])

    def execute_wcc(self) -> IVGResult:
        import json as _json
        try:
            if self._detect_arno() and "wcc" in self._arno_capabilities.get("algorithms", []):
                raw = self._arno_call("Graph.KG.NKGAccel", "WCCJson")
            else:
                raw = str(self._call_classmethod("Graph.KG.PageRank", "WCCJson"))
            results = _json.loads(raw) if raw else {}
            if isinstance(results, dict):
                rows = [[k, v] for k, v in results.items()]
            else:
                rows = [[r.get("id", ""), r.get("component_id", 0)] for r in results]
            return IVGResult(columns=["id", "component_id"], rows=rows)
        except Exception as e:
            logger.warning("WCC failed: %s", e)
            return IVGResult(columns=["id", "component_id"], rows=[])

    def execute_cdlp(self, max_iterations: int) -> IVGResult:
        import json as _json
        try:
            if self._detect_arno() and "cdlp" in self._arno_capabilities.get("algorithms", []):
                raw = self._arno_call("Graph.KG.NKGAccel", "CDLPJson", str(max_iterations))
            else:
                raw = str(self._call_classmethod("Graph.KG.PageRank", "CDLPJson", str(max_iterations)))
            results = _json.loads(raw) if raw else {}
            if isinstance(results, dict):
                rows = [[k, v] for k, v in results.items()]
            else:
                rows = [[r.get("id", ""), r.get("community_id", 0)] for r in results]
            return IVGResult(columns=["id", "community_id"], rows=rows)
        except Exception as e:
            logger.warning("CDLP failed: %s", e)
            return IVGResult(columns=["id", "community_id"], rows=[])

    def execute_subgraph(
        self, seed_ids: list, k_hops: int, edge_types: list, max_nodes: int
    ) -> IVGResult:
        import json as _json
        seeds_json = _json.dumps(seed_ids)
        edge_json = _json.dumps(edge_types) if edge_types else ""
        try:
            if self._detect_arno() and "subgraph" in self._arno_capabilities.get("algorithms", []):
                raw = self._arno_call("Graph.KG.NKGAccel", "SubgraphJson", seeds_json, str(k_hops), edge_json, str(max_nodes))
            else:
                raw = str(self._call_classmethod("Graph.KG.PageRank", "SubgraphJson", seeds_json, str(k_hops), edge_json, str(max_nodes)))
            result = _json.loads(raw) if raw else {"nodes": [], "edges": []}
            nodes = result.get("nodes", [])
            edges = result.get("edges", [])
            return IVGResult(columns=["nodes", "edges"], rows=[[_json.dumps(nodes), _json.dumps(edges)]])
        except Exception as e:
            logger.warning("Subgraph failed: %s", e)
            return IVGResult(columns=["nodes", "edges"], rows=[["[]", "[]"]])

    def execute_knn_vec(self, query_vector: list, k: int, label_filter: Optional[str]) -> IVGResult:
        try:
            vec_str = ",".join(str(x) for x in query_vector)
            result_json = str(self._call_classmethod(
                "Graph.KG.TemporalIndex", "kg_KNN_VEC",
                vec_str, str(k), label_filter or "",
            ))
            import json as _json
            results = _json.loads(result_json) if result_json else []
            rows = [[r.get("id", ""), float(r.get("score", 0))] for r in results]
            return IVGResult(columns=["id", "score"], rows=rows)
        except Exception as e:
            logger.warning("KNN_VEC failed: %s", e)
            cursor = self.conn.cursor()
            vec_str = ",".join(str(x) for x in query_vector)
            try:
                cursor.execute(
                    f"SELECT TOP {k} id, VECTOR_COSINE(emb, TO_VECTOR(?)) AS score "
                    f"FROM Graph_KG.kg_NodeEmbeddings ORDER BY score DESC",
                    [f"[{vec_str}]"],
                )
                rows = [[r[0], float(r[1])] for r in cursor.fetchall()]
                return IVGResult(columns=["id", "score"], rows=rows)
            except Exception as e2:
                logger.warning("KNN_VEC client-side fallback failed: %s", e2)
                return IVGResult(columns=["id", "score"], rows=[])

    # ── Temporal Edges ────────────────────────────────────────────────────────

    def write_temporal_edge(
        self, source_id: str, predicate: str, target_id: str,
        timestamp: int, weight: float = 1.0, attrs: Optional[dict] = None,
        upsert: bool = False
    ) -> IVGResult:
        import json as _json
        attrs_json = _json.dumps(attrs) if attrs else ""
        try:
            self._call_classmethod(
                "Graph.KG.TemporalIndex", "Insert",
                source_id, predicate, target_id,
                str(timestamp), str(weight), attrs_json, str(int(upsert)),
            )
        except Exception as e:
            logger.warning("write_temporal_edge failed: %s", e)
            return IVGResult(columns=[], rows=[], error=str(e)[:200])
        return IVGResult(columns=[], rows=[])

    def bulk_write_temporal_edges(self, edges: list, upsert: bool = False) -> IVGResult:
        import json as _json
        inserted = 0
        for edge in edges:
            src = edge.get("source", "")
            pred = edge.get("predicate", "")
            tgt = edge.get("target", "")
            ts = edge.get("timestamp", 0)
            weight = float(edge.get("weight", 1.0))
            attrs = edge.get("attrs") or {}
            result = self.write_temporal_edge(src, pred, tgt, ts, weight, attrs, upsert)
            if not result.error:
                inserted += 1
        return IVGResult(columns=["inserted"], rows=[[inserted]])

    def execute_temporal_window_query(
        self, source_id: str, predicate: str,
        ts_start: int, ts_end: int, direction: str = "out"
    ) -> IVGResult:
        import json as _json
        try:
            result_json = str(self._call_classmethod(
                "Graph.KG.TemporalIndex", "QueryWindow",
                source_id, predicate, str(ts_start), str(ts_end), direction,
            ))
            edges = _json.loads(result_json) if result_json else []
            rows = []
            for edge in edges:
                rows.append([
                    edge.get("s", source_id),
                    edge.get("p", predicate),
                    edge.get("o", ""),
                    edge.get("ts", 0),
                    edge.get("w", edge.get("weight", 1.0)),
                ])
            return IVGResult(
                columns=["source", "predicate", "target", "timestamp", "weight"],
                rows=rows,
            )
        except Exception as e:
            logger.warning("execute_temporal_window_query failed: %s", e)
            return IVGResult(
                columns=["source", "predicate", "target", "timestamp", "weight"],
                rows=[],
            )

    def execute_temporal_cypher(
        self, source_id: str, predicates: list, ts_start: int,
        ts_end: int, direction: str, max_hops: int
    ) -> IVGResult:
        import json as _json
        predicates_json = _json.dumps(predicates) if predicates else ""
        try:
            result_json = str(self._call_classmethod(
                "Graph.KG.TemporalIndex", "QueryWindowBFS",
                source_id, predicates_json, str(ts_start), str(ts_end),
                direction, str(max_hops),
            ))
            results = _json.loads(result_json) if result_json else []
            rows = [
                [r.get("id", ""), r.get("hops", 0), r.get("pred", ""), r.get("ts", 0)]
                for r in results
            ]
            return IVGResult(columns=["id", "hops", "pred", "ts"], rows=rows)
        except Exception as e:
            logger.warning("execute_temporal_cypher failed: %s", e)
            return IVGResult(columns=["id", "hops", "pred", "ts"], rows=[])

    def get_temporal_aggregate(
        self, source_id: str, predicate: str, metric: str,
        ts_start: int, ts_end: int
    ) -> IVGResult:
        try:
            val = self._call_classmethod(
                "Graph.KG.TemporalIndex", "GetAggregate",
                source_id, predicate, metric, str(ts_start), str(ts_end),
            )
            return IVGResult(columns=["value"], rows=[[float(str(val))]])
        except Exception as e:
            logger.warning("get_temporal_aggregate failed: %s", e)
            return IVGResult(columns=["value"], rows=[[0.0]])

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def capabilities(self) -> dict:
        caps = dict(_FULL_CAPABILITIES)
        if self._detect_arno():
            caps["bfs_arno"] = True
            caps["ppr_arno"] = "ppr" in self._arno_capabilities.get("algorithms", [])
            caps["wcc_arno"] = "wcc" in self._arno_capabilities.get("algorithms", [])
        return caps

    def close(self) -> None:
        pass
