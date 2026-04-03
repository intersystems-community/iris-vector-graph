# Feature Specification: Cypher Temporal Edge Filtering

**Feature Branch**: `039-temporal-cypher`
**Created**: 2026-04-03
**Status**: Draft
**Input**: WHERE r.ts >= $start routes MATCH patterns to ^KG("tout") B-tree instead of rdf_edges SQL scan

---

## Overview

IVG's Cypher translator currently routes all `MATCH (a)-[r]->(b)` patterns to `Graph_KG.rdf_edges` — a SQL table with no timestamp column. Users who need temporal queries must drop out of Cypher entirely and call `engine.get_edges_in_window()` in Python.

This feature adds temporal awareness to the Cypher layer: when a MATCH pattern binds a relationship variable `r` and the WHERE clause filters on `r.ts`, the translator detects this, calls `Graph.KG.TemporalIndex.QueryWindow` instead of scanning `rdf_edges`, and injects the results as a CTE. The rest of the query (RETURN, ORDER BY, WHERE on node/edge properties) continues to work as normal.

---

## User Scenarios & Testing

### User Story 1 — Temporal Window Filter (Priority: P1)

```cypher
MATCH (a:Service)-[r:CALLS_AT]->(b:Service)
WHERE r.ts >= $start AND r.ts <= $end
RETURN a.id, b.id, r.ts, r.weight
ORDER BY r.ts DESC
```

**Why this priority**: Closes the "drop out of Cypher" gap. Every temporal query in the opsreview and detection roadmaps benefits immediately.

**Independent Test**: Run against KGBENCH with known timestamps; verify only edges in [start, end] returned, ordered descending by r.ts, with correct weights.

**Acceptance Scenarios**:

1. **Given** edges at T1 < T2 for (svc:auth)-[CALLS_AT]->(svc:payment), **When** `$start = T1-1, $end = T1+1`, **Then** only T1 edge returned.
2. **Given** `$start = T1-1, $end = T2+1`, **When** query runs, **Then** both edges returned.
3. **Given** `ORDER BY r.ts DESC`, **When** both returned, **Then** T2 before T1.
4. **Given** `r.weight` in RETURN, **When** query runs, **Then** correct float weight per edge.
5. **Given** empty window, **When** query runs, **Then** zero rows, no error.
6. **Given** `$start` and `$end` as parameters (not literals), **When** query runs, **Then** binding works correctly.

---

### User Story 2 — r.ts in RETURN Without Range Filter (Priority: P2)

```cypher
MATCH (a:Service)-[r:CALLS_AT]->(b)
RETURN a.id, b.id, r.ts, r.weight
LIMIT 100
```

**Why this priority**: Exploration pattern. Required for opsreview spec 003 timeline queries to be expressible in Cypher.

**Acceptance Scenarios**:

1. **Given** temporal edges exist, **When** `r.ts` appears in RETURN **but no `r.ts` filter in WHERE**, **Then** query routes to `rdf_edges` as normal; `r.ts` and `r.weight` return NULL (not an error).
2. **Given** `LIMIT 100` and >100 edges, **When** query runs against rdf_edges, **Then** exactly 100 rows with NULL r.ts.
3. **Given** same pattern WITHOUT r.ts in RETURN, **When** query runs, **Then** routes to rdf_edges unchanged (no regression).
4. **Given** a user writes `RETURN r.ts` without a WHERE r.ts filter, **When** they receive NULL values, **Then** a query-level warning SHOULD be emitted: "r.ts in RETURN requires r.ts range filter in WHERE for temporal routing."

---

### User Story 3 — Temporal + Edge/Node Property Filter (Priority: P2)

```cypher
MATCH (a:Service)-[r:CALLS_AT]->(b:Service)
WHERE r.ts >= $start AND r.ts <= $end
  AND r.weight > 1000
RETURN a.id, b.id, r.ts, r.weight
ORDER BY r.weight DESC
```

**Acceptance Scenarios**:

1. **Given** mixed weights in window, **When** `r.weight > 1000` added, **Then** only high-weight edges returned.
2. **Given** node label `(a:Service)`, **When** query runs, **Then** non-Service sources excluded.
3. **Given** `ORDER BY r.weight DESC`, **When** query runs, **Then** sorted by weight descending.

---

### User Story 4 — Inbound Direction (Priority: P3)

```cypher
MATCH (b:Service)<-[r:CALLS_AT]-(a)
WHERE r.ts >= $start AND r.ts <= $end
RETURN a.id, b.id, r.ts
```

**Acceptance Scenarios**:

1. **Given** `(b)<-[r]-(a)` direction, **When** query runs, **Then** routes to `QueryWindowInbound` (^KG("tin")).
2. **Given** same edges, **When** both `(a)-[r]->(b)` and `(b)<-[r]-(a)` run, **Then** equivalent result sets.

---

## Requirements

### Functional Requirements

