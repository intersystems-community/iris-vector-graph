# Tasks: Cypher Relationship Pattern Support

**Input**: Design documents from `/specs/001-cypher-relationship-patterns/`
**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md

**Tests**: Integration tests are explicitly requested in the plan.md and spec.md (Constitution Check requirement for live IRIS testing).

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure

- [X] T001 Initialize branch environment and verify IRIS connectivity
- [X] T002 [P] Create integration test directory tests/integration/

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure and base fixes that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T003 Update regex constants in iris_vector_graph/cypher/parser.py (REL_PATTERN)
- [X] T004 Fix JOIN translation logic in iris_vector_graph/cypher/translator.py to support explicit ON conditions
- [X] T005 Update _parse_graph_pattern in iris_vector_graph/cypher/parser.py to sequentially extract nodes and relationships
- [X] T006 [P] Add shared test utility for live IRIS Cypher execution in tests/integration/conftest.py

**Checkpoint**: Foundation ready - parser regex and translator JOIN logic fixed for basic traversals.

---

## Phase 3: User Story 1 - Single Relationship Types (Priority: P1) 🎯 MVP

**Goal**: Support traversal using a single relationship type (e.g., -[:TYPE]->) with optional variable bindings.

**Independent Test**: Execute `MATCH (t:Transaction)-[r:FROM_ACCOUNT]->(a:Account) RETURN t, r, a` and verify correct data return.

### Tests for User Story 1 (REQUIRED)
- [X] T007 [P] [US1] Create integration tests for single-type patterns in tests/integration/test_cypher_single_type.py
- [X] T008 [P] [US1] Create integration tests for relationship variables in tests/integration/test_cypher_rel_vars.py
- [X] T009 [US1] Implement single type extraction in iris_vector_graph/cypher/parser.py
- [X] T010 [US1] Implement SQL translation for single predicate in iris_vector_graph/cypher/translator.py
- [X] T011 [US1] Update result mapping to support relationship object construction in api/routers/cypher.py

**Checkpoint**: User Story 1 is functional. Users can query basic graph traversals with types.

---

## Phase 4: User Story 2 - Multiple Relationship Types (Priority: P2)

**Goal**: Support multi-type relationship patterns (e.g., -[:TYPE1|TYPE2]->) using SQL IN clause.

**Independent Test**: Execute `MATCH (a)-[:FROM_ACCOUNT|TO_ACCOUNT]->(b)` and verify results include both relationship types.

### Tests for User Story 2 (REQUIRED)
- [X] T012 [P] [US2] Create integration tests for multi-type patterns in tests/integration/test_cypher_multi_type.py
- [X] T013 [US2] Update parser to handle pipe-separated types in iris_vector_graph/cypher/parser.py
- [X] T014 [US2] Implement SQL 'IN' clause translation for multiple types in iris_vector_graph/cypher/translator.py

**Checkpoint**: User Story 2 is functional. Complex fraud patterns involving multiple directions can be queried.

---

## Phase 5: User Story 3 - Any Relationship Type (Priority: P3)

**Goal**: Support untyped relationship queries (e.g., -[]-> or -[r]->).

**Independent Test**: Execute `MATCH (a)-[r]->(b)` and verify it returns all edges regardless of predicate.

### Tests for User Story 3 (REQUIRED)
- [X] T015 [P] [US3] Create integration tests for untyped relationship patterns in tests/integration/test_cypher_untyped.py
- [X] T016 [US3] Update parser to handle empty or variable-only relationship blocks in iris_vector_graph/cypher/parser.py
- [X] T017 [US3] Update translator to omit predicate filter for untyped relationships in iris_vector_graph/cypher/translator.py

**Checkpoint**: All user stories are independently functional.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final verification, documentation, and error handling.

- [X] T018 [P] Add detailed logging for Cypher translation in iris_vector_graph/cypher/translator.py
- [X] T019 Implement improved error handling for malformed patterns in iris_vector_graph/cypher/parser.py
- [X] T020 [P] Update documentation and examples in docs/article_dc_contest.md with working Cypher examples
- [X] T021 Run all integration tests in tests/integration/ to ensure no regressions
- [X] T022 Run quickstart.md validation to confirm all listed examples work

---

## Dependencies & Execution Order

### Phase Dependencies
- **Setup (Phase 1)**: Start immediately.
- **Foundational (Phase 2)**: Depends on Phase 1. BLOCKS all user stories.
- **User Stories (Phase 3+)**: Depend on Foundational (Phase 2).
  - US1 (P1) is the MVP.
  - US2 and US3 can proceed in parallel once US1 is stable, or sequentially.

### Parallel Opportunities
- All [P] marked tests can be written in parallel across all stories.
- Documentation and Logging (Phase 6) can start as soon as US1 is complete.

---

## Implementation Strategy

### MVP First (User Story 1 Only)
1. Complete Setup and Foundational phases.
2. Implement User Story 1 (Single types).
3. **STOP and VALIDATE**: Verify US1 works with `MATCH (t:Transaction)-[:FROM_ACCOUNT]->(a:Account)`.

### Incremental Delivery
- Add US2 (Multi-type) to support the "Mule Detection" pattern mentioned in the article.
- Add US3 (Untyped) for full graph exploration capability.
