import json
import logging
import math
import warnings
from typing import Any, Dict, List, NamedTuple, Optional

from iris_vector_graph._engine.ledger import ledger_check as _ledger_check
from iris_vector_graph._validate import (
    EdgeInput,
    NodeIdInput,
    graph_index_key,
    validate_graph_name,
)
from iris_vector_graph.constants import BULK_CHUNK_SIZE as _BULK_CHUNK_SIZE
from iris_vector_graph.constants import DEFAULT_GRAPH
from iris_vector_graph.constants import TEMPORAL_MARKED_ROW as _TEMPORAL_MARKED_ROW
from iris_vector_graph._write_txn import is_duplicate as _is_duplicate
from iris_vector_graph._write_txn import rollback as _rollback
from iris_vector_graph.errors import GraphWriteError
from iris_vector_graph.prop_values import decode_typed_text, parse_prop_text, prop_text
from iris_vector_graph.routing import graph_scope_predicate
from iris_vector_graph.schema import GraphSchema
from iris_vector_graph.schema_name import rewrite_schema_text

logger = logging.getLogger(__name__)


def _rollback_conn(conn) -> None:
    """Roll the connection back before raising; a failure here is cleanup and must not hide the error raised."""
    try:
        conn.rollback()
    except Exception as e:  # noqa: BLE001 - the write's own error is the one raised
        logger.debug("rollback before raising failed: %s", e)


def _ingest_left(engine, written: int) -> str:
    """What a failed ``bulk_ingest_edges`` on the SQL path leaves: its rows are rolled back; without the adjacency
    index the ^KG entries it wrote by hand for the edges before it are not."""
    if not written or engine._adjacency_indexed:
        return "nothing written"
    return f"rows rolled back, but the ^KG adjacency of the {written} edges before it stays (sync() rebuilds it)"


# a bulk load of at least this many rows gathers the tables' statistics when it ends (TUNE TABLE)
TUNE_AFTER_ROWS = 10_000


def _rebuild_after_load(cursor, schema: str, caller: str) -> List[str]:
    """Rebuild the indexes a bulk load dropped; the ones that did not come back, named in a WARNING (4.6.0 R257).

    GraphSchema.rebuild_indexes reported them only in a status dict, which no caller read: R243's lost indexes were
    hidden that way. A read that wants a missing index scans the table."""
    missing = GraphSchema.indexes_not_rebuilt(GraphSchema.rebuild_indexes(cursor, schema=schema))
    if missing:
        logger.warning(
            "%s: these indexes the load dropped were not rebuilt: %s; reads that use them scan the table. "
            "Rebuild them with GraphSchema.rebuild_indexes(cursor, schema=%r)",
            caller, ", ".join(missing), schema,
        )
    return missing


#: ``graph`` omitted: the one graph holding the node, or ValueError when more do (distinct from None, the default graph).
_EVERY_GRAPH: Any = object()

# Ids per graph-discovery statement; IRIS fails to prepare IN lists near 2000 params.
_DELETE_LOOKUP_CHUNK = 500

#: An explicit write adopts the row a temporal edge asserted (ADR-0009, spec 254): the marker goes, so a purge of the
#: triple's timestamps leaves the edge.
_ADOPT_TEMPORAL_ROW_SQL = (
    "UPDATE Graph_KG.rdf_edges SET qualifiers = NULL WHERE s = ? AND p = ? AND o_id = ? "
    f"AND COALESCE(graph_id, '') = COALESCE(?, '') AND {_TEMPORAL_MARKED_ROW}"
)


def _deleted_count(result: Any) -> int:
    try:
        return int(result.rows[0][0] or 0)
    except (AttributeError, IndexError, TypeError, ValueError):
        return 0


class DeleteResult(NamedTuple):
    deleted: int
    failed: int

    def __int__(self) -> int:
        return self.deleted

    def __bool__(self) -> bool:
        return self.deleted > 0


_IRIS_MAX_STMT = 16_384


