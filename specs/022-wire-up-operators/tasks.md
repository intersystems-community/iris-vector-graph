# Tasks: Wire Up Existing Operators

**Branch**: `022-wire-up-operators` | **Plan**: [plan.md](plan.md)

---

## Phase 1: Tests First (RED)

- [x] **T-001**: Create `tests/unit/test_operators_wiring.py` with all unit tests (10 tests)
- [x] **T-002**: Create `tests/e2e/test_operator_wiring_e2e.py` with all e2e tests (11 tests)
- [x] **T-003**: Verify all new tests FAIL (RED phase confirmation)

## Phase 2: Fix ^KG Subscript Bug (P0)

- [x] **T-004**: Fix `iris_vector_graph/operators.py` kg_GRAPH_WALK ^KG access — add `"out"` prefix to gref.order() calls
- [x] **T-005**: Verify `test_kg_graph_walk_kg_path_returns_results` passes
- [x] **T-006**: Verify `test_kg_graph_walk_kg_path_only` passes (^KG path alone works)

## Phase 2b: Fix kg_KNN_VEC 850x Regression (P0)

- [x] **T-004b**: Fix `_kg_KNN_VEC_hnsw_optimized` to query `kg_NodeEmbeddings` (not `_optimized`) with `TO_VECTOR(?, DOUBLE)`
- [x] **T-005b**: Verify `test_knn_vec_returns_results` passes
- [x] **T-006b**: Verify `test_knn_vec_no_fallback_warning` passes

## Phase 3: Add kg_PPR() to Operators (P1)

- [x] **T-007**: Add `kg_PPR()` method to `IRISGraphOperators` in `operators.py`
- [x] **T-008**: Verify `test_kg_ppr_method_exists` passes
- [x] **T-009**: Verify `test_kg_ppr_star_graph` passes

## Phase 4: Auto-install kg_PPR SQL Function (P1)

- [x] **T-010**: Add kg_PPR function to `get_procedures_sql_list()` in `schema.py`
- [x] **T-011**: Verify `test_get_procedures_sql_list_includes_kg_ppr` passes
- [x] **T-012**: Verify `test_kg_ppr_via_sql_function` passes

## Phase 5: Fix GraphOperators.cls Schema (P2)

- [x] **T-013**: Replace `SQLUSER.` with `Graph_KG.` in `GraphOperators.cls`
- [x] **T-014**: Verify `test_graphoperators_cls_uses_graph_kg_schema` passes

## Phase 6: Update sql/operators.sql (P2)

- [x] **T-015**: Update `kg_PPR` function in `sql/operators.sql` to use `Graph.KG.PageRank.RunJson`
- [x] **T-016**: Verify `test_kg_ppr_sql_calls_runjon_not_pagerank_embedded` passes

## Phase 7: Vector-Graph Search E2E (P2)

- [x] **T-017**: Verify `test_vector_graph_search_returns_expanded_nodes` passes
- [x] **T-018**: Verify `test_vector_graph_search_empty_db` passes

## Phase 8: Performance Verification

- [x] **T-019**: Verify `test_kg_graph_walk_performance_kg_vs_sql` passes (via `test_completes_under_5s`)
- [x] **T-020**: Verify `test_kg_ppr_performance` passes (via `test_completes_under_5s`)

## Phase 9: Final Validation

- [x] **T-021**: Full test suite passes: 140 unit + 11 e2e = 151 tests green
- [x] **T-022**: Lint check passes (no new lint errors)
- [x] **T-023**: Import check: `python3 -c "from iris_vector_graph import IRISGraphEngine"`
- [x] **T-024**: Verify `test_initialize_schema_idempotent` passes

## Phase 10: PageRank Bridge Fix (discovered during testing)

- [x] **T-025**: Remove `Run()` method (used `iris.cls()` + `^||PPR.Results` — both broken through native API bridge)
- [x] **T-026**: Rewrite `RunJson()` as pure ObjectScript (no `Language = python`, no `iris.gref` dependency)
- [x] **T-027**: Verify both SQL function path AND `_call_classmethod` path work
- [x] **T-028**: All 21 tests pass after rewrite

## Release

- [x] **T-029**: Publish v1.10.0 to PyPI (initial release)
- [x] **T-030**: Publish v1.10.1 to PyPI (remove dead `Run()` method)
- [x] **T-031**: Publish v1.10.2 to PyPI (pure ObjectScript `RunJson`)
