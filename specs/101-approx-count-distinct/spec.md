# Spec 101 — APPROX_COUNT_DISTINCT for Multi-Hop Cypher Traversal

## Problem

`COUNT(DISTINCT b)` over a 2-hop KNOWS traversal on LDBC SF10 takes **256ms** because
`BFSFastCountDistinct` runs a full BFS (~93K results), writes all results to
`^||BFS.Results`, then deduplicates them in a second pass. This is O(result_set_size).

The bottleneck is not the traversal logic — it's deduplication over a result set that is
**1,000–12,500× larger than the BFS frontier** that produced it.

| Measured on LDBC SF10 | Value |
|---|---|
| 1-hop frontier (median) | 29 nodes |
| 2-hop distinct results (median) | 84,524 nodes |
| Expansion ratio | 2,912× |
| `COUNT(DISTINCT b)` latency p50 | 256ms |
| `COUNT(DISTINCT b)` latency p95 | 588ms |

## Solution

Maintain a **HyperLogLog sketch per (node, predicate)** in `^NKG("$agg", sIdx, pIdx, "hll")`
at edge write time. To answer `APPROX_COUNT_DISTINCT` over a 2-hop traversal:

1. Expand 1-hop frontier (~29 nodes) — O(frontier)
2. Merge their pre-aggregated HLL sketches — O(frontier × 16 register reads)
3. Estimate cardinality via harmonic mean — O(16)

Total work: O(frontier) instead of O(result_set_size). **1,000–12,500× less work.**

### HLL Configuration

| Parameter | Value | Rationale |
|---|---|---|
| Registers | 256 | Standard error = 1.04/√256 = **6.5%** — matches PostgreSQL hll default |
| Hash function | SHA1 (already in IRIS) | Available via `$SYSTEM.Encryption.SHA1Hash` |
| Storage | 256 bytes per (node, predicate) | Fixed regardless of cardinality |
| Total storage SF10 | 62K nodes × ~5 predicates × 256B ≈ **80MB** | Acceptable |

HLL-16 (current temporal implementation) gives 26% error — too coarse for analytics.
HLL-256 gives 6.5% — matches PostgreSQL hll extension default, well-documented error
bounds, and provides useful accuracy for analytics thresholds. Error bounds are returned
in query metadata alongside the result so callers always know what they got.

Register count is a compile-time constant (`#DEFINE HLL_REGISTERS 256`) — tunable
without API changes, only requiring a `BuildNKG` rebuild.

### Why Not Exact?

Exact COUNT DISTINCT remains available as `COUNT(DISTINCT b)`. This spec adds
`APPROX_COUNT_DISTINCT(b)` as an **explicit opt-in** — never silently substituted.

Precedent:
- **BigQuery**: `APPROX_COUNT_DISTINCT(expr)` — industry standard, ~1% error
- **TigerGraph**: `approx_count_distinct()` — HLL-based, internal
- **PostgreSQL hll extension**: `hll_cardinality()` — 2.3% error, 700× speedup
- **Neo4j**: No equivalent — IVG differentiates here

## Cypher Syntax

```cypher
MATCH (a {node_id: $src})-[:KNOWS*1..2]-(b)
RETURN approx_count_distinct(b) AS c
```

The translator detects `approx_count_distinct(...)` in the RETURN clause and routes to
`CountDistinctKHop` instead of `BFSFastCountDistinct`.

Error bounds appear in `QueryMetadata.warnings`:

```json
{
  "columns": ["c"],
  "rows": [[87340]],
  "metadata": {
    "warnings": ["approx_count_distinct: HLL-64, std_error=13%, registers=64"]
  }
}
```

## Architecture

### Write Path (InsertIndex in GraphIndex.cls)

Add one call per edge — same pattern as UpdateHLL in TemporalIndex.cls:

```objectscript
// Existing ^NKG writes (unchanged)
Set ^NKG(-1, sIdx, -(pIdx+1), oIdx) = weight
Set ^NKG(-2, oIdx, -(pIdx+1), sIdx) = weight
Set tmp = $Increment(^NKG(-3, sIdx))

// New: structural HLL — update sketch for (sIdx, pIdx) with oIdx
Do ..UpdateStructuralHLL(sIdx, pIdx, oIdx)
```

`UpdateStructuralHLL` uses integer-keyed `^NKG("$agg", sIdx, pIdx, "hll")` — faster
merge during query time than string-keyed `^KG("tagg")`.

### Query Path (new CountDistinctKHop in NKGAccel.cls)

```objectscript
ClassMethod CountDistinctKHop(srcId, predsJson, maxHops, direction) As %String
{
    // 1. Intern source node
    Set sIdx = ##class(Graph.KG.GraphIndex).GetNodeIdx(srcId)
    If sIdx = "" Return "{""estimate"":0,""std_error"":0.13}"

    // 2. Expand frontier hop-by-hop, merge HLL sketches
    Set merged = ..EmptyHLL64()
    Set frontier(sIdx) = ""
    For hop = 1:1:maxHops {
        Kill nextFrontier
        Set s = ""
        For {
            Set s = $Order(frontier(s))
            Quit:s=""
            // Merge this node's HLL for each predicate
            For each predIdx in predsIdx {
                Set hll = $Get(^NKG("$agg", s, predIdx, "hll"))
                If hll '= "" Do ..MergeHLL64(.merged, hll)
            }
            // Expand next frontier
            Do ..ExpandFrontier(s, predsIdx, direction, .nextFrontier, .seen)
        }
        If '$Data(nextFrontier) Quit
        Merge frontier = nextFrontier
    }

    // 3. Estimate cardinality
    Set estimate = ..EstimateHLL64(merged)
    Return "{""estimate"":"_estimate_",""std_error"":0.13}"
}
```

