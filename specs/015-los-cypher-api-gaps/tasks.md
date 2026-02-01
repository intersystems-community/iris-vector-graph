# Tasks: LOS Cypher & API Integration Gaps

**Input**: Design documents from `/specs/015-los-cypher-api-gaps/`
**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/, quickstart.md

**Tests**: Included (plan.md specifies test-first approach)

**Organization**: Tasks grouped by user story for independent implementation/testing.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create test scaffolding used across stories

- [X] T001 Create test scaffolds in tests/unit/test_cypher_parser.py, tests/unit/test_cypher_translator.py, tests/integration/test_cypher_enhancements.py, tests/integration/test_embeddings_api.py

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared helpers needed by multiple stories

- [X] T002 Add shared SQL fragment helpers for labels/properties/patterns in iris_vector_graph/cypher/translator.py

**Checkpoint**: Foundation ready - user story implementation can begin

---

## Phase 3: User Story 1 - Retrieve Complete Node Data (Priority: P1) 🎯 MVP

**Goal**: Support RETURN n, labels(n), properties(n), and get_node API.

**Independent Test**: Create a node with labels/properties and verify a single query/API returns full data.

### Tests for User Story 1

- [X] T003 [P] [US1] Add parser tests for RETURN n, labels(n), properties(n) in tests/unit/test_cypher_parser.py
- [X] T004 [P] [US1] Add translator tests for RETURN n SQL shape in tests/unit/test_cypher_translator.py
- [X] T005 [P] [US1] Add integration tests for RETURN n and labels/properties in tests/integration/test_cypher_enhancements.py

### Implementation for User Story 1

- [X] T006 [US1] Extend Cypher AST return item nodes for whole-node returns in iris_vector_graph/cypher/ast.py
- [X] T007 [US1] Update parser for RETURN n and labels/properties functions in iris_vector_graph/cypher/parser.py
- [X] T008 [US1] Update translator to emit SQL for RETURN n, labels(), properties() in iris_vector_graph/cypher/translator.py
- [X] T009 [US1] Implement get_node and result post-processing in iris_vector_graph/engine.py

---

## Phase 4: User Story 2 - Store Node Embeddings via API (Priority: P1)

**Goal**: Provide store_embedding and store_embeddings with validation and atomic batch behavior.

**Independent Test**: Store an embedding and verify KNN returns the node; verify batch rejects invalid node.

### Tests for User Story 2

- [X] T010 [P] [US2] Add unit tests for embedding APIs in tests/unit/test_engine_embeddings.py
- [X] T011 [P] [US2] Add integration tests for embeddings in tests/integration/test_embeddings_api.py

### Implementation for User Story 2

- [X] T012 [US2] Add embedding dimension lookup/validation helper in iris_vector_graph/engine.py
- [X] T013 [US2] Implement store_embedding in iris_vector_graph/engine.py
- [X] T014 [US2] Implement store_embeddings (atomic batch) in iris_vector_graph/engine.py

---

## Phase 5: User Story 3 - Query with ORDER BY and LIMIT (Priority: P2)

**Goal**: Support ORDER BY/LIMIT with NULLS LAST behavior in SQL translation.

**Independent Test**: Create nodes with created_at values and verify sorted, limited results.

### Tests for User Story 3

- [X] T015 [P] [US3] Add parser tests for ORDER BY/LIMIT in tests/unit/test_cypher_parser.py
- [X] T016 [P] [US3] Add translator tests for ORDER BY/LIMIT/NULLS LAST in tests/unit/test_cypher_translator.py
- [X] T017 [P] [US3] Add integration tests for ORDER BY/LIMIT in tests/integration/test_cypher_enhancements.py

### Implementation for User Story 3

- [X] T018 [US3] Update AST for ORDER BY/LIMIT if needed in iris_vector_graph/cypher/ast.py
- [X] T019 [US3] Update parser to parse ORDER BY/LIMIT in iris_vector_graph/cypher/parser.py
- [X] T020 [US3] Update translator apply_pagination to emit NULLS LAST + LIMIT in iris_vector_graph/cypher/translator.py

---

## Phase 6: User Story 4 - Filter with Comparison Operators (Priority: P2)

**Goal**: Support comparison operators with numeric coercion on string properties.

**Independent Test**: Query confidence >= 0.7 and verify only expected nodes returned.

### Tests for User Story 4

