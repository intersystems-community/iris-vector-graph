# Data Model: Temporal Edge Indexing

**Feature**: 036-temporal-edges | **Date**: 2026-04-01

## New `^KG` Subscript Keys

### Out-edge time index
```
^KG("tout", timestamp:INTEGER, source:STRING, predicate:STRING, target:STRING) = weight:FLOAT
```
- `timestamp`: Unix epoch seconds (INTEGER). First subscript enables `$Order` range scan.
- Enables: "all edges from source in [t0, t1]", "all edges with predicate P in window"

### In-edge time index (reverse)
```
^KG("tin", timestamp:INTEGER, target:STRING, predicate:STRING, source:STRING) = weight:FLOAT
```
- Mirror of `tout` for incoming edge queries. "all edges to target in [t0, t1]"

### Velocity bucket index
```
^KG("bucket", bucket:INTEGER, source:STRING) = count:INTEGER
```
- `bucket = floor(timestamp / 300)` — 5-minute buckets by default
- Incremented atomically via `$Increment` on every edge write
- Enables O(1) velocity queries: "how many edges from source in last 5 min?"

## Existing `^KG` Keys (UNCHANGED)

```
^KG("out", source, predicate, target) = weight    // unchanged
^KG("in", target, predicate, source) = weight     // unchanged
^KG("deg", source) = degree                       // unchanged
^KG("label", label, source) = ""                  // unchanged
^KG("prop", source, key) = value                  // unchanged
```

All existing operators (PageRank, BFS, PPR, Subgraph, WCC, CDLP) read ONLY from the unchanged keys.

## No Schema Changes

Zero new SQL tables. Zero changes to `Graph_KG.*` tables. Purely additive global writes.

## Cleanup Requirements

When `delete_node(node_id)` is called:
```
Kill ^KG("tout", ..., node_id, ...)    // as source
Kill ^KG("tin", ..., node_id, ...)     // as target
Kill ^KG("bucket", ..., node_id)       // bucket entries
```
When `PurgeIndex()` is called: `Kill ^KG("tout")`, `Kill ^KG("tin")`, `Kill ^KG("bucket")`
