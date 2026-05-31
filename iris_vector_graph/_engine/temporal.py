from __future__ import annotations
import json
from typing import Optional
from iris_vector_graph._validate import TemporalEdgeInput


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
        graph: Optional[str] = None,
    ) -> bool:
        if timestamp is not None:
            TemporalEdgeInput(source=source, predicate=predicate, target=target,
                              timestamp=int(timestamp), weight=weight)
        result = self._store.write_temporal_edge(
            source, predicate, target,
            timestamp=int(timestamp) if timestamp is not None else 0,
            weight=weight, attrs=attrs, upsert=upsert,
        )
        return result.error is None

    def bulk_create_edges_temporal(
        self, edges: list, upsert: bool = False, graph: Optional[str] = None
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
        result = self._store.bulk_write_temporal_edges(normalized, upsert=upsert)
        return result.rows[0][0] if result.rows else 0

    def get_edges_in_window(
        self,
        source: str = "",
        predicate: str = "",
        start: int = 0,
        end: int = 0,
        direction: str = "out",
    ) -> list:
        result = self._store.execute_temporal_window_query(source, predicate, start, end, direction)
        return result.rows if not result.error else []

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
