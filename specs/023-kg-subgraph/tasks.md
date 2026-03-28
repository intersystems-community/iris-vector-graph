# Tasks: kg_SUBGRAPH

**Input**: Design documents from `/specs/023-kg-subgraph/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1–US7)
- Exact file paths included in all descriptions

---

## Phase 1: Setup

**Purpose**: Create new files, verify infrastructure

- [X] T001 Create `iris_vector_graph/models.py` with `SubgraphData` dataclass per data-model.md
- [X] T002 [P] Create empty test file `tests/unit/test_subgraph.py` with imports
- [X] T003 [P] Create empty test file `tests/e2e/test_subgraph_e2e.py` with imports and SKIP_IRIS guard

---

## Phase 2: Foundational (IRIS — Principle IV)

**Purpose**: Verify IRIS container and test infrastructure before any implementation

- [X] T004 Verify canonical test container name `iris-vector-graph-main` in `tests/conftest.py:153`
- [X] T005 Verify `IRISContainer.attach("iris-vector-graph-main")` resolves correctly in tests/conftest.py
- [X] T006 Confirm `tests/e2e/test_subgraph_e2e.py` uses `os.environ.get("SKIP_IRIS_TESTS", "false")`
- [X] T007 Confirm no hardcoded ports in new test files

**Checkpoint**: Foundation ready — user story implementation can begin

---

## Phase 3: User Story 1 + 5 — Core Subgraph Extraction + Server-Side (Priority: P0) 🎯 MVP

**Goal**: Single-call k-hop subgraph extraction with server-side ObjectScript implementation

**Independent Test**: Insert chain graph A->B->C->D, extract 2-hop from A, verify {A,B,C} returned with edges and properties

### Tests (RED phase — must fail before implementation)

- [X] T008 [P] [US1] Unit test: `test_subgraph_data_has_expected_fields` in `tests/unit/test_subgraph.py`
- [X] T009 [P] [US1] Unit test: `test_kg_subgraph_method_exists` in `tests/unit/test_subgraph.py`
- [X] T010 [P] [US1] Unit test: `test_empty_seeds_returns_empty_subgraph` in `tests/unit/test_subgraph.py`
- [X] T011 [P] [US1] Unit test: `test_json_response_parsing` — mock SubgraphJson, verify SubgraphData fields populated in `tests/unit/test_subgraph.py`
- [X] T012 [P] [US1] E2E test: `test_chain_graph_2hop` — A->B->C->D, seed A, k=2 → {A,B,C} in `tests/e2e/test_subgraph_e2e.py`
- [X] T013 [P] [US1] E2E test: `test_chain_graph_1hop` — same graph, k=1 → {A,B} in `tests/e2e/test_subgraph_e2e.py`
- [X] T014 [P] [US1] E2E test: `test_multi_seed_union` — seeds [A,D], k=1 → union in `tests/e2e/test_subgraph_e2e.py`
- [X] T015 [P] [US1] E2E test: `test_nonexistent_seed_excluded` in `tests/e2e/test_subgraph_e2e.py`
- [X] T016 [P] [US1] E2E test: `test_k_hops_zero_seeds_only` in `tests/e2e/test_subgraph_e2e.py`
- [X] T017 [P] [US1] E2E test: `test_cyclic_graph_no_duplicates` — A->B->A in `tests/e2e/test_subgraph_e2e.py`
- [X] T018 [P] [US1] E2E test: `test_properties_included` in `tests/e2e/test_subgraph_e2e.py`
- [X] T019 [P] [US1] E2E test: `test_labels_included` in `tests/e2e/test_subgraph_e2e.py`
- [X] T020 [P] [US5] E2E test: `test_server_side_matches_fallback` — compare ObjectScript vs Python in `tests/e2e/test_subgraph_e2e.py`

### Implementation

- [X] T021 [US5] Create `iris_src/src/Graph/KG/Subgraph.cls` — pure ObjectScript `SubgraphJson` method: BFS over ^KG, collect nodes/edges/properties/labels, return JSON per data-model.md wire format
- [X] T022 [US1] Add `kg_SUBGRAPH()` method to `IRISGraphOperators` in `iris_vector_graph/operators.py` — primary path via `_call_classmethod('Graph.KG.Subgraph', 'SubgraphJson', ...)`, parse JSON into SubgraphData, Python-side fallback via `kg_NEIGHBORS` + SQL
- [X] T023 [US1] Deploy `Subgraph.cls` to test container and verify compilation
- [X] T024 [US1] Run all Phase 3 tests — verify GREEN

**Checkpoint**: Core extraction works end-to-end. `ops.kg_SUBGRAPH(seeds, k_hops=2)` returns correct SubgraphData.

---

## Phase 4: User Story 2 — Edge Type Filtering (Priority: P1)

**Goal**: Filter subgraph traversal by edge type (predicate)

**Independent Test**: Graph with mixed edge types, extract with edge_types=["MENTIONS"], verify only MENTIONS edges

### Tests

- [X] T025 [P] [US2] Unit test: `test_edge_types_passed_to_json` — verify edgeTypesJson constructed correctly in `tests/unit/test_subgraph.py`
- [X] T026 [P] [US2] E2E test: `test_edge_type_filter_mentions_only` in `tests/e2e/test_subgraph_e2e.py`
- [X] T027 [P] [US2] E2E test: `test_edge_type_none_includes_all` in `tests/e2e/test_subgraph_e2e.py`

### Implementation

- [X] T028 [US2] Add edge type filtering to `SubgraphJson` in `iris_src/src/Graph/KG/Subgraph.cls` — parse edgeTypesJson, skip non-matching predicates during BFS
- [X] T029 [US2] Add `edge_types` parameter handling to `kg_SUBGRAPH()` in `iris_vector_graph/operators.py`
- [X] T030 [US2] Run Phase 4 tests — verify GREEN

**Checkpoint**: Edge type filtering works. MENTIONS-only extraction excludes CITES edges.

---

## Phase 5: User Story 3 — Safety Limits (Priority: P1)

**Goal**: max_nodes cap prevents runaway extraction on dense graphs

**Independent Test**: Hub with 500 neighbors, extract with max_nodes=50, verify ≤50 nodes

### Tests

- [X] T031 [P] [US3] E2E test: `test_max_nodes_caps_extraction` — hub with 100+ neighbors, max_nodes=10 in `tests/e2e/test_subgraph_e2e.py`

### Implementation

- [X] T032 [US3] Add `maxNodes` check to BFS loop in `SubgraphJson` in `iris_src/src/Graph/KG/Subgraph.cls` — stop frontier expansion when nodeCount >= maxNodes
- [X] T033 [US3] Run Phase 5 tests — verify GREEN

**Checkpoint**: Safety limits work. Dense graph extraction is bounded.

---

## Phase 6: User Story 4 — Embeddings (Priority: P1)

**Goal**: Include node embedding vectors when requested

**Independent Test**: Nodes with embeddings, extract with include_embeddings=True, verify vectors present

### Tests

- [X] T034 [P] [US4] Unit test: `test_embeddings_fetched_via_sql` — mock SQL cursor, verify IN query in `tests/unit/test_subgraph.py`
- [X] T035 [P] [US4] E2E test: `test_embeddings_included_when_requested` in `tests/e2e/test_subgraph_e2e.py`
- [X] T036 [P] [US4] E2E test: `test_embeddings_excluded_by_default` in `tests/e2e/test_subgraph_e2e.py`

### Implementation

- [X] T037 [US4] Add embedding fetch to `kg_SUBGRAPH()` in `iris_vector_graph/operators.py` — after SubgraphJson returns, one SQL query `SELECT id, emb FROM Graph_KG.kg_NodeEmbeddings WHERE id IN (?,...)` for returned node IDs, parse into node_embeddings dict
- [X] T038 [US4] Run Phase 6 tests — verify GREEN

**Checkpoint**: Embeddings available for ML feature matrix construction.

---

## Phase 7: User Story 5 — Performance Verification (Priority: P0)

**Goal**: Verify server-side extraction meets <100ms target on 10K-node graph

### Tests

- [X] T039 [US5] E2E test: `test_performance_10k_graph` — insert 10K-node graph (avg degree 10), time 2-hop extraction, assert <100ms in `tests/e2e/test_subgraph_e2e.py`

### Implementation

- [X] T040 [US5] If performance test fails: profile SubgraphJson, optimize label collection strategy (consider &sql() for labels per research.md decision #3)
- [X] T041 [US5] Run full test suite — `pytest tests/unit/test_subgraph.py tests/e2e/test_subgraph_e2e.py` — all GREEN

**Checkpoint**: Performance validated. Server-side extraction <100ms on 10K nodes.

---

## Phase 8: User Story 6 — PyG Tensor Output (Priority: P2, stretch)

**Goal**: numpy array output compatible with PyTorch Geometric

**Independent Test**: Extract tensors, verify edge_index shape [2,E], x shape [N,D]

- [X] T042 [P] [US6] Unit test: `test_subgraph_tensors_shapes` in `tests/unit/test_subgraph.py`
- [X] T043 [US6] Add `kg_SUBGRAPH_TENSORS()` to `IRISGraphOperators` in `iris_vector_graph/operators.py` — convert SubgraphData to edge_index + node feature matrix
- [X] T044 [US6] Run Phase 8 tests — verify GREEN

---

## Phase 9: User Story 7 — Cypher Procedure (Priority: P2, stretch)

**Goal**: `CALL ivg.subgraph($seeds, 2) YIELD nodes, edges`

- [X] T045 [P] [US7] Unit test: `test_cypher_subgraph_parse_and_translate` in `tests/unit/test_cypher_procedures.py`
- [X] T046 [US7] Add `_translate_subgraph` to `iris_vector_graph/cypher/translator.py` — generate CTE calling SubgraphJson via JSON_TABLE
- [X] T047 [US7] Run Phase 9 tests — verify GREEN

---

## Phase 10: Polish & Cross-Cutting

- [X] T048 [P] Update README.md changelog with v1.14.0 entry
- [X] T049 [P] Update `docs/python/PYTHON_SDK.md` with kg_SUBGRAPH API section
- [X] T050 Run full regression: `pytest tests/unit/ tests/e2e/` — all existing + new tests GREEN
- [X] T051 Bump version in `pyproject.toml` to 1.14.0
- [X] T052 Run quickstart.md validation — verify examples work

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies
- **Phase 2 (Foundational)**: Depends on Phase 1
- **Phase 3 (US1+US5 Core)**: Depends on Phase 2 — **MVP**
- **Phase 4 (US2 Filtering)**: Depends on Phase 3 (needs SubgraphJson + kg_SUBGRAPH to exist)
- **Phase 5 (US3 Safety)**: Depends on Phase 3
- **Phase 6 (US4 Embeddings)**: Depends on Phase 3
- **Phase 7 (Performance)**: Depends on Phases 3-6
- **Phase 8 (Tensors)**: Depends on Phase 3+6 — stretch
- **Phase 9 (Cypher)**: Depends on Phase 3 — stretch
- **Phase 10 (Polish)**: Depends on all desired phases

### User Story Independence

- **US1+US5 (Core + Server-side)**: Bundled as MVP — the server-side method IS the core implementation
- **US2 (Filtering)**: Independent of US3/US4 — can be done in any order after MVP
- **US3 (Safety)**: Independent of US2/US4
- **US4 (Embeddings)**: Independent of US2/US3
- **US6 (Tensors)**: Needs US4 (embeddings) as input
- **US7 (Cypher)**: Independent of US2-US6

### Parallel Opportunities

Within Phase 3: All T008-T020 test tasks can run in parallel (different test methods)
After Phase 3: Phases 4, 5, 6 can run in parallel (independent user stories)

---

## Implementation Strategy

### MVP First (Phase 1-3)

1. Setup + Foundational
2. Write all Phase 3 tests (RED)
3. Implement `Subgraph.cls` + `kg_SUBGRAPH()` + `SubgraphData`
4. GREEN — deploy, validate
5. **STOP**: This alone delivers the core value

### Incremental Delivery

- **v1.14.0-rc1**: Phase 3 (core extraction)
- **v1.14.0-rc2**: + Phases 4-6 (filtering, safety, embeddings)
- **v1.14.0**: + Phase 7 (performance verified) + Phase 10 (docs)
- **v1.15.0**: Phase 8-9 (tensors, Cypher — stretch)

---

## Notes

- Total tasks: **52**
- MVP tasks (Phases 1-3): **24**
- P1 tasks (Phases 4-6): **14**
- Stretch tasks (Phases 8-9): **6**
- Polish tasks: **5**
- E2E tests: **15** (all against live IRIS container `iris-vector-graph-main`)
- Unit tests: **8**
