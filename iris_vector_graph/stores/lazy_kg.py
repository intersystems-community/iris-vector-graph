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

Storage layout (spec 214 put the graph key between the tree name and the node):
    ^KG("out", graph, s, p, o)   outbound edges
    ^KG("in",  graph, o, p, s)   inbound edges
    ^KG("deg", graph, s)         total out-degree
    ^KG("degp", graph, s, pred)  per-predicate out-degree

`graph` is the derived index key — the integer 0 for the default graph, the name
itself otherwise (`iris_vector_graph._validate.graph_index_key`, ADR-0003). Specs
162/163 were written against the pre-214 layout, where `out`/`in` carried a shard
number that was always 0 and `deg`/`degp` carried nothing; a literal 0 therefore
still reads the default graph's `out`/`in` correctly by coincidence, and read
`deg`/`degp` one subscript short of anything at all.

Concurrency contract (inherited from spec 162 FR-021 / spec 163 FR-018):
    Reads `^KG` live — no snapshot, no lock. Concurrent mutations during a
    multi-second algorithm run may produce inconsistent neighbor sets. Run on
    a quiescent graph for reproducibility.
"""

from __future__ import annotations

from typing import Iterator, List, Optional

from iris_vector_graph._validate import graph_index_key


class LazyKG:
    """On-demand `^KG` neighbor adapter with per-node dict cache.

    Lifecycle: instantiate at the start of an algorithm run, use throughout,
    let Python GC reclaim at function return. No explicit close needed.

    Every read is confined to one graph, fixed at construction. There is no
    merged view here: a node ID can exist in two graphs with different
    neighbours (spec 227 broke `UNIQUE (node_id)`), so a walk that spanned graphs
    would return one node with both graphs' edges and no way to tell them apart.

    Methods:
        iter_nodes(): Iterator over all nodes in this graph with at least one
            outbound edge (i.e., nodes in `^KG("deg", graph)`). Plus optionally
            union with `^KG("in", graph, *)` keys when `include_sinks=True`.
        out_neighbors(node): Distinct outbound targets of node (across all predicates).
        in_neighbors(node): Distinct inbound sources of node.
        degree(node): Cached total out-degree (single `^KG("deg", graph, node)` get).
        degree_for_predicate(node, pred): Per-predicate out-degree.
        clear_cache(): Reset all internal caches (rare — used by tests).

    Cache: unbounded by default (Python heap GC handles eviction at function
    return). For very long-running algorithms over huge graphs, callers can
    explicitly `clear_cache()` between phases.
    """

    def __init__(self, conn, include_sinks: bool = True, graph: Optional[str] = None):
        """Construct a LazyKG over the given dbapi connection.

        Args:
            conn: An `iris.dbapi.Connection` (external) or embedded equivalent.
                Must support `iris.createIRIS(conn)` for Native API access.
            include_sinks: When True (default), `iter_nodes()` also enumerates
                nodes that appear only as inbound targets (sinks with no
                outbound edges). Set False for slight speedup when sinks
                aren't needed.
            graph: The named graph to read. `None` and `""` both mean the default
                graph. Validated and derived here so an invalid or reserved name
                fails at construction rather than returning an empty graph: a
                wrong subscript reads as "no edges", which no caller can tell
                from a graph that really has none.
        """
        import iris as _iris

        self._iris = _iris.createIRIS(conn)
        self._include_sinks = include_sinks
        self._graph = graph_index_key(graph)
        self._out_cache: dict = {}
        self._in_cache: dict = {}
        self._degree_cache: dict = {}
        self._degp_cache: dict = {}
        self._in_degree_cache: dict = {}
        self._in_degp_cache: dict = {}
        self._nodes_cache: Optional[list] = None

    def iter_nodes(self) -> Iterator[str]:
        """Yield each unique node ID exactly once.

        Walks `^KG("deg", graph, *)` for nodes with outbound edges, then optionally
        `^KG("in", graph, *)` for sinks. Cached on first complete iteration.

        Omitting the graph subscript here walked the graph *names*, so a
        single-graph install yielded one node called `"0"` and every per-node
        read below then found nothing for it.
        """
        if self._nodes_cache is not None:
            yield from self._nodes_cache
            return

        g = self._graph
        seen: set = set()
        result: list = []

        sub = self._iris.nextSubscript(False, "^KG", "deg", g, "")
        while sub is not None and sub != "":
            if sub not in seen:
                seen.add(sub)
                result.append(sub)
            sub = self._iris.nextSubscript(False, "^KG", "deg", g, sub)

        if self._include_sinks:
            sub = self._iris.nextSubscript(False, "^KG", "in", g, "")
            while sub is not None and sub != "":
                if sub not in seen:
                    seen.add(sub)
                    result.append(sub)
                sub = self._iris.nextSubscript(False, "^KG", "in", g, sub)

        self._nodes_cache = result
        yield from result

    def out_neighbors(self, node: str) -> List[str]:
        """Return distinct outbound targets of `node` (across all predicates).

        Self-loops included; multi-edges (same node pair across multiple
        predicates) deduplicated. Cached after first call.
        """
        if node in self._out_cache:
            return self._out_cache[node]
        g = self._graph
        ns: List[str] = []
        seen: set = set()
        p = self._iris.nextSubscript(False, "^KG", "out", g, node, "")
        while p is not None and p != "":
            o = self._iris.nextSubscript(False, "^KG", "out", g, node, p, "")
            while o is not None and o != "":
                if o not in seen:
                    seen.add(o)
                    ns.append(o)
                o = self._iris.nextSubscript(False, "^KG", "out", g, node, p, o)
            p = self._iris.nextSubscript(False, "^KG", "out", g, node, p)
        self._out_cache[node] = ns
        return ns

    def in_neighbors(self, node: str) -> List[str]:
        """Return distinct inbound sources of `node` (across all predicates)."""
        if node in self._in_cache:
            return self._in_cache[node]
        g = self._graph
        ns: List[str] = []
        seen: set = set()
        p = self._iris.nextSubscript(False, "^KG", "in", g, node, "")
        while p is not None and p != "":
            s = self._iris.nextSubscript(False, "^KG", "in", g, node, p, "")
            while s is not None and s != "":
                if s not in seen:
                    seen.add(s)
                    ns.append(s)
                s = self._iris.nextSubscript(False, "^KG", "in", g, node, p, s)
            p = self._iris.nextSubscript(False, "^KG", "in", g, node, p)
        self._in_cache[node] = ns
        return ns

    def degree(self, node: str) -> int:
        """Return total out-degree from `^KG("deg", graph, node)` (count of edges, not unique targets)."""
        if node in self._degree_cache:
            return self._degree_cache[node]
        raw = self._iris.get("^KG", "deg", self._graph, node)
        deg = int(raw) if raw is not None else 0
        self._degree_cache[node] = deg
        return deg

    def degree_for_predicate(self, node: str, predicate: str) -> int:
        """Return per-predicate out-degree from `^KG("degp", graph, node, predicate)`."""
        key = (node, predicate)
        if key in self._degp_cache:
            return self._degp_cache[key]
        raw = self._iris.get("^KG", "degp", self._graph, node, predicate)
        deg = int(raw) if raw is not None else 0
        self._degp_cache[key] = deg
        return deg

    def in_degree(self, node: str) -> int:
        """Return total in-degree by counting entries under `^KG("in", graph, node, *, *)`.

        Used by spec 162 degree centrality and BFS-based betweenness when
        traversing reverse edges.
        """
        if node in self._in_degree_cache:
            return self._in_degree_cache[node]
        g = self._graph
        count = 0
        p = self._iris.nextSubscript(False, "^KG", "in", g, node, "")
        while p is not None and p != "":
            s = self._iris.nextSubscript(False, "^KG", "in", g, node, p, "")
            while s is not None and s != "":
                count += 1
                s = self._iris.nextSubscript(False, "^KG", "in", g, node, p, s)
            p = self._iris.nextSubscript(False, "^KG", "in", g, node, p)
        self._in_degree_cache[node] = count
        return count

    def in_degree_for_predicate(self, node: str, predicate: str) -> int:
        """Return per-predicate in-degree by counting `^KG("in", graph, node, predicate, *)` keys."""
        key = (node, predicate)
        if key in self._in_degp_cache:
            return self._in_degp_cache[key]
        g = self._graph
        count = 0
        s = self._iris.nextSubscript(False, "^KG", "in", g, node, predicate, "")
        while s is not None and s != "":
            count += 1
            s = self._iris.nextSubscript(False, "^KG", "in", g, node, predicate, s)
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
