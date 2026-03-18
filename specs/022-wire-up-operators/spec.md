# Feature Specification: Wire Up Existing Operators

**Feature Branch**: `022-wire-up-operators`  
**Created**: 2026-03-18  
**Status**: Complete (v1.10.2 released to PyPI)  
**Depends on**: 021-deploy-cls-layer (provides .cls deployment + ^KG bootstrap)  
**Input**: Audit of 6 gaps between iris-vector-graph library code and actual runtime behavior

---

## Problem Statement

The iris-vector-graph library contains all components for a full vector-to-graph retrieval pipeline, but they are not wired together. The README promises "combines graph traversal, HNSW vector similarity, and lexical search" and showcases `kg_VECTOR_GRAPH_SEARCH`, but in practice every advanced operation silently falls back to slow Python paths or returns no results.

### Gap Summary

| # | What's Broken | Impact |
|---|---------------|--------|
| 1 | `kg_GRAPH_WALK()` accesses `^KG` global with wrong subscript structure — always finds nothing | Graph traversal never uses the fast `^KG` path; always falls back to SQL |
| 2 | `IRISGraphOperators` has no `kg_PPR()` method | Personalized PageRank unreachable from the Python API |
| 3 | `kg_PPR` SQL function not in `get_procedures_sql_list()` | PPR never auto-installed; SQL callers cannot use it |
| 4 | `kg_PPR` SQL function calls wrong class (`PageRankEmbedded`) | Type mismatch between `%DynamicArray` return and `VARCHAR` signature |
| 5 | `kg_VECTOR_GRAPH_SEARCH()` calls a `Vector_Graph_Search` TVF that doesn't exist | Flagship hybrid search always falls back to sequential Python |
| 6 | `iris.vector.graph.GraphOperators.cls` hardcodes `SQLUSER` schema | SqlProc methods query wrong tables when data lives in `Graph_KG` |
| 7 | `kg_KNN_VEC` HNSW path queries `kg_NodeEmbeddings_optimized` but data lives in `kg_NodeEmbeddings` | 850x performance regression (42s vs 50ms on 143K vectors) — falls back to brute-force Python CSV |
| 8 | `kg_KNN_VEC` HNSW path uses `TO_VECTOR(?)` without DOUBLE specifier | FLOAT/DOUBLE mismatch causes -259 error, forcing Python fallback |

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Graph Walk Uses ^KG Fast Path (Priority: P0)

As a developer calling `ops.kg_GRAPH_WALK("ENTITY:X", max_depth=2)`, when `^KG` globals are populated, the traversal MUST use the `^KG("out", entity, predicate, object)` global structure and return correct results — not silently find nothing due to a subscript bug and fall back to SQL.

**Why this priority**: This is a correctness bug. The `^KG` fast path exists, is populated by `BuildKG()`, but the Python code accesses the global at the wrong subscript level. Every `kg_GRAPH_WALK` call pays the SQL penalty unnecessarily.

**Independent Test**: Insert a test graph (A->B->C chain), call `BuildKG()`, then call `kg_GRAPH_WALK("A", max_depth=2)`. Assert results contain both hops. Then mock the SQL fallback to raise an exception and call again — assert the `^KG` path alone succeeds.

**Acceptance Scenarios**:

1. **Given** a graph with edges A->B->C and `^KG` populated via `BuildKG()`, **When** `kg_GRAPH_WALK("A", max_depth=2)` is called, **Then** results include both (A,rel,B) at depth 1 and (B,rel,C) at depth 2.
2. **Given** `^KG` globals are populated, **When** the SQL fallback path is disabled (mocked to raise), **Then** `kg_GRAPH_WALK` still returns correct results via the `^KG` path alone.
3. **Given** `^KG` globals are NOT populated, **When** `kg_GRAPH_WALK` is called, **Then** it gracefully falls back to SQL and returns correct results.
4. **Given** a 1000+ edge graph with `^KG` populated, **When** `kg_GRAPH_WALK` is called via `^KG` path, **Then** it completes measurably faster than the SQL fallback (verified via timing comparison in e2e test).

---

### User Story 2 — PPR Available on Python Operators API (Priority: P1)

As a developer, I want to call `ops.kg_PPR(seed_entities=["ENTITY:A"], damping=0.85)` and get back Personalized PageRank scores as a list of `(node_id, score)` tuples, so that I can rank nodes by graph proximity to seed entities.