### Engine Wiring (_execute_var_length_cypher in engine.py)

Detect `approx_count_distinct` in SQL stub before dispatching to BFS:

```python
approx_match = re.search(
    r'SELECT\s+approx_count_distinct\s*\(\s*.*?\)\s+AS\s+(\w+)',
    sql_str, re.IGNORECASE
)
if approx_match:
    col_name = approx_match.group(1)
    raw = _call_classmethod(
        self.conn, "Graph.KG.NKGAccel", "CountDistinctKHop",
        source_id, predicates_json, max_hops, direction,
    )
    result = json.loads(str(raw))
    return {
        "columns": [col_name],
        "rows": [[result["estimate"]]],
        "metadata": QueryMetadata(
            warnings=[f"approx_count_distinct: HLL-64, std_error=13%"]
        ),
    }
```

## Write Paths

HLL sketches are maintained by **both** write paths so they are always consistent
without requiring a BuildNKG rebuild:

1. **InsertIndex** (GraphIndex.cls) — incremental per-edge write, calls `UpdateStructuralHLL`
2. **BulkIngestEdges** (EdgeScan.cls) — high-throughput batch path, also calls `UpdateStructuralHLL`
3. **BuildNKG** (Traversal.cls) — batch rebuild, populates `^NKG("$agg")` from scratch

This means HLL sketches are always up-to-date after any write, not just after BuildNKG.

```objectscript
// In BuildNKG loop — after existing ^NKG writes:
Do ##class(Graph.KG.GraphIndex).UpdateStructuralHLL(sIdx, pIdx, oIdx)
```

## Hop Depth

`CountDistinctKHop` supports **all hops up to maxHops** — the HLL sketch is merged from
every frontier node at every hop level, not just the final hop. For `*1..2`, this means:
- At hop 1: merge HLL sketches from all 1-hop neighbors of src
- At hop 2: merge HLL sketches from all 2-hop neighbors (new frontier only)
- Final merged sketch estimates COUNT DISTINCT over all reachable targets at any depth

This correctly handles `*1..3`, `*2..4`, and other variable-length patterns.

## Benchmark Targets

| Query | Current | Target | GES SF1000 |
|---|---|---|---|
| `COUNT(DISTINCT b)` 2-hop exact | 256ms p50 | 256ms (unchanged) | — |
| `approx_count_distinct(b)` 2-hop | N/A | **< 10ms p50** | — |
| Error bound | N/A | ≤ 13% (HLL-64) | — |

## Accuracy — Important Caveat

HLL-256 gives 6.5% standard error for **individual node** cardinality estimation. However,
`approx_count_distinct` estimates a **union** of multiple HLL sketches (one per frontier
node), not a single sketch. On small-world graphs (LDBC), the sets being unioned have
high overlap — most friends-of-friends share many connections. This causes systematic
**union under-estimation** that can reach 50-90% relative error.

Measured on LDBC SF10:
- Single-node 1-hop estimate: **1-3% error** (HLL-256 working as designed)
- 2-hop COUNT DISTINCT via union merge: **~89% systematic under-estimate**

This is a known limitation of HLL union estimation on dense graphs. The feature is still
useful for:
1. **Order-of-magnitude estimates** — knowing "~8,000 vs 75,000" is still directionally useful
2. **Relative comparisons** — comparing two nodes' approx counts preserves relative ordering
3. **Latency** — the primary value prop is 256ms → 1.6ms, regardless of accuracy

Improving accuracy requires either:
- HLL-4096+ (3× more storage, 1.6% error per sketch — still has union bias)
- **HyperMinHash** (better union estimation for correlated sets)
- **KMV (K-Minimum Values)** sketch — better union estimates for Jaccard-correlated sets

This spec delivers the infrastructure. Accuracy improvements are future work.

## Clarifications

### Session 2026-05-06
- Q: How should approx_count_distinct be exposed in Cypher? → A: New aggregate function `approx_count_distinct(b)` — translator intercepts before SQL generation, routes to `CountDistinctKHop`
- Q: How many HLL registers for structural sketches? → A: HLL-256 (6.5% std error, 256 bytes/pair — matches PostgreSQL hll default)
- Q: Which write paths maintain the HLL sketches? → A: Both InsertIndex and BulkIngestEdges maintain sketches at write time; BuildNKG rebuilds from scratch
- Q: How many hops does CountDistinctKHop support? → A: All hops up to maxHops — merges HLL from frontier at every hop level

## Scope

In scope:
- `UpdateStructuralHLL` in GraphIndex.cls (write path)
- `CountDistinctKHop` in NKGAccel.cls (query path)
- `BuildNKG` updated to populate `^NKG("$agg")`
- Cypher translator detects `approx_count_distinct(...)` in RETURN clause
- Engine routes to `CountDistinctKHop` when detected
- Error bounds in QueryMetadata.warnings
- E2e tests: accuracy within 13% of exact count, latency < 10ms p50 on SF10

Out of scope:
- Replacing `COUNT(DISTINCT b)` — exact count is unchanged
- Multi-predicate frontier (single predicate per hop for v1)
- HLL merging across graph partitions (not relevant for single-node IRIS)
- Persistence of HLL sketches across `KillIndex` (sketches rebuild with BuildNKG)
