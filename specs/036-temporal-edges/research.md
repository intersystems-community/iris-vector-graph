# Research: Temporal Edge Indexing

**Feature**: 036-temporal-edges | **Date**: 2026-04-01

## R1: Global Structure Design

**Decision**: New subscript keys inside existing `^KG` global.

```
^KG("tout", timestamp, source, predicate, target) = weight   // time-ordered out-edges
^KG("tin",  timestamp, target, predicate, source) = weight   // time-ordered in-edges
^KG("bucket", floor(timestamp/300), source) = ""             // 5-min bucket index
```

**Rationale**: `$Order(^KG("tout", ts_start))` range scans efficiently skip to exact timestamp without scanning earlier edges. Separate global (`^KGt`) would offer no performance advantage and would complicate `PurgeIndex()`. Consistent with existing `^KG` conventions.

**Alternatives rejected**:
- Separate `^KGt` global: No performance benefit, splits backup/restore, extra global kill in Purge.
- Timestamp appended to `^KG("out",s,p,o,ts)`: Breaks `$Order` range scan (ts is last subscript, can't scan by time range without scanning all edges for s/p/o).

## R2: BulkInsert ObjectScript Design

**Decision**: Single `ClassMethod BulkInsert(batchJSON As %String) As %Integer` that parses `%DynamicArray` and loops over entries writing all three global keys per edge.

**Rationale**: Single `classMethodValue` call from Python — same pattern as `BulkLoader.SeedFromStaging` which achieves 46K rows/sec. Eliminates N round-trips. JSON parsing is O(N) and fast in ObjectScript via `%DynamicArray`. The loop does 5 global writes per edge (tout, tin, bucket + existing out + in) — all in-memory B+tree operations.

**Expected throughput**: At 5 global writes × 0.005ms each = 0.025ms/edge → theoretical 40K edges/sec. With JSON parse overhead, target 50K edges/sec is achievable with batches of 1K-10K.

## R3: Window Query Design

**Decision**: `ClassMethod QueryWindow(source, predicate, tsStart, tsEnd) As %String` using nested `$Order` for range scan.

```objectscript
Set ts = tsStart - 1
For {
    Set ts = $Order(^KG("tout", ts), 1, weight)
    If ts = "" || ts > tsEnd Quit
    Set pred = ""
    For {
        Set pred = $Order(^KG("tout", ts, source, pred))
        Quit:pred=""
        If predicate '= "" && pred '= predicate Continue
        // ... collect targets
    }
}
```

O(results) not O(total edges) — crucial for large graphs.

## R4: Velocity Detection Design

**Decision**: `ClassMethod GetVelocity(nodeId, windowSec) As %Integer` reads from `^KG("bucket")` index rather than scanning individual temporal edges.

**Bucket structure**: `^KG("bucket", floor(ts/300), nodeId) = edgeCount` — increment on every edge write. For a 60-second window across 5-minute buckets, sum the partial buckets. For a 5-minute window, read at most 2 bucket entries.

**Rationale**: O(1) velocity lookup regardless of edge count. Avoids scanning `^KG("tout", ...)` for every velocity check.

## R5: $vector Suitability Analysis (from arno team)

**Decision**: Phase 1 uses per-edge globals for ALL datasets. Phase 2 adds $vector chunking for dense metric series.

**Two distinct storage tiers**:

| Tier | Edge Type | Storage | Datasets |
|------|-----------|---------|----------|
| Event edges | CALLS_AT, CHILD_OF_AT, IMPACTS_AT | `^KG("tout", ts, s, p, o)` per-edge | RCAEval, Train-Ticket, TraceRCA, Alibaba call graphs, Azure |
| Metric edges | EMITS_METRIC_AT | Phase 2: `^KG("tmetric", s, p, o, bucket) = $vector(weights)` | Microsoft Cloud, IBM Cloud, Alibaba resource metrics |

**Why per-edge globals work for Phase 1**:
- RCAEval: ~20 data points per metric per case (too few for $vector)
- Train-Ticket: ~60s intervals, ~20 points per case
- TraceRCA: Pure event data (individual spans)
- All Tier A datasets are sparse event data or short metric windows

**Why $vector is needed for Phase 2**:
- IBM: 39,365 timestamps × 117K features = 413M rows. $vector of 39K doubles per feature = 117K global nodes instead of 413M
- Alibaba: 720 points × 180K series = 130M readings. $vector per series per hour
- Pattern: paired $vector (values + timestamps) per (source, metric_target) per time bucket

**Source**: arno team dataset analysis (2026-04-01)
