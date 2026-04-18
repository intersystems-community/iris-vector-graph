# Spec 048: Unified Edge Store

**Feature Branch**: `048-unified-edge-store`
**Created**: 2026-04-18
**Status**: Draft

## Overview

Today iris-vector-graph has two first-class edge storage paths that diverged over time:

- **Static edges**: written to `rdf_edges` SQL table; `^KG("out"/"in")` globals are populated lazily by running `BuildKG()` after the fact.
- **Temporal edges**: written directly to `^KG("out"/"in")` and `^KG("tout"/"tin")` globals; no SQL row exists.

This creates three real problems:

1. **Cypher `MATCH (a)-[r]->(b)` is blind to temporal edges** — the translator generates a SQL JOIN on `rdf_edges`. Temporal edges have no SQL row, so they never appear in pattern match results.
2. **`^KG("out")` goes stale after static writes** — `create_edge` only writes SQL. BFS, shortestPath, and PPR read `^KG("out")`, so they see a stale graph until `BuildKG()` is run manually.
3. **No partitioning path** — global-only edge storage has no natural shard key. As graphs reach tens of millions of edges, horizontal partitioning requires that adjacency can be keyed by a partition identifier.

The fix is a **unified write path**: every edge write — static or temporal — goes to `^KG("out"/"in")` synchronously. SQL (`rdf_edges`) becomes a durable append-only persistence layer, not the primary query surface. The Cypher translator's simple MATCH pattern is re-routed from SQL JOINs to `^KG` global iteration. Temporal timestamp data stays in `^KG("tout"/"tin")` as a secondary index on top of the unified adjacency.

## Clarifications

### Session 2026-04-18

- Q: What should `rdf_edges` SQL become? → A: Append-only durable log (WAL semantics) — written on every edge insert for crash recovery and bulk rebuild, never used as primary query path
- Q: Does the Cypher MATCH change affect CREATE/DELETE/MERGE write path? → A: No — write path is a separate concern; this spec only changes READ (MATCH) and WRITE-TO-GLOBALS synchronization
- Q: Partitioning approach? → A: Shard key as first subscript: `^KG("out", shard, s, p, o)` where shard = hash(s) % N. Default shard=0 for single-node deployments (backward compatible). Multi-shard routing is a follow-on feature; the subscript layout change is the prerequisite.

### Council Review 2026-04-18

- Q: Translator strategy for MATCH→globals? → A: **Strategy B (stored proc).** A new `Graph.KG.EdgeScan.MatchEdges(sourceId, predicate, shard)` ClassMethod returns JSON `[{s,p,o,w},...]`. The Cypher translator emits a `CALL` to this proc wrapped in `JSON_TABLE(...)` CTE, same pattern as `kg_BM25` / `kg_IVF`. This avoids rewriting the SQL assembly pipeline — the translator still emits SQL, but the FROM source is a proc-backed CTE instead of a table join. Predicate pushdown (FR-007) is handled by passing the predicate string to the proc, which uses `$Order(^KG("out", 0, s, predicate, o))` directly — no separate codepath, just a parameter.
- Q: FR-007 predicate pushdown complexity? → A: Not complex under Strategy B. `MatchEdges(sourceId, predicate, shard)` receives predicate as a parameter. If predicate is non-empty, the proc does `$Order(^KG("out", 0, s, predicate, o))` (single predicate scan). If empty, it scans all predicates via nested `$Order`. Both paths are in the same 20-line method. No separate spec needed.
- Q: `^NKG` consistency with new subscript layout? → A: `^NKG` is explicitly **out of scope** for this spec. `BuildNKG()` is called by `BuildKG()` and reads `^KG("out",...)` — after this spec, it reads the new layout. `BuildNKG()` MUST be updated to read `^KG("out", 0, s, p, o)` and tested. Added as FR-010.
- Q: P1/P2 as single branch? → A: **Split to two PRs.** PR-A (P1): synchronous writes + EdgeScan proc + translator CTE routing. PR-B (P2): shard subscript migration + BuildKG layout change + ^NKG update. PR-A is merge-ready independently.
- Q: NFR-001 benchmark methodology? → A: Benchmark at 4 tiers: 1K, 100K, 1M, 535M edges (mindwalk production). Compare `MATCH (a)-[r]->(b)` latency (p50/p99) between SQL JOIN and EdgeScan proc. Pass criterion: proc ≤ SQL at all tiers.
- Q: SQL retention policy for BuildKG recovery? → A: `rdf_edges` rows are never deleted by this spec. `BuildKG` can always reconstruct `^KG("out",...)` from `rdf_edges` + `^KG("tout",...)` combined. Statement added to Architecture Notes.
- Q: Merged-graph invariant? → A: Shard = routing key, NOT graph partition. All shards compose a single logical graph. A query against shard=0 sees the entire graph in single-node mode. Added to Architecture Notes.
- Q: BenchSeeder.cls writes old layout? → A: Added to task list (low priority, update ^KG subscript in BenchSeeder to include shard=0).
- Q: Does `MatchEdges` handle unbound-source MATCH patterns? → A: Yes. When `sourceId` is empty, the proc iterates `$Order(^KG("out", 0, s))` across all source nodes (full scan). No conditional fallback to SQL — query path is fully unified.
- Q: Does `bulk_create_edges` also write globals synchronously per-edge? → A: No. Single writes (`create_edge`) go synchronous to globals. Bulk ingest (`bulk_create_edges`) continues to use batch SQL + `BuildKG()` for performance at 535M-edge scale. This preserves load throughput while fixing the stale-after-single-write problem.
- Q: Does `MatchEdges` return node metadata or edges only? → A: Edges only (`{s,p,o,w}`). Node labels/props are fetched by the outer SQL JOINing the CTE output against `rdf_labels`/`rdf_props` tables — same as today but with the FROM source swapped from `rdf_edges` table to the `MatchEdges` CTE. Keeps the proc simple and composable.

