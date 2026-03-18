# Implementation Plan: Wire Up Existing Operators

**Branch**: `022-wire-up-operators` | **Date**: 2026-03-18 | **Spec**: [spec.md](spec.md)

---

## Summary

Fix 6 gaps between the iris-vector-graph library's existing code and its actual runtime behavior. The library has all components — they're just not wired together. This plan covers: fixing a `^KG` subscript bug, adding `kg_PPR()` to the Python API, auto-installing the `kg_PPR` SQL function, fixing GraphOperators.cls schema references, and ensuring `kg_VECTOR_GRAPH_SEARCH` works end-to-end.

---

## Technical Context

**Language/Version**: Python 3.11 + ObjectScript (IRIS 2025.1+)  
**Primary Dependencies**: `intersystems-irispython`, `iris-devtester` (test only)  
**Storage**: InterSystems IRIS — `Graph_KG` schema (data), `^KG` global (adjacency index)  
**Testing**: `pytest`; unit tests with `unittest.mock`; e2e tests via `iris-devtester` container  
**Container**: `iris-vector-graph-main` (from `tests/conftest.py`)  
**Constraints**: Backward-compatible; all existing tests must pass; no new dependencies

---

## Constitution Check

**Principle I (Library-First)**: All changes within `iris_vector_graph/` and `iris_src/`.  
**Principle II (Compatibility-First)**: New `kg_PPR()` method is additive. All existing method signatures unchanged.  
**Principle III (Test-First)**: Tests written BEFORE implementation for each phase.  
**Principle IV (Integration & E2E Testing)**: Comprehensive e2e tests against live IRIS container. No hardcoded ports. `SKIP_IRIS_TESTS` defaults to `"false"`.  
**Principle V (Simplicity)**: Surgical fixes to existing code. No new abstractions beyond what's needed.  
**Principle VI (Grounding)**: All container names, table names, global structures verified against authoritative sources in the repo.

---

## Phase 1 — Tests First: Write All Test Files (RED phase)

Write all test files before any implementation. Tests MUST fail initially.

### Unit Tests: `tests/unit/test_operators_wiring.py`

Tests that don't require a live IRIS connection:

- `test_kg_graph_walk_uses_correct_kg_subscripts` — Verify the code uses `["out", entity, p]` not `[entity, p]` (inspect source or mock iris.gref)
- `test_kg_ppr_method_exists` — `hasattr(IRISGraphOperators, 'kg_PPR')` is True
- `test_kg_ppr_returns_list_of_tuples` — Mock native API, verify return format
- `test_kg_ppr_empty_seeds_returns_empty` — `kg_PPR([])` returns `[]`
- `test_kg_ppr_falls_back_when_objectscript_unavailable` — Mock native API to raise, verify fallback runs
- `test_get_procedures_sql_list_includes_kg_ppr` — Assert `kg_PPR` appears in the SQL list
- `test_kg_ppr_sql_calls_runjon_not_pagerank_embedded` — Assert SQL body contains `Graph.KG.PageRank` not `PageRankEmbedded`

### E2E Tests: `tests/e2e/test_operator_wiring_e2e.py`

Tests that run against a live IRIS container with real data:

- `test_kg_graph_walk_kg_path_returns_results` — Insert edges, BuildKG, call kg_GRAPH_WALK, assert results
- `test_kg_graph_walk_kg_path_only` — Same but mock SQL fallback to raise, assert ^KG path alone works
- `test_kg_graph_walk_sql_fallback` — Don't build ^KG, verify SQL fallback works
- `test_kg_graph_walk_performance_kg_vs_sql` — Time both paths, assert ^KG is faster
- `test_kg_ppr_star_graph` — Build star graph, run PPR, verify hub gets highest score
- `test_kg_ppr_via_sql_function` — After initialize_schema(), call `SELECT Graph_KG.kg_PPR(...)`, parse JSON
- `test_kg_ppr_performance` — Assert < 100ms on 1K-node graph
- `test_vector_graph_search_returns_expanded_nodes` — Insert nodes with embeddings + edges, verify expansion
- `test_vector_graph_search_empty_db` — Verify empty result on empty database
- `test_graphoperators_cls_uses_graph_kg_schema` — After deployment, verify SqlProc queries Graph_KG tables
- `test_initialize_schema_idempotent` — Call twice, no errors

### E2E Test Data Fixtures

Each e2e test creates its own test data with a unique prefix (using `clean_test_data` fixture from conftest.py) and cleans up after. Standard fixture patterns:

- **Chain graph**: A->B->C (for graph walk tests)
- **Star graph**: Hub->S1, Hub->S2, Hub->S3, Hub->S4 (for PPR tests)
- **Embedding graph**: 10 nodes with random 768-dim embeddings + edges (for vector-graph search tests)

---

## Phase 2 — Fix ^KG Subscript Bug (FR-001, US1)

**File**: `iris_vector_graph/operators.py` lines 392-418

**Change**: In `kg_GRAPH_WALK()`, the `^KG` global access code uses:
```python
p = kg_global.order([current_entity, p])          # WRONG
t = kg_global.order([current_entity, p, t])        # WRONG
```
Must be changed to:
```python
p = kg_global.order(["out", current_entity, p])    # CORRECT
t = kg_global.order(["out", current_entity, p, t]) # CORRECT
```

This matches the `^KG` structure built by `Graph.KG.Traversal.BuildKG()` which stores edges as `^KG("out", s, p, o) = weight`.

**Verification**: Run `test_kg_graph_walk_kg_path_returns_results` — should now pass (was RED).

---

## Phase 3 — Add kg_PPR() to IRISGraphOperators (FR-002, FR-003, US2)