**Why this priority**: PPR is a core graph algorithm used in RAG pipelines. It exists as ObjectScript but is completely unreachable from the Python API that consumers actually use.

**Independent Test**: Build a 5-node star graph (hub connected to 4 spokes), call `kg_PPR` with one spoke as seed, verify the hub gets the highest score. Verify output format matches `List[Tuple[str, float]]`.

**Acceptance Scenarios**:

1. **Given** a star graph (H->S1, H->S2, H->S3, H->S4) with `^KG` populated, **When** `kg_PPR(seed_entities=["S1"], damping=0.85)` is called, **Then** node H appears in results with a score higher than S2/S3/S4.
2. **Given** ObjectScript classes are deployed and `^KG` is populated, **When** `kg_PPR` is called, **Then** it uses the native `Graph.KG.PageRank.RunJson()` path (not the SQL fallback).
3. **Given** ObjectScript classes are NOT deployed, **When** `kg_PPR` is called, **Then** it falls back gracefully and returns results (possibly via `PageRankEmbedded` SQL path or Python implementation).
4. **Given** an empty seed list, **When** `kg_PPR([])` is called, **Then** it returns an empty list without error.
5. **Given** a 1000-node graph, **When** `kg_PPR` is called, **Then** it completes in under 100ms.

---

### User Story 3 — kg_PPR SQL Function Auto-Installed (Priority: P1)

As a developer, after calling `initialize_schema()`, `SELECT Graph_KG.kg_PPR(...)` MUST work as a SQL function returning JSON-formatted PPR scores, so that SQL-only consumers can access graph ranking.

**Why this priority**: The SQL function exists in `sql/operators.sql` but is never installed by the library. It also calls the wrong ObjectScript class.

**Independent Test**: Call `initialize_schema()` on a clean IRIS instance, insert test data, then execute `SELECT Graph_KG.kg_PPR('["ENTITY:A"]', 0.85, 20, 0, 1.0)` via raw SQL. Parse the JSON result and verify it contains expected node IDs.

**Acceptance Scenarios**:

1. **Given** a freshly initialized schema, **When** `SELECT Graph_KG.kg_PPR('["A"]', 0.85, 20, 0, 1.0)` is called, **Then** it returns a valid JSON string parseable by `json.loads()`.
2. **Given** the kg_PPR function is installed, **When** called with seed entities that exist in the graph, **Then** the JSON result contains `[{"id": "...", "score": 0.xx}, ...]` sorted by score descending.
3. **Given** `initialize_schema()` is called twice, **Then** the kg_PPR function is not duplicated or corrupted (idempotent).
4. **Given** the kg_PPR function, **When** called, **Then** it invokes `Graph.KG.PageRank.RunJson()` (not `PageRankEmbedded.ComputePageRank()`).

---

### User Story 4 — Vector-Graph Search Works End-to-End (Priority: P2)

As a developer, `ops.kg_VECTOR_GRAPH_SEARCH(query_vector, k_vector=10, expansion_depth=2)` MUST return results that include both vector-similar nodes AND their graph neighbors, proving that vector seeding and graph expansion both execute.

**Why this priority**: This is the flagship feature advertised in the README. Currently it always falls back to sequential Python because the TVF doesn't exist. The Python fallback does work — this story ensures it works correctly end-to-end.

**Independent Test**: Insert 10 nodes with embeddings and graph edges between them. Call `kg_VECTOR_GRAPH_SEARCH` with a query vector similar to node A. Verify results include node A (vector hit) AND node B (graph neighbor of A, not directly vector-similar). Compare results against manual `kg_KNN_VEC` + `kg_GRAPH_WALK` to verify correctness.

**Acceptance Scenarios**:

1. **Given** nodes with embeddings and graph edges, **When** `kg_VECTOR_GRAPH_SEARCH(query_vector, k_vector=5, expansion_depth=2)` is called, **Then** results include nodes found by vector similarity AND nodes found by graph expansion (verified by checking for nodes not in the vector top-k but connected to them).
2. **Given** the same setup, **When** results are compared to manual `kg_KNN_VEC` + `kg_NEIGHBORHOOD_EXPANSION`, **Then** the combined scores are consistent (within floating-point tolerance).
3. **Given** no embeddings exist, **When** `kg_VECTOR_GRAPH_SEARCH` is called, **Then** it returns an empty list without error.

---

### User Story 5 — GraphOperators.cls Uses Correct Schema (Priority: P2)

