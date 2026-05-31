"""LazyKG — on-demand `^KG` adapter for IRIS via Native API (Spec 163 FR-025).

Wraps `iris.createIRIS(conn)` with lazy fetching + dict caching of the IVG `^KG`
global structure. Algorithms call `lkg.out_neighbors(node)` etc. — first call hits
IRIS via `nextSubscript`/`get`, subsequent calls hit the Python dict cache.

Avoids the `%SYS.DBSRV` class-lookup path: uses Native API direct global access
(no `##class()` lookup), so it is unaffected by SQL-bindings-server class
resolution issues. (Historically tracked as "Bug S"; that turned out to be an
SSH-tunnel-to-wrong-container artifact, not a real IRIS defect — the direct-gref
path remains valuable regardless.)

Designed to be shared across spec 162 (centrality retrofit per FR-026) and spec 163
(community detection algorithms). Sibling module `arno_bridge.py` provides the
optional Rust-accelerated path; `LazyKG` is the always-available fallback.

Storage layout assumed (from spec 162 / spec 163):
    ^KG("out", 0, s, p, o)   outbound edges (shard-0)
    ^KG("in",  0, o, p, s)   inbound edges (shard-0)
    ^KG("deg", node)         total out-degree
    ^KG("degp", node, pred)  per-predicate out-degree

Concurrency contract (inherited from spec 162 FR-021 / spec 163 FR-018):
    Reads `^KG` live — no snapshot, no lock. Concurrent mutations during a
    multi-second algorithm run may produce inconsistent neighbor sets. Run on
    a quiescent graph for reproducibility.
"""

from __future__ import annotations

from typing import Iterator, List, Optional


