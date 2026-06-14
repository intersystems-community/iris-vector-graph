import json
import logging
from typing import Dict, Any, Optional, List

from iris_vector_graph.schema import GraphSchema
from iris_vector_graph.cypher.translator import _table
from iris_vector_graph._validate import NodeIdInput, EdgeInput

logger = logging.getLogger(__name__)


class _BulkLoadSession:
    def __init__(self, engine, stats, max_retries):
        self._engine = engine
        self.stats = stats
        self._max_retries = max_retries

    def add_nodes(self, nodes):
        n = self._engine._with_reconnect(
            self._engine.bulk_create_nodes, nodes, max_retries=self._max_retries
        )
        self.stats["nodes"] += (n if isinstance(n, int) else len(nodes))
        return n

    def add_edges(self, edges, predicate="KNOWS"):
        n = self._engine._with_reconnect(
            self._engine.bulk_ingest_edges, edges, predicate,
            auto_sync=False, max_retries=self._max_retries,
        )
        self.stats["edges"] += (n if isinstance(n, int) else len(edges))
        return n


class NodesEdgesMixin:
    """Node and edge CRUD mixin for IRISGraphEngine.
    
    Provides node/edge creation, retrieval, deletion, and bulk operations."""

    def bulk_load_session(self, max_retries: int = 3, rebuild_indexes: bool = True,
                          incremental: bool = True):
        from contextlib import contextmanager

        @contextmanager
        def _session():
            import time as _time
            from iris_vector_graph.schema import GraphSchema

            stats: Dict[str, Any] = {
                "nodes": 0, "edges": 0, "retries": 0,
                "load_seconds": 0.0, "index_rebuild_seconds": 0.0,
                "sync_seconds": 0.0, "incremental": incremental,
            }
            if rebuild_indexes:
                try:
                    GraphSchema.disable_indexes(self.conn.cursor())
                    self.conn.commit()
                except Exception as e:
                    logger.warning("bulk_load_session: disable_indexes skipped: %s", str(e)[:120])

            if incremental:
                try:
                    self._iris_obj().classMethodValue("Graph.KG.Traversal", "InitNKGSkeleton")
                except Exception as e:
                    logger.warning("bulk_load_session: InitNKGSkeleton failed, falling back to full rebuild: %s", str(e)[:120])
                    incremental_ok = False
                else:
                    incremental_ok = True
            else:
                incremental_ok = False

            session = _BulkLoadSession(self, stats, max_retries)
            t0 = _time.perf_counter()
            try:
                yield session
            finally:
                stats["load_seconds"] = round(_time.perf_counter() - t0, 2)
                if rebuild_indexes:
                    tr = _time.perf_counter()
                    try:
                        GraphSchema.rebuild_indexes(self.conn.cursor())
                        self.conn.commit()
                    except Exception as e:
                        logger.warning("bulk_load_session: rebuild_indexes failed: %s", str(e)[:120])
                    stats["index_rebuild_seconds"] = round(_time.perf_counter() - tr, 2)
                ts = _time.perf_counter()
                if incremental_ok and not self._bulk_load_drifted():
                    self._nkg_dirty = False
                    try:
                        self._iris_obj().classMethodValue("Graph.KG.Traversal", "Build2HopStats")
                    except Exception:
                        pass
                else:
                    if incremental_ok:
                        logger.warning("bulk_load_session: ^NKG drift detected — running full sync()")
                    try:
                        self.sync()
                    except Exception as e:
                        logger.warning("bulk_load_session: final sync() failed: %s", str(e)[:120])
                stats["sync_seconds"] = round(_time.perf_counter() - ts, 2)

        return _session()


    def _bulk_load_drifted(self) -> bool:
        try:
            iris_obj = self._iris_obj()
            nkg_nodes = int(iris_obj.classMethodValue("Graph.KG.Traversal", "NKGNodeCount"))
            cur = self.conn.cursor()
            cur.execute("SELECT COUNT(*) FROM Graph_KG.rdf_edges")
            sql_edges = int(cur.fetchone()[0])
            if sql_edges == 0:
                return False
            return nkg_nodes == 0
        except Exception:
            return True


    def backfill_2hop_exact(self) -> int:
        try:
            return int(self._iris_obj().classMethodValue("Graph.KG.Traversal", "Build2HopExactStats"))
        except Exception as e:
            logger.warning("backfill_2hop_exact failed: %s", str(e)[:120])
            return 0


    def _assert_node_exists(self, node_id: str) -> None:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                f"SELECT COUNT(*) FROM {_table('nodes')} WHERE node_id = ?", [node_id]
            )
            result = cursor.fetchone()
            if not result or result[0] == 0:
                raise ValueError(f"Node does not exist: {node_id}")
        except ValueError:
            raise
        except Exception:
            pass
        finally:
            if hasattr(cursor, 'close'):
                cursor.close()


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
                # arno 1d75d97 bug: predicate is written as "R" placeholder.
                # Fall back to matching by (s, o) only when p is the sentinel.
                if p == "R":
                    cursor.execute(
                        f"SELECT qualifiers FROM {_table('rdf_edges')} "
                        "WHERE s=? AND o_id=?",
                        [s, o],
                    )
                else:
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
        self, node_id: str, labels: List[str] = None, properties: Dict[str, Any] = None,
        graph: Optional[str] = None,
    ) -> bool:
        """Create a node in the knowledge graph.

        Args:
            node_id: Unique string identifier for the node.
            labels: Optional list of label strings (e.g., ["Person", "Employee"]).
            properties: Optional dict of property key-value pairs.
            graph: Optional named graph identifier.

        Returns:
            True if the node was created or already existed, False on error.

        Example:
            >>> engine.create_node("gene:TP53", labels=["Gene"], properties={"name": "TP53"})
            True
        """
        NodeIdInput(node_id=node_id)
        cursor = self.conn.cursor()
        try:
            cursor.execute("START TRANSACTION")

            cursor.execute(
                f"INSERT INTO {_table('nodes')} (node_id) VALUES (?)", [node_id]
            )

            if labels:
                label_data = [[node_id, label] for label in labels]
                cursor.executemany(
                    f"INSERT INTO {_table('rdf_labels')} (s, label) VALUES (?, ?)",
                    label_data,
                )

            props = dict(properties) if properties else {}
            if "id" not in props:
                props["id"] = node_id
            if graph:
                props["__graph"] = graph

            prop_data = []
            for k, v in props.items():
                if v is None:
                    continue
                val_str = json.dumps(v) if isinstance(v, (dict, list)) else str(v)
                prop_data.append([node_id, k, val_str, node_id, k])

            prop_sql = f'INSERT INTO {_table("rdf_props")} (s, "key", val) SELECT ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM {_table("rdf_props")} WHERE s = ? AND "key" = ?)'
            cursor.executemany(prop_sql, prop_data)

            cursor.execute("COMMIT")
            return True
        except Exception as e:
            cursor.execute("ROLLBACK")
            err_lower = str(e).lower()
            if (
                "unique" in err_lower
                or "-119" in str(e)
                or "validation failed" in err_lower
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
        weight: float = 1.0,
        qualifiers: Dict[str, Any] = None,
        graph: Optional[str] = None,
    ) -> bool:
        """Create an edge in the knowledge graph.

        Args:
            source_id: Source node identifier.
            predicate: Relationship type (e.g., "KNOWS", "CALLS").
            target_id: Target node identifier.
            weight: Edge weight used by weighted shortest-path / cost traversals (default 1.0).
            qualifiers: Optional relationship properties.
            graph: Optional named graph identifier.

        Returns:
            True if the edge was created or already existed, False on error.
        """
        EdgeInput(source_id=source_id, predicate=predicate, target_id=target_id)
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
            err_lower = str(e).lower()
            if "unique" in err_lower or "-119" in str(e):
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
                str(float(weight)),
            )
        except Exception as e:
            logger.warning(f"create_edge ^KG write failed (BuildKG can recover): {e}")
        return True


    def set_edge_weight(
        self, source: str, predicate: str, target: str, weight: float
    ) -> bool:
        """Set or update the weight of an existing edge.

        Used by weighted shortest-path / cost traversals.

        Args:
            source: Source node identifier.
            predicate: Relationship type.
            target: Target node identifier.
            weight: New edge weight.

        Returns:
            True on success, False if the edge could not be updated.
        """
        EdgeInput(source_id=source, predicate=predicate, target_id=target)
        try:
            self._iris_obj().classMethodVoid(
                "Graph.KG.EdgeScan",
                "WriteAdjacency",
                source,
                predicate,
                target,
                str(float(weight)),
            )
            return True
        except Exception as e:
            logger.warning(f"set_edge_weight failed: {e}")
            return False


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

        # Index drop/rebuild (disable_indexes) calls DDL via cursor. On IRIS, DDL after
        # a createIRIS() native API call on the same connection corrupts parameter binding
        # state. Skip for small batches where the overhead is not justified anyway.
        _DISABLE_IDX_THRESHOLD = 500
        if disable_indexes and len(nodes) < _DISABLE_IDX_THRESHOLD:
            disable_indexes = False

        if self.capabilities.objectscript_deployed:
            try:
                import json as _json
                from iris_vector_graph.schema import _call_classmethod_large
                iris_obj = self._iris_obj()
                normalized = [
                    {
                        "id": n.get("id", ""),
                        "labels": n.get("labels", []),
                        "props": n.get("properties", {}),
                    }
                    for n in nodes if n.get("id")
                ]
                created = []
                for i in range(0, len(normalized), _BULK_CHUNK_SIZE):
                    chunk = normalized[i:i + _BULK_CHUNK_SIZE]
                    count = int(_call_classmethod_large(
                        iris_obj, "Graph.KG.EdgeScan", "BulkIngestNodesSQL",
                        _json.dumps(chunk),
                    ))
                    created.extend(c["id"] for c in chunk[:count])
                return created
            except Exception as e:
                logger.warning("BulkIngestNodesSQL failed (%s), falling back to SQL path", e)

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
                if "id" not in props:
                    props["id"] = node_id
                if node.get("graph"):
                    props["__graph"] = node["graph"]

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
        auto_sync: bool = True,
        auto_rebuild_kg: bool = None,
    ) -> int:
        if auto_rebuild_kg is not None:
            import warnings
            warnings.warn(
                "auto_rebuild_kg= is deprecated. Use auto_sync= instead.",
                DeprecationWarning,
                stacklevel=2,
            )
            auto_sync = auto_rebuild_kg

        if len(edges) > 250_000 and disable_indexes and not getattr(self, "_large_load_hinted", False):
            self._large_load_hinted = True
            logger.info(
                "bulk_create_edges called with %d edges and per-call index rebuild "
                "(disable_indexes=True). For multi-million-edge loads use "
                "engine.bulk_load_session() — it disables/rebuilds indexes once and "
                "syncs once, avoiding O(table-size) per-batch rebuilds.",
                len(edges),
            )

        if not edges:
            return 0

        # Same guard as bulk_create_nodes: DDL after createIRIS() on the same connection
        # permanently corrupts IRIS driver parameter binding state. Skip for small batches.
        _DISABLE_IDX_THRESHOLD = 500
        if disable_indexes and len(edges) < _DISABLE_IDX_THRESHOLD:
            disable_indexes = False

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
            if auto_sync:
                self.sync()


    def bulk_ingest_edges(
        self,
        edges: List[Dict[str, Any]],
        predicate: str = "KNOWS",
        auto_sync: bool = True,
    ) -> int:
        if not edges:
            return 0
        import json as _json
        normalized = []
        for e in edges:
            if isinstance(e, (list, tuple)):
                s, o = str(e[0]), str(e[1])
                p = str(e[2]) if len(e) > 2 else predicate
            else:
                s = str(e.get("s", e.get("source", "")))
                p = str(e.get("p", e.get("predicate", predicate)))
                o = str(e.get("o", e.get("target", "")))
            if s and o:
                normalized.append({"s": s, "p": p, "o": o})

        if self.capabilities.objectscript_deployed:
            try:
                from iris_vector_graph.schema import _call_classmethod_large
                iris_obj = self._iris_obj()
                n = 0
                for i in range(0, len(normalized), _BULK_CHUNK_SIZE):
                    chunk = normalized[i:i + _BULK_CHUNK_SIZE]
                    n += int(_call_classmethod_large(
                        iris_obj, "Graph.KG.EdgeScan", "BulkIngestEdgesSQL",
                        _json.dumps(chunk), predicate,
                    ))
                self._nkg_dirty = True
                if auto_sync:
                    self.sync()
                return n
            except Exception as e:
                logger.warning("BulkIngestEdgesSQL failed (%s), falling back to SQL path", e)

        cursor = self.conn.cursor()
        n = 0
        err_lower = lambda ex: ("unique" in str(ex).lower() or "-119" in str(ex))
        for edge in normalized:
            s, p, o = edge["s"], edge["p"], edge["o"]
            try:
                cursor.execute(
                    "INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
                    [s, p, o],
                )
            except Exception as ex:
                if err_lower(ex):
                    continue  # duplicate edge — skip silently
            try:
                self._iris_obj().classMethodVoid("Graph.KG.EdgeScan", "WriteAdjacency", s, p, o, "1.0")
            except Exception:
                pass
            n += 1
        self.conn.commit()
        self._nkg_dirty = True
        if auto_sync:
            self.sync()
        return n




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


    def bulk_delete_nodes(self, node_ids: List[str], batch_size: int = 200) -> int:
        deleted = 0
        for i in range(0, len(node_ids), batch_size):
            batch = node_ids[i : i + batch_size]
            phs = ",".join(["?"] * len(batch))
            cursor = self.conn.cursor()
            try:
                cursor.execute(
                    f"DELETE FROM {_table('rdf_reifications')} WHERE edge_id IN "
                    f"(SELECT edge_id FROM {_table('rdf_edges')} WHERE s IN ({phs}) OR o_id IN ({phs}))",
                    batch + batch,
                )
                cursor.execute(
                    f"DELETE FROM {_table('kg_NodeEmbeddings')} WHERE id IN ({phs})", batch
                )
                cursor.execute(
                    f"DELETE FROM {_table('rdf_edges')} WHERE s IN ({phs}) OR o_id IN ({phs})",
                    batch + batch,
                )
                cursor.execute(f"DELETE FROM {_table('rdf_labels')} WHERE s IN ({phs})", batch)
                cursor.execute(f"DELETE FROM {_table('rdf_props')} WHERE s IN ({phs})", batch)
                cursor.execute(
                    f"DELETE FROM {_table('nodes')} WHERE node_id IN ({phs})", batch
                )
                self.conn.commit()
                deleted += len(batch)
            except Exception as e:
                logger.warning(f"bulk_delete_nodes batch failed: {e}")
            finally:
                cursor.close()
        return deleted



    def get_node_properties(self, node_id: str) -> Dict[str, Any]:
        node = self.get_node(node_id)
        if not node:
            return {}
        return {k: v for k, v in node.items() if k not in ("id", "labels")}


    def get_node_name(self, node_id: str) -> Optional[str]:
        props = self.get_node_properties(node_id)
        return props.get("name") or props.get("label") or props.get("title")


    def get_nodes_by_ids(self, node_ids: List[str]) -> List[Dict[str, Any]]:
        if not node_ids:
            return []
        return self.get_nodes(node_ids)


    def node_count(self) -> int:
        result = self.execute_cypher("MATCH (n) RETURN count(n) AS c")
        rows = result.get("rows") or []
        return int(rows[0][0]) if rows else 0


    def edge_count(self) -> int:
        result = self.execute_cypher("MATCH ()-[r]->() RETURN count(r) AS c")
        rows = result.get("rows") or []
        return int(rows[0][0]) if rows else 0


    def store_node(self, node_id: str, properties: Optional[Dict[str, Any]] = None,
                   labels: Optional[List[str]] = None) -> bool:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                f"INSERT INTO {_table('nodes')} (node_id) VALUES (?)", [node_id]
            )
            self.conn.commit()
        except Exception as e:
            err_lower = str(e).lower()
            if "-119" not in str(e) and "duplicate" not in err_lower and "unique" not in err_lower:
                raise
        finally:
            cursor.close()
        if properties:
            for k, v in (properties or {}).items():
                val_str = json.dumps(v) if isinstance(v, (dict, list)) else str(v)
                cursor2 = self.conn.cursor()
                try:
                    cursor2.execute(
                        f"DELETE FROM {_table('rdf_props')} WHERE s = ? AND \"key\" = ?",
                        [node_id, k]
                    )
                    cursor2.execute(
                        f"INSERT INTO {_table('rdf_props')} (s, \"key\", val) VALUES (?, ?, ?)",
                        [node_id, k, val_str]
                    )
                    self.conn.commit()
                except Exception:
                    pass
                finally:
                    cursor2.close()
        if labels:
            for lbl in labels:
                cursor3 = self.conn.cursor()
                try:
                    cursor3.execute(
                        f"INSERT INTO {_table('rdf_labels')} (s, label) VALUES (?, ?)",
                        [node_id, lbl]
                    )
                    self.conn.commit()
                except Exception as e:
                    err_lower = str(e).lower()
                    if "-119" not in str(e) and "duplicate" not in err_lower and "unique" not in err_lower:
                        raise
                finally:
                    cursor3.close()
        return True


    def store_edge(self, source_id: str, predicate: str, target_id: str,
                   qualifiers: Optional[Dict[str, Any]] = None) -> bool:
        self.store_node(source_id)
        self.store_node(target_id)
        cursor = self.conn.cursor()
        try:
            qual_json = json.dumps(qualifiers) if qualifiers else None
            cursor.execute(
                f"INSERT INTO {_table('rdf_edges')} (s, p, o_id, qualifiers) VALUES (?, ?, ?, ?)",
                [source_id, predicate, target_id, qual_json],
            )
            self.conn.commit()
        except Exception as e:
            err_lower = str(e).lower()
            if "-119" not in str(e) and "duplicate" not in err_lower and "unique" not in err_lower:
                raise
        finally:
            cursor.close()
        return True


    def nodes_exist(self, node_ids: List[str]) -> set:
        if not node_ids:
            return set()
        existing: set = set()
        cursor = self.conn.cursor()
        try:
            for i in range(0, len(node_ids), 200):
                batch = node_ids[i:i + 200]
                phs = ",".join(["?"] * len(batch))
                try:
                    cursor.execute(
                        f"SELECT node_id FROM {_table('nodes')} WHERE node_id IN ({phs})",
                        batch,
                    )
                    for row in cursor.fetchall():
                        existing.add(row[0])
                except Exception:
                    for nid in batch:
                        try:
                            cursor.execute(
                                f"SELECT COUNT(*) FROM {_table('nodes')} WHERE node_id = ?",
                                [nid],
                            )
                            row = cursor.fetchone()
                            if row and int(row[0]) > 0:
                                existing.add(nid)
                        except Exception:
                            pass
        finally:
            cursor.close()
        return existing
