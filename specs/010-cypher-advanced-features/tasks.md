# Tasks: Advanced Cypher Features

**Input**: Design documents from `/specs/010-cypher-advanced-features/`
**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, quickstart.md

**Tests**: Integration tests for write operations and paths are REQUIRED per constitution and plan.md.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure

- [X] T001 Initialize branch environment and verify IRIS connectivity for write operations
- [X] T002 [P] Create algorithms directory in iris_vector_graph/cypher/algorithms/

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T003 Extend Parser class for UpdatingClause and collection support in iris_vector_graph/cypher/parser.py
- [X] T004 Implement Transactional DML generator base in iris_vector_graph/cypher/translator.py
- [X] T005 [P] Setup integration test structure for advanced features in tests/integration/test_cypher_advanced.py
- [X] T006 Add shortestPath and allShortestPaths recursive CTE templates in iris_vector_graph/cypher/algorithms/paths.py

**Checkpoint**: Foundation ready - transactional infrastructure and path logic structures in place.

---

## Phase 3: User Story 1 - Transactional Graph Management (Priority: P1) 🎯 MVP

**Goal**: Transform the retrieval engine into a transactional engine with CREATE, DELETE, and MERGE support.

**Independent Test**: Create a node, verify it exists, update it, and then delete it via Cypher API.

### Tests for User Story 1 (REQUIRED)
- [X] T007 [P] [US1] Create unit tests for updating clauses (CREATE/DELETE/MERGE) parsing in tests/unit/cypher/test_parser_advanced.py
- [X] T010 [P] [US1] Create integration tests for updating clauses (CREATE/DELETE/MERGE) in tests/integration/test_cypher_advanced.py
- [X] T010b [P] [US1] Create integration tests for SET and REMOVE property/label logic in tests/integration/test_cypher_advanced.py

### Implementation for User Story 1
- [X] T011 [US1] Implement CREATE parsing for nodes and relationships in iris_vector_graph/cypher/parser.py
- [X] T012 [US1] Implement DELETE and DETACH DELETE translation in iris_vector_graph/cypher/translator.py
- [X] T013 [US1] Implement MERGE as a multi-statement transaction block (existence check + conditional DML) in iris_vector_graph/cypher/translator.py
- [X] T014 [US1] Implement SET and REMOVE translation for properties and labels in iris_vector_graph/cypher/translator.py
- [X] T015 [US1] Update api/routers/cypher.py to handle transactional commit/rollback and isolation levels

**Checkpoint**: User Story 1 complete. Full graph lifecycle management is now supported.

---

## Phase 4: User Story 2 - Resilient Traversal with OPTIONAL MATCH (Priority: P2)

**Goal**: Support traversal even when certain relationships are missing (SQL LEFT JOIN behavior).

**Independent Test**: Execute query with OPTIONAL MATCH where no link exists and verify NULL return instead of row termination.

### Tests for User Story 2 (REQUIRED)
- [X] T016 [P] [US2] Create unit tests for OPTIONAL MATCH parsing in tests/unit/cypher/test_parser_advanced.py
- [X] T017 [P] [US2] Add integration test for missing relationships returning NULL in tests/integration/test_cypher_advanced.py

### Implementation for User Story 2
- [X] T018 [US2] Implement OPTIONAL MATCH parsing in iris_vector_graph/cypher/parser.py
- [X] T019 [US2] Implement LEFT OUTER JOIN translation for OPTIONAL MATCH in iris_vector_graph/cypher/translator.py

**Checkpoint**: User Story 2 complete. Queries are now resilient to missing data.

---

## Phase 5: User Story 3 - Bulk Data Processing with UNWIND (Priority: P3)

**Goal**: Expand collection parameters into rows for batch operations.

**Independent Test**: Pass a list of 100 IDs and create 100 nodes in a single query.

### Tests for User Story 3 (REQUIRED)
- [X] T020 [P] [US3] Create unit tests for UNWIND parsing in tests/unit/cypher/test_parser_advanced.py
- [X] T021 [P] [US3] Add integration test for bulk creation (100+ nodes) using UNWIND in tests/integration/test_cypher_advanced.py

### Implementation for User Story 3
- [X] T022 [US3] Implement UNWIND parsing in iris_vector_graph/cypher/parser.py
- [X] T023 [US3] Implement JSON_TABLE translation for UNWIND collections in iris_vector_graph/cypher/translator.py

**Checkpoint**: User Story 3 complete. High-performance batch operations supported.

---

## Phase 6: User Story 4 - Algorithmic Path Finding (Priority: P4)

**Goal**: Find shortest paths between nodes using recursive CTEs.

**Independent Test**: Find 5-hop path between two distant nodes in fraud dataset.

### Tests for User Story 4 (REQUIRED)
- [X] T024 [P] [US4] Create unit tests for shortestPath and allShortestPaths function parsing in tests/unit/cypher/test_parser_advanced.py
- [X] T025 [P] [US4] Add integration tests for 1-10 hop paths in tests/integration/test_cypher_advanced.py

### Implementation for User Story 4
- [X] T026 [US4] Implement shortestPath and allShortestPaths recursive CTE generation in iris_vector_graph/cypher/algorithms/paths.py
- [X] T027 [US4] Integrate path finding into iris_vector_graph/cypher/translator.py

**Checkpoint**: User Story 4 complete. Graph algorithmic capabilities unlocked.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Final validation, performance, and documentation.

- [X] T028 [P] Update docs/article_dc_contest.md with transactional examples and ERD updates
- [X] T029 Run performance benchmark for 10-hop paths to verify SC-004 targets
- [X] T030 Verify all user scenarios in quickstart.md against final implementation
- [X] T031 Final code cleanup and transaction logic review in iris_vector_graph/cypher/translator.py

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Stories (Phase 3+)**: All depend on Foundational phase completion
  - US1 (P1) is the core MVP requirement.
  - US2, US3, US4 can proceed in parallel once US1 foundation is stable.

### User Story Dependencies

- **User Story 1 (P1)**: Foundation for all write-related clauses.
- **User Story 2 (P2)**: Independent query enhancement.
- **User Story 3 (P3)**: Enhances US1 for bulk scenarios.
- **User Story 4 (P4)**: Independent algorithmic enhancement.

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 & 2.
2. Implement CREATE/DELETE/MERGE (US1).
3. **STOP and VALIDATE**: Verify transactional graph management works.

### Incremental Delivery

1. Add OPTIONAL MATCH (US2) for data discovery.
2. Add UNWIND (US3) for batch processing.
3. Add shortestPath (US4) for network analysis.