## User Scenarios & Testing

### User Story 1 — Temporal edges visible in MATCH (P1)

A mindwalk user writes:
```cypher
MATCH (a {id: 'hla-a*02:01'})-[r]->(b)
RETURN type(r), b.id
```
Today this returns only static edges. After this fix it returns ALL edges — static and temporal — because `^KG("out")` is the query path and both edge types write there.

**Independent Test**: Insert one static edge and one temporal edge from the same source node. Run `MATCH (a)-[r]->(b) RETURN type(r), b.id`. Assert both edges appear in results.

**Acceptance Scenarios**:
1. **Given** a temporal edge `A -[COST_ON]-> B` exists, **When** `MATCH (a {id:'A'})-[r]->(b) RETURN type(r)`, **Then** `COST_ON` appears in results
2. **Given** a static edge `A -[TREATS]-> B` exists and `BuildKG` has NOT been run, **When** `MATCH (a {id:'A'})-[r]->(b) RETURN type(r)`, **Then** `TREATS` appears immediately (no rebuild needed)
3. **Given** BFS `shortestPath((a)-[*..5]-(b))`, **When** only a temporal path connects a to b, **Then** path is found (already works today — regression test)

### User Story 2 — No more BuildKG requirement (P1)

A developer inserts nodes and edges via `create_node` / `create_edge` and immediately runs a BFS/MATCH query — without calling `BuildKG()`. Everything works.

**Independent Test**: Insert edge, immediately BFS from source without BuildKG. Assert neighbor found.

**Acceptance Scenarios**:
1. **Given** `create_edge(A, TREATS, B)` called, **When** `MATCH (a {id:'A'})-[r]->(b)` immediately, **Then** B appears — no BuildKG needed
2. **Given** `bulk_create_edges([...])` called, **When** BFS from any inserted source, **Then** all inserted edges reachable

### User Story 3 — Partition-ready global layout (P2)

The `^KG("out")` global uses a shard-keyed layout: `^KG("out", 0, s, p, o)` for single-node (shard=0). Existing code that reads `^KG("out", s, p, o)` is updated to `^KG("out", 0, s, p, o)`. `BuildKG` writes the new layout.

**Independent Test**: After `BuildKG`, verify `$Order(^KG("out", 0, ...))` returns expected edges. Verify old unsubscripted `^KG("out", s, ...)` no longer exists.

**Acceptance Scenarios**:
1. **Given** single-node deployment (shard=0), **When** BFS runs, **Then** results identical to today
2. **Given** new layout, **When** `BuildKG` is called, **Then** migrates old `^KG("out", s, p, o)` to `^KG("out", 0, s, p, o)`

### User Story 4 — Cypher MATCH reads globals (P2)

Simple `MATCH (a)-[r]->(b)` Cypher generates `$Order(^KG("out", 0, a, p, o))` iteration via ObjectScript stored proc instead of a SQL JOIN on `rdf_edges`.

**Independent Test**: Run `MATCH (a {id:'X'})-[r]->(b) RETURN type(r), b.id` on a graph where `rdf_edges` is empty but `^KG("out")` is populated. Assert results are returned.

**Acceptance Scenarios**:
1. `MATCH (a)-[r]->(b)` returns temporal edges (US1 coverage)
2. `MATCH (a)-[r]->(b) WHERE type(r) = 'TREATS'` filters correctly via predicate scan on `^KG("out", 0, a, "TREATS", o)`
3. `MATCH (a)-[r]->(b) RETURN r.weight` returns edge weight from `^KG("out", 0, a, p, o)` value

### Edge Cases