As a developer, the ObjectScript `iris.vector.graph.GraphOperators` SqlProc methods MUST reference `Graph_KG` schema tables, not `SQLUSER`, so that the SQL procedures work correctly when data lives in the `Graph_KG` schema.

**Why this priority**: The SqlProc methods are deployed and callable, but they query the wrong schema — returning empty results or errors when `SQLUSER` views don't exist.

**Independent Test**: After ObjectScript deployment, call the `kg_KNN_VEC` SqlProc via SQL using `Graph_KG` tables. Verify it queries `Graph_KG.kg_NodeEmbeddings_optimized`, not `SQLUSER.kg_NodeEmbeddings_optimized`.

**Acceptance Scenarios**:

1. **Given** data in `Graph_KG.kg_NodeEmbeddings_optimized`, **When** the ObjectScript `kgKNNVEC` SqlProc is called, **Then** it returns results from `Graph_KG` tables.
2. **Given** no `SQLUSER` views exist, **When** the SqlProc methods are called, **Then** they still work (proving they don't depend on `SQLUSER`).

---

### User Story 6 — kg_KNN_VEC Queries Correct Table with Correct Types (Priority: P0)

As a developer, `ops.kg_KNN_VEC(query_vector, k=10)` MUST query `kg_NodeEmbeddings` (where data actually lives) using the correct DOUBLE vector type, not query `kg_NodeEmbeddings_optimized` (which may not exist or have FLOAT/DOUBLE mismatch) and fall back to 42-second brute-force Python CSV computation.

**Why this priority**: This is the most impactful production bug. On the 143K-node Mindwalk dataset, the HNSW path fails with a -259 datatype mismatch, forcing a brute-force cosine over CSV strings that takes 42,728ms vs 50ms with correct HNSW SQL — an 850x performance regression.

**Independent Test**: Insert embeddings into `kg_NodeEmbeddings` (DOUBLE vectors), call `kg_KNN_VEC`. Assert HNSW path succeeds without falling back to Python CSV. Assert latency < 500ms on 1K+ embeddings.

**Acceptance Scenarios**:

1. **Given** 1000+ embeddings in `Graph_KG.kg_NodeEmbeddings` with `VECTOR(DOUBLE, N)`, **When** `kg_KNN_VEC(query_vector, k=10)` is called, **Then** the HNSW path succeeds (no fallback warning logged).
2. **Given** the HNSW path succeeds, **When** timed, **Then** latency is under 500ms (not 42 seconds).
3. **Given** `kg_NodeEmbeddings_optimized` does NOT exist, **When** `kg_KNN_VEC` is called, **Then** it still works by querying `kg_NodeEmbeddings` directly.
4. **Given** a query vector as JSON string, **When** `TO_VECTOR(?, DOUBLE)` is used in the SQL, **Then** no -259 vector datatype mismatch error occurs.

---

### Edge Cases

- `kg_GRAPH_WALK` called with an entity that has no outgoing edges in `^KG` — should return empty list, not error
- `kg_PPR` called with seed entities not in the graph — should return empty list
- `kg_PPR` called with very large damping factor (0.99) — should still converge
- `kg_VECTOR_GRAPH_SEARCH` called with `expansion_depth=0` — should return only vector results, no graph expansion
- `kg_GRAPH_WALK` with `predicate_filter` that matches no edges — should return empty list
- `initialize_schema()` called when `kg_PPR` function already exists with old definition — `CREATE OR REPLACE` should update it
- `^KG` global populated but ObjectScript classes not deployed — Python operators should still use `iris.gref("^KG")` directly

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `kg_GRAPH_WALK()` in `operators.py` MUST access `^KG` globals using the correct subscript structure: `^KG("out", entity, predicate)` for predicate iteration and `^KG("out", entity, predicate, object)` for object iteration.
- **FR-002**: `IRISGraphOperators` MUST expose a `kg_PPR()` method that accepts `seed_entities: List[str]`, `damping: float`, `max_iterations: int`, and optional `bidirectional: bool` and `reverse_weight: float` parameters. It MUST return `List[Tuple[str, float]]` sorted by score descending.
- **FR-003**: `kg_PPR()` MUST attempt the native ObjectScript path first (`Graph.KG.PageRank.RunJson()`) and fall back to a SQL/Python path when ObjectScript is unavailable.
- **FR-004**: `GraphSchema.get_procedures_sql_list()` MUST include a `kg_PPR` SQL function that calls `Graph.KG.PageRank.RunJson()` and returns `VARCHAR(8000)`.
- **FR-005**: The `kg_PPR` SQL function MUST call `Graph.KG.PageRank.RunJson()`, NOT `PageRankEmbedded.ComputePageRank()`.
- **FR-006**: `iris.vector.graph.GraphOperators.cls` MUST reference `Graph_KG` schema in all SQL queries, not `SQLUSER`.
- **FR-007**: `kg_VECTOR_GRAPH_SEARCH()` MUST work end-to-end, returning results that include both vector seeds and graph-expanded neighbors.
- **FR-008**: All changes MUST be idempotent — calling `initialize_schema()` multiple times MUST NOT cause errors.
- **FR-009**: All changes MUST be backward-compatible — existing callers MUST NOT break.
- **FR-010**: `kg_KNN_VEC()` HNSW path MUST query `Graph_KG.kg_NodeEmbeddings` (the canonical table where data lives), NOT `Graph_KG.kg_NodeEmbeddings_optimized`. The `_optimized` table is a migration artifact that may not exist on all deployments.
- **FR-011**: `kg_KNN_VEC()` HNSW path MUST use `TO_VECTOR(?, DOUBLE)` (with explicit DOUBLE specifier) to match the `VECTOR(DOUBLE, N)` column type in `kg_NodeEmbeddings`. Using bare `TO_VECTOR(?)` causes a -259 datatype mismatch on IRIS.

### Key Entities

- **`IRISGraphOperators`**: Python API class exposing all graph retrieval operators. Gets new `kg_PPR()` method.
- **`GraphSchema`**: Schema management class. `get_procedures_sql_list()` gains `kg_PPR` function.
- **`^KG` global**: IRIS global storing adjacency lists as `^KG("out", s, p, o)`, `^KG("in", o, p, s)`, `^KG("deg", s)`.
- **`Graph.KG.PageRank`**: ObjectScript class with `RunJson()` method returning PPR results as JSON string.
- **`iris.vector.graph.GraphOperators`**: ObjectScript class with SqlProc methods for server-side SQL functions.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `kg_GRAPH_WALK` with populated `^KG` returns correct results without falling back to SQL — proven by e2e test where SQL fallback is disabled and traversal still succeeds.
- **SC-002**: `kg_PPR(seed_entities=["hub_node"])` returns the expected hub node with highest score on a star graph — proven by e2e test with known topology.
- **SC-003**: `SELECT Graph_KG.kg_PPR(...)` returns valid JSON after `initialize_schema()` — proven by e2e test executing raw SQL and parsing JSON result.
- **SC-004**: `kg_VECTOR_GRAPH_SEARCH` returns nodes that are graph neighbors of vector seeds (not just vector-similar) — proven by e2e test checking for expansion.
- **SC-005**: `GraphOperators.cls` SqlProc methods work against `Graph_KG` schema — proven by e2e test after deployment.
- **SC-006**: All existing tests pass (`pytest` green) — no regressions.
- **SC-007**: Performance: `kg_GRAPH_WALK` via `^KG` measurably faster than SQL, `kg_PPR` completes in under 100ms on 1K nodes — proven by timed e2e assertions.
- **SC-008**: `kg_KNN_VEC` HNSW path succeeds without fallback on `kg_NodeEmbeddings` (DOUBLE vectors) — proven by e2e test asserting no fallback warning and latency under 500ms on 1K+ embeddings.

---

## Out of Scope

- Replacing the Python fallback paths entirely — they remain as safety nets
- Implementing a true server-side `Vector_Graph_Search` TVF in ObjectScript — the Python orchestration fallback is acceptable
- Schema migrations for existing databases — `CREATE OR REPLACE` handles procedure updates
- Changes to `Graph.KG.Edge` table backing or functional index wiring
- REST/GraphQL layer changes

---

## Assumptions

- Spec 021 (`deploy-cls-layer`) has been implemented: `.cls` files can be deployed, `BuildKG()` can be called, `^KG` globals can be populated, `IRISCapabilities` flags work.
- The test infrastructure from `tests/conftest.py` (container lifecycle, password reset, schema setup) works correctly.
- `Graph.KG.PageRank.RunJson()` method exists and works correctly (verified by reading the .cls file).
- `Graph.KG.Traversal.BFSFastJson()` method exists and works correctly (verified by reading the .cls file).
- IRIS Community 2025.1+ supports `CREATE OR REPLACE FUNCTION` with `LANGUAGE OBJECTSCRIPT`.
