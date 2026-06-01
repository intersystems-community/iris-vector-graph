import json
import logging
import os
from typing import Any, Callable, Dict, List, Optional

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
    "degree_centrality": True,
    "betweenness": True,
    "closeness": True,
    "eigenvector": True,
    "leiden": True,
    "triangle_count": True,
    "scc": True,
    "k_core": True,
}

import re as _re_global

def _fix_iris_json(raw3: str) -> str:
    return _re_global.sub(r'(?<=[:\[,])(\.\d)', r'0\1', raw3)


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
                    "INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
                    [s, p, o],
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
                "", direction, str(max_results),
            ))
        except Exception as e:
            logger.warning("BFS ObjectScript failed: %s", e)
            return self._sql_bfs_fallback(source_id, predicates, max_hops, direction, max_results)

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

    def _sql_bfs_fallback(
        self, source_id: str, predicates: list, max_hops: int,
        direction: str, max_results: int
    ) -> "IVGResult":
        cursor = self.conn.cursor()
        visited: dict[str, int] = {source_id: 0}
        frontier: list[str] = [source_id]
        result_rows: list[list] = []
        for hop in range(1, max_hops + 1):
            if not frontier:
                break
            preds_clause = ""
            params = list(frontier)
            if predicates:
                placeholders = ",".join("?" * len(predicates))
                preds_clause = f" AND p IN ({placeholders})"
                params += predicates
            placeholders_f = ",".join("?" * len(frontier))
            if direction in ("out", "outbound"):
                sql = f"SELECT DISTINCT o_id, p FROM Graph_KG.rdf_edges WHERE s IN ({placeholders_f}){preds_clause}"
            elif direction in ("in", "inbound"):
                sql = f"SELECT DISTINCT s, p FROM Graph_KG.rdf_edges WHERE o_id IN ({placeholders_f}){preds_clause}"
            else:
                sql_out = f"SELECT DISTINCT o_id AS nbr, p FROM Graph_KG.rdf_edges WHERE s IN ({placeholders_f}){preds_clause}"
                sql_in  = f"SELECT DISTINCT s AS nbr, p FROM Graph_KG.rdf_edges WHERE o_id IN ({placeholders_f}){preds_clause}"
                cursor.execute(sql_out, params)
                nbrs_out = cursor.fetchall()
                cursor.execute(sql_in, list(frontier) + (predicates if predicates else []))
                nbrs_in = cursor.fetchall()
                nbrs = [(r[0], r[1]) for r in nbrs_out + nbrs_in]
                new_frontier = []
                for nbr, pred in nbrs:
                    if nbr not in visited:
                        visited[nbr] = hop
                        new_frontier.append(nbr)
                        result_rows.append([nbr, hop, pred])
                        if max_results and len(result_rows) >= max_results:
                            return IVGResult(columns=["id", "hops", "pred"], rows=result_rows)
                frontier = new_frontier
                continue
            cursor.execute(sql, params)
            new_frontier = []
            for nbr, pred in cursor.fetchall():
                if nbr not in visited:
                    visited[nbr] = hop
                    new_frontier.append(nbr)
                    result_rows.append([nbr, hop, pred])
                    if max_results and len(result_rows) >= max_results:
                        return IVGResult(columns=["id", "hops", "pred"], rows=result_rows)
            frontier = new_frontier
        return IVGResult(columns=["id", "hops", "pred"], rows=result_rows)

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

    def _count_query(self, sql: str, params: list = None) -> IVGResult:
        cursor = self.conn.cursor()
        try:
            cursor.execute(sql, params or [])
            row = cursor.fetchone()
            return IVGResult(columns=["count"], rows=[[int(row[0]) if row else 0]])
        except Exception as e:
            return IVGResult(columns=["count"], rows=[[0]], error=str(e)[:200])

    def _distinct_query(self, sql: str, col: str) -> IVGResult:
        cursor = self.conn.cursor()
        try:
            cursor.execute(sql)
            return IVGResult(columns=[col], rows=[[r[0]] for r in cursor.fetchall()])
        except Exception as e:
            return IVGResult(columns=[col], rows=[], error=str(e)[:200])

    def get_node_count(self, label: Optional[str] = None) -> IVGResult:
        if label:
            return self._count_query(
                "SELECT COUNT(*) FROM Graph_KG.rdf_labels WHERE label = ?", [label]
            )
        return self._count_query("SELECT COUNT(*) FROM Graph_KG.nodes")

    def get_edge_count(self, predicate: Optional[str] = None) -> IVGResult:
        if predicate:
            return self._count_query(
                "SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE p = ?", [predicate]
            )
        return self._count_query("SELECT COUNT(*) FROM Graph_KG.rdf_edges")

    def get_labels(self) -> IVGResult:
        return self._distinct_query(
            "SELECT DISTINCT label FROM Graph_KG.rdf_labels ORDER BY label", "label"
        )

    def get_relationship_types(self) -> IVGResult:
        return self._distinct_query(
            "SELECT DISTINCT p FROM Graph_KG.rdf_edges ORDER BY p", "relationshipType"
        )

    def list_indexes(self) -> IVGResult:
        cols = ["name", "type", "state"]
        rows = []
        cursor = self.conn.cursor()

        def _try_count(sql):
            try:
                cursor.execute(sql)
                r = cursor.fetchone()
                return int(r[0]) if r else 0
            except Exception:
                return -1

        hnsw = _try_count("SELECT COUNT(*) FROM Graph_KG.kg_NodeEmbeddings_optimized")
        rows.append(["hnsw_node_embeddings", "VECTOR(HNSW)",
                      "ONLINE" if hnsw > 0 else "NOT_BUILT"])

        for table, idx_type in [
            ("Graph_KG.kg_IVFMeta", "VECTOR(IVF)"),
            ("Graph_KG.kg_BM25Meta", "FULLTEXT(BM25)"),
        ]:
            try:
                cursor.execute(f"SELECT DISTINCT name FROM {table}")
                for (name,) in cursor.fetchall():
                    rows.append([name, idx_type, "ONLINE"])
            except Exception:
                pass

        try:
            cursor.execute("SELECT DISTINCT idx_name FROM Graph_KG.kg_PlaidMeta")
            for (name,) in cursor.fetchall():
                rows.append([name, "VECTOR(PLAID)", "ONLINE"])
        except Exception:
            pass

        rows.append(["pk_nodes", "UNIQUE", "ONLINE"])
        rows.append(["pk_rdf_edges", "UNIQUE", "ONLINE"])
        return IVGResult(columns=cols, rows=rows)

    def server_info(self) -> IVGResult:
        try:
            cursor = self.conn.cursor()
            cursor.execute("SELECT %Version.GetVersion()")
            row = cursor.fetchone()
            iris_ver = str(row[0]) if row else "unknown"
        except Exception:
            iris_ver = "unknown"
        try:
            from importlib.metadata import version as pkg_version
            ivg_ver = pkg_version("iris-vector-graph")
        except Exception:
            ivg_ver = "unknown"
        return IVGResult(
            columns=["iris_version", "ivg_version"],
            rows=[[iris_ver, ivg_ver]],
        )

    def execute_degree_centrality(self, direction: str, predicate: str,
                                   top_k: int) -> IVGResult:
        import json as _json
        try:
            raw = str(self._call_classmethod(
                "Graph.KG.Centrality", "DegreeCentralityJson",
                direction, predicate, str(top_k),
            ))
            results = _json.loads(raw) if raw else []
            rows = [[r.get("id", ""), float(r.get("score", 0)), int(r.get("degree", 0))]
                    for r in results]
            return IVGResult(columns=["id", "score", "degree"], rows=rows)
        except Exception as e:
            err_str = str(e)
            if "CLASS DOES NOT EXIST" in err_str or "DBSRV" in err_str:
                logger.info(
                    "DegreeCentrality classMethodValue blocked by Bug S; "
                    "falling back to gref-direct ^KG iteration"
                )
                try:
                    return self._degree_centrality_gref_fallback(
                        direction, predicate, top_k
                    )
                except Exception as fallback_err:
                    logger.warning(
                        "DegreeCentrality gref fallback also failed: %s", fallback_err
                    )
                    return IVGResult(
                        columns=["id", "score", "degree"], rows=[],
                        error=str(fallback_err)[:200],
                    )
            logger.warning("DegreeCentrality failed: %s", e)
            return IVGResult(columns=["id", "score", "degree"], rows=[], error=err_str[:200])

    def _degree_centrality_gref_fallback(self, direction: str, predicate: str,
                                          top_k: int) -> IVGResult:
        """Bug S workaround via LazyKG adapter (v1.99.0 retrofit).

        Reads ^KG directly via the IRIS Native API rather than
        ##class(Graph.KG.Centrality).DegreeCentralityJson, which fails with
        <CLASS DOES NOT EXIST> when %SYS.DBSRV's class cache rejects user-class
        XDCall lookups. Native API global access bypasses DBSRV entirely.
        See ENGINEERING_DEBT.md Bug S for diagnosis.
        """
        from iris_vector_graph.stores.lazy_kg import LazyKG
        lkg = LazyKG(self.conn, include_sinks=(direction in ("in", "both")))

        all_nodes = list(lkg.iter_nodes())
        node_count = len(all_nodes)
        if node_count == 0:
            return IVGResult(columns=["id", "score", "degree"], rows=[])
        norm = 1.0 / max(node_count - 1, 1)

        scored = []
        for node in all_nodes:
            if predicate == "":
                if direction == "out":
                    deg = lkg.degree(node)
                elif direction == "in":
                    deg = lkg.in_degree(node)
                else:
                    deg = lkg.degree(node) + lkg.in_degree(node)
            else:
                if direction == "out":
                    deg = lkg.degree_for_predicate(node, predicate)
                elif direction == "in":
                    deg = lkg.in_degree_for_predicate(node, predicate)
                else:
                    deg = (lkg.degree_for_predicate(node, predicate)
                           + lkg.in_degree_for_predicate(node, predicate))

            scored.append((node, deg * norm, deg))

        scored.sort(key=lambda r: -r[1])
        if top_k > 0:
            scored = scored[:top_k]

        return IVGResult(
            columns=["id", "score", "degree"],
            rows=[[nid, sc, dg] for (nid, sc, dg) in scored],
        )

    def execute_betweenness(self, sample_size: int, direction: str, max_hops: int,
                             top_k: int, mem_budget_mb: int,
                             progress_callback: Optional[Callable[[int, int], None]] = None) -> IVGResult:
        try:
            return self._betweenness_gref(
                sample_size, direction, max_hops, top_k, mem_budget_mb,
                progress_callback,
            )
        except Exception as e:
            logger.warning("Betweenness failed: %s", e)
            return IVGResult(
                columns=["id", "score"], rows=[], error=str(e)[:200],
            )

    def execute_betweenness_neighborhood(
        self, seed: str, hops: int, sample_size: int, top_k: int,
    ) -> IVGResult:
        try:
            import iris as _iris
            import json as _json
            iris_obj = self._iris_obj()
            if not iris_obj.classMethodValue("Graph.KG.NKGAccel", "IsLoaded"):
                import warnings
                warnings.warn(
                    "betweenness_centrality_neighborhood: arno not loaded — OS fallback active.",
                    RuntimeWarning, stacklevel=4,
                )
            raw = str(iris_obj.classMethodValue(
                "Graph.KG.NKGAccel", "BetweennessNeighborhood",
                seed, hops, sample_size, top_k,
            ))
            if raw.startswith("OK:"):
                rows = [[r.get("id", ""), float(r.get("score", 0.0))]
                        for r in sorted(_json.loads(_fix_iris_json(raw[3:])), key=lambda x: -x.get("score", 0))]
                if top_k > 0:
                    rows = rows[:top_k]
                return IVGResult(columns=["id", "score"], rows=rows)
            return IVGResult(columns=["id", "score"], rows=[], error=raw)
        except Exception as e:
            return IVGResult(columns=["id", "score"], rows=[], error=str(e)[:200])

    def _betweenness_gref(self, sample_size: int, direction: str, max_hops: int,
                          top_k: int, mem_budget_mb: int,
                          progress_callback: Optional[Callable[[int, int], None]], lkg=None) -> IVGResult:
        """Brandes (2001) Betweenness via ObjectScript/arno fast path.

        Dispatch order:
          1. arno Rust (kg_betweenness_global_v) — Rust rayon parallel, ~8ms ER(2000)
          2. OS Brandes (%SYSTEM.WorkMgr 8-way parallel) — ~830ms ER(2000)
          3. Python LazyKG Brandes — last resort, very slow

        Performance cliff: if libarno_callout.so is not deployed, tier 2 is
        ~100x slower than tier 1. Deploy arno for production use.
        """
        try:
            import iris as _iris
            iris_obj = self._iris_obj()
            # Check if arno is loaded — warn if not, OS fallback is much slower
            if not iris_obj.classMethodValue("Graph.KG.NKGAccel", "IsLoaded"):
                import warnings
                warnings.warn(
                    "betweenness_centrality: arno callout not loaded — "
                    "falling back to parallel ObjectScript (~100x slower). "
                    "Deploy libarno_callout.so for production performance.",
                    RuntimeWarning,
                    stacklevel=4,
                )
            # Pass sampleSize=0 (let OS use maxSources cap), topK, maxSources=200
            raw = str(iris_obj.classMethodValue(
                "Graph.KG.NKGAccel", "BetweennessGlobal",
                sample_size, top_k, 200,
            ))
            if raw.startswith("OK:"):
                import json as _json
                parsed = _json.loads(raw[3:])
                rows = [[r.get("id", ""), float(r.get("score", 0.0))]
                        for r in sorted(parsed, key=lambda x: -x.get("score", 0))]
                if top_k > 0:
                    rows = rows[:top_k]
                if progress_callback:
                    total = len(parsed) or 1
                    progress_callback(total, total)
                return IVGResult(columns=["id", "score"], rows=rows)
        except Exception:
            pass

        import random
        from iris_vector_graph.stores.lazy_kg import LazyKG
        iris_inst = self._iris_obj()
        lkg = LazyKG(self.conn, include_sinks=(direction in ("in", "both")))

        all_nodes = list(lkg.iter_nodes())
        if not all_nodes:
            return IVGResult(columns=["id", "score"], rows=[])

        if sample_size > 0 and sample_size < len(all_nodes):
            sources = random.sample(all_nodes, sample_size)
            scaling = len(all_nodes) / sample_size
        else:
            sources = list(all_nodes)
            scaling = 1.0

        bc: Dict[str, float] = {n: 0.0 for n in all_nodes}
        skipped_sources = 0
        budget_subscripts = max(1, mem_budget_mb) * 10485

        if direction == "out":
            forward = lkg.out_neighbors
        elif direction == "in":
            forward = lkg.in_neighbors
        else:
            def forward(n: str) -> List[str]:
                seen = set()
                combined = []
                for x in list(lkg.out_neighbors(n)) + list(lkg.in_neighbors(n)):
                    if x not in seen:
                        seen.add(x)
                        combined.append(x)
                return combined

        n_total = len(sources)
        for idx, s in enumerate(sources):
            stack: List[str] = []
            preds: Dict[str, List[str]] = {}
            sigma: Dict[str, int] = {s: 1}
            dist: Dict[str, int] = {s: 0}
            queue = [s]

            preds_count = 0
            budget_exceeded = False

            while queue:
                next_queue: List[str] = []
                for v in queue:
                    stack.append(v)
                    if max_hops > 0 and dist[v] >= max_hops:
                        continue
                    for w in forward(v):
                        if w not in dist:
                            dist[w] = dist[v] + 1
                            sigma[w] = 0
                            preds[w] = []
                            next_queue.append(w)
                        if dist[w] == dist[v] + 1:
                            sigma[w] += sigma.get(v, 0)
                            preds.setdefault(w, []).append(v)
                            preds_count += 1
                            if preds_count > budget_subscripts:
                                budget_exceeded = True
                                break
                    if budget_exceeded:
                        break
                if budget_exceeded:
                    break
                queue = next_queue

            if budget_exceeded:
                skipped_sources += 1
                try:
                    import datetime as _dt
                    ts_key = _dt.datetime.now().strftime("%Y%m%d%H%M%S%f")
                    iris_inst.set("BC mem budget exceeded", "^IVG.warnings", "centrality", ts_key, s)
                except Exception:
                    pass
                if progress_callback:
                    progress_callback(idx + 1, n_total)
                continue

            delta: Dict[str, float] = {}
            while stack:
                w = stack.pop()
                for v in preds.get(w, ()):
                    if sigma.get(w, 0) > 0:
                        delta[v] = delta.get(v, 0.0) + (sigma.get(v, 0) / sigma[w]) * (1.0 + delta.get(w, 0.0))
                if w != s:
                    bc[w] = bc.get(w, 0.0) + delta.get(w, 0.0)

            if progress_callback:
                progress_callback(idx + 1, n_total)

        if scaling != 1.0:
            for k in bc:
                bc[k] *= scaling

        scored = sorted(bc.items(), key=lambda kv: -kv[1])
        if top_k > 0:
            scored = scored[:top_k]
        rows = [[nid, sc] for (nid, sc) in scored]

        if skipped_sources > 0:
            rows.append(["_meta", {"_approximate": True, "_skipped_sources": skipped_sources}])

        return IVGResult(columns=["id", "score"], rows=rows)

    def execute_closeness(self, formula: str, direction: str, max_hops: int, top_k: int,
                           progress_callback: Optional[Callable[[int, int], None]] = None) -> IVGResult:
        if max_hops == 0:
            srv = self._closeness_serverside(formula, top_k)
            if srv is not None:
                return srv
        try:
            return self._closeness_gref(formula, direction, max_hops, top_k, progress_callback)
        except Exception as e:
            logger.warning("Closeness failed: %s", e)
            return IVGResult(columns=["id", "score"], rows=[], error=str(e)[:200])

    def _closeness_serverside(self, formula: str, top_k: int) -> Optional[IVGResult]:
        """Server-side Graph.KG.Communities.ClosenessJsonPy — full-graph harmonic/
        classical closeness via igraph in IRIS embedded Python (native multi-core C,
        no data transfer). Used only for max_hops=0 (full closeness). Returns None
        (caller falls back to the ObjectScript/LazyKG BFS) when igraph is absent.
        """
        try:
            import json as _json
            iris_obj = self._iris_obj()
            raw = str(iris_obj.classMethodValue(
                "Graph.KG.Communities", "ClosenessJsonPy", formula, int(top_k)))
            if not raw.startswith("OK:"):
                return None
            data = _json.loads(raw[3:])
            rows = [[r.get("id", ""), float(r.get("score", 0.0))] for r in data]
            return IVGResult(columns=["id", "score"], rows=rows)
        except Exception as e:
            logger.debug("Closeness server-side path unavailable, falling back: %s", str(e)[:120])
            return None

    def _closeness_gref(self, formula: str, direction: str, max_hops: int, top_k: int,
                         progress_callback: Optional[Callable[[int, int], None]], lkg=None) -> IVGResult:
        """Closeness Centrality via LazyKG-backed Native API (Bug S workaround).

        Per-source BFS sums distances. Two formulas:
        - "harmonic" (default): score(v) = sum(1/d(v,u)) / (n-1) — robust to disconnection
        - "classical": score(v) = (n-1)/sum(d(v,u)) — returns 0 if any unreachable

        See spec 162 clarification 2 + research.md R2 (matches networkx.harmonic_centrality).
        v1.99.0: refactored from inline ^KG walk to LazyKG adapter.
        v2.0.0 spec 168: try ClosenessGlobal ObjectScript path first (1 round-trip).
        """
        try:
            import iris as _iris
            iris_obj = self._iris_obj()
            raw = str(iris_obj.classMethodValue(
                "Graph.KG.NKGAccel", "ClosenessGlobal",
                formula, direction, max_hops, top_k,
            ))
            if raw.startswith("OK:"):
                import json as _json
                rows = [[r.get("id", ""), float(r.get("score", 0.0))]
                        for r in _json.loads(raw[3:])]
                return IVGResult(columns=["id", "score"], rows=rows)
        except Exception:
            pass

        from iris_vector_graph.stores.lazy_kg import LazyKG
        lkg = lkg if lkg is not None else LazyKG(self.conn, include_sinks=(direction in ("in", "both")))

        all_nodes = list(lkg.iter_nodes())
        if not all_nodes:
            return IVGResult(columns=["id", "score"], rows=[])

        if direction == "out":
            forward = lkg.out_neighbors
        elif direction == "in":
            forward = lkg.in_neighbors
        else:
            def forward(n: str) -> List[str]:
                seen_n = set()
                combined = []
                for x in list(lkg.out_neighbors(n)) + list(lkg.in_neighbors(n)):
                    if x not in seen_n:
                        seen_n.add(x)
                        combined.append(x)
                return combined

        n_total = len(all_nodes)
        norm = 1.0 / max(n_total - 1, 1)
        scored: List[tuple] = []

        for idx, source in enumerate(all_nodes):
            dist: Dict[str, int] = {source: 0}
            queue = [source]
            while queue:
                next_queue: List[str] = []
                for v in queue:
                    if max_hops > 0 and dist[v] >= max_hops:
                        continue
                    for w in forward(v):
                        if w not in dist:
                            dist[w] = dist[v] + 1
                            next_queue.append(w)
                queue = next_queue

            reachable = [(node, d) for node, d in dist.items() if node != source and d > 0]

            if formula == "classical":
                if len(reachable) < n_total - 1:
                    score = 0.0
                else:
                    total_dist = sum(d for _, d in reachable)
                    score = (n_total - 1) / total_dist if total_dist > 0 else 0.0
            else:
                harmonic_sum = sum(1.0 / d for _, d in reachable)
                score = harmonic_sum * norm

            scored.append((source, score))
            if progress_callback:
                progress_callback(idx + 1, n_total)

        scored.sort(key=lambda r: -r[1])
        if top_k > 0:
            scored = scored[:top_k]
        return IVGResult(
            columns=["id", "score"],
            rows=[[nid, sc] for (nid, sc) in scored],
        )

    def execute_eigenvector(self, max_iter: int, tol: float, top_k: int,
                             progress_callback: Optional[Callable[[int, int], None]] = None) -> IVGResult:
        try:
            return self._eigenvector_gref(max_iter, tol, top_k, progress_callback)
        except Exception as e:
            logger.warning("Eigenvector failed: %s", e)
            return IVGResult(columns=["id", "score"], rows=[], error=str(e)[:200])

    def _eigenvector_gref(self, max_iter: int, tol: float, top_k: int,
                           progress_callback: Optional[Callable[[int, int], None]], lkg=None) -> IVGResult:
        """Eigenvector Centrality via LazyKG-backed Native API (Bug S workaround).

        Power iteration over RAW adjacency matrix A (NOT the transition matrix
        M = D^-1 A used by PageRank). For each iteration: x' = A·x, L2-normalize.
        Matches networkx.eigenvector_centrality_numpy semantics.

        See spec 162 research.md R2 (Eigenvector ≠ PageRank with α=1).
        v1.99.0: refactored from inline ^KG walk to LazyKG adapter.
        v2.0.0 spec 169: try EigenvectorGlobal ObjectScript path first (1 round-trip).
        """
        try:
            import iris as _iris
            iris_obj = self._iris_obj()
            raw = str(iris_obj.classMethodValue(
                "Graph.KG.NKGAccel", "EigenvectorGlobal",
                max_iter, tol, top_k,
            ))
            if raw.startswith("OK:"):
                import json as _json
                rows = [[r.get("id", ""), float(r.get("score", 0.0))]
                        for r in sorted(_json.loads(raw[3:]), key=lambda x: -x.get("score", 0))]
                if top_k > 0:
                    rows = rows[:top_k]
                return IVGResult(columns=["id", "score"], rows=rows)
        except Exception:
            pass

        from iris_vector_graph.stores.lazy_kg import LazyKG
        lkg = lkg if lkg is not None else LazyKG(self.conn, include_sinks=True)

        all_nodes = list(lkg.iter_nodes())
        n = len(all_nodes)
        if n == 0:
            return IVGResult(columns=["id", "score"], rows=[])

        out_neighbors: Dict[str, List[str]] = {
            node: list(lkg.out_neighbors(node)) for node in all_nodes
        }

        x: Dict[str, float] = {node: 1.0 / n for node in all_nodes}

        for it in range(max_iter):
            x_new: Dict[str, float] = {node: 0.0 for node in all_nodes}
            for u, neighbors in out_neighbors.items():
                xu = x.get(u, 0.0)
                if xu == 0.0:
                    continue
                for w in neighbors:
                    if w in x_new:
                        x_new[w] += xu

            norm_sq = sum(v * v for v in x_new.values())
            if norm_sq <= 0.0:
                break
            norm = norm_sq ** 0.5
            for k in x_new:
                x_new[k] /= norm

            max_delta = max(abs(x_new[k] - x[k]) for k in x)
            x = x_new

            if progress_callback:
                progress_callback(it + 1, max_iter)

            if max_delta < tol:
                break

        scored = sorted(x.items(), key=lambda kv: -kv[1])
        if top_k > 0:
            scored = scored[:top_k]
        return IVGResult(
            columns=["id", "score"],
            rows=[[nid, sc] for (nid, sc) in scored],
        )

    def execute_leiden(self, max_levels: int, gamma: float, tol: float, top_k: int,
                       mem_budget_mb: int, random_seed: Optional[int] = None,
                       progress_callback: Optional[Callable[[int, int], None]] = None) -> IVGResult:
        srv = self._leiden_serverside(gamma, top_k, random_seed)
        if srv is not None:
            return srv
        if os.environ.get("IVG_DISABLE_ARNO") != "1":
            try:
                return self._leiden_arno(max_levels, gamma, tol, top_k, mem_budget_mb, random_seed)
            except Exception as e:
                from iris_vector_graph.stores.arno_bridge import ArnoError
                if not isinstance(e, ArnoError):
                    logger.warning("Leiden arno path raised non-ArnoError: %s", e)
        return self._leiden_lazykg(max_levels, gamma, tol, top_k, mem_budget_mb,
                                    random_seed, progress_callback)

    def _leiden_serverside(self, gamma: float, top_k: int,
                           random_seed: Optional[int]) -> Optional[IVGResult]:
        """Server-side Graph.KG.Communities.LeidenJsonAuto — canonical leidenalg in
        IRIS embedded Python (native multi-core, no data transfer) when igraph+
        leidenalg are installed in mgr/python; greedy ObjectScript otherwise.
        Returns None (caller falls back to LazyKG) if the path is unavailable.
        """
        try:
            import json as _json
            iris_obj = self._iris_obj()
            seed = -1 if random_seed is None else int(random_seed)
            raw = str(iris_obj.classMethodValue(
                "Graph.KG.Communities", "LeidenJsonAuto",
                10, float(gamma), 0.0001, int(top_k), 256, seed,
            ))
            if not raw.startswith("OK:"):
                return None
            data = _json.loads(raw[3:])
            rows = [[r.get("id", ""), int(r.get("community", 0)), int(r.get("size", 0))]
                    for r in data]
            return IVGResult(columns=["id", "community", "size"], rows=rows)
        except Exception as e:
            logger.debug("Leiden server-side path unavailable, falling back: %s", str(e)[:120])
            return None

    def _leiden_arno(self, max_levels: int, gamma: float, tol: float, top_k: int,
                     mem_budget_mb: int, random_seed: Optional[int]) -> IVGResult:
        """Spec 163 FR-024 arno path via chunked NKG-format adjacency upload."""
        from iris_vector_graph.stores.arno_bridge import (
            arno_call, build_kg_adjacency_chunked,
        )
        seed_arg = -1 if random_seed is None else int(random_seed)
        idx_to_node, _edge_count = build_kg_adjacency_chunked(self.conn)
        raw = arno_call(self.conn, "kg_leiden_run",
                        int(max_levels), float(gamma), float(tol),
                        int(top_k), int(mem_budget_mb), seed_arg)
        import json as _json; results = _json.loads(raw) if raw else []
        rows = [[r.get("id", ""), int(r.get("community", 0)), int(r.get("size", 0))]
                for r in results]
        return IVGResult(columns=["id", "community", "size"], rows=rows)

    def _leiden_lazykg(self, max_levels: int, gamma: float, tol: float, top_k: int,
                        mem_budget_mb: int, random_seed: Optional[int],
                        progress_callback: Optional[Callable[[int, int], None]]) -> IVGResult:
        """Spec 163 FR-025: LazyKG-backed Leiden community detection.

        Reads ^KG via LazyKG (direct gref, no class lookup), builds a symmetrized in-memory
        igraph.Graph, delegates to leidenalg (canonical Leiden, Traag 2019).

        Why leidenalg (not networkx.community.louvain_communities):
          1. networkx ships Louvain, NOT Leiden. Louvain caps at ARI ≈ 0.62
             on Zachary's karate club; Leiden routinely hits > 0.95.
          2. FR-005 explicitly requires Leiden; FR-007 requires ARI > 0.85.
          3. leidenalg is the reference implementation cited in the spec
             plan.md and used by Neo4j GDS, cdlib, and graph-tool.

        igraph + leidenalg are optional [full] extras; falls back to networkx
        Louvain if not installed (degraded quality, but produces valid output).

        Output community IDs remapped to contiguous 0..K-1 sorted by descending
        community size (community 0 = largest), per spec 163 Q3.
        """
        from iris_vector_graph.stores.lazy_kg import LazyKG

        lkg = LazyKG(self.conn, include_sinks=True)
        all_nodes: List[str] = sorted(lkg.iter_nodes())
        if not all_nodes:
            return IVGResult(columns=["id", "community", "size"], rows=[])

        node_index: Dict[str, int] = {n: i for i, n in enumerate(all_nodes)}
        edge_set: set = set()
        for v in all_nodes:
            v_idx = node_index[v]
            out_neighbors = sorted(n for n in lkg.out_neighbors(v) if n != v and n in node_index)
            for w in out_neighbors:
                pair = (v_idx, node_index[w]) if v_idx < node_index[w] else (node_index[w], v_idx)
                edge_set.add(pair)
            in_neighbors = sorted(n for n in lkg.in_neighbors(v) if n != v and n in node_index)
            for u in in_neighbors:
                pair = (v_idx, node_index[u]) if v_idx < node_index[u] else (node_index[u], v_idx)
                edge_set.add(pair)

        if progress_callback is not None:
            progress_callback(1, 2)

        try:
            import igraph as _ig
            import leidenalg as _la
            G = _ig.Graph(n=len(all_nodes), edges=list(edge_set), directed=False)
            if abs(gamma - 1.0) < 1e-9:
                partition = _la.find_partition(
                    G,
                    _la.ModularityVertexPartition,
                    seed=random_seed if random_seed is not None else 0,
                    n_iterations=max_levels,
                )
            else:
                partition = _la.find_partition(
                    G,
                    _la.CPMVertexPartition,
                    resolution_parameter=gamma,
                    seed=random_seed if random_seed is not None else 0,
                    n_iterations=max_levels,
                )
            communities: List[List[str]] = [
                [all_nodes[idx] for idx in comm] for comm in partition
            ]
        except ImportError:
            try:
                import networkx as _nx
                graph = _nx.Graph()
                for nid in all_nodes:
                    graph.add_node(nid)
                for v_idx, w_idx in edge_set:
                    graph.add_edge(all_nodes[v_idx], all_nodes[w_idx])
                fallback_communities = _nx.community.louvain_communities(
                    graph, resolution=gamma, threshold=tol, seed=random_seed,
                )
                communities = [list(c) for c in fallback_communities]
            except ImportError:
                return IVGResult(
                    columns=["id", "community", "size"], rows=[],
                    error="Leiden requires python-igraph+leidenalg (preferred) or networkx",
                )
        except Exception as e:
            return IVGResult(
                columns=["id", "community", "size"], rows=[],
                error=f"leidenalg failed: {str(e)[:200]}",
            )

        if progress_callback is not None:
            progress_callback(2, 2)

        sorted_communities = sorted(communities, key=len, reverse=True)
        final_label: Dict[str, int] = {}
        sizes: Dict[int, int] = {}
        for new_id, members in enumerate(sorted_communities):
            for m_node in members:
                final_label[m_node] = new_id
            sizes[new_id] = len(members)

        rows = [[node, final_label[node], sizes[final_label[node]]]
                for node in final_label]
        rows.sort(key=lambda r: (-r[2], r[1], r[0]))
        if top_k > 0:
            rows = rows[:top_k]

        return IVGResult(columns=["id", "community", "size"], rows=rows)

    def execute_triangle_count(self, top_k: int,
                                progress_callback: Optional[Callable[[int, int], None]] = None) -> IVGResult:
        if os.environ.get("IVG_DISABLE_ARNO") != "1":
            try:
                return self._triangle_count_arno(top_k)
            except Exception as e:
                from iris_vector_graph.stores.arno_bridge import ArnoError
                if not isinstance(e, ArnoError):
                    logger.warning("TriangleCount arno path raised non-ArnoError: %s", e)
        return self._triangle_count_lazykg(top_k, progress_callback)

    def _triangle_count_arno(self, top_k: int) -> IVGResult:
        """Spec 163 FR-024 arno path via chunked NKG-format adjacency upload."""
        from iris_vector_graph.stores.arno_bridge import (
            arno_call, build_kg_adjacency_chunked,
        )
        idx_to_node, _edge_count = build_kg_adjacency_chunked(self.conn)
        raw = arno_call(self.conn, "kg_triangle_count_run", int(top_k))
        import json as _json; results = _json.loads(raw) if raw else []
        rows = [[r.get("id", ""), int(r.get("triangles", 0)), float(r.get("lcc", 0.0))]
                for r in results]
        return IVGResult(columns=["id", "triangles", "lcc"], rows=rows)

    def _triangle_count_lazykg(self, top_k: int,
                                progress_callback: Optional[Callable[[int, int], None]]) -> IVGResult:
        """Spec 163 FR-025: triangle count + LCC over symmetrized neighbors via LazyKG.

        For each node v, build N(v) = out_neighbors(v) ∪ in_neighbors(v) (skip
        self-loops, dedupe multi-edges). For each unordered pair (u, w) ∈ N(v),
        increment triangle count if u and w are also adjacent. LCC normalizes:
            lcc(v) = triangles(v) / C(|N(v)|, 2)   if |N(v)| ≥ 2 else 0.0

        Matches networkx.triangles(networkx.Graph(G_directed)) convention
        (spec 163 Q1 clarification: symmetrize edges).
        """
        from iris_vector_graph.stores.lazy_kg import LazyKG

        lkg = LazyKG(self.conn, include_sinks=True)
        all_nodes = list(lkg.iter_nodes())
        n_total = len(all_nodes)

        neighbors: Dict[str, set] = {}
        for v in all_nodes:
            sym = set()
            for w in lkg.out_neighbors(v):
                if w != v:
                    sym.add(w)
            for u in lkg.in_neighbors(v):
                if u != v:
                    sym.add(u)
            neighbors[v] = sym

        scored: List[tuple] = []
        for idx, v in enumerate(all_nodes):
            if progress_callback is not None and idx % 100 == 0:
                progress_callback(idx, n_total)
            n_set = neighbors[v]
            triangles = 0
            n_list = sorted(n_set)
            for i in range(len(n_list)):
                u = n_list[i]
                u_neighbors = neighbors.get(u, set())
                for j in range(i + 1, len(n_list)):
                    w = n_list[j]
                    if w in u_neighbors:
                        triangles += 1
            k = len(n_set)
            lcc = (triangles / (k * (k - 1) / 2)) if k >= 2 else 0.0
            scored.append((v, triangles, lcc))

        if progress_callback is not None:
            progress_callback(n_total, n_total)

        scored.sort(key=lambda r: (-r[1], r[0]))
        if top_k > 0:
            scored = scored[:top_k]
        return IVGResult(
            columns=["id", "triangles", "lcc"],
            rows=[[v, t, l] for (v, t, l) in scored],
        )

    def execute_scc(self, top_k: int,
                    progress_callback: Optional[Callable[[int, int], None]] = None) -> IVGResult:
        if os.environ.get("IVG_DISABLE_ARNO") != "1":
            try:
                return self._scc_arno(top_k)
            except Exception as e:
                from iris_vector_graph.stores.arno_bridge import ArnoError
                if not isinstance(e, ArnoError):
                    logger.warning("SCC arno path raised non-ArnoError: %s", e)
        return self._scc_lazykg(top_k, progress_callback)

    def _scc_arno(self, top_k: int) -> IVGResult:
        """Spec 163 FR-024 arno path via chunked NKG-format adjacency upload."""
        from iris_vector_graph.stores.arno_bridge import (
            arno_call, build_kg_adjacency_chunked,
        )
        idx_to_node, _edge_count = build_kg_adjacency_chunked(self.conn)
        raw = arno_call(self.conn, "kg_scc_run", int(top_k))
        import json as _json; results = _json.loads(raw) if raw else []
        rows = [[r.get("id", ""), int(r.get("component", 0)), int(r.get("size", 0))]
                for r in results]
        return IVGResult(columns=["id", "component", "size"], rows=rows)

    def _scc_lazykg(self, top_k: int,
                     progress_callback: Optional[Callable[[int, int], None]]) -> IVGResult:
        """Spec 163 FR-025: Strongly Connected Components via iterative Tarjan.

        Tarjan 1972: single DFS pass with low-link tracking. Iterative version
        uses explicit stack frames to avoid Python recursion limit (~1000 frames)
        on graphs with deep DFS chains (e.g., long directed paths in 100K+
        node graphs).

        Direction-aware: SCC is inherently DIRECTED; does NOT symmetrize
        adjacency (key difference from existing Algorithms.WCCJson which is
        undirected).

        Output component IDs remapped to contiguous 0..K-1 sorted by
        descending component size (component 0 = largest), per spec 163 FR-009.
        """
        from iris_vector_graph.stores.lazy_kg import LazyKG

        lkg = LazyKG(self.conn, include_sinks=True)
        all_nodes = list(lkg.iter_nodes())
        n_total = len(all_nodes)
        if n_total == 0:
            return IVGResult(columns=["id", "component", "size"], rows=[])

        index: Dict[str, int] = {}
        lowlink: Dict[str, int] = {}
        on_stack: set = set()
        scc_stack: List[str] = []
        components: List[List[str]] = []
        counter = [0]

        for start in all_nodes:
            if start in index:
                continue
            work_stack: List[tuple] = [(start, iter(lkg.out_neighbors(start)), False)]
            index[start] = counter[0]
            lowlink[start] = counter[0]
            counter[0] += 1
            scc_stack.append(start)
            on_stack.add(start)

            while work_stack:
                v, it, _ = work_stack[-1]
                try:
                    w = next(it)
                    if w not in index:
                        index[w] = counter[0]
                        lowlink[w] = counter[0]
                        counter[0] += 1
                        scc_stack.append(w)
                        on_stack.add(w)
                        work_stack.append((w, iter(lkg.out_neighbors(w)), False))
                    elif w in on_stack:
                        lowlink[v] = min(lowlink[v], index[w])
                except StopIteration:
                    if lowlink[v] == index[v]:
                        comp: List[str] = []
                        while True:
                            u = scc_stack.pop()
                            on_stack.discard(u)
                            comp.append(u)
                            if u == v:
                                break
                        components.append(comp)
                    work_stack.pop()
                    if work_stack:
                        parent = work_stack[-1][0]
                        lowlink[parent] = min(lowlink[parent], lowlink[v])

            if progress_callback is not None:
                progress_callback(len(index), n_total)

        components.sort(key=len, reverse=True)
        rows: List[list] = []
        for comp_id, comp in enumerate(components):
            size = len(comp)
            for node in comp:
                rows.append([node, comp_id, size])

        rows.sort(key=lambda r: (-r[2], r[1], r[0]))
        if top_k > 0:
            rows = rows[:top_k]
        return IVGResult(columns=["id", "component", "size"], rows=rows)

    def execute_k_core(self, top_k: int,
                       progress_callback: Optional[Callable[[int, int], None]] = None) -> IVGResult:
        if os.environ.get("IVG_DISABLE_ARNO") != "1":
            try:
                return self._k_core_arno(top_k)
            except Exception as e:
                from iris_vector_graph.stores.arno_bridge import ArnoError
                if not isinstance(e, ArnoError):
                    logger.warning("K-Core arno path raised non-ArnoError: %s", e)
        return self._k_core_lazykg(top_k, progress_callback)

    def _k_core_arno(self, top_k: int) -> IVGResult:
        """Spec 163 FR-024 arno path via chunked NKG-format adjacency upload."""
        from iris_vector_graph.stores.arno_bridge import (
            arno_call, build_kg_adjacency_chunked,
        )
        idx_to_node, _edge_count = build_kg_adjacency_chunked(self.conn)
        raw = arno_call(self.conn, "kg_kcore_run", int(top_k))
        import json as _json; results = _json.loads(raw) if raw else []
        rows = [[r.get("id", ""), int(r.get("coreness", 0))] for r in results]
        return IVGResult(columns=["id", "coreness"], rows=rows)

    def _k_core_lazykg(self, top_k: int,
                        progress_callback: Optional[Callable[[int, int], None]]) -> IVGResult:
        """Spec 163 FR-025: K-Core decomposition via Batagelj-Zaversnik (2003).

        Linear-time bucket-sort algorithm:
          1. Build symmetrized adjacency (K-Core is inherently undirected;
             matches networkx convention). Skip self-loops, collapse multi-edges.
          2. Compute initial degrees, bucket nodes by degree.
          3. Repeatedly pop lowest-degree node v, record coreness=current k,
             decrement neighbors' effective degrees (move them to lower buckets).

        Complexity: O(V+E) amortized.

        Output sorted by descending coreness then ascending node_id (stable
        tie-break).
        """
        from iris_vector_graph.stores.lazy_kg import LazyKG

        lkg = LazyKG(self.conn, include_sinks=True)
        all_nodes = list(lkg.iter_nodes())
        n_total = len(all_nodes)
        if n_total == 0:
            return IVGResult(columns=["id", "coreness"], rows=[])

        adj: Dict[str, set] = {node: set() for node in all_nodes}
        for v in all_nodes:
            for w in lkg.out_neighbors(v):
                if w != v and w in adj:
                    adj[v].add(w)
                    adj[w].add(v)
            for u in lkg.in_neighbors(v):
                if u != v and u in adj:
                    adj[v].add(u)
                    adj[u].add(v)

        deg: Dict[str, int] = {v: len(neighbors) for v, neighbors in adj.items()}
        max_deg = max(deg.values()) if deg else 0
        buckets: List[List[str]] = [[] for _ in range(max_deg + 1)]
        for v, d in deg.items():
            buckets[d].append(v)
        in_bucket: Dict[str, int] = dict(deg)

        coreness: Dict[str, int] = {}
        for k in range(max_deg + 1):
            while buckets[k]:
                v = buckets[k].pop()
                if v in coreness:
                    continue
                if in_bucket.get(v) != k:
                    continue
                coreness[v] = k
                if progress_callback is not None and len(coreness) % 100 == 0:
                    progress_callback(len(coreness), n_total)
                for w in adj[v]:
                    if w in coreness:
                        continue
                    cur = in_bucket[w]
                    if cur > k:
                        new_d = cur - 1
                        in_bucket[w] = new_d
                        buckets[new_d].append(w)

        if progress_callback is not None:
            progress_callback(n_total, n_total)

        rows = sorted(
            ([node, c] for node, c in coreness.items()),
            key=lambda r: (-r[1], r[0]),
        )
        if top_k > 0:
            rows = rows[:top_k]
        return IVGResult(columns=["id", "coreness"], rows=rows)

    def close(self) -> None:
        pass