- `create_edge` called concurrently: `^KG("out")` write uses `LOCK ^KG("out", shard, s)` for process safety (consistent with existing TemporalIndex pattern)
- `delete_edge` / `delete_node` must Kill the corresponding `^KG` entry synchronously
- `rdf_edges` SQL: continues to be written on every edge insert (no change to write volume); never read during query execution
- `BuildKG` remains valid for cold-start recovery from SQL (crash recovery, import from backup)
- Shard=0 default means zero behavior change for single-node; shard assignment function is pluggable

## Requirements

### Functional Requirements

- **FR-001**: `create_edge(s, p, o)` MUST write `^KG("out", 0, s, p, o)` and `^KG("in", 0, o, p, s)` synchronously on every call. `bulk_create_edges` is exempt — it continues to use batch SQL + `BuildKG()` for performance at scale.
- **FR-002**: `create_edge_temporal(s, p, o, ts)` MUST continue writing `^KG("out", 0, s, p, o)` synchronously (already does — verify preserved)
- **FR-003**: `delete_edge` MUST Kill `^KG("out", 0, s, p, o)` and `^KG("in", 0, o, p, s)` synchronously
- **FR-004**: `BuildKG` MUST write new shard-keyed layout `^KG("out", 0, s, p, o)` and migrate any old `^KG("out", s, p, o)` entries
- **FR-005**: All ObjectScript BFS/traversal code (`BFSFast`, `ShortestPathJson`, `PPR`, etc.) MUST be updated to read `^KG("out", 0, s, p, o)`
- **FR-006**: Cypher simple MATCH `(a)-[r]->(b)` MUST route to `Graph.KG.EdgeScan.MatchEdges(sourceId, predicate, shard)` stored proc via `JSON_TABLE(...)` CTE — same pattern as `kg_BM25` / `kg_IVF`. The translator emits SQL with a proc-backed CTE, not a direct table join on rdf_edges. When `sourceId` is empty (unbound source), the proc performs a full `$Order(^KG("out", 0, s))` scan — no fallback to SQL.
- **FR-007**: `MATCH (a)-[r]->(b) WHERE type(r) = 'X'` MUST pass predicate `'X'` as a parameter to `MatchEdges`, which uses `$Order(^KG("out", 0, a, "X", o))` directly. No full scan. Not a separate codepath — same proc, one parameter.
- **FR-008**: `rdf_edges` SQL table continues to receive every edge insert (durability preserved). Rows are never deleted by this spec.
- **FR-009**: Single-node shard key defaults to 0; shard assignment is a pluggable function with signature `shard(node_id) -> int`
- **FR-010**: `BuildNKG()` MUST be updated to read `^KG("out", 0, s, p, o)` instead of `^KG("out", s, p, o)` and tested for consistency with the new layout (PR-B scope)

### Non-Functional Requirements

- **NFR-001**: `MATCH (a)-[r]->(b)` via EdgeScan proc MUST be ≤ SQL JOIN latency at all benchmark tiers: 1K, 100K, 1M, 535M edges (p50 and p99). Methodology: BenchSeeder creates graph at each tier, run 100 random MATCH queries, compare mean latency.
- **NFR-002**: `create_edge` write overhead MUST be < 2x current (one extra global Set per direction)
- **NFR-003**: `BuildKG` migration MUST be idempotent — safe to run multiple times
- **NFR-004**: All existing tests MUST pass after migration (zero regression)
- **NFR-005**: Shard layout MUST support future horizontal partitioning without further global key restructuring
- **NFR-006**: `^NKG` index MUST be consistent with `^KG("out", 0, ...)` layout after `BuildNKG()` runs (PR-B scope)

## Success Criteria

- **SC-001**: Zero calls to `BuildKG()` required in any test or application code after initial schema setup
- **SC-002**: `MATCH (a)-[r]->(b)` returns both static and temporal edges in a single query
- **SC-003**: All 492+ existing passing tests continue to pass
- **SC-004**: A graph expert reviewing the global key schema can identify the shard slot and understand the partitioning strategy without additional documentation

## Out of Scope

- Multi-shard routing (assigning different nodes to different shards, cross-shard BFS) — this spec only establishes the subscript layout
- Migrating `rdf_edges` SQL to a different schema or deleting rows
- Changing `^KG("tout"/"tin")` temporal index structure
- Changing `^KG("tagg")` aggregate structure
- `^NKG` integer-key acceleration: `BuildNKG()` subscript update is IN scope (FR-010, PR-B); full NKG redesign is OUT of scope

## Implementation Scope Split

**PR-A (P1 — merge independently):**
- FR-001: synchronous `^KG("out"/"in")` writes in `create_edge`
- FR-002: verify temporal path preserved
- FR-003: synchronous `delete_edge` kills
- FR-006: `Graph.KG.EdgeScan.MatchEdges` stored proc
- FR-007: predicate pushdown via proc parameter
- FR-008: rdf_edges retention
- US1, US2 acceptance tests

