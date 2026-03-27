# Tasks: Recursive-Descent Cypher Parser

**Input**: Design documents from `/specs/001-cypher-rd-parser/`
**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md

**Tests**: Unit tests for lexer/parser and integration tests against live IRIS are mandatory.

**Organization**: Tasks are grouped by foundational work and then by user story priority.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Parallelizable (no dependencies on other incomplete tasks)
- **[Story]**: US1, US2, US3 (maps to spec.md)

---

## Phase 1: Setup

**Purpose**: Initialize project structure and baseline tests

- [X] T001 Initialize branch environment and verify IRIS connectivity
- [X] T002 [P] Create unit test structure in tests/unit/cypher/
- [X] T003 [P] Create integration test file tests/integration/test_cypher_rd.py

---

## Phase 2: Foundational

**Purpose**: Core engine components (Lexer, AST, Parser Base)

- [X] T004 Implement Token class and Lexer in iris_vector_graph/cypher/lexer.py
- [X] T005 [P] Enrich AST nodes for multi-stage queries in iris_vector_graph/cypher/ast.py
- [X] T006 Implement base RD Parser class with peek/eat/expect in iris_vector_graph/cypher/parser.py
- [X] T007 [P] Create lexer unit tests in tests/unit/cypher/test_lexer.py

**Checkpoint**: Core engine components are ready for specific query clause implementation.

---

## Phase 3: User Story 1 - Standard Graph Retrieval (Priority: P1) 🎯 MVP

**Goal**: Support MATCH/WHERE/RETURN/LIMIT/SKIP with 100% regression parity.

**Independent Test**: Run `python examples/demo_fraud_detection.py` and verify all 6 steps pass.

### Implementation for User Story 1
- [X] T008 [US1] Implement MATCH pattern parsing (nodes/rels) in iris_vector_graph/cypher/parser.py
- [X] T009 [US1] Implement basic WHERE clause comparisons (=, <>, <, >) in iris_vector_graph/cypher/parser.py
- [X] T010 [US1] Implement recursive boolean expression parsing (AND, OR, NOT, parentheses) in iris_vector_graph/cypher/parser.py
- [X] T011 [US1] Implement RETURN clause parsing in iris_vector_graph/cypher/parser.py
- [X] T012 [US1] Implement LIMIT/SKIP parsing in iris_vector_graph/cypher/parser.py
- [X] T013 [US1] Update translator.py to handle the new enriched AST for single-stage queries
- [X] T014 [US1] Switch api/routers/cypher.py to use the new RD parser

### Tests for User Story 1
- [X] T015 [P] [US1] Create parser unit tests for standard patterns and multi-match scenarios in tests/unit/cypher/test_parser.py
- [X] T016 [P] [US1] Add integration tests for basic retrieval and multi-match queries in tests/integration/test_cypher_rd.py

**Checkpoint**: MVP Complete. Regression parity achieved with the new RD engine.

---

## Phase 4: User Story 2 - Chained Query Logic with WITH (Priority: P2)

**Goal**: Support multi-stage traversals and strict variable scoping.

**Independent Test**: Execute `MATCH (a)-[r]->(t) WITH a, count(t) AS tc WHERE tc > 1 RETURN a.node_id`

### Implementation for User Story 2
- [X] T017 [US2] Implement WITH clause parsing in iris_vector_graph/cypher/parser.py
- [X] T018 [US2] Implement multi-part query parsing logic in iris_vector_graph/cypher/parser.py
- [X] T019 [US2] Update translator.py to generate SQL Common Table Expressions (CTEs) for WITH stages
- [X] T020 [US2] Implement strict variable scoping validation in iris_vector_graph/cypher/parser.py

### Tests for User Story 2
- [ ] T021 [P] [US2] Add unit tests for WITH clause parsing in tests/unit/cypher/test_parser.py
- [X] T022 [P] [US2] Add integration tests for multi-stage queries in tests/integration/test_cypher_rd.py

**Checkpoint**: Complex multi-stage queries are now functional.

---

## Phase 5: User Story 3 - Analytical Aggregations (Priority: P3)

**Goal**: Support COUNT, SUM, AVG, MIN, MAX and built-in functions.

**Independent Test**: Execute `MATCH (t:Transaction) RETURN sum(t.amount), avg(t.amount)`

### Implementation for User Story 3
- [X] T023 [US3] Implement function call and aggregation parsing in iris_vector_graph/cypher/parser.py
- [X] T024 [US3] Update translator.py to generate explicit GROUP BY for aggregations
- [X] T025 [US3] Implement built-in functions id(), type(), labels() in translator.py

### Tests for User Story 3
- [X] T026 [P] [US3] Add unit tests for aggregations in tests/unit/cypher/test_parser.py
- [X] T027 [P] [US3] Add integration tests for analytical queries in tests/integration/test_cypher_rd.py

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Error reporting, performance, and documentation.

- [X] T028 [P] Implement line/column position tracking in iris_vector_graph/cypher/lexer.py
- [X] T029 Update CypherParseError to include position data in iris_vector_graph/cypher/parser.py
- [X] T030 [P] Update docs/article_dc_contest.md with complex Cypher examples (WITH/Agg)
- [X] T031 Run performance benchmarks to verify <10ms parsing overhead
- [X] T032 Final verification of all user scenarios in quickstart.md

---

## Dependencies & Execution Order

### Phase Dependencies
- **Setup (Phase 1)**: Start immediately.
- **Foundational (Phase 2)**: Depends on Phase 1. BLOCKS all user stories.
- **US1 (Phase 3)**: Depends on Phase 2. MVP Target.
- **US2 (Phase 4)**: Depends on US1.
- **US3 (Phase 5)**: Depends on US1 (can run in parallel with US2).

### Parallel Opportunities
- Test writing (T007, T014, T015, T020, T021, T025, T026) can run in parallel with implementation.
- AST enrichment (T005) can run in parallel with Lexer implementation (T004).
- Documentation updates (T029) can run anytime after US2/US3 are stable.

---

## Implementation Strategy

### MVP First (Phases 1-3)
1. Build the Lexer/Parser foundation.
2. Port existing functionality to the new RD engine.
3. Verify zero regression using existing demos.

### Incremental Delivery
- Add `WITH` clause support (Phase 4) to unlock multi-stage logic.
- Add aggregations (Phase 5) for analytical reporting.
- Polish error messages and documentation (Phase 6).
