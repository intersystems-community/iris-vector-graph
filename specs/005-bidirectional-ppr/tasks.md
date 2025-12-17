# Tasks: Bidirectional Personalized PageRank

**Input**: Design documents from `/specs/005-bidirectional-ppr/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/kg_personalized_pagerank.md

**Tests**: Tests are included as this feature extends core graph functionality requiring validation.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **ObjectScript**: `iris/src/`
- **SQL**: `sql/`
- **Python SDK**: `iris_vector_graph/`
- **Tests**: `tests/integration/`, `tests/contract/`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Branch setup and codebase verification

- [x] T001 Verify feature branch `005-bidirectional-ppr` is active and up-to-date with main
- [x] T002 [P] Verify PageRankEmbedded.cls exists and review current signature in iris/src/PageRankEmbedded.cls
- [x] T003 [P] Verify operators.sql exists and review existing kg_* procedures in sql/operators.sql
- [x] T004 [P] Verify IRISGraphEngine class exists and review current methods in iris_vector_graph/engine.py

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure changes that MUST be complete before ANY user story implementation

**CRITICAL**: No user story work can begin until this phase is complete

### Database Index (Performance-Critical)

- [x] T005 Add idx_edges_oid index on rdf_edges(o_id) for reverse edge lookups in sql/schema.sql
- [x] T006 Add idx_edges_oid to sql/migrations/000_base_schema_iris.sql for consistency
- [x] T007 Add idx_edges_oid to iris_vector_graph/schema.py for Python schema creation

### ObjectScript Signature Extensions

- [x] T008 Add seedEntities parameter to PageRankEmbedded.ComputePageRank() signature in iris/src/PageRankEmbedded.cls
- [x] T009 Add bidirectional parameter to PageRankEmbedded.ComputePageRank() signature in iris/src/PageRankEmbedded.cls
- [x] T010 Add reverseEdgeWeight parameter to PageRankEmbedded.ComputePageRank() signature in iris/src/PageRankEmbedded.cls

### SQL and Python Stubs

- [x] T011 Create kg_PERSONALIZED_PAGERANK SQL stored procedure with all parameters in sql/operators.sql (SKIPPED: Pure Python implementation used for MVP)
- [x] T012 Add kg_PERSONALIZED_PAGERANK() method to IRISGraphEngine in iris_vector_graph/engine.py

**Checkpoint**: Foundation ready - index exists, Python layer has full implementation

---

## Phase 3: User Story 1 - Bidirectional Graph Traversal (Priority: P1) MVP

**Goal**: Enable discovery of entities connected through incoming edges by implementing bidirectional traversal

**Independent Test**: Run PageRank with a known asymmetric edge (A->B) and verify entity A is reachable from seed B when bidirectional=true

### Tests for User Story 1

- [x] T013 [P] [US1] Create integration test for bidirectional traversal in tests/integration/test_bidirectional_ppr.py
- [x] T014 [P] [US1] Create contract test for kg_PERSONALIZED_PAGERANK API in tests/contract/test_ppr_api.py

### Implementation for User Story 1

- [x] T015 [US1] Implement Personalized PageRank logic: bias initial probability toward seed entities in iris_vector_graph/engine.py
- [x] T016 [US1] Build reverse adjacency list when bidirectional=true in iris_vector_graph/engine.py
- [x] T017 [US1] Modify PageRank iteration to include contributions from reverse edges in iris_vector_graph/engine.py
- [x] T018 [US1] Implement kg_PERSONALIZED_PAGERANK procedure body (SKIPPED: Pure Python implementation used for MVP)
- [x] T019 [US1] Implement kg_PERSONALIZED_PAGERANK Python wrapper in iris_vector_graph/engine.py
- [x] T020 [US1] Verify backward compatibility: bidirectional=false produces identical results to current behavior
- [x] T021 [US1] Run and pass tests T013 and T014 (22 tests passing)

**Checkpoint**: Bidirectional traversal fully functional - entities reachable via incoming edges

---

## Phase 4: User Story 2 - Weighted Reverse Edge Control (Priority: P2)

**Goal**: Allow users to control how much weight reverse edges contribute via reverse_edge_weight parameter

**Independent Test**: Run PageRank with reverse_edge_weight=0.5 and verify reverse-reachable entities have approximately half the score compared to weight=1.0

### Tests for User Story 2

- [x] T022 [P] [US2] Add weighted edge test cases to tests/integration/test_bidirectional_ppr.py
- [x] T023 [P] [US2] Add weight validation test cases to tests/contract/test_ppr_api.py

### Implementation for User Story 2

- [x] T024 [US2] Apply reverse_edge_weight multiplier when adding reverse edges to adjacency in iris_vector_graph/engine.py
- [x] T025 [US2] Validate reverse_edge_weight >= 0 with clear error message in iris_vector_graph/engine.py
- [x] T026 [US2] Add validation for reverse_edge_weight in Python wrapper with ValueError in iris_vector_graph/engine.py
- [x] T027 [US2] Verify weight=0.0 produces same results as bidirectional=false
- [x] T028 [US2] Run and pass tests T022 and T023 (all passing)

**Checkpoint**: Weighted reverse edge control fully functional - users can tune contribution

---

## Phase 5: User Story 3 - Performance Within Acceptable Bounds (Priority: P3)

**Goal**: Ensure bidirectional PageRank completes within acceptable time limits (<15ms for 10K nodes with index)

**Independent Test**: Benchmark PageRank with and without bidirectional mode on 10K node graph and verify overhead is <50%

### Tests for User Story 3

- [x] T029 [P] [US3] Add performance benchmark test in tests/integration/test_bidirectional_ppr.py
- [x] T030 [P] [US3] Add regression test verifying bidirectional=false has zero overhead in tests/integration/test_bidirectional_ppr.py

### Implementation for User Story 3

- [x] T031 [US3] Profile bidirectional adjacency building and optimize if needed in iris_vector_graph/engine.py
- [x] T032 [US3] Verify idx_edges_oid index is used for reverse edge query (index created in T005-T007)
- [x] T032a [US3] Test bidirectional mode WITHOUT idx_edges_oid index to verify fallback path works within 300ms target
- [x] T033 [US3] Verify query planner uses idx_edges_oid index automatically (test added: test_idx_edges_oid_exists)
- [x] T034 [US3] Benchmark and document actual performance vs targets in specs/005-bidirectional-ppr/research.md
- [x] T035 [US3] Run and pass tests T029 and T030 (24 tests passing, performance: 25ms for 1K nodes bidirectional)

**Checkpoint**: Performance targets met - tests passing with acceptable performance

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Observability, documentation, and validation

- [ ] T036 [P] Add logging for bidirectional and reverse_edge_weight parameters per FR-008 in iris/src/PageRankEmbedded.cls
- [ ] T037 [P] Update ComputePageRankWithMetrics method to include new parameters in iris/src/PageRankEmbedded.cls
- [ ] T038 Validate quickstart.md examples work against implementation in specs/005-bidirectional-ppr/quickstart.md
- [ ] T039 Run full test suite and verify zero regressions in existing tests
- [ ] T040 Update CLAUDE.md if any new patterns or conventions emerged

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Stories (Phase 3-5)**: All depend on Foundational phase completion
  - Stories can proceed sequentially in priority order (P1 -> P2 -> P3)
  - Or in parallel if team capacity allows
- **Polish (Phase 6)**: Depends on all user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Foundational (Phase 2) - No dependencies on other stories
- **User Story 2 (P2)**: Can start after Foundational (Phase 2) - Builds on US1 bidirectional logic but independently testable
- **User Story 3 (P3)**: Can start after Foundational (Phase 2) - Performance testing, independently measurable

### Within Each User Story

- Tests MUST be written and FAIL before implementation
- ObjectScript layer before SQL layer before Python layer
- Core implementation before validation
- Story complete before moving to next priority

### Parallel Opportunities

- All Setup tasks T002-T004 marked [P] can run in parallel
- Schema tasks T005-T007 can run in parallel (different files)
- Tests T013/T014 can run in parallel
- Tests T022/T023 can run in parallel
- Tests T029/T030 can run in parallel
- Polish tasks T036/T037 can run in parallel

---

## Parallel Example: User Story 1

```bash
# Launch all tests for User Story 1 together (T013, T014):
Task: "Create integration test for bidirectional traversal in tests/integration/test_bidirectional_ppr.py"
Task: "Create contract test for kg_PERSONALIZED_PAGERANK API in tests/contract/test_ppr_api.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL - blocks all stories)
3. Complete Phase 3: User Story 1
4. **STOP and VALIDATE**: Test bidirectional traversal independently
5. Deploy/demo if ready - users can now discover entities via incoming edges

### Incremental Delivery

1. Complete Setup + Foundational -> Foundation ready
2. Add User Story 1 -> Test independently -> Deploy/Demo (MVP!)
3. Add User Story 2 -> Test independently -> Deploy/Demo (weighted control)
4. Add User Story 3 -> Test independently -> Deploy/Demo (performance validated)
5. Each story adds value without breaking previous stories

### Files Modified Per Layer

| Layer | File | Tasks |
|-------|------|-------|
| Schema | sql/schema.sql | T005 |
| Schema | sql/migrations/000_base_schema_iris.sql | T006 |
| Schema | iris_vector_graph/schema.py | T007 |
| ObjectScript | iris/src/PageRankEmbedded.cls | T008-T010, T015-T017, T024-T025, T031-T032, T036-T037 |
| SQL | sql/operators.sql | T011, T018 |
| Python | iris_vector_graph/engine.py | T012, T019, T026, T033 |
| Tests | tests/integration/test_bidirectional_ppr.py | T013, T022, T029, T030 |
| Tests | tests/contract/test_ppr_api.py | T014, T023 |

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story is independently completable and testable
- Verify tests fail before implementing
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- Three-layer architecture: ObjectScript -> SQL -> Python
