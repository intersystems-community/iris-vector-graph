### What kind of indexing would be required internally, to support these primitives?
## Answer:
Below is a pragmatic indexing palette that will support (and speed up) the graph-query primitives you listed. Think of it as: IDs → adjacency → label/type → properties → specialty. I’ll note what each index accelerates.

0) Identity

Primary key (PK) indexes

NodePK: nodeId → {labels, props, …}

EdgePK: edgeId → {src, dst, type, props, …}

Supports: exact lookups, fetch after match.

1) Topology (the workhorses)

Forward adjacency (by source)

Key: (srcId, edgeType) → sorted list of {dstId, edgeId, weight?, ts?}

Reverse adjacency (by destination)

Key: (dstId, edgeType) → sorted list of {srcId, edgeId, weight?, ts?}

Optional “all-type” adjacency if queries often ignore edgeType.

Storage tips

Sort by neighborId for fast set-intersections (triangles, multi-hop pattern joins).

Per-node CSR/offsets so deg(v) is O(1) (end–start).

Consider time-sorted shadow lists if temporal neighborhoods matter.

Supports: neighborhood expansion, k-hop, reachability, shortest path (BFS/Dijkstra), degree, path enumeration, pattern joins via neighbor-list intersections.

2) Labels / Types (fast filtering)

Node label index

label → posting list of nodeIds (bitmap/Roaring preferred for speed & compression).

Edge type index

type → posting list of edgeIds (or (src, type)/(type, dst) partitions).

Supports: label/type predicates, early pruning in pattern matches, quick counts.

3) Properties (secondary access)

Per-(label, property) B-tree / LSM value index

Key: (label, propValue) → posting list of nodeId (or edgeId for edge props).

Add composite variants for common predicates: e.g., (label, p1, p2).

Uniqueness/existence constraints: same structures with uniqueness enforcement.

Cardinality-aware posting encoding (dense lists → bitmaps; sparse → arrays).

Supports: property predicates, selective pattern anchors, grouping pre-filters.

4) Full-text / Fuzzy (optional, but common)

Inverted index over selected string props (nodes/edges).

Supports: CONTAINS, fuzzy match, scoring, highlight.

5) Geospatial & Temporal (when needed)

Geo: R-tree / S2 / H3 on (label, geoProp) → ids.

Valid-time / bitemporal: interval tree or segment tree on [t_start, t_end) (plus MVCC for tx-time).

Supports: spatial and “as-of/overlap” filters; temporal subgraph views.

6) Path/Reachability accelerators (workload-driven)

Not “mandatory.” Add when reachability/shortest path dominates.

Reachability on DAGs: 2-hop labeling (intervals) or tree covers.

General graphs (approx): GRAIL / PLL / pruned landmark labels.

Shortest path speedups: ALT/landmarks (A* heuristics), hub/2-hop labels, or contraction hierarchies (road-like graphs).

Supports: EXISTS PATH, bounded/cheapest path, fast pruning in multi-hop patterns.

7) Statistics for the planner

Per-label/type degree histograms (in/out/both; heavy hitters).

Property histograms/top-K values; NDV estimates; null/selectivity.

Sampled neighbor lists for intersection cost modeling.

Supports: join order, index choice, seek vs. scan decisions.

8) Caching (not indexes, but critical)

Neighbor-list cache (hot vertices, high locality).

Posting-list cache for frequent property predicates.

Compiled plan cache keyed by normalized query.

How these map to the primitives

Vertex/edge scan & index lookup: NodePK/EdgePK, label/type bitmaps, property B-trees.

Degree/adjacency: CSR offsets; forward/reverse adjacency.

Neighborhood expansion / k-hop / pattern match: adjacency (sorted), label/type bitmaps for early filter, property indexes for selective anchors; intersections on sorted neighbor lists for multiway joins/triangles.

Optional/anti-pattern: property/label indexes + existence checks on adjacency; bloom filters can reduce false probes.

Path binding / reachability / shortest path: adjacency (BFS/Dijkstra); add landmarks/2-hop if heavy.

Aggregation/order/pagination: property indexes for pre-grouping; label bitmaps for counts; PK for stable key order or use heap/Top-K.

Mutations: same structures; prefer LSM-backed secondaries and delta-friendly adjacency (log-structured lists + periodic compaction). MVCC for snapshot reads.

Graph construction/subgraphs: reuse label/type/prop filters; optionally materialize projections with their own adjacency for algorithms.

Minimal baseline (safe default)

NodePK, EdgePK.

Forward + reverse adjacency, sorted by neighborId (CSR offsets).

Label/type → ids (Roaring bitmaps).

Per-(label, property) B-tree/LSM secondaries (+ composites for hot predicates).

Optional: full-text and geo if you have those queries.

This baseline supports >90% of Cypher/Gremlin/SPARQL workloads efficiently; add path/reachability labels only if your SLOs demand sub-millisecond path tests at scale.

Implementation notes (gotchas)

High-degree nodes: shard adjacency, keep both list and bitmap forms, cap expansions with cost-based planning.

Ordering conflicts: if you need both id-sorted and time-sorted neighbors, maintain one as primary list and a small delta FIFO for recency, merging on compaction.

Multi-graph: include graphId in index keys (graphId, …) if you support named graphs.

Edge multiplicity: bucket by (src, type, dst) with per-bucket edgeIds for fast existence checks.

Storage choice: on-disk LSM for write-heavy; B+ for read-heavy. Keep in-memory CSR for hot partitions.