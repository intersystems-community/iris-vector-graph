# Tasks: Wire Up Existing Operators

**Branch**: `022-wire-up-operators` | **Plan**: [plan.md](plan.md)

---

## Phase 1: Tests First (RED)

- [ ] **T-001**: Create `tests/unit/test_operators_wiring.py` with all unit tests (7 tests)
- [ ] **T-002**: Create `tests/e2e/test_operator_wiring_e2e.py` with all e2e tests (11 tests)
- [ ] **T-003**: Verify all new tests FAIL (RED phase confirmation)

## Phase 2: Fix ^KG Subscript Bug (P0)

- [ ] **T-004**: Fix `iris_vector_graph/operators.py` kg_GRAPH_WALK ^KG access — add `"out"` prefix to gref.order() calls
- [ ] **T-005**: Verify `test_kg_graph_walk_kg_path_returns_results` passes
- [ ] **T-006**: Verify `test_kg_graph_walk_kg_path_only` passes (^KG path alone works)

## Phase 3: Add kg_PPR() to Operators (P1)

- [ ] **T-007**: Add `kg_PPR()` method to `IRISGraphOperators` in `operators.py`
- [ ] **T-008**: Verify `test_kg_ppr_method_exists` passes
- [ ] **T-009**: Verify `test_kg_ppr_star_graph` passes

## Phase 4: Auto-install kg_PPR SQL Function (P1)

- [ ] **T-010**: Add kg_PPR function to `get_procedures_sql_list()` in `schema.py`
- [ ] **T-011**: Verify `test_get_procedures_sql_list_includes_kg_ppr` passes
- [ ] **T-012**: Verify `test_kg_ppr_via_sql_function` passes

## Phase 5: Fix GraphOperators.cls Schema (P2)

- [ ] **T-013**: Replace `SQLUSER.` with `Graph_KG.` in `GraphOperators.cls`
- [ ] **T-014**: Verify `test_graphoperators_cls_uses_graph_kg_schema` passes

## Phase 6: Update sql/operators.sql (P2)

- [ ] **T-015**: Update `kg_PPR` function in `sql/operators.sql` to use `Graph.KG.PageRank.RunJson`
- [ ] **T-016**: Verify `test_kg_ppr_sql_calls_runjon_not_pagerank_embedded` passes

## Phase 7: Vector-Graph Search E2E (P2)

- [ ] **T-017**: Verify `test_vector_graph_search_returns_expanded_nodes` passes
- [ ] **T-018**: Verify `test_vector_graph_search_empty_db` passes

## Phase 8: Performance Verification

- [ ] **T-019**: Verify `test_kg_graph_walk_performance_kg_vs_sql` passes
- [ ] **T-020**: Verify `test_kg_ppr_performance` passes

## Phase 9: Final Validation

- [ ] **T-021**: Full test suite passes: `pytest`
- [ ] **T-022**: Lint check passes: `ruff check .`
- [ ] **T-023**: Import check: `python3 -c "from iris_vector_graph import IRISGraphEngine"`
- [ ] **T-024**: Verify `test_initialize_schema_idempotent` passes
