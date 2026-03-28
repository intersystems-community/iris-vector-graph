# Tasks: Graph Analytics Kernels

**Input**: Design documents from `/specs/024-graph-kernels/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1–US5)

---

## Phase 1: Setup

**Purpose**: Create test files, verify infrastructure

- [X] T001 [P] Create `tests/unit/test_graph_kernels.py` with imports
- [X] T002 [P] Create `tests/e2e/test_graph_kernels_e2e.py` with imports and SKIP_IRIS guard
- [X] T003 Verify container `iris-vector-graph-main` in `tests/conftest.py:153`
- [X] T004 Verify no hardcoded ports in new test files

**Checkpoint**: Test infrastructure ready

---

## Phase 2: Tests First — ALL Tests for ALL Phases (RED)

**Purpose**: TDD — write every test before any implementation. All must fail initially.

### Unit Tests

- [X] T005 [P] [US1] Unit test: `test_kg_pagerank_method_exists` in `tests/unit/test_graph_kernels.py`
- [X] T006 [P] [US1] Unit test: `test_kg_pagerank_returns_list_of_tuples` — mock classmethod, verify format in `tests/unit/test_graph_kernels.py`
- [X] T007 [P] [US2] Unit test: `test_kg_wcc_method_exists` in `tests/unit/test_graph_kernels.py`
- [X] T008 [P] [US2] Unit test: `test_kg_wcc_returns_dict` — mock classmethod, verify format in `tests/unit/test_graph_kernels.py`
- [X] T009 [P] [US3] Unit test: `test_kg_cdlp_method_exists` in `tests/unit/test_graph_kernels.py`
- [X] T010 [P] [US3] Unit test: `test_kg_cdlp_returns_dict` — mock classmethod, verify format in `tests/unit/test_graph_kernels.py`

### E2E Tests

- [X] T011 [P] [US1] E2E test: `test_pagerank_star_graph` — hub has highest score in `tests/e2e/test_graph_kernels_e2e.py`
- [X] T012 [P] [US1] E2E test: `test_pagerank_all_nodes_scored` — all 5 nodes have scores in `tests/e2e/test_graph_kernels_e2e.py`
- [X] T013 [P] [US1] E2E test: `test_pagerank_scores_sum_to_one` — scores sum ≈ 1.0 in `tests/e2e/test_graph_kernels_e2e.py`
- [X] T014 [P] [US1] E2E test: `test_pagerank_early_termination` — max_iter=100, verify doesn't run all 100 in `tests/e2e/test_graph_kernels_e2e.py`
- [X] T015 [P] [US2] E2E test: `test_wcc_disconnected_clusters` — {A-B-C} and {D-E} → 2 components in `tests/e2e/test_graph_kernels_e2e.py`
- [X] T016 [P] [US2] E2E test: `test_wcc_fully_connected` — all nodes share one component in `tests/e2e/test_graph_kernels_e2e.py`
- [X] T017 [P] [US2] E2E test: `test_wcc_isolated_node` — gets own component in `tests/e2e/test_graph_kernels_e2e.py`
- [X] T018 [P] [US3] E2E test: `test_cdlp_bridge_clusters` — two dense clusters with bridge → 2 communities in `tests/e2e/test_graph_kernels_e2e.py`
- [X] T019 [P] [US1] E2E test: `test_pagerank_empty_graph` — returns empty list in `tests/e2e/test_graph_kernels_e2e.py`
- [X] T020 [P] [US2] E2E test: `test_wcc_empty_graph` — returns empty dict in `tests/e2e/test_graph_kernels_e2e.py`
- [X] T021 [P] [US3] E2E test: `test_cdlp_empty_graph` — returns empty dict in `tests/e2e/test_graph_kernels_e2e.py`

**Checkpoint**: All tests written. Verify they FAIL (RED phase).

---

## Phase 3: User Story 1 — Global PageRank (Priority: P0) 🎯 MVP

**Goal**: Compute global importance scores for all nodes with uniform teleport

**Independent Test**: Star graph hub has highest score, all nodes scored, scores sum to ~1.0

### Implementation

- [X] T022 [US1] Add `PageRankGlobalJson` to `iris_src/src/Graph/KG/PageRank.cls` — uniform initialization over ALL ^KG("deg") nodes, same convergence check as RunJson
- [X] T023 [US1] Add `kg_PAGERANK()` to `IRISGraphOperators` in `iris_vector_graph/operators.py` — primary via `_call_classmethod('Graph.KG.PageRank', 'PageRankGlobalJson', ...)`, parse JSON, Python fallback
- [X] T024 [US1] Deploy `PageRank.cls` to test container, verify compilation
- [X] T025 [US1] Run Phase 3 tests — verify GREEN for US1 tests (T005-T006, T011-T014, T019)

**Checkpoint**: `ops.kg_PAGERANK()` returns correct global scores. Star graph hub wins.

---

## Phase 4: User Story 2 — WCC (Priority: P0)

**Goal**: Find all weakly connected components using bidirectional label propagation

**Independent Test**: Two disconnected clusters → 2 distinct component labels

### Implementation

- [X] T026 [US2] Create `iris_src/src/Graph/KG/Algorithms.cls` with `WCCJson` — iterate labels(node)=node, adopt MIN neighbor label via ^KG("out")+^KG("in"), converge on no changes
- [X] T027 [US2] Add `kg_WCC()` to `IRISGraphOperators` in `iris_vector_graph/operators.py` — primary via `_call_classmethod('Graph.KG.Algorithms', 'WCCJson', ...)`, parse JSON, Python fallback
- [X] T028 [US2] Deploy `Algorithms.cls` to test container, verify compilation
- [X] T029 [US2] Run Phase 4 tests — verify GREEN for US2 tests (T007-T008, T015-T017, T020)

**Checkpoint**: `ops.kg_WCC()` correctly identifies disconnected components.

---

## Phase 5: User Story 3 — CDLP (Priority: P1)

**Goal**: Detect communities via most-frequent-neighbor-label propagation

**Independent Test**: Bridge-connected dense clusters → 2 distinct community labels

### Implementation

- [X] T030 [US3] Add `CDLPJson` to `iris_src/src/Graph/KG/Algorithms.cls` — iterate labels, adopt most-frequent neighbor label (smallest wins ties), converge on no changes
- [X] T031 [US3] Add `kg_CDLP()` to `IRISGraphOperators` in `iris_vector_graph/operators.py` — primary via `_call_classmethod('Graph.KG.Algorithms', 'CDLPJson', ...)`, parse JSON, Python fallback
- [X] T032 [US3] Run Phase 5 tests — verify GREEN for US3 tests (T009-T010, T018, T021)

**Checkpoint**: `ops.kg_CDLP()` detects dense clusters.

---

## Phase 6: User Story 4 — Performance Verification (Priority: P1)

**Goal**: Verify all three kernels meet performance targets

- [X] T033 [US4] E2E test: `test_pagerank_performance` — assert <500ms on test graph in `tests/e2e/test_graph_kernels_e2e.py`
- [X] T034 [US4] E2E test: `test_wcc_performance` — assert <1s on test graph in `tests/e2e/test_graph_kernels_e2e.py`
- [X] T035 [US4] E2E test: `test_cdlp_performance` — assert <1s on test graph in `tests/e2e/test_graph_kernels_e2e.py`
- [X] T036 [US4] Run all performance tests — verify GREEN

**Checkpoint**: Performance validated on test data.

---

## Phase 7: Polish & Cross-Cutting

- [X] T037 [P] Update `README.md` changelog with v1.16.0 entry
- [X] T038 [P] Update `docs/python/PYTHON_SDK.md` with kg_PAGERANK, kg_WCC, kg_CDLP sections
- [X] T039 Run full regression: `pytest tests/unit/ tests/e2e/` — all existing + new tests GREEN
- [X] T040 Bump version in `pyproject.toml` to 1.16.0
- [X] T041 Build and publish to PyPI

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies
- **Phase 2 (Tests)**: Depends on Phase 1
- **Phase 3 (PageRank)**: Depends on Phase 2 — **MVP**
- **Phase 4 (WCC)**: Depends on Phase 2 (NOT on Phase 3 — independent algorithm)
- **Phase 5 (CDLP)**: Depends on Phase 4 (same Algorithms.cls file)
- **Phase 6 (Performance)**: Depends on Phases 3-5
- **Phase 7 (Polish)**: Depends on Phase 6

### Parallel Opportunities

- Phase 2: ALL test tasks (T005-T021) can run in parallel
- Phase 3 + Phase 4: Can run in parallel (different .cls files, different operator methods)
- Phase 5 depends on Phase 4 only because CDLP goes in the same Algorithms.cls
- Phase 7: T037, T038 can run in parallel

---

## Implementation Strategy

### MVP First (Phase 1-3)

1. Setup + all tests (RED)
2. Implement PageRankGlobalJson + kg_PAGERANK()
3. GREEN on PageRank tests
4. **STOP**: Global PageRank alone is the highest-value kernel

### Incremental

- **v1.16.0-rc1**: PageRank (Phase 3)
- **v1.16.0-rc2**: + WCC (Phase 4)
- **v1.16.0**: + CDLP (Phase 5) + performance (Phase 6) + docs (Phase 7)

---

## Notes

- Total tasks: **41**
- MVP tasks (Phases 1-3): **25** (including all tests)
- Unit tests: **6**
- E2E tests: **14** (11 functional + 3 performance)
- All three algorithms use the same `$ORDER` loop over `^KG` — proven pattern from PPR
- Container: `iris-vector-graph-main` (conftest.py:153)
