# Feature Specification: IC3 Exact 2-Hop COUNT — O(1) Lookup via Precomputed deg2p_exact

**Feature Branch**: `152-ic3-exact-2hop-count`
**Created**: 2026-05-07
**Status**: Draft

## Background

IC3 is a standard LDBC SNB Interactive query: count the distinct 2-hop neighbors of a person via KNOWS. IVG currently takes **70ms** for this query on SF10, bottlenecked by a 136K `$Data` dedup scan on a process-private local array. The target is **<1ms**.

### Root cause

`KHop2Count` works by:
1. Building a hop-1 set (1553 nodes into `^||kh2all`) — fast
2. For each of 1553 mid-nodes, walking their outbound KNOWS edges and checking `$Data(^||kh2all(o2))` for each — this is 136K `$Data` operations on a growing local array = 70ms

The fix: **precompute the exact distinct count at `BuildNKG` time** and store it in `^KG("deg2p_exact", src, pred)`. Runtime becomes an O(1) `$Get`.

`KHop2CountFast` already stores an upper bound (sum of neighbor degrees, 3.67× overcount). This spec adds the **exact** precomputed count.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — IC3 COUNT query completes in <1ms (Priority: P1)

A developer runs `MATCH (s {node_id:$id})-[:KNOWS*2]->(n) RETURN count(n) AS cnt` against a high-degree LDBC node. Today: 70ms. After this feature: <1ms via O(1) global lookup.

**Independent Test**: After `BuildNKG` (or `backfill_deg2p_exact`), `KHop2CountExact` returns the same result as `KHop2Count` in <1ms.

**Acceptance Scenarios**:

1. **Given** a node with 1553 KNOWS neighbors, **When** `execute_cypher('MATCH (s)-[:KNOWS*2]->(n) RETURN count(n)', ...)` is called, **Then** result is returned in <1ms p50 and matches `KHop2Count` exactly.
2. **Given** `^KG("deg2p_exact")` is not populated (e.g. fresh bulk load), **When** `KHop2CountExact` is called, **Then** it falls back to `KHop2Count` (70ms) without crashing.
3. **Given** `BuildNKG` completes, **When** `^KG("deg2p_exact")` is checked, **Then** it is populated for all nodes with outgoing edges.

---

### User Story 2 — BuildNKG total time stays under 30s on SF10 (Priority: P1)

Adding 2-hop dedup to `BuildNKG` should not make it unusably slow. Current Rust: 19s. Adding precomputation in Rust: target ≤30s total.

**Independent Test**: `engine.rebuild_nkg()` completes in ≤30s on LDBC SF10.

**Acceptance Scenarios**:

1. **Given** SF10 data (62K nodes, 3.87M KNOWS edges), **When** `engine.rebuild_nkg()` is called, **Then** it completes in ≤30s.
2. **Given** ObjectScript fallback path (no Rust), **When** `BuildNKG` is called, **Then** it completes in ≤90s (acceptable for deploy-time operation).

---

### Edge Cases

