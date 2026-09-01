from __future__ import annotations
import json
from typing import NamedTuple, Optional

from iris_vector_graph._validate import TemporalEdgeInput


class PurgeResult(NamedTuple):
    """Return value of purge_raw_before: counts of deleted and skipped edges.

    int(result) == result.deleted for backward-compatible callers.
    """

    deleted: int
    skipped: int

    def __int__(self) -> int:
        return self.deleted


class TemporalMixin:
    """Temporal property graph domain mixin for IRISGraphEngine.
    
    Provides time-indexed edge storage, windowed queries, pre-aggregated analytics,
    and burst detection over the temporal edge store (^KG("tout"/"tin"/"bucket")/^KG("tagg")).
    """

    def create_edge_temporal(
        self,
        source: str,
        predicate: str,
        target: str,
        timestamp: int = None,
        weight: float = 1.0,
        attrs: dict = None,
        upsert: bool = False,
        suppress_reverse_index: bool = False,
        graph: Optional[str] = None,
    ) -> bool:
        """Create a timestamped edge in the temporal index.

        ADJACENCY CONTRACT: TemporalIndex.InsertEdge writes the temporal globals
        (^KG "tout"/"tin"/buckets/aggregates) AND a shadow ^KG("out",0,...)
        adjacency entry — so temporal edges ARE visible to the ^KG-based readers
        (MatchEdges, Algorithms, Centrality). They are NOT written to the
        interned ^NKG(-1,...) index, so the NKG-accelerated (arno/Rust) BFS path
        will not see them until a BuildNKG. Call engine.sync() if you need
        temporal edges in NKG-accelerated traversals. Edges are only mirrored
        into the rdf_edges SQL table when ``graph=`` is supplied.
        """
        if timestamp is not None:
            TemporalEdgeInput(source=source, predicate=predicate, target=target,
                              timestamp=int(timestamp), weight=weight)
        result = self._store.write_temporal_edge(
            source, predicate, target,
            timestamp=int(timestamp) if timestamp is not None else 0,
            weight=weight, attrs=attrs, upsert=upsert,
            suppress_reverse_index=suppress_reverse_index,
        )
        if result.error is None and graph is not None:
            cursor = self.conn.cursor()
            for nid in (source, target):
                try:
                    cursor.execute(
                        f"INSERT INTO {self._t('nodes')} (node_id) SELECT ? "
                        f"WHERE NOT EXISTS (SELECT 1 FROM {self._t('nodes')} WHERE node_id=?)",
                        [nid, nid],
                    )
                except Exception:
                    pass
            try:
                cursor.execute(
                    f"INSERT INTO {self._t('rdf_edges')} (s, p, o_id, graph_id) "
                    f"SELECT ?, ?, ?, ? WHERE NOT EXISTS "
                    f"(SELECT 1 FROM {self._t('rdf_edges')} WHERE s=? AND p=? AND o_id=? AND graph_id=?)",
                    [source, predicate, target, graph, source, predicate, target, graph],
                )
                self.conn.commit()
            except Exception:
                pass
        return result.error is None

    def bulk_create_edges_temporal(
        self, edges: list, upsert: bool = False,
        suppress_reverse_index: bool = False, graph: Optional[str] = None
    ) -> int:
        normalized = [
            {
                "source": e.get("s") or e.get("source_id") or e.get("source", ""),
                "predicate": e.get("p") or e.get("predicate", ""),
                "target": e.get("o") or e.get("target_id") or e.get("target", ""),
                "timestamp": int(e.get("ts") or e.get("timestamp", 0)),
                "weight": float(e.get("w") or e.get("weight", 1.0)),
                "attrs": e.get("attrs") or {},
            }
            for e in edges
        ]
        result = self._store.bulk_write_temporal_edges(
            normalized, upsert=upsert, suppress_reverse_index=suppress_reverse_index
        )
        try:
            count = int(result.rows[0][0]) if result.rows else 0
        except (TypeError, ValueError, IndexError):
            count = 0
        if count > 0 and graph is not None:
            cursor = self.conn.cursor()
            for e in normalized:
                for nid in (e["source"], e["target"]):
                    try:
                        cursor.execute(
                            f"INSERT INTO {self._t('nodes')} (node_id) SELECT ? "
                            f"WHERE NOT EXISTS (SELECT 1 FROM {self._t('nodes')} WHERE node_id=?)",
                            [nid, nid],
                        )
                    except Exception:
                        pass
                try:
                    cursor.execute(
                        f"INSERT INTO {self._t('rdf_edges')} (s, p, o_id, graph_id) "
                        f"SELECT ?, ?, ?, ? WHERE NOT EXISTS "
                        f"(SELECT 1 FROM {self._t('rdf_edges')} WHERE s=? AND p=? AND o_id=? AND graph_id=?)",
                        [e["source"], e["predicate"], e["target"], graph,
                         e["source"], e["predicate"], e["target"], graph],
                    )
                except Exception:
                    pass
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
        """Return edges in a time window.

        direction="in" queries inbound edges (QueryWindowInbound).
        WARNING: Edges written with suppress_reverse_index=True are NOT visible
        when direction="in". Those edges have no ^KG("tin") entry by design.
        Callers mixing suppressed and non-suppressed writes will see only
        non-suppressed edges in inbound queries.

        source="" returns edges from all sources (QueryWindow all-sources path).
        """
        result = self._store.execute_temporal_window_query(source, predicate, start, end, direction)
        if result.error:
            return []
        cols = result.columns
        if cols and result.rows and isinstance(result.rows[0], (list, tuple)):
            long_to_short = {"source": "s", "predicate": "p", "target": "o", "timestamp": "ts", "weight": "w"}
            short_to_long = {v: k for k, v in long_to_short.items()}
            out = []
            for row in result.rows:
                d = dict(zip(cols, row))
                extras = {long_to_short[k]: v for k, v in d.items() if k in long_to_short}
                extras.update({short_to_long[k]: v for k, v in d.items() if k in short_to_long})
                d.update(extras)
                out.append(d)
            return out
        return result.rows

    def purge_before(self, ts: int) -> None:
        self._iris_obj().classMethodVoid(
            "Graph.KG.TemporalIndex", "PurgeBefore", int(ts)
        )

    def purge_bucket_range(self, bucket_start: int, bucket_end: int) -> int:
        """Delete ^KG("tagg") and ^KG("bucket") entries in [bucket_start, bucket_end].

        Raw edges (tout/tin/edgeprop) are never touched.
        bucket = timestamp // BUCKET_SIZE (300 for seconds, 300000 for ms).
        Returns count of buckets removed. Returns 0 if bucket_start > bucket_end.
        """
        return self._store.purge_bucket_range(bucket_start, bucket_end)

    def purge_raw_before(self, ts_end: int, ts_start: int = 0) -> "PurgeResult":
        """Delete raw temporal edges in [ts_start, ts_end). Preserves aggregates
        and ^KG("labelset") (append-only, never purged).

        ts_start=0 (default): byte-identical to v2.8.0 behavior.
        Returns PurgeResult(deleted, skipped). int(result) == result.deleted.
        """
        return self._store.purge_raw_before(ts_end, ts_start=ts_start)

    def intern_label_set(self, attrs: dict) -> str:
        """Canonicalize, hash, and intern an attribute dict. Returns SHA1 hex hash.

        Type fidelity: numeric values intern as numbers, booleans as booleans,
        nulls as null. {"port": 1972} resolves as integer 1972, not "1972".

        ^KG("labelset") is append-only and never touched by any purge operation.
        Interned label sets outlive raw edges — safe for long-term retention.
        """
        import json as _json

        return self._store.intern_label_set(_json.dumps(attrs, separators=(",", ":")))

    def resolve_label_set(self, hash_hex: str) -> str:
        """Return the canonical JSON for a known label set hash, or '' if unknown."""
        return self._store.resolve_label_set(hash_hex)

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
        result = self._store.get_temporal_aggregate(source, predicate, metric, ts_start, ts_end)
        if result.rows:
            val = result.rows[0][0]
            return int(val) if metric == "count" else float(val)
        return 0 if metric == "count" else None

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

    def get_window_sources(
        self,
        predicate: str,
        ts_start: int,
        ts_end: int,
    ) -> list[str]:
        """Return distinct source node IDs with at least one edge for a
        predicate over a time window, without materializing per-edge JSON.

        Unlike ``get_edges_in_window`` (which returns every matching edge —
        source, target, timestamp, weight — and requires the caller to
        de-duplicate sources in Python), this walks ``^KG("tout")`` only down
        to the source level per timestamp bucket and returns each matching
        source once. Use this when you only need "which sources fired this
        predicate in this window" (e.g. fleet/tenant enumeration) and would
        otherwise be discarding target/timestamp/weight from a full window
        scan.

        Args:
            predicate: Edge type to filter on. Empty string matches any
                predicate (fastest — skips the per-source predicate check).
            ts_start: Window start as Unix timestamp (inclusive).
            ts_end: Window end as Unix timestamp (inclusive).

        Returns:
            list[str]: Distinct source node IDs, each returned once even if
                they have multiple matching edges in the window.
        """
        result = self._iris_obj().classMethodValue(
            "Graph.KG.TemporalIndex",
            "QueryWindowSources",
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
