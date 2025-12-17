# Tasks: Test Infrastructure Standardization

**Input**: Design documents from `/specs/006-test-infra-fixes/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, quickstart.md

**Tests**: No new tests requested - focus is on fixing existing test infrastructure

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Prepare the foundation for test infrastructure fixes

- [x] T001 Document the import conflict issue in CLAUDE.md iris/ directory section
- [x] T002 Verify nodes table is created by running migrations in sql/migrations/001_add_nodepk_table.sql

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Fix the shared fixture that ALL tests depend on

**⚠️ CRITICAL**: All test files depend on conftest.py - this must be fixed first

- [x] T003 Fix tests/conftest.py fallback to use importlib instead of `import iris`
- [x] T004 Add pytest.skip() when no database available in tests/conftest.py
- [x] T005 Fix api/main.py to use importlib instead of `import iris`

**Checkpoint**: Foundation ready - user story implementation can now begin in parallel

---

## Phase 3: User Story 1 - Run Full Test Suite Successfully (Priority: P1) 🎯 MVP

**Goal**: Run `pytest tests/` and have all tests pass with iris-devtester auto-discovery

**Independent Test**: `pytest tests/ -v --tb=short` passes with zero connection errors

### Implementation for User Story 1

- [ ] T006 [P] [US1] Fix tests/python/test_vector_functions.py to remove `import iris`
- [ ] T007 [P] [US1] Fix tests/python/test_sql_queries.py to remove `import iris`
- [ ] T008 [P] [US1] Fix tests/python/test_schema_validation.py to remove `import iris`
- [ ] T009 [P] [US1] Fix tests/python/test_python_sdk.py to remove `import iris`
- [ ] T010 [P] [US1] Fix tests/python/test_python_operators.py to remove `import iris`
- [ ] T011 [P] [US1] Fix tests/python/test_performance_benchmarks.py to remove `import iris`
- [ ] T012 [P] [US1] Fix tests/python/test_networkx_loader.py to remove `import iris`
- [ ] T013 [P] [US1] Fix tests/python/run_all_tests.py to remove `import iris`
- [ ] T014 [P] [US1] Fix tests/test_working_system.py to remove `import iris`
- [ ] T015 [P] [US1] Fix tests/unit/test_graphql_dataloader.py to remove `import iris`
- [ ] T016 [US1] Run pytest tests/ and verify no import errors

**Checkpoint**: Full test suite runs without import conflicts

---

## Phase 4: User Story 2 - E2E Tests Validate Multi-Query Engine Platform (Priority: P2)

**Goal**: E2E tests validate GraphQL, openCypher, and SQL return consistent results

**Independent Test**: `pytest tests/e2e/ -v` passes and validates all three query engines

### Implementation for User Story 2

- [ ] T017 [US2] Remove local iris_connection fixture from tests/e2e/test_multi_query_engine_platform.py
- [ ] T018 [US2] Verify health endpoint returns correct status in tests/e2e/test_multi_query_engine_platform.py
- [ ] T019 [US2] Run pytest tests/e2e/ and verify all 9 tests pass

**Checkpoint**: E2E tests validate cross-engine consistency

---

## Phase 5: User Story 3 - Consistent Test Fixtures Across All Test Files (Priority: P2)

**Goal**: All test files use shared fixture from conftest.py

**Independent Test**: `grep -r "def iris_connection" tests/` only shows conftest.py

### Implementation for User Story 3

- [ ] T020 [P] [US3] Remove local fixture from tests/integration/test_nodepk_production_scale.py
- [ ] T021 [P] [US3] Remove local fixture from tests/integration/test_nodepk_performance.py
- [ ] T022 [P] [US3] Remove local fixture from tests/integration/test_nodepk_migration.py
- [ ] T023 [P] [US3] Remove local fixture from tests/integration/test_nodepk_graph_analytics.py
- [ ] T024 [P] [US3] Remove local fixture from tests/integration/test_nodepk_constraints.py
- [ ] T025 [P] [US3] Remove local fixture from tests/integration/test_nodepk_advanced_benchmarks.py
- [ ] T026 [P] [US3] Remove local fixture from tests/integration/test_pagerank_sql_optimization.py
- [ ] T027 [P] [US3] Remove local fixture from tests/integration/gql/test_graphql_vector_search.py
- [ ] T028 [P] [US3] Remove local fixture from tests/integration/gql/test_graphql_queries.py
- [ ] T029 [P] [US3] Remove local fixture from tests/integration/gql/test_graphql_nested_queries.py
- [ ] T030 [P] [US3] Remove local fixture from tests/integration/gql/test_graphql_mutations.py
- [ ] T031 [US3] Verify only conftest.py defines iris_connection fixture

**Checkpoint**: All fixtures consolidated to conftest.py

---

## Phase 6: User Story 4 - Database Schema Ready for All Tests (Priority: P3)

**Goal**: All required database tables exist before tests run

**Independent Test**: `pytest tests/e2e/` passes with fresh database after running schema setup

### Implementation for User Story 4

- [ ] T032 [US4] Verify sql/schema.sql includes kg_NodeEmbeddings table (already present)
- [ ] T033 [US4] Verify sql/migrations/001_add_nodepk_table.sql creates nodes table
- [ ] T034 [US4] Update scripts/setup_schema.py to run all migrations in order
- [ ] T035 [US4] Test fresh database setup and run e2e tests

**Checkpoint**: Schema setup creates all required tables

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Final validation and documentation

- [ ] T036 Run full test suite: pytest tests/ -v --tb=short
- [ ] T037 Verify success criteria SC-006: grep "import iris" tests/ returns no matches
- [ ] T038 Update quickstart.md with any lessons learned
- [ ] T039 Verify test collection completes in under 5 seconds

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup - BLOCKS all user stories
- **User Stories (Phase 3-6)**: All depend on Foundational phase completion
  - US1, US3, US4 can proceed in parallel (different files)
  - US2 depends on US1 completion (needs working fixtures first)
- **Polish (Phase 7)**: Depends on all user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Foundational - fixes import conflicts
- **User Story 2 (P2)**: Depends on US1 - e2e tests need working imports
- **User Story 3 (P2)**: Can start after Foundational - removes local fixtures
- **User Story 4 (P3)**: Can start after Foundational - schema verification

### Within Each User Story

- All [P] tasks within a story can run in parallel
- Non-[P] tasks depend on previous tasks in sequence

### Parallel Opportunities

Within Phase 3 (US1): T006-T015 can all run in parallel (different files)
Within Phase 5 (US3): T020-T030 can all run in parallel (different files)

---

## Parallel Example: User Story 1

```bash
# Launch all import fixes together (10 files, no dependencies):
Task: "Fix tests/python/test_vector_functions.py to remove import iris"
Task: "Fix tests/python/test_sql_queries.py to remove import iris"
Task: "Fix tests/python/test_schema_validation.py to remove import iris"
Task: "Fix tests/python/test_python_sdk.py to remove import iris"
# ... and so on for all [P] tasks in US1
```

---

## Parallel Example: User Story 3

```bash
# Launch all fixture removals together (11 files, no dependencies):
Task: "Remove local fixture from tests/integration/test_nodepk_production_scale.py"
Task: "Remove local fixture from tests/integration/test_nodepk_performance.py"
# ... and so on for all [P] tasks in US3
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL - fixes conftest.py)
3. Complete Phase 3: User Story 1 (fixes import conflicts)
4. **STOP and VALIDATE**: `pytest tests/ -v` runs without import errors
5. Continue with remaining stories

### Incremental Delivery

1. Setup + Foundational → conftest.py fixed
2. User Story 1 → Import conflicts resolved → Basic tests work
3. User Story 3 → Fixtures consolidated → All integration tests use shared fixture
4. User Story 2 → E2E tests validated → Full platform verified
5. User Story 4 → Schema complete → Fresh database works
6. Polish → Final validation

---

## Notes

- [P] tasks = different files, no dependencies - can run in parallel
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable
- Commit after each task or logical group
- The key insight: `import iris` conflicts with `iris/` directory - use importlib pattern