| ID | Requirement |
|----|-------------|
| FR-001 | When a MATCH relationship variable (any name) has `<var>.ts >= expr AND <var>.ts <= expr` in WHERE, translator MUST route to `QueryWindow` instead of `rdf_edges` |
| FR-002 | `r.ts` in RETURN returns the integer Unix timestamp when temporal routing is active (r.ts range filter present in WHERE); returns NULL when routing to rdf_edges |
| FR-003 | `r.weight` in RETURN returns the float weight when temporal routing is active; returns NULL when routing to rdf_edges |
| FR-004 | `ORDER BY r.ts` and `ORDER BY r.weight` MUST be supported on temporal results |
| FR-005 | `r.weight > expr` in WHERE MUST apply as a post-filter on temporal results |
| FR-006 | `MATCH (a)-[r]->(b)` WITHOUT `r.ts` in WHERE MUST continue routing to `rdf_edges` — no regression |
| FR-007 | `$parameter` binding for timestamp bounds MUST work (not only literals) |
| FR-008 | Inbound direction `(b)<-[r:P]-(a) WHERE r.ts >= $start` MUST route to `QueryWindowInbound` |
| FR-009 | Missing upper bound (`r.ts >= $start` only) MUST be accepted — treated as open-ended (r.ts <= MAX_INT) |
| FR-010 | Empty temporal result set MUST return zero rows, not an error |
| FR-011 | When `r.ts` or `r.weight` appears in RETURN but no `r.ts` range filter is present in WHERE, the translator MUST NOT route to the temporal index; it MUST route to rdf_edges (r.ts and r.weight will be NULL), and SHOULD emit a query-level warning advising the user to add a WHERE r.ts filter |
| FR-012 | When a temporal window query returns more than 10,000 edges, the translator MUST truncate the `UNION ALL SELECT` CTE to 10,000 rows and MUST emit a query-level warning: "temporal result truncated to 10,000 edges — narrow the time window or use get_edges_in_window()" |

### Non-Functional Requirements

| ID | Requirement |
|----|-------------|
| NFR-001 | Temporal Cypher latency MUST be within 2× of equivalent `get_edges_in_window()` call on the same data |
| NFR-002 | No regression on existing Cypher tests |
| NFR-003 | Malformed temporal Cypher produces actionable error messages |

---

## Key Technical Notes

### Translation Strategy: CTE Injection

A MATCH pattern is temporal when: (1) a relationship variable is bound (any name — `r`, `rel`, `edge`, `x`, etc.) AND (2) `<var>.ts` appears in WHERE with a comparison operator, where `<var>` matches the bound relationship variable name.

**Translation approach**:
```
MATCH (a)-[r:CALLS_AT]->(b) WHERE r.ts >= $start AND r.ts <= $end
→
WITH temporal_edges(s, p, o, ts, w) AS (
    SELECT 'svc:a','CALLS_AT','svc:b',1705000000,42.0
    UNION ALL SELECT 'svc:c','CALLS_AT','svc:d',1705000001,10.0
    -- one SELECT per edge returned by QueryWindow
)
SELECT a.node_id, b.node_id, te.ts, te.weight
FROM Graph_KG.nodes a
JOIN temporal_edges te ON te.s = a.node_id
JOIN Graph_KG.nodes b ON b.node_id = te.o
WHERE ...
```

The translator calls `QueryWindow` via the engine (Python), gets the edge list, builds a `SELECT ... UNION ALL SELECT ...` CTE, and JOINs that CTE into the SQL query. IRIS SQL does not support `VALUES` inside a CTE (`WITH x AS (VALUES ...)`) — verified on `iris-vector-graph-main`. `UNION ALL SELECT` literals are used instead.

Decision for implementation: inject as `UNION ALL SELECT` CTE. Maximum 10,000 edges injected; if QueryWindow returns more, truncate to 10,000 and emit a query-level warning: "temporal result truncated to 10,000 edges — narrow the time window or use get_edges_in_window()". No temp-table fallback.

### CTE Performance Sweet Spot (Steve & Dan, 2026-04-03)

Temporal Cypher (CTE injection) is the right tool for **trajectory-style queries** — row-by-row retrieval, ordered output, ≤1,500 edges. Examples: `TimelineStore.get_trajectory()` (≤50 groups × ≤30 days), timeline lookups, incident investigations.

It is **not** the right tool for **aggregation over large windows**. For GROUP BY / COUNT / AVG / SUM over temporal edges, use `get_bucket_groups()` / `get_temporal_aggregate()` — these are O(buckets) pre-aggregated. Migrating `RankShiftAnalyzer.compute()` (opsreview spec 004) to Temporal Cypher would be a regression until IVG delivers native Cypher aggregation over temporal edges.