class LazyKG:
    """On-demand `^KG` neighbor adapter with per-node dict cache.

    Lifecycle: instantiate at the start of an algorithm run, use throughout,
    let Python GC reclaim at function return. No explicit close needed.

    Methods:
        iter_nodes(): Iterator over all nodes with at least one outbound edge
            (i.e., nodes in `^KG("deg")`). Plus optionally union with
            `^KG("in", 0, *)` keys when `include_sinks=True`.
        out_neighbors(node): Distinct outbound targets of node (across all predicates).
        in_neighbors(node): Distinct inbound sources of node.
        degree(node): Cached total out-degree (single `^KG("deg", node)` get).
        degree_for_predicate(node, pred): Per-predicate out-degree.
        clear_cache(): Reset all internal caches (rare — used by tests).

    Cache: unbounded by default (Python heap GC handles eviction at function
    return). For very long-running algorithms over huge graphs, callers can
    explicitly `clear_cache()` between phases.
    """

    def __init__(self, conn, include_sinks: bool = True):
        """Construct a LazyKG over the given dbapi connection.

        Args:
            conn: An `iris.dbapi.Connection` (external) or embedded equivalent.
                Must support `iris.createIRIS(conn)` for Native API access.
            include_sinks: When True (default), `iter_nodes()` also enumerates
                nodes that appear only as inbound targets (sinks with no
                outbound edges). Set False for slight speedup when sinks
                aren't needed.
        """
        import iris as _iris
        self._iris = _iris.createIRIS(conn)
        self._include_sinks = include_sinks
        self._out_cache: dict = {}
        self._in_cache: dict = {}
        self._degree_cache: dict = {}
        self._degp_cache: dict = {}
        self._in_degree_cache: dict = {}
        self._in_degp_cache: dict = {}
        self._nodes_cache: Optional[list] = None

    def iter_nodes(self) -> Iterator[str]:
        """Yield each unique node ID exactly once.

        Walks `^KG("deg", *)` for nodes with outbound edges, then optionally
        `^KG("in", 0, *)` for sinks. Cached on first complete iteration.
        """
        if self._nodes_cache is not None:
            yield from self._nodes_cache
            return

        seen: set = set()
        result: list = []

        sub = self._iris.nextSubscript(False, "^KG", "deg", "")
        while sub is not None and sub != "":
            if sub not in seen:
                seen.add(sub)
                result.append(sub)
            sub = self._iris.nextSubscript(False, "^KG", "deg", sub)

        if self._include_sinks:
            sub = self._iris.nextSubscript(False, "^KG", "in", 0, "")
            while sub is not None and sub != "":
                if sub not in seen:
                    seen.add(sub)
                    result.append(sub)
                sub = self._iris.nextSubscript(False, "^KG", "in", 0, sub)

        self._nodes_cache = result
        yield from result

    def out_neighbors(self, node: str) -> List[str]:
        """Return distinct outbound targets of `node` (across all predicates).

        Self-loops included; multi-edges (same node pair across multiple
        predicates) deduplicated. Cached after first call.
        """
        if node in self._out_cache:
            return self._out_cache[node]
        ns: List[str] = []
        seen: set = set()
        p = self._iris.nextSubscript(False, "^KG", "out", 0, node, "")
        while p is not None and p != "":
            o = self._iris.nextSubscript(False, "^KG", "out", 0, node, p, "")
            while o is not None and o != "":
                if o not in seen:
                    seen.add(o)
                    ns.append(o)
                o = self._iris.nextSubscript(False, "^KG", "out", 0, node, p, o)
            p = self._iris.nextSubscript(False, "^KG", "out", 0, node, p)
        self._out_cache[node] = ns
        return ns

    def in_neighbors(self, node: str) -> List[str]:
        """Return distinct inbound sources of `node` (across all predicates)."""
        if node in self._in_cache:
            return self._in_cache[node]
        ns: List[str] = []
        seen: set = set()
        p = self._iris.nextSubscript(False, "^KG", "in", 0, node, "")
        while p is not None and p != "":
            s = self._iris.nextSubscript(False, "^KG", "in", 0, node, p, "")
            while s is not None and s != "":
                if s not in seen:
                    seen.add(s)
                    ns.append(s)
                s = self._iris.nextSubscript(False, "^KG", "in", 0, node, p, s)
            p = self._iris.nextSubscript(False, "^KG", "in", 0, node, p)
        self._in_cache[node] = ns
        return ns

    def degree(self, node: str) -> int:
        """Return total out-degree from `^KG("deg", node)` (count of edges, not unique targets)."""
        if node in self._degree_cache:
            return self._degree_cache[node]
        raw = self._iris.get("^KG", "deg", node)
        deg = int(raw) if raw is not None else 0
        self._degree_cache[node] = deg
        return deg

    def degree_for_predicate(self, node: str, predicate: str) -> int:
        """Return per-predicate out-degree from `^KG("degp", node, predicate)`."""
        key = (node, predicate)
        if key in self._degp_cache:
            return self._degp_cache[key]
        raw = self._iris.get("^KG", "degp", node, predicate)
        deg = int(raw) if raw is not None else 0
        self._degp_cache[key] = deg
        return deg

    def in_degree(self, node: str) -> int:
        """Return total in-degree by counting entries under `^KG("in", 0, node, *, *)`.

        Used by spec 162 degree centrality and BFS-based betweenness when
        traversing reverse edges.
        """
        if node in self._in_degree_cache:
            return self._in_degree_cache[node]
        count = 0
        p = self._iris.nextSubscript(False, "^KG", "in", 0, node, "")
        while p is not None and p != "":
            s = self._iris.nextSubscript(False, "^KG", "in", 0, node, p, "")
            while s is not None and s != "":
                count += 1
                s = self._iris.nextSubscript(False, "^KG", "in", 0, node, p, s)
            p = self._iris.nextSubscript(False, "^KG", "in", 0, node, p)
        self._in_degree_cache[node] = count
        return count

    def in_degree_for_predicate(self, node: str, predicate: str) -> int:
        """Return per-predicate in-degree by counting `^KG("in", 0, node, predicate, *)` keys."""
        key = (node, predicate)
        if key in self._in_degp_cache:
            return self._in_degp_cache[key]
        count = 0
        s = self._iris.nextSubscript(False, "^KG", "in", 0, node, predicate, "")
        while s is not None and s != "":
            count += 1
            s = self._iris.nextSubscript(False, "^KG", "in", 0, node, predicate, s)
        self._in_degp_cache[key] = count
        return count

    def clear_cache(self) -> None:
        """Reset all internal caches. Used by tests; rarely needed in algorithms."""
        self._out_cache.clear()
        self._in_cache.clear()
        self._degree_cache.clear()
        self._degp_cache.clear()
        self._in_degree_cache.clear()
        self._in_degp_cache.clear()
        self._nodes_cache = None

    def cache_stats(self) -> dict:
        """Return cache size info — diagnostic helper for perf tests / debugging."""
        return {
            "out_cached_nodes": len(self._out_cache),
            "in_cached_nodes": len(self._in_cache),
            "degree_cached_nodes": len(self._degree_cache),
            "degp_cached_pairs": len(self._degp_cache),
            "nodes_enumerated": self._nodes_cache is not None,
            "total_nodes_known": len(self._nodes_cache) if self._nodes_cache else 0,
        }