- [X] T021 [P] [US4] Add translator tests for numeric comparison CAST in tests/unit/test_cypher_translator.py
- [X] T022 [P] [US4] Add integration tests for comparisons in tests/integration/test_cypher_enhancements.py
- [X] T023 [P] [US4] Add test for non-numeric comparison skip behavior in tests/integration/test_cypher_enhancements.py

### Implementation for User Story 4

- [X] T024 [US4] Update translator boolean expression handling with CAST in iris_vector_graph/cypher/translator.py
- [X] T025 [US4] Update parser/AST comparison operator handling if needed in iris_vector_graph/cypher/parser.py

---

## Phase 7: User Story 5 - Search with String Pattern Matching (Priority: P3)

**Goal**: Support CONTAINS/STARTS WITH/ENDS WITH in Cypher WHERE clauses.

**Independent Test**: Query name patterns and verify correct subset returned.

### Tests for User Story 5

- [ ] T026 [P] [US5] Add translator tests for pattern operators in tests/unit/test_cypher_translator.py
- [ ] T027 [P] [US5] Add integration tests for pattern matching in tests/integration/test_cypher_enhancements.py

### Implementation for User Story 5

- [ ] T028 [US5] Update parser to parse CONTAINS/STARTS WITH/ENDS WITH in iris_vector_graph/cypher/parser.py
- [ ] T029 [US5] Update translator to emit LIKE patterns in iris_vector_graph/cypher/translator.py

---

## Phase 8: User Story 6 - Get Relationship Type (Priority: P3)

**Goal**: Verify type(r) returns relationship type string.

**Independent Test**: Create edge with type and verify type(r) output.

### Tests for User Story 6

- [ ] T030 [P] [US6] Add integration tests for type(r) in tests/integration/test_cypher_enhancements.py

### Implementation for User Story 6

- [ ] T031 [US6] Verify/adjust type(r) mapping in iris_vector_graph/cypher/translator.py

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: Documentation and cleanup

- [ ] T032 [P] Sync quickstart examples with final behavior in specs/015-los-cypher-api-gaps/quickstart.md
- [ ] T033 [P] Update API contract notes if needed in specs/015-los-cypher-api-gaps/contracts/api.md
- [ ] T034 [P] Replace direct SQL workarounds with new APIs in tools/los/knowledge_graph/iris_client.py
- [ ] T035 [P] Replace direct SQL sorting with Cypher ORDER BY/LIMIT in tools/los/knowledge_graph/repositories/checkpoint_repository.py
- [ ] T036 [P] Replace direct SQL filtering with Cypher comparison operators in tools/los/knowledge_graph/repositories/evidence_repository.py
- [ ] T037 [P] Replace direct SQL pattern matching with Cypher CONTAINS/STARTS WITH/ENDS WITH in tools/los/knowledge_graph/repositories/goal_repository.py
- [ ] T038 [P] Audit remaining direct SQL calls and remove/replace in tools/los/knowledge_graph/

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Stories (Phases 3-8)**: Depend on Foundational completion; can run in priority order (P1 → P2 → P3)
- **Polish (Phase 9)**: Depends on desired user stories being complete

### User Story Dependencies

- **US1 (P1)**: Independent after Foundational
- **US2 (P1)**: Independent after Foundational
- **US3 (P2)**: Independent after Foundational
- **US4 (P2)**: Independent after Foundational
- **US5 (P3)**: Independent after Foundational
- **US6 (P3)**: Independent after Foundational

### Parallel Opportunities

- Tests within a story marked [P] can be written in parallel
- US1 and US2 can proceed in parallel after Foundational
- Parser and translator changes for different stories can be parallelized if touching separate files

---

## Parallel Execution Examples

### User Story 1

- [P] T003 Add parser tests in tests/unit/test_cypher_parser.py
- [P] T004 Add translator tests in tests/unit/test_cypher_translator.py
- [P] T005 Add integration tests in tests/integration/test_cypher_enhancements.py

### User Story 2

- [P] T010 Add unit tests in tests/unit/test_engine_embeddings.py
- [P] T011 Add integration tests in tests/integration/test_embeddings_api.py

---

## Implementation Strategy

### MVP First (User Story 1)

1. Complete Setup + Foundational (T001–T002)
2. Implement US1 (T003–T009)
3. Validate US1 independently via tests

### Incremental Delivery

1. Add US2 (T010–T014) → test
2. Add US3 (T015–T020) → test
3. Add US4 (T021–T025) → test
4. Add US5 (T026–T029) → test
5. Add US6 (T030–T031) → test
6. Polish + LOS cleanup (T032–T038)
