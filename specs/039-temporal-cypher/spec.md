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

1. **Given** temporal edges exist, **When** no r.ts WHERE filter, **Then** edges returned with timestamp and weight.
2. **Given** `LIMIT 100` and >100 edges, **When** query runs, **Then** exactly 100 rows.
3. **Given** same pattern WITHOUT r.ts in RETURN, **When** query runs, **Then** routes to rdf_edges as before (no regression).

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
| FR-001 | When a MATCH relationship variable `r` has `r.ts >= expr AND r.ts <= expr` in WHERE, translator MUST route to `QueryWindow` instead of `rdf_edges` |
| FR-002 | `r.ts` in RETURN MUST return the integer Unix timestamp |
| FR-003 | `r.weight` in RETURN MUST return the float weight |
| FR-004 | `ORDER BY r.ts` and `ORDER BY r.weight` MUST be supported on temporal results |
| FR-005 | `r.weight > expr` in WHERE MUST apply as a post-filter on temporal results |
| FR-006 | `MATCH (a)-[r]->(b)` WITHOUT `r.ts` in WHERE MUST continue routing to `rdf_edges` — no regression |
| FR-007 | `$parameter` binding for timestamp bounds MUST work (not only literals) |
| FR-008 | Inbound direction `(b)<-[r:P]-(a) WHERE r.ts >= $start` MUST route to `QueryWindowInbound` |
| FR-009 | Missing upper bound (`r.ts >= $start` only) MUST be accepted — treated as open-ended (r.ts <= MAX_INT) |
| FR-010 | Empty temporal result set MUST return zero rows, not an error |

### Non-Functional Requirements

| ID | Requirement |
|----|-------------|
| NFR-001 | Temporal Cypher latency MUST be within 2× of equivalent `get_edges_in_window()` call on the same data |
| NFR-002 | No regression on existing Cypher tests |
| NFR-003 | Malformed temporal Cypher produces actionable error messages |

---

## Key Technical Notes

### Translation Strategy: CTE Injection

A MATCH pattern is temporal when: (1) a relationship variable is bound AND (2) `r.ts` appears in WHERE or RETURN.

**Translation approach**:
```
MATCH (a)-[r:CALLS_AT]->(b) WHERE r.ts >= $start AND r.ts <= $end
→
WITH temporal_edges AS (
    SELECT * FROM TABLE(Graph_KG.QueryWindow_TV(?, ?, ?, ?))
    -- OR: call QueryWindow classmethod, inject result as CTE
)
SELECT a.node_id, b.node_id, te.ts, te.weight
FROM Graph_KG.nodes a
JOIN temporal_edges te ON te.s = a.node_id
JOIN Graph_KG.nodes b ON b.node_id = te.o
WHERE ...
```

The alternative (call `QueryWindow` in Python, inject results as literal SQL) is simpler and avoids table-valued function complexity. The translator detects temporal pattern, calls `QueryWindow` via the engine, gets the edge list, and either:
- (A) Injects as a `VALUES (...)` CTE — avoids stored procedure complexity
- (B) Creates a temp table — heavier but supports large result sets

Decision for implementation: start with (A) VALUES injection for result sets ≤10K edges; fall back to (B) for larger. Document in plan.md.

### Relationship Properties on Temporal Edges

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
| SC-003 | Temporal Cypher within 2× latency of `get_edges_in_window()` | Benchmark on KGBENCH |
| SC-004 | r.ts, r.weight, ORDER BY r.ts, ORDER BY r.weight all work | US1–US3 pass |
| SC-005 | Inbound direction routes to QueryWindowInbound | US4 pass |
| SC-006 | Non-temporal MATCH unchanged | Regression tests |
| SC-007 | Clear error on malformed temporal Cypher | Unit test |

---

## Edge Cases

- `r.ts >= $start` without upper bound — treat as open-ended (per FR-009)
- `WHERE r.ts = $exact` — single timestamp; route as `QueryWindow($exact, $exact)`
- `r.ts` with no temporal data for matched nodes — empty result, no error
- Mixed: `MATCH (a)-[r1:CALLS_AT]->(b), (b)-[r2:STATIC]->(c) WHERE r1.ts >= $start` — r1 temporal, r2 uses rdf_edges
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