- Node with 0 KNOWS neighbors: `KHop2CountExact` returns 0.
- Node with 1 KNOWS neighbor that has no outgoing KNOWS: returns 0.
- Very high-degree node (>5K neighbors): precomputed count is exact regardless of degree.
- `^KG("deg2p_exact")` populated but stale (bulk load added edges after): falls back to `KHop2Count`.
- `BuildNKG` interrupted mid-run: partial `^KG("deg2p_exact")` is acceptable; missing entries fall back to scan.

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `Traversal.cls` MUST add `KHop2CountExact(srcId, pred)` — O(1) `$Get(^KG("deg2p_exact", srcId, pred), -1)`, fallback to `KHop2Count` on -1.
- **FR-002**: `Traversal.cls` `BuildNKG` MUST call `Build2HopExactStats()` at the end (after `Build2HopStats`).
- **FR-003**: `Build2HopExactStats()` MUST write `^KG("deg2p_exact", src, pred)` = exact distinct 2-hop neighbor count for each `(src, pred)` pair. Must be implemented in Rust (`ffi_kg_build_2hop_exact`) for SF10 performance. ObjectScript fallback acceptable for non-Rust deployments.
- **FR-004**: `engine.rebuild_nkg()` MUST call `Build2HopExactStats()` (already handled if `BuildNKG` calls it).
- **FR-005**: `engine.execute_cypher` fast-path `_2HOP_COUNT_RE` MUST route to `KHop2CountExact` instead of `KHop2Count`.
- **FR-006**: `engine.khop2_count_exact(node_id, pred)` MUST be added as a public engine method.
- **FR-007**: `engine.backfill_deg2p_exact()` MUST be added — walks `^KG("out")` to populate `^KG("deg2p_exact")` for graphs loaded without `BuildNKG` (e.g. after `BulkIngestEdges`).
- **FR-008**: All existing `KHop2Count` tests MUST continue to pass (FR-001 fallback guarantees this).

### Key Entities

- **`^KG("deg2p_exact", src, pred)`**: Integer global. Exact distinct 2-hop neighbor count reachable from `src` via `pred` in exactly 2 hops. Populated by `Build2HopExactStats` during `BuildNKG`.
- **`KHop2CountExact(srcId, pred)`**: ObjectScript method. O(1) `$Get` with fallback scan.
- **`ffi_kg_build_2hop_exact()`**: Rust function. Reads `^KG("out",0,...)`, builds per-node HashSet for 2-hop dedup, writes `^KG("deg2p_exact",...)`.
- **`Build2HopExactStats()`**: ObjectScript wrapper calling Rust or falling back to ObjectScript scan.

## Success Criteria *(mandatory)*

- **SC-001**: `execute_cypher('MATCH (s)-[:PRED*2]->(n) RETURN count(n)', ...)` returns in <1ms p50 on SF10 after `BuildNKG`.
- **SC-002**: `KHop2CountExact` result matches `KHop2Count` exactly for all tested nodes.
- **SC-003**: `engine.rebuild_nkg()` completes in ≤30s on SF10 with Rust enabled.
- **SC-004**: All existing VL path and BFS e2e tests pass — zero regressions.
- **SC-005**: `^KG("deg2p_exact")` not populated → falls back to `KHop2Count` without error.

## Assumptions

- Rust `libarno_callout.so` is available on the target container (already deployed in spec 094).
- The 2-hop dedup uses the same predicate for both hops (`[:P*2]`). Mixed-predicate 2-hop is out of scope.
- `^KG("deg2p_exact")` is invalidated by `Kill ^KG("deg2p")` in `Build2HopStats` — both are rebuilt together.
- ObjectScript fallback for `Build2HopExactStats` is acceptable to be slow (minutes on SF10) since it's a build-time operation, not a query-time operation.

## Implementation Notes

### Why Rust for Build2HopExactStats

ObjectScript `Build2HopExactStats` doing full dedup = O(nodes × degree²) = ~238M operations. At 10M ops/sec = 24s in ObjectScript, making total BuildNKG ~43s. Rust can use `HashMap<u32, HashSet<u32>>` for dedup, operating on integer-indexed nodes from `^NKG(-1, sIdx, ...)`. Expected: 5-8s in Rust, total BuildNKG ≤27s.

### Why not HyperLogLog

HLL gives ~89% accuracy on social graphs (union bias). This spec targets exact counts. The `approx_count_distinct` Cypher function covers the approximate path.

### Relationship to existing deg2p

`^KG("deg2p", src, pred)` = sum of neighbor degrees (upper bound, 3.67× overcount).
`^KG("deg2p_exact", src, pred)` = exact distinct count (this spec).
Both are retained: `deg2p` for threshold detection (0.07ms), `deg2p_exact` for exact reporting (0.1ms after this spec).