**PR-B (P2 — after PR-A merged):**
- FR-004: `BuildKG` shard layout migration
- FR-005: all traversal code updated to `^KG("out", 0, ...)`
- FR-009: shard function
- FR-010: `BuildNKG()` subscript update
- US3, US4 acceptance tests
- BenchSeeder.cls update (low)

## Architecture Notes

### Global Key Schema (after this spec)

```
^KG("out", shard, s, p, o)      = weight   ← CHANGED: shard subscript added
^KG("in",  shard, o, p, s)      = weight   ← CHANGED
^KG("tout", ts, s, p, o)        = weight   ← UNCHANGED
^KG("tin",  ts, o, p, s)        = weight   ← UNCHANGED
^KG("tagg", bucket, s, p, ...)  = agg      ← UNCHANGED
^KG("edgeprop", ts, s, p, o, k) = val      ← UNCHANGED
^KG("deg", s)                   = count    ← UNCHANGED
^KG("label", label, s)          = ""       ← UNCHANGED
^KG("bucket", bucket, s)        = count    ← UNCHANGED
```

### Write Path (after this spec)

```
create_edge(s, p, o)
  → INSERT rdf_edges (durability)
  → Set ^KG("out", 0, s, p, o) = weight   ← NEW
  → Set ^KG("in",  0, o, p, s) = weight   ← NEW

create_edge_temporal(s, p, o, ts)
  → TemporalIndex.InsertEdge(...)          ← already writes ^KG("out", 0, s, p, o)
  → verify shard slot is correct
```

### Read Path (after this spec)

```
MATCH (a)-[r]->(b)
  → ObjectScript: $Order(^KG("out", 0, a, p, o))   ← NEW (replaces SQL JOIN)

MATCH (a)-[*..N]->(b)
  → BFSFast: $Order(^KG("out", 0, a, p, o))         ← updated subscript

shortestPath((a)-[*..N]-(b))
  → ShortestPathJson: $Order(^KG("out", 0, ...))     ← updated subscript

MATCH (a)-[r]->(b) WHERE ts BETWEEN x AND y
  → TemporalIndex: $Order(^KG("tout", ts, ...))      ← UNCHANGED
```

### Partitioning Path (future, enabled by this spec)

```
shard(node_id) = hash(node_id) % num_shards

^KG("out", shard(s), s, p, o)   ← all edges from s live on shard(s)
^KG("in",  shard(o), o, p, s)   ← all in-edges to o live on shard(o)

Cross-shard BFS: each hop may change shard → routed to correct IRIS namespace
```

### Merged-Graph Invariant

Shard is a **routing key**, not a graph partition. All shards compose a single logical graph. In single-node mode (shard=0), a query against shard=0 sees the entire graph — there is no data on other shards. In multi-shard mode (future), a query MUST fan out to all shards to produce a complete result. No shard is an island.

### SQL Retention Policy

`rdf_edges` rows are never deleted by any operation in this spec. `BuildKG()` can always reconstruct `^KG("out", 0, ...)` from the union of `rdf_edges` (static edges) and `^KG("tout", ...)` (temporal edges). This is the crash recovery path: if `^KG` globals are lost (database restore from backup without journal), `BuildKG()` + temporal data in `^KG("tout")` fully reconstructs the adjacency index.

### Translator Strategy: Stored Proc CTE (Strategy B)

The Cypher translator does NOT rewrite its SQL assembly pipeline. Instead, for `MATCH (a)-[r]->(b)`, it replaces the `FROM rdf_edges` table reference with a proc-backed CTE:

```sql
WITH EdgeScan AS (
  SELECT j.s, j.p, j.o, j.w
  FROM JSON_TABLE(
    Graph_KG.MatchEdges(:sourceId, :predicate, 0),
    '$[*]' COLUMNS(s VARCHAR(256) PATH '$.s', p VARCHAR(256) PATH '$.p',
                    o VARCHAR(256) PATH '$.o', w DOUBLE PATH '$.w')
  ) j
)
SELECT ... FROM EdgeScan e ...
```

This is the same pattern used for `kg_BM25`, `kg_IVF`, and `kg_PPR` stored procs. The translator already knows how to emit these CTEs. The only new work is: (1) the `MatchEdges` ObjectScript proc (edge-only: returns `{s,p,o,w}` tuples, no node metadata), and (2) teaching the translator when to emit this CTE instead of a table join. Node labels, properties, and other metadata are resolved by the outer SQL JOINing the CTE's `o` column against `rdf_labels`/`rdf_props` — the same JOINs used today, just with the FROM source swapped.