def _batch_size_for(ids: list) -> int:
    if not ids:
        return 200
    avg_len = sum(len(i) for i in ids[:20]) / min(20, len(ids))
    return max(10, int(_IRIS_MAX_STMT // (avg_len + 3)))


def _swallow_duplicate(conn, exc: Exception, exists_sql: str, params: list) -> None:
    """Return if `exc` is a duplicate-key error, otherwise re-raise it.

    The usual case is text we can match: `SQLCODE -119`, "duplicate", "unique".
    The awkward case is a connection whose cached INSERT for a class predates
    another connection's `%BuildIndices` on that same class — which is what a
    `%NOINDEX` bulk load does. It can no longer decode that class's own -119, and
    the violation arrives as `<LIST ERROR> Incorrect list format ... type detected
    : 0`, permanently, with nothing in the text to match on. Rather than treat
    every `<LIST ERROR>` as a duplicate, ask the database whether the row is in
    fact there; if it is not, the error was something else and is re-raised.
    """
    txt = str(exc)
    low = txt.lower()
    if "-119" in txt or "duplicate" in low or "unique" in low:
        return
    if "<list error>" not in low:
        raise exc
    cur = conn.cursor()
    try:
        cur.execute(exists_sql, params)
        found = cur.fetchone() is not None
    except Exception:
        raise exc
    finally:
        cur.close()
    if not found:
        raise exc


#: ``bulk_ingest_edges``'s dict keys, by role: the first one present names it (R213).
_INGEST_KEYS = {
    "source": ("source_id", "s", "source"),
    "target": ("target_id", "o", "target"),
    "predicate": ("predicate", "p"),
}
_INGEST_SHAPES = (
    "a (source, target) or (source, target, predicate) tuple, or a dict with source_id/s/source, "
    "target_id/o/target and optionally predicate/p"
)
#: ``bulk_create_edges``'s and ``bulk_create_nodes``'s dict keys (R213).
_CREATE_EDGE_KEYS = ("source_id", "predicate", "target_id")
_CREATE_EDGE_OPTIONAL = ("graph",)
_CREATE_NODE_KEYS = ("id", "labels", "properties", "graph")


def _ingest_edge_triple(i: int, e: Any, predicate: str) -> Dict[str, str]:
    """``{"s", "p", "o"}`` for edge ``i`` of a ``bulk_ingest_edges`` batch; ValueError for a shape it does not take.

    Before R213 a dict keyed ``source_id``/``target_id`` (the shape ``bulk_create_edges`` takes) became an empty source
    and target, and the edge was dropped: the call wrote 0 edges and said nothing.
    """
    if isinstance(e, (list, tuple)):
        if len(e) not in (2, 3):
            raise ValueError(f"bulk_ingest_edges: edge {i} {e!r} has {len(e)} items, not 2 or 3; give {_INGEST_SHAPES}")
        s, o = e[0], e[1]
        p = e[2] if len(e) > 2 else predicate
    elif isinstance(e, dict):
        known = {k for keys in _INGEST_KEYS.values() for k in keys}
        unknown = sorted(str(k) for k in e if k not in known)
        if "graph" in e:
            raise ValueError(
                f"bulk_ingest_edges: edge {i} names a graph ({e['graph']!r}); it writes the default graph only. "
                "Use bulk_create_edges(..., graph=...) for a named graph"
            )
        if unknown:
            raise ValueError(
                f"bulk_ingest_edges: edge {i} has key(s) {', '.join(repr(k) for k in unknown)}, which it does not "
                f"store (no weights or qualifiers); give {_INGEST_SHAPES}"
            )

        def pick(role, default=None):
            for k in _INGEST_KEYS[role]:
                if k in e:
                    return e[k]
            return default

        s, o, p = pick("source"), pick("target"), pick("predicate", predicate)
    else:
        raise ValueError(
            f"bulk_ingest_edges: edge {i} is a {type(e).__name__}, not a tuple or a dict; give {_INGEST_SHAPES}"
        )
    for role, v in (("source", s), ("target", o), ("predicate", p)):
        if v is None or str(v) == "":
            raise ValueError(f"bulk_ingest_edges: edge {i} {e!r} has no {role}; give {_INGEST_SHAPES}")
    return {"s": str(s), "p": str(p), "o": str(o)}


def _check_create_edges(edges: List[Any]) -> None:
    """ValueError naming the first edge ``bulk_create_edges`` cannot write as given (R213: it was skipped)."""
    allowed = set(_CREATE_EDGE_KEYS) | set(_CREATE_EDGE_OPTIONAL)
    for i, e in enumerate(edges):
        if not isinstance(e, dict):
            raise ValueError(
                f"bulk_create_edges: edge {i} is a {type(e).__name__}, not a dict with source_id, predicate and "
                "target_id (bulk_ingest_edges takes tuples)"
            )
        missing = [k for k in _CREATE_EDGE_KEYS if e.get(k) in (None, "")]
        if missing:
            raise ValueError(
                f"bulk_create_edges: edge {i} {e!r} has no {', '.join(missing)}; each edge is a dict with "
                "source_id, predicate, target_id and optionally graph"
            )
        unknown = sorted(str(k) for k in e if k not in allowed)
        if unknown:
            raise ValueError(
                f"bulk_create_edges: edge {i} has key(s) {', '.join(repr(k) for k in unknown)}, which it does not "
                "store; it writes source_id, predicate, target_id and graph only. Use create_edge(..., weight=..., "
                "qualifiers=...) for an edge with properties"
            )


_CREATE_NODE_SHAPE = "a dict with 'id' and optionally 'labels', 'properties' and 'graph'"


def _check_create_nodes(nodes: List[Any]) -> None:
    """ValueError naming the first node ``bulk_create_nodes`` cannot write as given (R213: it was skipped)."""
    for i, n in enumerate(nodes):
        if not isinstance(n, dict):
            raise ValueError(f"bulk_create_nodes: node {i} is a {type(n).__name__}, not {_CREATE_NODE_SHAPE}")
        if n.get("id") in (None, ""):
            raise ValueError(f"bulk_create_nodes: node {i} {n!r} has no 'id'; each node is {_CREATE_NODE_SHAPE}")
        unknown = sorted(str(k) for k in n if k not in _CREATE_NODE_KEYS)
        if unknown:
            raise ValueError(
                f"bulk_create_nodes: node {i} has key(s) {', '.join(repr(k) for k in unknown)}, which it does not "
                f"store; a node is {_CREATE_NODE_SHAPE}"
            )


def _weight_text(weight: Any) -> str:
    """The text ``create_edge(weight=...)`` stores, as a Cypher ``CREATE`` of ``{weight: w}`` stores it (``str``)."""
    if isinstance(weight, bool) or not isinstance(weight, (int, float)):
        weight = float(weight)
    if isinstance(weight, float) and not math.isfinite(weight):
        raise ValueError(f"create_edge: weight {weight!r} is not a finite number")
    return str(weight)


def _adjacency_weight(quals: Dict[str, Any]) -> str:
    """The weight a ``^KG`` entry written by hand gets: the row's ``weight`` qualifier, else 1.0 (the index's rule)."""
    try:
        return str(float(quals["weight"]))
    except (KeyError, TypeError, ValueError):
        return "1.0"


class _BulkLoadSession:
    """The session ``bulk_load_session`` yields. ``add_nodes`` takes what ``bulk_create_nodes`` takes, ``add_edges``
    what ``bulk_ingest_edges`` takes (default graph only); either raises ``ValueError`` on another shape, before
    writing."""

    def __init__(self, engine, stats, max_retries):
        self._engine = engine
        self.stats = stats
        self._max_retries = max_retries

    def add_nodes(self, nodes):
        n = self._engine._with_reconnect(
            self._engine.bulk_create_nodes, nodes, max_retries=self._max_retries
        )
        self.stats["nodes"] += n if isinstance(n, int) else len(nodes)
        return n

    def add_edges(self, edges, predicate="KNOWS"):
        n = self._engine._with_reconnect(
            self._engine.bulk_ingest_edges,
            edges,
            predicate,
            auto_sync=False,
            max_retries=self._max_retries,
        )
        self.stats["edges"] += n if isinstance(n, int) else len(edges)
        return n


class NodesEdgesMixin:
    """Node and edge CRUD mixin for IRISGraphEngine.

    Provides node/edge creation, retrieval, deletion, and bulk operations."""

    def _bulk_sql(self, kind: str) -> str:
        """`GraphSchema.get_bulk_insert_sql(kind)`, written for Graph_KG, in this engine's IVG Schema (spec 262)."""
        return rewrite_schema_text(GraphSchema.get_bulk_insert_sql(kind), getattr(self, "_schema_prefix", "Graph_KG"))

    def bulk_load_session(
        self, max_retries: int = 3, rebuild_indexes: bool = True, incremental: bool = True
    ):
        """A context manager for a large load, yielding a session whose ``add_nodes(nodes)`` takes
        ``bulk_create_nodes``'s node dicts (``{"id", "labels", "properties"}``) and whose
        ``add_edges(edges, predicate="KNOWS")`` takes ``bulk_ingest_edges``'s edges: dicts with
        ``source_id``, ``predicate``, ``target_id`` (or ``s``/``p``/``o``), or ``(source, target[,
        predicate])`` tuples, written to the default graph. Another shape raises ``ValueError``
        before anything is written.

        ``add_nodes`` calls ``bulk_create_nodes`` and ``add_edges`` calls ``bulk_ingest_edges``
        (default graph only), each retried on a dropped connection up to ``max_retries`` times.
        Indexes are disabled once at the start and rebuilt once at the end (``rebuild_indexes``),
        the tables are tuned after a large load, and the adjacency is synced once on exit: with
        ``incremental`` the ``^NKG`` built during the load is kept, else ``sync`` runs. The
        session's ``stats`` dict holds counts and timings, and ``indexes_not_rebuilt``: the indexes
        that did not come back, each also named in a WARNING (4.6.0 R257).
        """
        _ledger_check(self, "bulk_load_session")
        from contextlib import contextmanager

        @contextmanager
        def _session():
            import time as _time

            from iris_vector_graph.schema import GraphSchema

            stats: Dict[str, Any] = {
                "nodes": 0,
                "edges": 0,
                "retries": 0,
                "load_seconds": 0.0,
                "index_rebuild_seconds": 0.0,
                "sync_seconds": 0.0,
                "incremental": incremental,
            }
            if rebuild_indexes:
                try:
                    GraphSchema.disable_indexes(self.conn.cursor(), schema=self._ivg_schema)
                    self.conn.commit()
                except Exception as e:
                    logger.warning("bulk_load_session: disable_indexes skipped: %s", str(e)[:120])

            if incremental:
                try:
                    self._iris_obj().classMethodValue("Graph.KG.Traversal", "InitNKGSkeleton")
                except Exception as e:
                    logger.warning(
                        "bulk_load_session: InitNKGSkeleton failed, falling back to full rebuild: %s",
                        str(e)[:120],
                    )
                    incremental_ok = False
                else:
                    incremental_ok = True
            else:
                incremental_ok = False

            session = _BulkLoadSession(self, stats, max_retries)
            self._in_bulk_load = True
            t0 = _time.perf_counter()
            try:
                yield session
            finally:
                self._in_bulk_load = False
                stats["load_seconds"] = round(_time.perf_counter() - t0, 2)
                if rebuild_indexes:
                    tr = _time.perf_counter()
                    try:
                        stats["indexes_not_rebuilt"] = _rebuild_after_load(
                            self.conn.cursor(), self._ivg_schema, "bulk_load_session"
                        )
                        self.conn.commit()
                    except Exception as e:
                        logger.warning(
                            "bulk_load_session: rebuild_indexes failed: %s", str(e)[:120]
                        )
                    stats["index_rebuild_seconds"] = round(_time.perf_counter() - tr, 2)
                if stats["nodes"] + stats["edges"] >= TUNE_AFTER_ROWS:
                    # schema setup tuned the tables while they were empty, so the optimizer planned every read after
                    # a load for empty tables (DRKG: MIN/MAX(graph_id) read all 97,238 nodes, 90 ms, against 0.3 ms
                    # tuned); tuning also drops the namespace's cached plans, which a small load is not worth
                    tt = _time.perf_counter()
                    try:
                        GraphSchema.tune_tables(self.conn.cursor(), schema=self._ivg_schema)
                        self.conn.commit()
                    except Exception as e:
                        logger.warning("bulk_load_session: tune_tables failed: %s", str(e)[:120])
                    stats["tune_seconds"] = round(_time.perf_counter() - tt, 2)
                ts = _time.perf_counter()
                if incremental_ok and not self._bulk_load_drifted():
                    self._nkg_dirty = False
                    # The load wrote adjacency through Graph.KG.Adjacency, which bumps generations and fills no
                    # cache. Warm it once now (one pass over the sources) so the first reader and Build2HopStats
                    # find every degree ready, as they found the stored counters before ADR-0012. Answers are
                    # exact either way; a failure only leaves the cache cold, so say so and carry on.
                    try:
                        self._iris_obj().classMethodValue("Graph.KG.Adj", "WarmCache")
                    except Exception as e:
                        logger.warning("bulk_load_session: degree cache warm-up failed (readers recount): %s", str(e)[:120])
                    try:
                        self._iris_obj().classMethodValue("Graph.KG.Traversal", "Build2HopStats")
                    except Exception:
                        pass
                else:
                    if incremental_ok:
                        logger.warning(
                            "bulk_load_session: ^NKG drift detected — running full sync()"
                        )
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
            cur.execute(f"SELECT COUNT(*) FROM {self._ivg_schema}.rdf_edges")
            sql_edges = int(cur.fetchone()[0])
            if sql_edges == 0:
                return False
            return nkg_nodes == 0
        except Exception:
            return True

    def backfill_2hop_exact(self) -> int:
        """Recompute the exact two-hop reach counts in ``^KG("out")``, per graph, node, predicate.

        Same call as ``backfill_deg2p_exact``: ``Graph.KG.Traversal.Build2HopExactStats``, which
        rewrites ``^KG("deg2p_exact")``. Returns the number of entries written; 0 when the call
        fails (logged as a warning).
        """
        try:
            return int(
                self._iris_obj().classMethodValue("Graph.KG.Traversal", "Build2HopExactStats")
            )
        except Exception as e:
            logger.warning("backfill_2hop_exact failed: %s", str(e)[:120])
            return 0

    def _assert_node_exists(self, node_id: str, *, graph: Optional[str] = None) -> None:
        """Does this node exist *in this graph*?

        Scoped since spec 227 broke `UNIQUE (node_id)`: a node ID can now exist in
        graph B and not in graph A, so an unscoped count says yes to a write that
        has nothing to attach to. The legacy embedding tables declare no FK, so
        this check is the only thing standing between that write and an orphan row.
        """
        graph_id = DEFAULT_GRAPH if graph is None else graph
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                f"SELECT COUNT(*) FROM {self._t('nodes')}"
                f" WHERE node_id = ? AND {graph_scope_predicate('graph_id')}",
                [node_id, graph_id],
            )
            result = cursor.fetchone()
            if not result or result[0] == 0:
                raise ValueError(f"Node does not exist in graph '{graph_id}': {node_id}")
        except ValueError:
            raise
        except Exception as e:
            # It passed (4.6.0 R276), so the write went ahead on a node it never found.
            raise RuntimeError(
                f"could not check that node {node_id!r} exists in graph {graph_id!r} before writing to it: {e}. "
                "Nothing was written"
            ) from e
        finally:
            if hasattr(cursor, "close"):
                cursor.close()

    def get_node(self, node_id: str, graph: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Retrieve a node by ID using optimized direct SQL, as a dict of ``id``, ``labels`` and its properties. A
        property named ``id`` or ``labels`` comes back as ``p_id`` / ``p_labels``, so it cannot overwrite those two:
        ``create_node``, ``bulk_create_nodes`` and ``store_node`` store an ``id`` property equal to the node ID (so
        ``MATCH (n {id: ...})`` finds it), and it reads back as ``p_id`` (4.6.0 R207, R225).

        Bypasses Cypher translation for 80x+ faster single-node lookups.

        Args:
            node_id: Node identifier
            graph: Named graph to read from; omitted means the default graph. Since
                spec 227 a node ID can exist in more than one graph, so an unscoped
                read would merge their labels and properties into one node.

        Returns:
            Dict with 'id', 'labels', and properties, or None if not found
        """
        nodes = self.get_nodes([node_id], graph=graph)
        return nodes[0] if nodes else None

    def _filter_edges_by_properties(self, bfs_results: list, prop_filter: dict) -> list:
        if not prop_filter:
            return bfs_results
        import json as _json

        edges = [
            (r["s"], r["p"], r["o"])
            for r in bfs_results
            if r.get("s") and r.get("p") and r.get("o")
        ]
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
                        f"SELECT qualifiers FROM {self._t('rdf_edges')} " "WHERE s=? AND o_id=?",
                        [s, o],
                    )
                else:
                    cursor.execute(
                        f"SELECT qualifiers FROM {self._t('rdf_edges')} "
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
            logger.debug("_filter_edges_by_properties: no edges match filter %s", prop_filter)

        return [r for r in bfs_results if (r.get("s"), r.get("p"), r.get("o")) in passing]

    def get_nodes(
        self, node_ids: List[str], graph: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Retrieve multiple nodes by ID using optimized batch SQL.

        Eliminates N+1 query patterns by fetching all labels and properties
        for a set of nodes in two efficient queries.

        Args:
            node_ids: List of node identifiers
            graph: Named graph to read from; omitted means the default graph. Every
                statement below is scoped to it — spec 227 allows one node ID in several
                graphs, and an unscoped read merged their label lists and let one graph's
                property values overwrite another's.

        Returns:
            List of node dicts with 'id', 'labels', and properties
        """
        if not node_ids:
            return []

        graph_id = DEFAULT_GRAPH if graph is None else graph

        _IN_CHUNK = 499

        try:
            cursor = self.conn.cursor()
            node_map = {nid: {"id": nid, "labels": []} for nid in node_ids}

            for i in range(0, len(node_ids), _IN_CHUNK):
                chunk = node_ids[i : i + _IN_CHUNK]
                placeholders = ",".join(["?"] * len(chunk))

                cursor.execute(
                    f"SELECT s, label FROM {self._t('rdf_labels')} "
                    f"WHERE graph_id = ? AND s IN ({placeholders})",
                    [graph_id] + list(chunk),
                )
                for s, label in cursor.fetchall():
                    if s in node_map:
                        node_map[s]["labels"].append(label)

                # R218: CY_TYPED reads a value stored as a number (a Cypher write's) as that number under
                # every driver version; 5.4.1 read it as IRIS text (.94999999999999995559)
                cursor.execute(
                    f'SELECT s, "key", {self._ivg_schema}.CY_TYPED(val) FROM {self._t("rdf_props")} '
                    f"WHERE graph_id = ? AND s IN ({placeholders})",
                    [graph_id] + list(chunk),
                )
                _STRUCTURAL_KEYS = ("id", "labels")
                for s, key, val in cursor.fetchall():
                    if s in node_map:
                        store_key = f"p_{key}" if key in _STRUCTURAL_KEYS else key
                        val = decode_typed_text(val)
                        if isinstance(val, str):
                            parsed_val = val
                            try:
                                if (str(val).startswith("{") and str(val).endswith("}")) or (
                                    str(val).startswith("[") and str(val).endswith("]")
                                ):
                                    parsed_val = json.loads(val)
                                else:
                                    parsed_val = parse_prop_text(val)
                            except Exception:
                                pass
                            node_map[s][store_key] = parsed_val
                        else:
                            # a typed number, or NULL
                            node_map[s][store_key] = val

            empty_nids = [
                nid for nid, data in node_map.items() if not data["labels"] and len(data) <= 2
            ]
            if empty_nids:
                existing_empty: set = set()
                for i in range(0, len(empty_nids), _IN_CHUNK):
                    chunk = empty_nids[i : i + _IN_CHUNK]
                    e_placeholders = ",".join(["?"] * len(chunk))
                    cursor.execute(
                        f"SELECT node_id FROM {self._t('nodes')} "
                        f"WHERE graph_id = ? AND node_id IN ({e_placeholders})",
                        [graph_id] + list(chunk),
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

        if not result.rows:
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

    def count_nodes(self, label: Optional[str] = None, graph: Optional[str] = None) -> int:
        """
        Count the nodes in ``graph``, or those with ``label``; 0 on error.

        ``graph`` omitted (or None, ``""``) is the default graph; before 4.6.0 this counted every graph (R249, as
        R219 did for ``node_count``). Raises ``ValueError`` for a graph name that cannot be one.

        Args:
            label: Optional label filter
            graph: The graph counted

        Returns:
            Node count (filtered by label if provided)
        """
        gid = validate_graph_name(graph)
        cursor = self.conn.cursor()
        try:
            if label:
                cursor.execute(
                    f"SELECT COUNT(*) FROM {self._ivg_schema}.rdf_labels WHERE label = ? AND COALESCE(graph_id, '') = ?",
                    [label, gid],
                )
            else:
                cursor.execute(f"SELECT COUNT(*) FROM {self._ivg_schema}.nodes WHERE COALESCE(graph_id, '') = ?", [gid])

            result = cursor.fetchone()
            return result[0] if result else 0
        except Exception as e:
            logger.error(f"Count nodes failed: {e}")
            return 0

    def get_node_ids_by_label(self, label: str) -> List[str]:
        """
        Return all node IDs with the given label via a direct ``rdf_labels`` scan, in all graphs.

        Mirrors Neo4j's NodeByLabelScan: label-membership lookup is a distinct,
        common-enough access pattern to warrant its own primitive rather than
        going through the Cypher MATCH translator, whose generated multi-table
        JOIN can hit the IRIS %qaqpre compiler fault on some builds/instances.

        Args:
            label: Label to filter by.

        Returns:
            List of node IDs (empty list if none match).
        """
        cursor = self.conn.cursor()
        cursor.execute(f"SELECT s FROM {self._t('rdf_labels')} WHERE label = ?", [label])
        return [row[0] for row in cursor.fetchall() if row and row[0]]

    def get_nodes_by_label(self, label: str) -> List[Dict[str, Any]]:
        """
        Return hydrated nodes (id, labels, properties) with the given label.

        The IDs come from every graph and are hydrated from the default graph.
        Equivalent to get_node_ids_by_label + get_nodes, avoiding the JOIN-based
        Cypher MATCH translation path.

        Args:
            label: Label to filter by.

        Returns:
            List of node dicts (see get_nodes), empty list if none match.
        """
        ids = self.get_node_ids_by_label(label)
        return self.get_nodes(ids) if ids else []

    # ------------------------------------------------------------------
    # Property-side primitives (v2.5.0)
    # Single-table scans over rdf_props / nodes only — no JOIN, no FETCH FIRST.
    # ------------------------------------------------------------------

    def get_node_ids_by_property(
        self,
        key: str,
        val: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[str]:
        """Subject ids carrying property `key`, optionally filtered to `= val`.

        Direct mirror of get_node_ids_by_label for the property dimension.
        Single-table scan; no JOIN; uses TOP n (never FETCH FIRST).

        Args:
            key: Property name.
            val: Optional exact value filter.  None means "carries the key at all".
            limit: Cap on returned rows.  Non-positive means no limit.

        Returns:
            List of node ids.
        """
        top = f"TOP {int(limit)} " if limit and int(limit) > 0 else ""
        sql = f'SELECT {top}s FROM {self._t("rdf_props")} WHERE "key" = ?'
        params: List[Any] = [key]
        if val is not None:
            sql += " AND val = ?"
            params.append(val)
        cursor = self.conn.cursor()
        try:
            cursor.execute(sql, params)
            return [row[0] for row in cursor.fetchall() if row and row[0]]
        except Exception as e:
            logger.error("get_node_ids_by_property(%s) failed: %s", key, e)
            return []
        finally:
            cursor.close()

    def get_nodes_by_property(
        self,
        key: str,
        val: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Hydrated nodes carrying property `key` (optionally `= val`).

        Equivalent to get_node_ids_by_property + get_nodes.
        """
        ids = self.get_node_ids_by_property(key, val=val, limit=limit)
        return self.get_nodes(ids) if ids else []

    def get_property_pairs(self, key: str) -> List[tuple]:
        """(subject, value) pairs for every row under property `key`.

        Returns one pair per subject and graph (the key is (graph_id, s, key)); not graph-scoped.
        Useful when you need both the subject id and the value in one pass
        without hydrating full nodes via get_nodes. A value is returned as it is stored, as get_property_values
        returns it (4.6.0 R248).
        """
        sql = f'SELECT s, {self._ivg_schema}.CY_TYPED(val) FROM {self._t("rdf_props")} WHERE "key" = ?'
        cursor = self.conn.cursor()
        try:
            cursor.execute(sql, [key])
            return [(row[0], decode_typed_text(row[1])) for row in cursor.fetchall() if row and row[0]]
        except Exception as e:
            logger.error("get_property_pairs(%s) failed: %s", key, e)
            return []
        finally:
            cursor.close()

    def get_property_values(self, key: str) -> List[Any]:
        """Every value stored under property `key`, across all subjects.

        Value-side scan without subject context: use to seed dedup sets in one
        pass over large property populations (e.g. all source_urls). A value is
        returned as it is stored: text as a string, and a number a Cypher write
        stored as the number (4.6.0; with intersystems-irispython 5.4.1 it was
        IRIS's text, ``'.94999999999999995559'`` for 0.95).
        """
        sql = f'SELECT {self._ivg_schema}.CY_TYPED(val) FROM {self._t("rdf_props")} WHERE "key" = ?'
        cursor = self.conn.cursor()
        try:
            cursor.execute(sql, [key])
            return [decode_typed_text(row[0]) for row in cursor.fetchall() if row and row[0] not in (None, "")]
        except Exception as e:
            logger.error("get_property_values(%s) failed: %s", key, e)
            return []
        finally:
            cursor.close()

    def property_value_exists(self, key: str, like: str) -> bool:
        """True if any row under `key` has a value matching SQL LIKE `like`.

        TOP 1 probe — never FETCH FIRST (SIGSEGV on IRIS AI builds, DP-451209).
        """
        sql = f'SELECT TOP 1 1 FROM {self._t("rdf_props")} WHERE "key" = ? AND val LIKE ?'
        cursor = self.conn.cursor()
        try:
            cursor.execute(sql, [key, like])
            return cursor.fetchone() is not None
        except Exception as e:
            logger.error("property_value_exists(%s LIKE %s) failed: %s", key, like, e)
            return False
        finally:
            cursor.close()

    def get_property_pairs_like(
        self,
        key: str,
        like: str,
        limit: Optional[int] = None,
    ) -> List[tuple]:
        """(subject, value) pairs under `key` whose value matches SQL LIKE `like`.

        Keyword-search primitive: bounded LIKE probe beats hydrating all nodes.
        `limit` non-positive means no cap. The LIKE reads the stored text; a value is returned as it is stored
        (4.6.0 R248).
        """
        top = f"TOP {int(limit)} " if limit and int(limit) > 0 else ""
        sql = (
            f'SELECT {top}s, {self._ivg_schema}.CY_TYPED(val) FROM {self._t("rdf_props")} '
            f'WHERE "key" = ? AND val LIKE ?'
        )
        cursor = self.conn.cursor()
        try:
            cursor.execute(sql, [key, like])
            return [(row[0], decode_typed_text(row[1])) for row in cursor.fetchall() if row and row[0]]
        except Exception as e:
            logger.error("get_property_pairs_like(%s LIKE %s) failed: %s", key, like, e)
            return []
        finally:
            cursor.close()

    def get_json_field_values(self, key: str, field: str) -> List[str]:
        """Values of one JSON `field` inside every `key` blob, extracted server-side.

        Uses $PIECE extraction to avoid moving multi-MB JSON blobs to Python.
        """
        sql = (
            f"SELECT $PIECE($PIECE(val, '\"{field}\": \"', 2), '\"', 1) "
            f'FROM {self._t("rdf_props")} WHERE "key" = ? AND val LIKE ?'
        )
        cursor = self.conn.cursor()
        try:
            cursor.execute(sql, [key, f"%{field}%"])
            return [row[0] for row in cursor.fetchall() if row and row[0]]
        except Exception as e:
            logger.error("get_json_field_values(%s.%s) failed: %s", key, field, e)
            return []
        finally:
            cursor.close()

    def get_node_ids_like(self, pattern: str) -> List[str]:
        """Node ids matching a SQL LIKE `pattern`.

        Used for test-fixture cleanup (ids like `test-run-1234:%`) and for
        producing the id list that bulk_delete_nodes requires.
        """
        sql = f"SELECT node_id FROM {self._t('nodes')} WHERE node_id LIKE ?"
        cursor = self.conn.cursor()
        try:
            cursor.execute(sql, [pattern])
            return [row[0] for row in cursor.fetchall() if row and row[0]]
        except Exception as e:
            logger.error("get_node_ids_like(%s) failed: %s", pattern, e)
            return []
        finally:
            cursor.close()

    def count_subjects_with_property(
        self,
        key: str,
        val: Optional[str] = None,
    ) -> int:
        """COUNT(*) of subjects carrying `key` (optionally `= val`).

        Does not hydrate nodes — pure count for cardinality estimates.
        """
        sql = f'SELECT COUNT(*) FROM {self._t("rdf_props")} WHERE "key" = ?'
        params: List[Any] = [key]
        if val is not None:
            sql += " AND val = ?"
            params.append(val)
        cursor = self.conn.cursor()
        try:
            cursor.execute(sql, params)
            row = cursor.fetchone()
            return int(row[0]) if row and row[0] else 0
        except Exception as e:
            logger.error("count_subjects_with_property(%s=%s) failed: %s", key, val, e)
            return 0
        finally:
            cursor.close()

    def create_node(
        self,
        node_id: str,
        labels: List[str] = None,
        properties: Dict[str, Any] = None,
        graph: Optional[str] = None,
    ) -> bool:
        """Create a node with its labels and properties in one graph, in one transaction.

        ``graph`` None is the default graph. The ``id`` property is set to ``node_id`` unless
        given, and None-valued properties are skipped. Returns False, writing nothing, when
        the node already exists in that graph.

        Args:
            node_id: Unique string identifier for the node.
            labels: Optional list of label strings (e.g., ["Person", "Employee"]).
            properties: Optional dict of property key-value pairs.
            graph: Optional named graph identifier.

        Returns:
            True if the node was created; False if it already existed in that graph
            (nothing is added to it).

        Raises:
            GraphWriteError: IRIS refused the write (for example SQLCODE -99 for a user
                without INSERT on the IVG Schema); the transaction is rolled back, so
                nothing is written. Before 4.6.0 this answered False, like a duplicate.

        Example:
            >>> engine.create_node("gene:TP53", labels=["Gene"], properties={"name": "TP53"})
            True
        """
        _ledger_check(self, "create_node")
        NodeIdInput(node_id=node_id)
        cursor = self.conn.cursor()
        try:
            cursor.execute("START TRANSACTION")

            graph_id = graph if graph is not None else ""
            # Use a schema-compatible INSERT: include graph_id only when the column
            # exists (post-214 schema).  Pre-214 clusters that haven't run the migration
            # yet still work; they just can't use named graphs until migrated.
            if getattr(self, "_nodes_has_graph_id", True):
                cursor.execute(
                    f"INSERT INTO {self._t('nodes')} (node_id, graph_id) VALUES (?, ?)",
                    [node_id, graph_id],
                )
            else:
                cursor.execute(
                    f"INSERT INTO {self._t('nodes')} (node_id) VALUES (?)",
                    [node_id],
                )

            # Spec 227 (FR-034): the children carry the graph too. Without this the
            # node lands in `graph` and its labels and properties land in the default
            # graph, so a scoped read of `graph` finds a node with no labels while the
            # default graph acquires labels for a node it does not hold.
            _children_scoped = getattr(self, "_children_have_graph_id", True)

            if labels:
                if _children_scoped:
                    cursor.executemany(
                        f"INSERT INTO {self._t('rdf_labels')} (graph_id, s, label) VALUES (?, ?, ?)",
                        [[graph_id, node_id, label] for label in labels],
                    )
                else:
                    cursor.executemany(
                        f"INSERT INTO {self._t('rdf_labels')} (s, label) VALUES (?, ?)",
                        [[node_id, label] for label in labels],
                    )

            props = dict(properties) if properties else {}
            if "id" not in props:
                props["id"] = node_id
            # spec 214 FR-019: __graph pseudo-property no longer written; graph_id is a real column

            prop_data = []
            for k, v in props.items():
                if v is None:
                    continue
                val_str = prop_text(v)
                if _children_scoped:
                    prop_data.append([graph_id, node_id, k, val_str, graph_id, node_id, k])
                else:
                    prop_data.append([node_id, k, val_str, node_id, k])

            if _children_scoped:
                # The guard is scoped as well as the insert: keyed on (s, "key") alone it
                # reports "already there" for another graph's property of the same name
                # and skips the write, and create_node still returns True.
                prop_sql = (
                    f'INSERT INTO {self._t("rdf_props")} (graph_id, s, "key", val) '
                    f"SELECT ?, ?, ?, ? WHERE NOT EXISTS ("
                    f'SELECT 1 FROM {self._t("rdf_props")} '
                    f'WHERE graph_id = ? AND s = ? AND "key" = ?)'
                )
            else:
                prop_sql = f'INSERT INTO {self._t("rdf_props")} (s, "key", val) SELECT ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM {self._t("rdf_props")} WHERE s = ? AND "key" = ?)'
            cursor.executemany(prop_sql, prop_data)

            cursor.execute("COMMIT")
            return True
        except Exception as e:
            _rollback(cursor)
            err_lower = str(e).lower()
            if "unique" in err_lower or "-119" in str(e) or "validation failed" in err_lower:
                logger.debug(f"create_node skipped: {node_id}: {str(e)[:80]}")
                return False
            # R203: a refusal (SQLCODE -99, a lock timeout ...) is not "already there"
            raise GraphWriteError("create_node", e) from e

    def _ensure_edge_endpoints(self, cursor, pairs) -> None:
        """Make sure `nodes` holds a row for every `(graph, node_id)` an edge names.

        Spec 227 re-keyed `nodes` on `(graph_id, node_id)`, so `rdf_edges` carries
        composite foreign keys — `fk_edges_source (graph_id, s)` and `fk_edges_dest
        (graph_id, o_id)`. Under the old single-column key a node created once, in
        any graph, satisfied an edge written in every graph; under the composite key
        it satisfies only edges in its own graph. Every pre-227 caller loads nodes
        into the default graph and then writes edges into a named one, and that pair
        of calls is now `SQLCODE -121`.

        The writers swallowed that: `create_edge` returned the same False it returns for
        a duplicate, `BulkIngestEdgesSQL` skipped the row and did not count it. So the
        endpoint rows are ensured here rather than left to the caller — `nodes` is the
        registry of what the graph contains, not an input the caller has to prepare
        twice.

        Duplicates within one call are collapsed, and a row a concurrent writer got to
        first is not an error.

        Args:
            cursor: the caller's cursor, so this shares the caller's transaction.
            pairs: `(graph_id, node_id)` tuples; `None` graph means the default graph.
        """
        scoped = getattr(self, "_nodes_has_graph_id", True)
        sql = self._bulk_sql("nodes_with_graph" if scoped else "nodes")
        seen = set()
        for graph_id, node_id in pairs:
            if not node_id:
                continue
            graph_id = graph_id or ""
            if (graph_id, node_id) in seen:
                continue
            seen.add((graph_id, node_id))
            try:
                if scoped:
                    cursor.execute(sql, [node_id, graph_id, node_id, graph_id])
                else:
                    cursor.execute(sql, [node_id, node_id])
            except Exception as e:
                # Lost the race to another writer: the row is there, which is all
                # the foreign key asked for.
                if "unique" not in str(e).lower() and "-119" not in str(e):
                    raise

    def create_edge(
        self,
        source_id: str,
        predicate: str,
        target_id: str,
        weight: Optional[float] = None,
        qualifiers: Dict[str, Any] = None,
        graph: Optional[str] = None,
    ) -> bool:
        """Create an edge in the knowledge graph.

        Args:
            source_id: Source node identifier.
            predicate: Relationship type (e.g., "KNOWS", "CALLS").
            target_id: Target node identifier.
            weight: The edge's ``weight`` property, which weighted shortest-path / cost
                traversals read. Given, it is stored as a Cypher ``CREATE`` stores
                ``{weight: 1.0}`` (the number's text), so ``r.weight`` reads it back as
                the same number: 1.0 as 1.0, 1 as 1. Omitted, the edge has no ``weight``
                property (``r.weight`` is null) and traversals cost it 1.0. Before 4.6.0
                a weight of 1.0 was not stored, and 2.0 read back as the integer 2.
            qualifiers: Optional relationship properties; ``weight`` overrides a
                ``"weight"`` key here.
            graph: Optional named graph identifier.

        Both endpoints are registered in `nodes` for `graph` if they are not there
        already (see `_ensure_edge_endpoints`): an edge names its endpoints, and since
        227 re-keyed `nodes` on `(graph_id, node_id)` a node created in the default
        graph does not satisfy an edge written in a named one.

        The endpoint rows and the edge row are one transaction: a failed edge leaves
        no node row behind.

        Returns:
            True if a NEW edge was created (or a temporal edge's marked row was adopted).
            False if the edge already existed (a duplicate is never an error).

        Raises:
            GraphWriteError: when IRIS refuses the write (for example SQLCODE -415 for a
                name pair too long for the edge index's ~511-byte global reference, or
                -104 for a predicate wider than its column). Nothing was written. Before
                4.5.0 this logged the error and returned False, the duplicate's value,
                after the endpoint rows had already committed.
        """
        _ledger_check(self, "create_edge")
        EdgeInput(source_id=source_id, predicate=predicate, target_id=target_id)
        graph_key = graph_index_key(graph)
        quals = dict(qualifiers or {})
        if weight is not None:
            # The weight lives in the row (FR-015): the adjacency index, a rebuild and Cypher's r.weight read it
            # there. R215: as Cypher's CREATE stores {weight: 1.0}, the text of the number, which r.weight reads
            # back with its type; 1.0 used to be left out (r.weight null) and a JSON number 2.0 read back as 2.
            quals["weight"] = _weight_text(weight)
        qual_json = json.dumps(quals) if quals else None
        cursor = self.conn.cursor()
        try:
            cursor.execute("START TRANSACTION")
            self._ensure_edge_endpoints(cursor, ((graph, source_id), (graph, target_id)))
            # Guarded, not refused: a duplicate the unique key u_spo_graph refuses (-119) is rolled back through the
            # journal, ~30 ms against a probe's microseconds (KNOWN_ISSUES). The row count says which happened.
            gid = graph or ""
            cursor.execute(
                f"INSERT INTO {self._t('rdf_edges')} (s, p, o_id, qualifiers, graph_id) SELECT ?, ?, ?, ?, ? "
                f"WHERE NOT EXISTS (SELECT 1 FROM {self._t('rdf_edges')} WHERE s = ? AND p = ? AND o_id = ? "
                f"AND COALESCE(graph_id, '') = ?{self._ekey_guard(cursor)})",
                [source_id, predicate, target_id, qual_json, gid, source_id, predicate, target_id, gid],
            )
            inserted = cursor.rowcount
            cursor.execute("COMMIT")
        except Exception as e:
            _rollback(cursor)
            if _is_duplicate(e):
                # a concurrent writer won the race between the probe and the insert
                inserted = 0
            else:
                raise GraphWriteError("create_edge", e) from e
        if not inserted:
            if self._adopt_temporal_row(source_id, predicate, target_id, graph, qual_json):
                return True
            logger.debug(f"create_edge duplicate: {source_id}-[{predicate}]->{target_id}")
            return False
        if self._adjacency_indexed:
            return True  # the adjacency index wrote ^KG with the row (spec 252)
        try:
            self._iris_obj().classMethodVoid(
                "Graph.KG.EdgeScan",
                "WriteAdjacency",
                source_id,
                predicate,
                target_id,
                _adjacency_weight(quals),
                graph_key,
            )
        except Exception as e:
            logger.warning(f"create_edge ^KG write failed (BuildKG can recover): {e}")
        return True

    def _ekey_guard(self, cursor) -> str:
        """The guarded edge insert's ekey term: the unique key is (s, p, o_id, graph_id, ekey) and a plain insert lands
        at ekey 0, so the guard compares ekey 0 too. Empty before spec 234's migration added the column. Checked once
        per engine; initialize_schema resets it."""
        cached = self.__dict__.get("_ekey_guard_cached")
        if cached is None:
            try:
                cursor.execute(
                    f"SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = '{self._ivg_schema}' "
                    "AND TABLE_NAME = 'rdf_edges' AND COLUMN_NAME = 'ekey'"
                )
                cached = " AND ekey = 0" if int(cursor.fetchone()[0]) else ""
            except Exception as ex:
                logger.debug("ekey probe failed (%s); guarding on the triple alone", ex)
                cached = ""
            self._ekey_guard_cached = cached
        return cached

    def _adopt_temporal_row(self, source_id, predicate, target_id, graph, qual_json) -> bool:
        """ADR-0009: an explicit create of a triple whose only row is a temporal edge's marked row adopts it.

        The row's qualifiers become the explicit ones and the marker goes, so no temporal delete can remove the
        edge afterwards. False when the triple's row is not marked (a true duplicate); a failed read or update
        raises GraphWriteError (it used to answer False, which create_edge reported as a duplicate).
        """
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                f"SELECT %ID, qualifiers FROM {self._t('rdf_edges')} WHERE s = ? AND p = ? AND o_id = ? "
                "AND COALESCE(graph_id, '') = COALESCE(?, '')",
                [source_id, predicate, target_id, graph or ""],
            )
            rows = [tuple(r) for r in cursor.fetchall()]
            if len(rows) != 1:
                return False
            row_id, quals = rows[0]
            try:
                parsed = json.loads(quals) if quals else {}
            except (TypeError, ValueError):
                return False
            if not (isinstance(parsed, dict) and parsed.get("temporal") is True):
                return False
            cursor.execute(f"UPDATE {self._t('rdf_edges')} SET qualifiers = ? WHERE %ID = ?", [qual_json, row_id])
            self.conn.commit()
            return True
        except Exception as e:
            self.conn.rollback()
            raise GraphWriteError("create_edge", e) from e

    def set_edge_weight(self, source: str, predicate: str, target: str, weight: float) -> bool:
        """Set the weight of an existing edge in the default graph; False when it has no row there.

        Used by weighted shortest-path / cost traversals.

        Args:
            source: Source node identifier.
            predicate: Relationship type.
            target: Target node identifier.
            weight: New edge weight.

        Every row of the triple (a multigraph triple has one per edge key) is updated in
        one transaction: all of them take the weight, or, on a failure, none does.

        Returns:
            True on success, False when the default graph has no row of the edge.

        Raises:
            GraphWriteError: IRIS refused an update; every row keeps its old weight (before
                4.6.0 this answered False, like a missing edge).
        """
        _ledger_check(self, "set_edge_weight")
        EdgeInput(source_id=source, predicate=predicate, target_id=target)
        # The weight lives in the row's qualifiers (FR-015). This used to write ^KG alone, so a rebuild lost it,
        # and on an edge with no row it created adjacency nothing stood behind.
        cursor = self.conn.cursor()
        try:
            # one transaction: outside it each UPDATE committed on its own, and a failure on a multigraph
            # triple's second row left the first at the new weight
            cursor.execute("START TRANSACTION")
            cursor.execute(
                f"SELECT %ID, qualifiers FROM {self._t('rdf_edges')} "
                "WHERE s = ? AND p = ? AND o_id = ? AND COALESCE(graph_id, '') = ''",
                [source, predicate, target],
            )
            rows = [tuple(r) for r in cursor.fetchall()]
            if not rows:
                _rollback(cursor)
                return False
            new_weight = float(weight)
            for row_id, qual_json in rows:
                try:
                    quals = json.loads(qual_json) if qual_json else {}
                except (TypeError, ValueError):
                    quals = {}
                if not isinstance(quals, dict):
                    quals = {}
                quals["weight"] = new_weight
                cursor.execute(
                    f"UPDATE {self._t('rdf_edges')} SET qualifiers = ? WHERE %ID = ?", [json.dumps(quals), row_id]
                )
            cursor.execute("COMMIT")
        except Exception as e:
            _rollback(cursor)
            # R203: False is the answer for an edge with no row; a refused update raises
            raise GraphWriteError("set_edge_weight", e) from e
        if not self._adjacency_indexed:
            try:
                self._iris_obj().classMethodVoid(
                    "Graph.KG.EdgeScan", "WriteAdjacency", source, predicate, target, str(new_weight)
                )
            except Exception as e:
                logger.warning(f"set_edge_weight ^KG write failed (BuildKG can recover): {e}")
        return True

    def delete_edge(
        self,
        source_id: str,
        predicate: str,
        target_id: str,
        graph: str = None,
        all_graphs: bool = False,
    ) -> bool:
        """Delete a structural edge from one graph; True also when no row matched.

        A delete IRIS refuses raises ``GraphWriteError`` and deletes nothing (it answered False
        before 4.6.0).
        Spec 214 FR-006: graph= scopes deletion to one graph (default: '' for default graph).
        Spec 214 FR-007: all_graphs=True restores pre-214 cross-graph deletion.
        Breaking change: default (no graph, all_graphs=False) deletes only the
        default-graph row (graph_id=''), not all rows matching (s, p, o_id).
        Callers that relied on cross-graph deletion must pass all_graphs=True.

        Raises:
            ValueError: if ``graph`` cannot be represented as a subscript
                (ADR-0003), before the row is touched.
        """
        _ledger_check(self, "delete_edge")
        # Derive before branching. The index key used to be built inline at the
        # ^KG call from a graph_id assigned only in the else branch below, so
        # all_graphs=True raised UnboundLocalError there. The call sits in a try
        # that logs and swallows, so every row left SQL and every ^KG entry
        # stayed: the exact drift verify_graph exists to catch, in the one path
        # that opts into touching every graph.
        graph_key = graph_index_key(graph)
        cursor = self.conn.cursor()
        try:
            if all_graphs:
                cursor.execute(
                    f"DELETE FROM {self._t('rdf_edges')} WHERE s = ? AND p = ? AND o_id = ?",
                    [source_id, predicate, target_id],
                )
            else:
                # COALESCE both sides: graph_id is nullable and the default graph
                # has two spellings. create_edge writes '', but every INSERT that
                # omits the column — the bulk loaders, the map_sql_table bridge —
                # writes NULL. A predicate matching only one of them deletes half
                # the default graph and reports success.
                cursor.execute(
                    f"DELETE FROM {self._t('rdf_edges')} WHERE s = ? AND p = ? AND o_id = ? "
                    "AND COALESCE(graph_id, '') = COALESCE(?, '')",
                    [source_id, predicate, target_id, "" if graph_key == 0 else graph_key],
                )
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            # R203: answering False made a refused delete look like one that found nothing
            raise GraphWriteError("delete_edge", e) from e
        if self._adjacency_indexed:
            return True  # the adjacency index removed the entries with the rows (spec 252)
        try:
            if all_graphs:
                # Every graph is not a key value, so it cannot be expressed by
                # passing one. EdgeScan walks its own tree and delegates per edge.
                self._iris_obj().classMethodVoid(
                    "Graph.KG.EdgeScan",
                    "DeleteAdjacencyAllGraphs",
                    source_id,
                    predicate,
                    target_id,
                )
            else:
                self._iris_obj().classMethodVoid(
                    "Graph.KG.EdgeScan",
                    "DeleteAdjacency",
                    source_id,
                    predicate,
                    target_id,
                    graph_key,
                )
        except Exception as e:
            logger.warning(f"delete_edge ^KG kill failed (BuildKG can recover): {e}")
        return True

    def list_graphs(self) -> List[str]:
        """The named graphs that have edges. The default graph is not one of them.

        Both spellings of the default graph are excluded. The predicate used to be
        ``graph_id IS NOT NULL``, which excluded only the NULL-spelled rows and
        returned '' for the rows create_edge wrote — so whether the default graph
        appeared in this list depended on which writer had produced its edges.
        """
        cursor = self.conn.cursor()
        cursor.execute(
            f"SELECT DISTINCT graph_id FROM {self._t('rdf_edges')} "
            "WHERE COALESCE(graph_id, '') <> '' ORDER BY graph_id"
        )
        return [row[0] for row in cursor.fetchall()]

    def erase_graph(self, graph: Optional[str] = None) -> int:
        """Remove one graph's content from every store that holds it.

        The single deletion path. ``drop_graph`` deleted rows from five tables and
        said so in its own comment — ``# BYPASS: SQL rows deleted without touching
        ^KG/^NKG; flag stale`` — leaving the structural adjacency answering
        traversals for a graph that no longer had a row, the whole temporal index
        untouched, and ``_nkg_dirty`` set so the inconsistency waited on a rebuild
        that might never run. ``restore_snapshot`` cleared a different list, and
        ``bulk_delete_adjacency`` produced the mirror-image drift.

        The work happens in ``Graph.KG.Eraser``, which owns its own transaction.
        That is not an implementation detail of this method: the Native API rides
        the same connection as the DBAPI cursor, so ``Kill`` and SQL ``DELETE``
        share one transaction context, but ``$TLevel`` is invisible from here.
        Splitting the transaction across the seam is what made a partial erasure
        possible (ADR-0004).

        Args:
            graph: The graph to erase. ``None`` and ``""`` both mean the default
                graph, which is erased in both of its spellings — ``graph_id`` is
                nullable, so ``create_edge`` writes ``''`` while an INSERT omitting
                the column leaves NULL.

        Returns:
            The number of ``Graph_KG.nodes`` and ``Graph_KG.rdf_edges`` rows
            removed. ``0`` means nothing matched, which is not an error.

        Raises:
            ValueError: if ``graph`` cannot be represented as a subscript
                (ADR-0003), before anything is deleted.
            Exception: if any step fails, after the whole erase has rolled back.
                Erasure either completes or leaves the graph untouched; a partial
                erasure is a defect, not an outcome, so this raises rather than
                returning a count that looks like success.

        A graph ID is collision avoidance, not an authorisation boundary: it is
        supplied by the caller, so it cannot decide what that caller may read
        (FR-032).
        """
        _ledger_check(self, "erase_graph")
        canonical = validate_graph_name(graph)
        removed = int(
            self._iris_obj().classMethodValue("Graph.KG.Eraser", "EraseGraph", canonical)
        )
        # No _nkg_dirty. The Eraser drops ^NKG rather than flagging it, so there is
        # no deferred inconsistency left for a caller to remember to resolve.
        self._nkg_dirty = False
        # An erase takes this graph's routed tables and registry rows with it. A cached
        # route would outlive them and send the next write at a table that is gone,
        # which surfaces as a missing table rather than as a stale cache.
        self.invalidate_route_cache(canonical)
        # The Eraser kills ^IVG.GraphMode(graph) too; a cached "multi" would outlive it.
        getattr(self, "_graph_mode_forget", lambda *_: None)(canonical)
        return removed

    def delete_nodes_by_prefix(self, prefix: str, graph: Optional[str] = None) -> int:
        """Delete every node in one graph whose id starts with ``prefix``.

        Runs ``Graph.KG.Eraser.EraseNodes`` in one transaction. It takes the
        matching nodes' labels, props, documents and embeddings, their incident
        edges in that graph (both directions) with the edges' reifications and
        embeddings, and their ``^KG`` adjacency, props and labels. It bumps
        ``^KG("__version")`` so a cached Arno snapshot is noticed as stale, and
        drops ``^NKG`` when edges went and any erased node is interned in it. Nodes
        that were never interned have no edge in ``^NKG``, so erasing only those
        leaves it standing.

        Args:
            prefix: A non-empty id prefix, matched exactly (case-sensitive).
            graph: The graph to delete from; ``None`` and ``""`` are the default
                graph. The same id in another graph is untouched.

        Returns:
            The number of ``Graph_KG.nodes`` rows removed.

        Raises:
            ValueError: for an empty prefix (that is ``erase_graph``), or a graph
                name that cannot be a subscript.
        """
        _ledger_check(self, "delete_nodes_by_prefix")
        if not prefix:
            raise ValueError(
                "delete_nodes_by_prefix refuses an empty prefix, which would erase "
                "the whole graph; use erase_graph(graph) for that"
            )
        canonical = validate_graph_name(graph)
        removed = int(self._store.delete_nodes_by_prefix(prefix, graph=canonical))
        # EraseNodes drops ^NKG itself when it touched edges that ^NKG can hold; nothing is deferred.
        self._nkg_dirty = False
        return removed

    def erase_all(self) -> int:
        """Remove every graph's content, including the stores no per-graph erase reaches.

        ``rdf_labels`` and ``rdf_props`` are keyed by node id alone, ``^NKG``
        interns identifiers with no graph dimension, ``^KG("labelset")`` is
        append-only by design, and ``^ArnoKG`` caches a whole-database snapshot.
        None can be split by graph — which is why they accumulate. "All" has no
        attribution problem, so this is the only operation that can empty them
        honestly.

        ``^IVG.Ledger("rec")`` survives: erasure removes content, not history
        (ADR-0004).

        Returns:
            The number of ``Graph_KG.nodes`` and ``Graph_KG.rdf_edges`` rows removed.
        """
        _ledger_check(self, "erase_all")
        removed = int(self._iris_obj().classMethodValue("Graph.KG.Eraser", "EraseAll"))
        self._nkg_dirty = False
        self.invalidate_route_cache()
        getattr(self, "_graph_mode_forget", lambda *_: None)()
        return removed

    def drop_graph(self, graph_id: str) -> int:
        """Deprecated name for :meth:`erase_graph`. Removed in the next major release.

        The name survives the change; the behaviour does not. Keeping the name is
        a compatibility decision, and keeping the old body would have meant two
        deletion paths again — which is the thing the Eraser exists to end.
        """
        return self.erase_graph(graph_id)

    def bulk_create_nodes(
        self,
        nodes: List[Dict[str, Any]],
        disable_indexes: bool = True,
    ) -> List[str]:
        """
        Insert many nodes, each a dict with ``id`` and optionally ``labels``, ``properties`` and
        ``graph``; returns the IDs written. Another shape raises ``ValueError`` before anything is
        written.

        Each node goes to its ``graph`` key (default graph if absent) with an ``id`` property
        (as ``create_node`` writes); a label or property the node already has is kept, not
        overwritten (``store_node`` replaces properties). A node that is not a dict, has no
        ``id``, or has a key other than ``id``, ``labels``, ``properties`` and ``graph`` raises
        ``ValueError`` naming it, before anything is written (4.6.0; it was skipped). With the classes deployed
        (``initialize_schema``) a batch with no ``graph`` keys goes to the server loader,
        otherwise to batch SQL; both write the same rows. Batches of 500 or more on the SQL
        path disable and rebuild the indexes (``disable_indexes``). Raises on a failed SQL
        batch, after rolling back.

        Args:
            nodes: List of node dicts, each with:
                - id: Node ID (required)
                - labels: List of labels (optional)
                - properties: Dict of properties (optional)
                - graph: Named graph (optional; absent is the default graph)
            disable_indexes: Drop indexes before load, rebuild after (default True)

        Returns:
            The IDs of the nodes with an ``id``, including any that already existed
        """
        _ledger_check(self, "bulk_create_nodes")
        if not nodes:
            return []
        _check_create_nodes(nodes)

        # Index drop/rebuild (disable_indexes) calls DDL via cursor. On IRIS, DDL after
        # a createIRIS() native API call on the same connection corrupts parameter binding
        # state. Skip for small batches where the overhead is not justified anyway.
        _DISABLE_IDX_THRESHOLD = 500
        if disable_indexes and len(nodes) < _DISABLE_IDX_THRESHOLD:
            disable_indexes = False

        # Only capabilities already set (initialize_schema(), or a caller) send nodes to
        # the server loader; this does not detect them. BulkIngestNodesSQL writes every
        # property through the iFind index: ~40 nodes/s on SNB data, against ~1.6K/s on
        # the path below, which disables the indexes around the batch. A fresh engine
        # keeps loading nodes the way it always has.
        # The server loader writes the default graph only, so a batch naming another graph goes
        # the SQL way, which writes each node's `graph` (R089: it landed in the default graph).
        if self._known_capabilities.objectscript_deployed and not any(
            n.get("graph") for n in nodes
        ):
            try:
                import json as _json

                from iris_vector_graph.schema import _call_classmethod_large

                iris_obj = self._iris_obj()
                # Serialised here, as the SQL path below does: the class method used
                # to JSON-quote strings itself, so `name` read back as '"Olaparib"'.
                # The `id` property is stored, as the SQL path and create_node store it (R089).
                normalized = [
                    {
                        "id": n["id"],
                        "labels": n.get("labels", []),
                        "props": {
                            k: prop_text(v)
                            for k, v in {"id": n["id"], **(n.get("properties") or {})}.items()
                            if v is not None
                        },
                    }
                    for n in nodes
                    if n.get("id")
                ]
                created = []
                for i in range(0, len(normalized), _BULK_CHUNK_SIZE):
                    chunk = normalized[i : i + _BULK_CHUNK_SIZE]
                    count = int(
                        _call_classmethod_large(
                            iris_obj,
                            "Graph.KG.EdgeScan",
                            "BulkIngestNodesSQL",
                            _json.dumps(chunk),
                        )
                    )
                    # The loader writes every node of the chunk or raises (and rolls back).
                    if count != len(chunk):
                        raise RuntimeError(
                            f"BulkIngestNodesSQL wrote {count} of {len(chunk)} nodes"
                        )
                    created.extend(c["id"] for c in chunk)
                return created
            except Exception as e:
                logger.warning("BulkIngestNodesSQL failed (%s), falling back to SQL path", e)

        cursor = self.conn.cursor()
        created_ids = []

        try:
            # 1. Pre-load setup
            if disable_indexes:
                GraphSchema.disable_indexes(cursor, schema=self._ivg_schema)

            # 2. SQL templates (using %NOINDEX for speed)
            # Use graph-aware template only when the schema has the graph_id column.
            _graph_col = getattr(self, "_nodes_has_graph_id", True)
            node_sql = self._bulk_sql(
                "nodes_with_graph" if _graph_col else "nodes"
            )
            # Spec 227 (FR-034): the children carry the graph too, probed
            # separately because a 214-era schema has graph_id on `nodes` and not
            # on rdf_labels / rdf_props, where naming it is SQLCODE -29.
            _children_scoped = getattr(self, "_children_have_graph_id", True)
            label_sql = self._bulk_sql(
                "rdf_labels_with_graph" if _children_scoped else "rdf_labels"
            )
            prop_sql = self._bulk_sql(
                "rdf_props_with_graph" if _children_scoped else "rdf_props"
            )

            # 3. Collect and prepare data
            all_labels = []
            all_props = []
            valid_nodes = []

            for node in nodes:
                node_id = node.get("id")
                if not node_id:
                    continue

                created_ids.append(node_id)
                # The requested graph goes in the column, not in a `__graph`
                # property (spec 227 FR-034; spec 214 FR-019 replaced the
                # pseudo-property and schema.py:614 deletes every row of it, so a
                # graph claimed that way disappears on the next upgrade).
                node_graph = node.get("graph") or ""
                # params depend on template: nodes_with_graph needs
                # [node_id, graph_id, node_id, graph_id] — its guard names the graph
                # too; plain nodes needs [node_id, node_id]
                if _graph_col:
                    valid_nodes.append([node_id, node_graph, node_id, node_graph])
                else:
                    valid_nodes.append([node_id, node_id])

                for label in node.get("labels", []):
                    if _children_scoped:
                        # params: [graph_id, s, label, graph_id, s, label]
                        all_labels.append((node_graph, node_id, label, node_graph, node_id, label))
                    else:
                        # params: [s, label, s, label]
                        all_labels.append((node_id, label, node_id, label))

                props = node.get("properties", {})
                if "id" not in props:
                    props["id"] = node_id

                for k, v in props.items():
                    if v is None:
                        continue
                    val_str = prop_text(v)
                    if _children_scoped:
                        # params: [graph_id, s, key, val, graph_id, s, key]
                        all_props.append(
                            (node_graph, node_id, k, val_str, node_graph, node_id, k)
                        )
                    else:
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
                _rebuild_after_load(cursor, self._ivg_schema, "bulk_create_nodes")
                self.conn.commit()

    def bulk_create_edges(
        self,
        edges: List[Dict[str, Any]],
        disable_indexes: bool = True,
        graph: Optional[str] = None,
        auto_sync: bool = True,
        auto_rebuild_kg: bool = None,
    ) -> int:
        """Insert many edges, each a dict with ``source_id``, ``predicate``, ``target_id`` and
        optionally ``graph``. Another shape raises ``ValueError`` before anything is written.

        Each edge goes to its own ``graph`` key, else to ``graph`` (None is the default graph);
        missing endpoint nodes are created in that graph first and an edge already present is
        not duplicated. Those four keys are the only ones: an edge that is not a dict, lacks
        one of the three or has another key (``weight``, ``qualifiers``: this writes neither)
        raises ``ValueError`` naming it, before anything is written (4.6.0; it was skipped,
        or the key dropped). One transaction, rolled back and re-raised on error. Batches of
        500 or more disable and rebuild the indexes (``disable_indexes``); ``auto_sync`` runs
        ``sync`` afterwards, except inside ``bulk_load_session``. Returns the number of edges
        in the batch, including those that already existed.
        """
        _ledger_check(self, "bulk_create_edges")
        if auto_rebuild_kg is not None:
            warnings.warn(
                "auto_rebuild_kg= is deprecated. Use auto_sync= instead.",
                DeprecationWarning,
                stacklevel=2,
            )
            auto_sync = auto_rebuild_kg

        if (
            len(edges) > 250_000
            and disable_indexes
            and not getattr(self, "_large_load_hinted", False)
        ):
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
        _check_create_edges(edges)

        # Same guard as bulk_create_nodes: DDL after createIRIS() on the same connection
        # permanently corrupts IRIS driver parameter binding state. Skip for small batches.
        _DISABLE_IDX_THRESHOLD = 500
        if disable_indexes and len(edges) < _DISABLE_IDX_THRESHOLD:
            disable_indexes = False

        cursor = self.conn.cursor()
        try:
            if disable_indexes:
                GraphSchema.disable_indexes(cursor, schema=self._ivg_schema)

            edge_sql = self._bulk_sql("rdf_edges")
            edge_params = []
            has_graph = graph is not None or any(e.get("graph") for e in edges)
            # Before any edge row: the composite foreign keys need the endpoints in
            # the edge's own graph (see _ensure_edge_endpoints). A batch names each
            # endpoint many times, so the helper's de-duplication does the work here.
            self._ensure_edge_endpoints(
                cursor,
                (
                    (e.get("graph", graph), e[key])
                    for e in edges
                    if all(k in e for k in ("source_id", "predicate", "target_id"))
                    for key in ("source_id", "target_id")
                ),
            )
            if has_graph:
                graph_sql = self._bulk_sql("rdf_edges_with_graph")
                plain_sql = self._bulk_sql("rdf_edges")
                for e in edges:
                    if all(k in e for k in ("source_id", "predicate", "target_id")):
                        s, p, o = e["source_id"], e["predicate"], e["target_id"]
                        g = e.get("graph", graph)
                        if g is not None:
                            # One graph parameter, not two: the guard coalesces both
                            # sides rather than spelling the NULL case separately
                            # (spec 230, FR-005).
                            row = [s, p, o, g, s, p, o, g]
                            edge_params.append(row)
                            cursor.execute(graph_sql, row)
                        else:
                            # Counted as well as written. This branch used to execute
                            # the row and skip the append, so a batch mixing named and
                            # default-graph edges reported fewer edges than it wrote.
                            row = [s, p, o, s, p, o]
                            edge_params.append(row)
                            cursor.execute(plain_sql, row)
            else:
                for e in edges:
                    if all(k in e for k in ("source_id", "predicate", "target_id")):
                        s, p, o = e["source_id"], e["predicate"], e["target_id"]
                        edge_params.append([s, p, o, s, p, o])
                cursor.executemany(edge_sql, edge_params)

            # ADR-0009 / spec 254 FR-002: an explicit write of a triple whose row a temporal edge
            # asserted adopts that row, as create_edge does, so purging the triple's timestamps
            # cannot remove it. The inserts above skip an existing row and leave its marker.
            adopt = [
                [e["source_id"], e["predicate"], e["target_id"], e.get("graph", graph) or ""]
                for e in edges
                if all(k in e for k in ("source_id", "predicate", "target_id"))
            ]
            if adopt:
                cursor.executemany(rewrite_schema_text(_ADOPT_TEMPORAL_ROW_SQL, self._ivg_schema), adopt)

            self.conn.commit()
            return len(edge_params)
        except Exception as e:
            self.conn.rollback()
            logger.error(f"Bulk edge load failed: {e}")
            raise
        finally:
            if disable_indexes:
                _rebuild_after_load(cursor, self._ivg_schema, "bulk_create_edges")
                self.conn.commit()
            if auto_sync:
                if getattr(self, "_in_bulk_load", False):
                    logger.debug("auto_sync suppressed inside bulk_load_session")
                else:
                    self.sync()

    def bulk_ingest_edges(
        self,
        edges: List[Dict[str, Any]],
        predicate: str = "KNOWS",
        auto_sync: bool = True,
    ) -> int:
        """Insert many edges into the default graph and index them in ``^KG``, each a ``(source,
        target[, predicate])`` tuple or a dict with ``source_id``, ``predicate``, ``target_id`` (or
        ``s``/``p``/``o``). Another shape raises ``ValueError`` before anything is written.

        An edge is a ``(source, target)`` or ``(source, target, predicate)`` tuple, or a dict
        with ``source_id``/``s``/``source``, ``target_id``/``o``/``target`` and optionally
        ``predicate``/``p`` (the shape ``bulk_create_edges`` takes is one of these);
        ``predicate`` is the default. Any other shape, an empty source or target, a dict key
        outside that list (it stores no weight or qualifiers) or a ``graph`` key raises
        ``ValueError`` naming the edge, before anything is written. Runs server-side
        (``Graph.KG.EdgeScan.BulkIngestEdgesSQL``) when the classes are deployed, else row by
        row in SQL; duplicates are skipped. ``auto_sync`` runs ``sync`` afterwards. Returns
        the number of edges written.
        """
        _ledger_check(self, "bulk_ingest_edges")
        if not edges:
            return 0
        import json as _json

        # Every edge is checked before anything is written (R213).
        normalized = [_ingest_edge_triple(i, e, predicate) for i, e in enumerate(edges)]

        if self.capabilities.objectscript_deployed:
            try:
                from iris_vector_graph.schema import _call_classmethod_large

                iris_obj = self._iris_obj()
                n = 0
                for i in range(0, len(normalized), _BULK_CHUNK_SIZE):
                    chunk = normalized[i : i + _BULK_CHUNK_SIZE]
                    n += int(
                        _call_classmethod_large(
                            iris_obj,
                            "Graph.KG.EdgeScan",
                            "BulkIngestEdgesSQL",
                            _json.dumps(chunk),
                            predicate,
                        )
                    )
                self._nkg_dirty = True
            except Exception as e:
                logger.warning("BulkIngestEdgesSQL failed (%s), falling back to SQL path", e)
            else:
                # Outside the try: a failed sync() raises (R135) rather than sending the edges down the SQL path again.
                if auto_sync:
                    self.sync()
                return n

        cursor = self.conn.cursor()
        n = 0
        err_lower = lambda ex: ("unique" in str(ex).lower() or "-119" in str(ex))
        # This path writes the default graph only, so that is where the endpoints go.
        self._ensure_edge_endpoints(
            cursor, ((None, e[k]) for e in normalized for k in ("s", "o"))
        )
        for edge in normalized:
            s, p, o = edge["s"], edge["p"], edge["o"]
            try:
                # guarded: a refused duplicate costs a journaled rollback (~30 ms), a guarded one a probe
                cursor.execute(
                    f"INSERT INTO {self._ivg_schema}.rdf_edges (s, p, o_id, graph_id) SELECT ?, ?, ?, '' WHERE NOT EXISTS "
                    f"(SELECT 1 FROM {self._ivg_schema}.rdf_edges WHERE s = ? AND p = ? AND o_id = ? AND COALESCE(graph_id, '') = ''"
                    f"{self._ekey_guard(cursor)})",
                    [s, p, o, s, p, o],
                )
                if not cursor.rowcount:
                    continue  # duplicate edge — skip silently
            except Exception as ex:
                if err_lower(ex):
                    continue  # duplicate edge (a concurrent writer) — skip silently
                # 4.6.0 R276: any other refusal fell through, wrote ^KG for the row that was not there, and
                # counted it.
                _rollback_conn(self.conn)
                raise GraphWriteError("bulk_ingest_edges", ex, left=_ingest_left(self, n)) from ex
            if not self._adjacency_indexed:  # with the index the row wrote ^KG (spec 252)
                try:
                    self._iris_obj().classMethodVoid(
                        "Graph.KG.EdgeScan", "WriteAdjacency", s, p, o, "1.0"
                    )
                except Exception as ex:
                    # Without the Graph.KG classes there is no ^KG to keep; any other failure left ^KG without
                    # the edge, and was passed over (4.6.0 R276).
                    if "<CLASS DOES NOT EXIST>" not in str(ex):
                        _rollback_conn(self.conn)
                        raise GraphWriteError("bulk_ingest_edges", ex, left=_ingest_left(self, n)) from ex
            n += 1
        self.conn.commit()
        self._nkg_dirty = True
        if auto_sync:
            self.sync()
        return n

    def _graphs_holding(self, node_ids: List[str]) -> Dict[str, List[str]]:
        """``{canonical graph: ids}`` for each graph with a node or edge row naming one of ``node_ids``."""
        held: Dict[str, set] = {}
        cursor = self.conn.cursor()
        try:
            for i in range(0, len(node_ids), _DELETE_LOOKUP_CHUNK):
                chunk = node_ids[i : i + _DELETE_LOOKUP_CHUNK]
                ph = ",".join("?" * len(chunk))
                # %EXACT: graph_id and the id columns come back in collation form otherwise.
                for table, col in (("nodes", "node_id"), ("rdf_edges", "s"), ("rdf_edges", "o_id")):
                    cursor.execute(
                        f"SELECT DISTINCT %EXACT(COALESCE(graph_id, '')), %EXACT({col}) "
                        f"FROM {self._t(table)} WHERE {col} IN ({ph})",
                        chunk,
                    )
                    for g, nid in cursor.fetchall():
                        held.setdefault(validate_graph_name(g), set()).add(nid)
        finally:
            cursor.close()
        return {g: [n for n in node_ids if n in ids] for g, ids in sorted(held.items())}

    def _reifiers_of(self, node_ids: List[str], graph: str) -> List[str]:
        """Reifier ids of the edges in ``graph`` incident to ``node_ids``."""
        found: Dict[str, None] = {}
        cursor = self.conn.cursor()
        try:
            for i in range(0, len(node_ids), _DELETE_LOOKUP_CHUNK):
                chunk = node_ids[i : i + _DELETE_LOOKUP_CHUNK]
                ph = ",".join("?" * len(chunk))
                for col in ("s", "o_id"):
                    cursor.execute(
                        f"SELECT %EXACT(reifier_id) FROM {self._t('rdf_reifications')} "
                        f"WHERE edge_id IN (SELECT edge_id FROM {self._t('rdf_edges')} "
                        f"WHERE COALESCE(graph_id, '') = ? AND {col} IN ({ph}))",
                        [graph, *chunk],
                    )
                    for (reifier_id,) in cursor.fetchall():
                        found[reifier_id] = None
        finally:
            cursor.close()
        return list(found)

    def _delete_in_scope(self, node_ids: List[str], graph: str) -> int:
        """Delete ``node_ids`` from ``graph``, then the reifiers of their edges.

        A reifier names exactly one edge (``pk_reifications``), so it is dead once
        that edge is; the Eraser removes the ``rdf_reifications`` row, not the node.
        The reifiers go after, in each graph holding them, and are not counted.
        """
        reifiers = [r for r in self._reifiers_of(node_ids, graph) if r not in node_ids]
        deleted = _deleted_count(self._store.delete_nodes(node_ids, graph=graph))
        if reifiers:
            for scope, ids in self._graphs_holding(reifiers).items():
                self._store.delete_nodes(ids, graph=scope)
        return deleted

    def _delete_scopes(self, node_ids: List[str], graph: Any) -> Dict[str, List[str]]:
        """``{graph: ids}`` to delete. ``graph`` omitted: the one graph holding each id; an id two or more graphs hold
        raises ``ValueError`` before anything is deleted (4.6.0, #112: it was deleted from every one of them)."""
        if graph is not _EVERY_GRAPH:
            return {validate_graph_name(graph): node_ids}
        held = self._graphs_holding(node_ids)
        holders: Dict[str, List[str]] = {}
        for g, ids in held.items():
            for nid in ids:
                holders.setdefault(nid, []).append(g)
        shared = {nid: gs for nid, gs in holders.items() if len(gs) > 1}
        if shared:
            def named(g: str) -> str:
                return "'' (the default graph)" if g == "" else repr(g)

            listed = "; ".join(
                f"{nid!r} in {', '.join(named(g) for g in gs)}" for nid, gs in list(shared.items())[:10]
            )
            more = f" (and {len(shared) - 10} more)" if len(shared) > 10 else ""
            raise ValueError(
                f"{len(shared)} node id(s) are held by more than one graph, so nothing was deleted: {listed}{more}. "
                "Pass graph=None for the default graph or graph='<name>' for a named one."
            )
        return held

    def delete_node(self, node_id: str, graph: Any = _EVERY_GRAPH) -> bool:
        """Delete one node with its labels, props, documents, vectors and incident edges,
        and the reifier nodes of those edges.

        Each graph's delete is one ``Graph.KG.Eraser.EraseNodeIds`` transaction
        (via ``store.delete_nodes``), which reaches every vector table the graph
        routes to, the edges' reifications and embeddings, and ``^KG``. 4.1.0 ran
        its own statements instead: it missed a named graph's ``kg_emb_<hash>``
        table, whose foreign key then failed the node-row delete after the labels,
        props and edges had already been committed, and reported that as ``False``.

        Args:
            node_id: The node to delete.
            graph: The graph to delete it from; ``None`` and ``""`` are the default
                graph. Omitted, the graph holding ``node_id`` (a node or edge row
                naming it); before 4.6.0 it was every graph holding it, FHIR graphs
                included.

        Returns:
            True when a node row was removed, False when there was none.

        Raises:
            ValueError: for a graph name that cannot be a subscript, or, with
                ``graph`` omitted, when more than one graph holds ``node_id`` (the
                message names them); before anything is deleted.
            Exception: when a delete fails; that graph's delete has rolled back.
        """
        _ledger_check(self, "delete_node")
        deleted = 0
        for scope in self._delete_scopes([node_id], graph):
            deleted += self._delete_in_scope([node_id], scope)
        # The Eraser drops ^NKG itself when edges ^NKG can hold went; nothing is deferred.
        return deleted > 0

    def bulk_delete_nodes(
        self, node_ids: List[str], batch_size: int = None, graph: Any = _EVERY_GRAPH
    ) -> "DeleteResult":
        """Delete many nodes; ``graph`` as for :meth:`delete_node`. With ``graph``
        omitted, each id goes from the one graph holding it, and an id that more
        than one graph holds raises ``ValueError`` before any id is deleted.

        Each ``batch_size`` batch in each graph is one transaction. A batch that
        fails deleted nothing, is logged, and counts its ids in ``failed``; the
        other batches still run.
        """
        _ledger_check(self, "bulk_delete_nodes")
        ids = list(dict.fromkeys(str(n) for n in node_ids or [] if n is not None and str(n) != ""))
        if not ids:
            return DeleteResult(0, 0)
        size = batch_size if batch_size is not None else _batch_size_for(ids)
        deleted = 0
        failed = 0
        for scope, scoped_ids in self._delete_scopes(ids, graph).items():
            for i in range(0, len(scoped_ids), size):
                batch = scoped_ids[i : i + size]
                try:
                    deleted += self._delete_in_scope(batch, scope)
                except Exception as e:
                    logger.warning(
                        "bulk_delete_nodes: batch of %d in graph %r rolled back: %s",
                        len(batch), scope, e,
                    )
                    failed += len(batch)
        return DeleteResult(deleted, failed)

    def bulk_delete_adjacency(self, node_ids: List[str]) -> int:
        """Deprecated: deletes every edge row with one of ``node_ids`` as source or target.

        Every graph is covered. It used to remove only the adjacency and leave the rows, which
        the next rebuild (or, with the adjacency index, the row itself) brought back (spec 252,
        FR-016). The edges go through ``Graph.KG.Eraser.EraseEdges``, with their reifications,
        embeddings and ledger statements; the nodes stay. Returns the number of node ids.
        """
        warnings.warn(
            f"bulk_delete_adjacency is deprecated and deletes rows: every {self._ivg_schema}.rdf_edges row "
            "with one of these nodes as source or target, in every graph. Use delete_node / "
            "bulk_delete_nodes to remove the nodes too.",
            DeprecationWarning,
            stacklevel=2,
        )
        ids = [n for n in dict.fromkeys(node_ids) if n]
        if not ids:
            return 0
        # {graph: ordered set of (s, p, o)}; an edge seen from both ends is erased once
        edges: Dict[str, Dict[tuple, None]] = {}
        cursor = self.conn.cursor()
        for i in range(0, len(ids), _DELETE_LOOKUP_CHUNK):
            chunk = ids[i : i + _DELETE_LOOKUP_CHUNK]
            ph = ",".join("?" * len(chunk))
            for col in ("s", "o_id"):
                cursor.execute(
                    f"SELECT graph_id, s, p, o_id FROM {self._t('rdf_edges')} WHERE {col} IN ({ph})",
                    chunk,
                )
                for g, s, p, o in cursor.fetchall():
                    edges.setdefault(g or "", {})[(s, p, o)] = None
        native = self._iris_obj()
        for g, spo in edges.items():
            payload = json.dumps([{"s": s, "p": p, "o": o} for s, p, o in spo])
            native.classMethodValue("Graph.KG.Eraser", "EraseEdges", g, payload)
        if not self._adjacency_indexed:
            # without the index, adjacency with no row behind it can exist; sweep it as before
            native.classMethodValue("Graph.KG.EdgeScan", "BulkDeleteAdjacency", json.dumps(ids))
        return len(ids)

    def get_node_properties(self, node_id: str) -> Dict[str, Any]:
        """The default-graph node's properties: ``get_node`` without ``id`` and ``labels``.

        Returns ``{}`` when the node does not exist.
        """
        node = self.get_node(node_id)
        if not node:
            return {}
        return {k: v for k, v in node.items() if k not in ("id", "labels")}

    def get_node_name(self, node_id: str) -> Optional[str]:
        """The default-graph node's ``name``, else ``label``, else ``title`` property; else None."""
        props = self.get_node_properties(node_id)
        return props.get("name") or props.get("label") or props.get("title")

    def get_nodes_by_ids(self, node_ids: List[str]) -> List[Dict[str, Any]]:
        """``get_nodes`` for the default graph: one dict per existing node, ``[]`` for no IDs."""
        if not node_ids:
            return []
        return self.get_nodes(node_ids)

    def node_count(self, graph: Optional[str] = None) -> int:
        """The number of nodes in ``graph``; omitted (or None, ``""``), the default graph.

        ``engine.status()`` counts every graph's nodes together (``status().tables.nodes``, and the report's ``Graph:``
        line), so the two differ once a named graph has nodes. Before 4.6.0 this counted every graph (a Cypher ``MATCH (n)`` without ``USE GRAPH``), against
        the Python API's rule that an omitted graph is the default graph. Raises ``ValueError`` for a
        graph name that cannot be one.
        """
        return self._count_in_graph("nodes", graph)

    def edge_count(self, graph: Optional[str] = None) -> int:
        """The number of edges in ``graph``; omitted (or None, ``""``), the default graph.

        ``engine.status()`` counts every graph's edges together (``status().tables.edges``, and the report's ``Graph:``
        line). Before 4.6.0 this counted every graph. A multigraph's parallel edges each count.
        """
        return self._count_in_graph("rdf_edges", graph)

    def _count_in_graph(self, table: str, graph: Optional[str]) -> int:
        """Rows of ``table`` in one graph (R219). ``COALESCE``: a row a 4.x upgrade left NULL is the default graph's."""
        gid = validate_graph_name(graph)
        cursor = self.conn.cursor()
        try:
            cursor.execute(f"SELECT COUNT(*) FROM {self._t(table)} WHERE COALESCE(graph_id, '') = ?", [gid])
            row = cursor.fetchone()
            return int(row[0]) if row else 0
        finally:
            cursor.close()

    def store_node(
        self,
        node_id: str,
        properties: Optional[Dict[str, Any]] = None,
        labels: Optional[List[str]] = None,
        graph: Optional[str] = None,
    ) -> bool:
        """Write a node in one graph: its row if missing, ``properties`` replaced, ``labels`` added.

        Returns True; a duplicate node or label is not an error, any other failure raises
        ``GraphWriteError`` with nothing written. ``graph`` None is the default graph.
        The ``id`` property is set to ``node_id`` when ``properties`` has none and the node
        holds none yet, as ``create_node`` does; a None value removes that property.

        Naming `graph_id` is not only the spec 227 scope fix. A one-column
        `INSERT INTO nodes (node_id) VALUES (?)` cached on this connection stops
        being decodable once any other connection runs `%BuildIndices` on
        `Graph.KG.nodes` — a `%NOINDEX` bulk load does exactly that — and the
        duplicate then arrives as `<LIST ERROR> ... type detected : 0` instead of
        `SQLCODE -119`, which the string match below cannot recognise. Naming the
        column decodes correctly, so the duplicate stays idempotent.

        The node row, its properties and its labels are one transaction. A duplicate
        node row or label is not an error. Any other failure raises GraphWriteError
        with nothing written; before 4.5.0 a failed property was skipped silently
        (``except: pass``) after the node row had committed.
        """
        _ledger_check(self, "store_node")
        graph_id = graph if graph is not None else ""
        _scoped = getattr(self, "_nodes_has_graph_id", True)
        _children_scoped = getattr(self, "_children_have_graph_id", True)
        cursor = self.conn.cursor()
        try:
            cursor.execute("START TRANSACTION")
            try:
                if _scoped:
                    cursor.execute(
                        f"INSERT INTO {self._t('nodes')} (node_id, graph_id) VALUES (?, ?)",
                        [node_id, graph_id],
                    )
                else:
                    cursor.execute(f"INSERT INTO {self._t('nodes')} (node_id) VALUES (?)", [node_id])
            except Exception as e:
                if _scoped:
                    _swallow_duplicate(
                        self.conn,
                        e,
                        f"SELECT 1 FROM {self._t('nodes')} WHERE node_id = ? "
                        "AND COALESCE(graph_id, '') = COALESCE(?, '')",
                        [node_id, graph_id],
                    )
                else:
                    _swallow_duplicate(
                        self.conn,
                        e,
                        f"SELECT 1 FROM {self._t('nodes')} WHERE node_id = ?",
                        [node_id],
                    )
            for k, v in (properties or {}).items():
                # A None value removes the property: it was stored as the text 'None'.
                if _children_scoped:
                    # Scope the DELETE too: keyed on (s, "key") alone it wipes
                    # another graph's property of the same name.
                    cursor.execute(
                        f'DELETE FROM {self._t("rdf_props")} '
                        'WHERE graph_id = ? AND s = ? AND "key" = ?',
                        [graph_id, node_id, k],
                    )
                    if v is not None:
                        cursor.execute(
                            f'INSERT INTO {self._t("rdf_props")} (graph_id, s, "key", val) '
                            "VALUES (?, ?, ?, ?)",
                            [graph_id, node_id, k, prop_text(v)],
                        )
                else:
                    cursor.execute(
                        f'DELETE FROM {self._t("rdf_props")} WHERE s = ? AND "key" = ?',
                        [node_id, k],
                    )
                    if v is not None:
                        cursor.execute(
                            f'INSERT INTO {self._t("rdf_props")} (s, "key", val) VALUES (?, ?, ?)',
                            [node_id, k, prop_text(v)],
                        )
            if "id" not in (properties or {}):
                # 4.6.0 R181: the `id` property create_node writes, so `MATCH (n {id: ...})` and `n.id` find the
                # node. Only when the node has none: an `id` it already holds is the caller's and is kept.
                if _children_scoped:
                    cursor.execute(
                        f'INSERT INTO {self._t("rdf_props")} (graph_id, s, "key", val) '
                        f'SELECT ?, ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM {self._t("rdf_props")} '
                        'WHERE graph_id = ? AND s = ? AND "key" = ?)',
                        [graph_id, node_id, "id", node_id, graph_id, node_id, "id"],
                    )
                else:
                    cursor.execute(
                        f'INSERT INTO {self._t("rdf_props")} (s, "key", val) SELECT ?, ?, ? '
                        f'WHERE NOT EXISTS (SELECT 1 FROM {self._t("rdf_props")} WHERE s = ? AND "key" = ?)',
                        [node_id, "id", node_id, node_id, "id"],
                    )
            for lbl in labels or []:
                try:
                    if _children_scoped:
                        cursor.execute(
                            f"INSERT INTO {self._t('rdf_labels')} (graph_id, s, label) "
                            "VALUES (?, ?, ?)",
                            [graph_id, node_id, lbl],
                        )
                    else:
                        cursor.execute(
                            f"INSERT INTO {self._t('rdf_labels')} (s, label) VALUES (?, ?)",
                            [node_id, lbl],
                        )
                except Exception as e:
                    if _children_scoped:
                        _swallow_duplicate(
                            self.conn,
                            e,
                            f"SELECT 1 FROM {self._t('rdf_labels')} "
                            "WHERE COALESCE(graph_id, '') = COALESCE(?, '') "
                            "AND s = ? AND label = ?",
                            [graph_id, node_id, lbl],
                        )
                    else:
                        _swallow_duplicate(
                            self.conn,
                            e,
                            f"SELECT 1 FROM {self._t('rdf_labels')} WHERE s = ? AND label = ?",
                            [node_id, lbl],
                        )
            cursor.execute("COMMIT")
        except Exception as e:
            _rollback(cursor)
            raise GraphWriteError("store_node", e) from e
        finally:
            cursor.close()
        return True

    def store_edge(
        self,
        source_id: str,
        predicate: str,
        target_id: str,
        qualifiers: Optional[Dict[str, Any]] = None,
        graph: Optional[str] = None,
    ) -> bool:
        """Insert an edge row and its endpoint nodes in one graph; True, or ``GraphWriteError``.

        A duplicate edge is not an error. ``graph`` None is the default graph, and the
        endpoints register in the edge's own graph.

        The 4.0.0 composite FKs `fk_edges_source (graph_id, s)` and
        `fk_edges_dest (graph_id, o_id)` point at `nodes (graph_id, node_id)`, so
        an edge in graph `g` whose endpoints sit in the default graph is rejected
        with `SQLCODE -121`.

        Writes SQL only. Where the adjacency index (spec 252) is declared, the row maintains
        ``^KG`` itself; otherwise it does not feed the ``^KG`` adjacency that traversal
        (``ShortestPathJson``, BFS, PageRank) reads, so call ``sync()`` after a batch;
        ``create_edge`` writes both.

        The endpoint rows and the edge row are one transaction; a duplicate edge is not
        an error. Any other failure raises GraphWriteError with nothing written (before
        4.5.0 the endpoints, written through ``store_node``, had committed already).
        """
        _ledger_check(self, "store_edge")
        graph_id = graph if graph is not None else ""
        cursor = self.conn.cursor()
        try:
            cursor.execute("START TRANSACTION")
            self._ensure_edge_endpoints(cursor, ((graph_id, source_id), (graph_id, target_id)))
            qual_json = json.dumps(qualifiers) if qualifiers else None
            try:
                cursor.execute(
                    f"INSERT INTO {self._t('rdf_edges')} (s, p, o_id, qualifiers, graph_id) "
                    "VALUES (?, ?, ?, ?, ?)",
                    [source_id, predicate, target_id, qual_json, graph_id],
                )
            except Exception as e:
                _swallow_duplicate(
                    self.conn,
                    e,
                    f"SELECT 1 FROM {self._t('rdf_edges')} "
                    "WHERE COALESCE(graph_id, '') = COALESCE(?, '') "
                    "AND s = ? AND p = ? AND o_id = ?",
                    [graph_id, source_id, predicate, target_id],
                )
            cursor.execute("COMMIT")
        except Exception as e:
            _rollback(cursor)
            raise GraphWriteError("store_edge", e) from e
        finally:
            cursor.close()
        return True

    def nodes_exist(self, node_ids: List[str]) -> set:
        """The subset of ``node_ids`` that have a ``nodes`` row in any graph. Reads only.

        Not graph-scoped. A lookup IRIS refuses raises; before 4.6.0 (R276) the ID was left out, so it read as a node
        that does not exist.
        """
        if not node_ids:
            return set()
        existing: set = set()
        cursor = self.conn.cursor()
        try:
            for i in range(0, len(node_ids), 200):
                batch = node_ids[i : i + 200]
                phs = ",".join(["?"] * len(batch))
                try:
                    cursor.execute(
                        f"SELECT node_id FROM {self._t('nodes')} WHERE node_id IN ({phs})",
                        batch,
                    )
                    for row in cursor.fetchall():
                        existing.add(row[0])
                except Exception:
                    for nid in batch:
                        try:
                            cursor.execute(
                                f"SELECT COUNT(*) FROM {self._t('nodes')} WHERE node_id = ?",
                                [nid],
                            )
                            row = cursor.fetchone()
                            if row and int(row[0]) > 0:
                                existing.add(nid)
                        except Exception as e:
                            raise RuntimeError(f"nodes_exist: could not look up {nid!r}: {e}") from e
        finally:
            cursor.close()
        return existing