**File**: `iris_vector_graph/operators.py`

Add new method `kg_PPR()` to `IRISGraphOperators` class:

1. **Primary path**: Try `_call_classmethod(conn, 'Graph.KG.PageRank', 'RunJson', seed_json, alpha, max_iter, bidir, rev_weight)` — this returns a JSON string `[{"id":"X","score":0.1},...]`.
2. **Parse JSON** into `List[Tuple[str, float]]`.
3. **Fallback**: If native API call fails, execute `PageRankEmbedded` SQL approach or return empty list with warning.

Import `_call_classmethod` from `schema.py` (already exists there, may need to make it importable).

**Signature**:
```python
def kg_PPR(self, seed_entities: List[str], damping: float = 0.85,
           max_iterations: int = 20, bidirectional: bool = False,
           reverse_weight: float = 1.0) -> List[Tuple[str, float]]:
```

**Verification**: Run `test_kg_ppr_star_graph` — should now pass.

---

## Phase 4 — Add kg_PPR to get_procedures_sql_list() (FR-004, FR-005, US3)

**File**: `iris_vector_graph/schema.py`

Add kg_PPR SQL function to the list returned by `get_procedures_sql_list()`:

```sql
CREATE OR REPLACE FUNCTION {table_schema}.kg_PPR(
  seedEntities VARCHAR(32000),
  dampingFactor DOUBLE DEFAULT 0.85,
  maxIterations INT DEFAULT 100,
  bidirectional INT DEFAULT 0,
  reverseEdgeWeight DOUBLE DEFAULT 1.0
)
RETURNS VARCHAR(8000)
LANGUAGE OBJECTSCRIPT
{
    set result = ##class(Graph.KG.PageRank).RunJson(
        seedEntities, dampingFactor, maxIterations, bidirectional, reverseEdgeWeight)
    quit result
}
```

**Key difference from old `sql/operators.sql`**: Calls `Graph.KG.PageRank.RunJson()` (returns JSON string directly) instead of `PageRankEmbedded.ComputePageRank()` (returns `%DynamicArray`).

**Verification**: Run `test_kg_ppr_via_sql_function` — should now pass.

---

## Phase 5 — Fix GraphOperators.cls Schema References (FR-006, US5)

**File**: `iris_src/src/iris/vector/graph/GraphOperators.cls`

Replace all `SQLUSER.` references with `Graph_KG.`:
- `SQLUSER.rdf_edges` -> `Graph_KG.rdf_edges`
- `SQLUSER.rdf_labels` -> `Graph_KG.rdf_labels`
- `SQLUSER.kg_NodeEmbeddings_optimized` -> `Graph_KG.kg_NodeEmbeddings_optimized`

**Verification**: Run `test_graphoperators_cls_uses_graph_kg_schema` — should now pass.

---

## Phase 6 — Update sql/operators.sql kg_PPR (FR-005)

**File**: `sql/operators.sql`

Update the `kg_PPR` function definition to call `Graph.KG.PageRank.RunJson()` instead of `PageRankEmbedded.ComputePageRank()`. This file is not auto-installed but serves as documentation and manual install reference.

**Verification**: Run `test_kg_ppr_sql_calls_runjon_not_pagerank_embedded` — should now pass.

---

## Phase 7 — Verify kg_VECTOR_GRAPH_SEARCH End-to-End (FR-007, US4)

No code changes needed — the Python fallback in `_vector_graph_search_fallback()` already works. The ^KG subscript fix (Phase 2) means `kg_GRAPH_WALK` now works correctly, so the fallback chain (vector search -> neighborhood expansion -> combine) should produce correct results.

**Verification**: Run `test_vector_graph_search_returns_expanded_nodes` — should pass with existing code + Phase 2 fix.

If this test fails, debug the fallback path and fix as needed.

---

## Phase 8 — Performance Verification with EXPLAIN

Run performance comparison tests with timing:

1. `kg_GRAPH_WALK` via `^KG` vs via SQL on 1K+ edge graph — assert `^KG` path is faster
2. `kg_PPR` on 1K-node graph — assert < 100ms
3. `kg_KNN_VEC` on graph with embeddings — verify HNSW is used (check query plan with EXPLAIN)

**Verification**: Run `test_kg_graph_walk_performance_kg_vs_sql` and `test_kg_ppr_performance`.

---

## Phase 9 — Final Validation

1. Run full test suite: `pytest` — all tests green
2. Run `ruff check .` — no lint errors
3. Verify `from iris_vector_graph import IRISGraphEngine` — imports clean
4. Verify `initialize_schema()` is idempotent — run `test_initialize_schema_idempotent`

---

## Files Changed

```
iris_vector_graph/operators.py                     # Fix ^KG subscript, add kg_PPR()
iris_vector_graph/schema.py                        # Add kg_PPR to get_procedures_sql_list()
iris_src/src/iris/vector/graph/GraphOperators.cls  # Fix SQLUSER -> Graph_KG
sql/operators.sql                                  # Update kg_PPR to use RunJson
tests/unit/test_operators_wiring.py                # NEW: unit tests
tests/e2e/test_operator_wiring_e2e.py              # NEW: comprehensive e2e tests
```

---

## Risk Assessment

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| `^KG` global empty because 021 not fully deployed | Medium | Tests call `BuildKG()` explicitly in fixtures |
| `Graph.KG.PageRank.RunJson()` not compiled in test container | Low | conftest.py already loads .cls via `$system.OBJ.LoadDir` |
| `kg_PPR` SQL function fails with IRIS parse error | Medium | Test in e2e; fallback path always available |
| Performance assertions too tight for CI | Low | Use generous thresholds (2x, not 10x) |