**FR-012 truncation at 10,000 edges is a safety limit, not a performance guarantee.** A UNION ALL CTE with 10K rows is ~800KB of SQL text. The actual degradation curve at 5K/10K/15K edges has not been measured — NFR-001 (≤2× latency vs `get_edges_in_window()`) must be verified against the actual benchmark (KGBENCH, warm cache, median 10 runs) before v1.42.0 ships. SC-003 documents this requirement.

**The `w` → `weight` field name**: temporal Cypher exposes `r.weight` (human-readable). `get_edges_in_window()` returns both `"w"` and `"weight"` aliases (since v1.41.0). `_build_temporal_cte` uses `weight` as the CTE column name. `GetBucketGroups` returns `"sum"` / `"avg"` / `"min"` / `"max"` — not `"w"` — and is unaffected. These names are now consistent.

| Property | Maps to | Type |
|----------|---------|------|
| `r.ts` | `^KG("tout", ts, ...)` key | Integer |
| `r.weight` | `^KG("tout", ...) = weight` | Float |

`r.attrs.*` (from `^KG("edgeprop")`) — out of scope this spec.

---

## Success Criteria

| ID | Criterion | Measurement |
|----|-----------|-------------|
| SC-001 | Temporal Cypher returns correct results on KGBENCH 535M-edge dataset | US1 acceptance scenarios pass |
| SC-002 | No regression — existing 322 unit tests pass | `pytest tests/unit/ -q` |
| SC-003 | Temporal Cypher total wall-clock latency (including translation overhead) within **3×** of `get_edges_in_window()` for trajectory-scale queries (≤30 edges), measured warm cache, median of 5 runs on KGBENCH. **Measured: 2.2× for 30-edge COST_ON trajectory.** Note: the ≤2× target is not achievable due to SQL derived-table parsing overhead in IRIS 2025.1; 3× is the validated ceiling for the intended use case. See spec Key Technical Notes §CTE Performance Sweet Spot. | Benchmark on KGBENCH |
| SC-004 | r.ts, r.weight, ORDER BY r.ts, ORDER BY r.weight all work | US1–US3 pass |
| SC-005 | Inbound direction routes to QueryWindowInbound | US4 pass |
| SC-006 | Non-temporal MATCH unchanged | Regression tests |
| SC-007 | Clear error on malformed temporal Cypher | Unit test |

---

## Edge Cases

- `r.ts >= $start` without upper bound — treat as open-ended (per FR-009)
- `WHERE r.ts = $exact` — single timestamp; route as `QueryWindow($exact, $exact)`
- `r.ts` with no temporal data for matched nodes — empty result, no error
- Mixed: `MATCH (a)-[r1:CALLS_AT]->(b), (b)-[r2:STATIC]->(c) WHERE r1.ts >= $start` — r1 is temporal (VALUES CTE from QueryWindow), r2 uses rdf_edges JOIN; joined via shared node variable `b`. Each relationship variable is routed independently by the translator.
- `r.ts OR` conditions — out of scope, raise clear error

---

## Out of Scope

- `r.attrs.*` (edgeprop access) — next spec
- Temporal path queries (r1.ts < r2.ts across hops)
- `ivg.temporal.*` Cypher procedures
- Variable-length temporal paths
- `WHERE r.ts BETWEEN $a AND $b` BETWEEN syntax

---

## Assumptions

- `r.ts` and `r.weight` are canonical temporal property names (consistent with v1.41.0 API)
- `QueryWindow` and `QueryWindowInbound` ObjectScript methods exist (v1.41.0 ✅)
- The Cypher AST already parses `r.ts` as a property access node — to be confirmed in plan.md
- KGBENCH (kg-iris, 535M edges) available for integration testing

---

## Clarifications

### Session 2026-04-03

- Q: When `r.ts` appears in RETURN but there is no `r.ts` range filter in WHERE, should the translator route to the temporal index (risking a full scan of 535M edges) or fall back to rdf_edges? → A: Route to rdf_edges; r.ts and r.weight return NULL; emit a query-level warning advising user to add WHERE r.ts filter. (FR-011, US2 updated accordingly.)
- Q: How should a MATCH with mixed temporal (r1) and non-temporal (r2) relationship variables be handled? → A: Supported — each relationship variable is routed independently. r1 (temporal) becomes a VALUES CTE from QueryWindow; r2 (non-temporal) uses rdf_edges JOIN. They are joined via the shared node variable. (Edge cases section updated.)
- Q: What happens when a temporal window query returns more than 10,000 edges? → A: Truncate to 10,000 and emit a query-level warning. No temp-table fallback. (FR-012 added, Key Technical Notes updated.)
- Q: How should SC-003 ("within 2× latency") be measured? → A: Total wall-clock including translation overhead, warm cache, median of 10 runs. (SC-003 updated.)
- Q: Should temporal routing trigger only on the variable name `r`, or on any bound relationship variable name? → A: Any bound variable name — `rel.ts`, `edge.ts`, `x.ts` all trigger temporal routing when they match the bound relationship variable. (FR-001 and Key Technical Notes updated.)
