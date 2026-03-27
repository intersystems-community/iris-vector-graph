# Tasks: Subquery Clauses (CALL { ... })

**Input**: Design documents from `/specs/026-subquery-call/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Required (SC-005: ≥8 unit tests, ≥4 e2e tests; Constitution Principle IV: integration + e2e mandatory)

**Organization**: Tasks grouped by user story. US1 is the foundation; US2 depends on US1's CTE infrastructure. US3 (IN TRANSACTIONS) is parser-only and can parallelize with US2.

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

- [X] T001 Verify all existing Cypher unit tests pass: `python3 -m pytest tests/unit/cypher/ tests/unit/test_cypher_*.py tests/unit/test_named_paths.py -q`
- [X] T002 Verify all existing e2e tests pass: `python3 -m pytest tests/e2e/ -q`

**Checkpoint**: Baseline green — zero regressions before any changes.

---

## Phase 2: Foundational (Blocking Prerequisites)

- [X] T003 Add `SubqueryCall` dataclass to `iris_vector_graph/cypher/ast.py` with fields `inner_query: CypherQuery`, `import_variables: List[str]`, `in_transactions: bool`, `transactions_batch_size: Optional[int]`
- [X] T004 Extend `QueryPart.clauses` union type in `iris_vector_graph/cypher/ast.py` to include `SubqueryCall`
- [X] T005 Add `TRANSACTIONS` and `ROWS` keyword tokens to the keyword map in `iris_vector_graph/cypher/lexer.py`
- [X] T006 Add `parse_subquery_call` method to `iris_vector_graph/cypher/parser.py`: consume `CALL LBRACE`, check for inner `WITH` (extract import variables), parse inner query parts + return clause, consume `RBRACE`, check for `IN TRANSACTIONS [OF N ROWS]` suffix
- [X] T007 Modify `parse_query_part` in `iris_vector_graph/cypher/parser.py` to detect `CALL` followed by `LBRACE` (subquery) vs `CALL` followed by `IDENTIFIER` (procedure); dispatch to `parse_subquery_call` for `LBRACE`
- [X] T008 Validate that `parse_subquery_call` raises `CypherParseError` when the inner query has no RETURN clause (FR-008) in `iris_vector_graph/cypher/parser.py`
- [X] T008a [P] Unit test: parse `CALL { MATCH (n) }` (no RETURN) raises `CypherParseError` with "RETURN" in error message in `tests/unit/test_subquery_call.py`

**Checkpoint**: Parser correctly parses `CALL { MATCH ... RETURN ... }` into AST with `SubqueryCall`. All existing tests still pass.

---

## Phase 3: User Story 1 — Independent Subquery with Aggregation (Priority: P1) MVP

**Goal**: `CALL { MATCH (n:Drug) RETURN count(n) AS cnt } RETURN cnt` translates to a CTE and executes correctly.

**Independent Test**: Execute independent subquery against live IRIS, verify CTE-based result.

### Tests for User Story 1

- [X] T009 [P] [US1] Unit test: parse `CALL { MATCH (n:Drug) RETURN n.name AS name } RETURN name` produces AST with `SubqueryCall(import_variables=[])` in `tests/unit/test_subquery_call.py`
- [X] T010 [P] [US1] Unit test: parse `CALL { MATCH (n) RETURN count(n) AS cnt } RETURN cnt` produces AST with aggregation in inner return in `tests/unit/test_subquery_call.py`
- [X] T011 [P] [US1] Unit test: translate independent subquery emits CTE with `SubQuery` prefix in SQL in `tests/unit/test_subquery_call.py`
- [X] T012 [P] [US1] Unit test: subquery output variable `name` is accessible in outer RETURN in `tests/unit/test_subquery_call.py`

### Implementation for User Story 1

- [X] T013 [US1] Add `translate_subquery_call` function to `iris_vector_graph/cypher/translator.py`: for independent subqueries (import_variables=[]), translate inner query to SQL via recursive `translate_to_sql`, wrap as CTE `SubQueryN`, register subquery RETURN aliases in outer context's `variable_aliases`
- [X] T014 [US1] Wire `translate_subquery_call` into the main translation loop: in `translate_to_sql`, handle `SubqueryCall` in the clause dispatch (alongside MatchClause, UnwindClause, etc.) in `iris_vector_graph/cypher/translator.py`

**Checkpoint**: Independent subqueries translate to CTEs. Unit tests pass.

---

## Phase 4: User Story 2 — Correlated Subquery with Imported Variable (Priority: P2)

**Goal**: `MATCH (p:Protein) CALL { WITH p MATCH (p)-[:X]->(q) RETURN count(q) AS degree } RETURN p.id, degree` translates to scalar subquery in SELECT with COALESCE.

**Independent Test**: Execute correlated subquery against live IRIS, verify per-row aggregation.

### Tests for User Story 2

- [X] T015 [P] [US2] Unit test: parse `MATCH (p) CALL { WITH p MATCH (p)-[]->(q) RETURN count(q) AS deg } RETURN p.id, deg` produces AST with `SubqueryCall(import_variables=["p"])` in `tests/unit/test_subquery_call.py`
- [X] T016 [P] [US2] Unit test: translate correlated subquery emits scalar subquery with COALESCE in SELECT in `tests/unit/test_subquery_call.py`
- [X] T017 [P] [US2] Unit test: scope isolation — independent subquery referencing outer variable without WITH raises error during translation in `tests/unit/test_subquery_call.py`

### Implementation for User Story 2

- [X] T018 [US2] Extend `translate_subquery_call` in `iris_vector_graph/cypher/translator.py` for correlated subqueries: create child `TranslationContext` inheriting only imported variables, translate inner query to scalar subquery SQL fragment, wrap with `COALESCE(..., 0)`, inject into parent context's `select_items`
- [X] T019 [US2] Add scope isolation enforcement in `iris_vector_graph/cypher/translator.py`: when translating inner query, if a variable reference is not in the child context's `variable_aliases` and not in `import_variables`, raise `ValueError` with descriptive message

**Checkpoint**: Correlated subqueries translate to scalar subqueries. Scope isolation enforced. Unit tests pass.

---

## Phase 5: User Story 3 — IN TRANSACTIONS Parsing (Priority: P3)

**Goal**: `CALL { ... } IN TRANSACTIONS OF 500 ROWS` parses correctly with no-op execution.

### Tests for User Story 3

- [X] T020 [P] [US3] Unit test: parse `CALL { MATCH (n) DELETE n } IN TRANSACTIONS OF 500 ROWS` produces AST with `in_transactions=True` and `transactions_batch_size=500` in `tests/unit/test_subquery_call.py`
- [X] T021 [P] [US3] Unit test: parse `CALL { MATCH (n) RETURN n } IN TRANSACTIONS` without batch size produces `in_transactions=True` and `transactions_batch_size=None` in `tests/unit/test_subquery_call.py`

### Implementation for User Story 3

- [X] T022 [US3] Verify IN TRANSACTIONS parsing already handled by T006 — no additional implementation needed, validate via tests only

**Checkpoint**: IN TRANSACTIONS parsed and treated as no-op. Unit tests pass.

---

## Phase 5.5: Integration Tests (SQL Translation — Principle IV)

- [X] T023 [US1] Integration test: `translate_to_sql()` for independent subquery produces SQL with CTE containing `SubQuery` prefix and correct column aliases in `tests/integration/test_subquery_call_integration.py`
- [X] T024 [US2] Integration test: `translate_to_sql()` for correlated subquery produces SQL with `COALESCE` scalar subquery in SELECT in `tests/integration/test_subquery_call_integration.py`
- [X] T024a [US2] Integration test: `translate_to_sql()` for independent subquery referencing outer variable without WITH import raises `ValueError` with scope error message in `tests/integration/test_subquery_call_integration.py`

**Checkpoint**: SQL translation verified structurally before live execution.

---

## Phase 6: End-to-End Tests (IRIS — Principle IV, Non-Optional)

- [X] T025 [US1] E2e test: `CALL { MATCH (n:Label) RETURN n.name AS name } RETURN name` returns correct rows against live IRIS in `tests/e2e/test_subquery_call_e2e.py`
- [X] T026 [US1] E2e test: `CALL { MATCH (n:Label) RETURN count(n) AS cnt } RETURN cnt` returns correct aggregation in `tests/e2e/test_subquery_call_e2e.py`
- [X] T027 [US2] E2e test: `MATCH (p:Label) CALL { WITH p MATCH (p)-[:REL]->(q) RETURN count(q) AS deg } RETURN p.id, deg` returns per-node degree with 0 for nodes with no edges in `tests/e2e/test_subquery_call_e2e.py`
- [X] T028 [US1] E2e test: independent subquery with no matching data returns empty result set in `tests/e2e/test_subquery_call_e2e.py`
- [X] T029 E2e test: subquery missing RETURN raises error in `tests/e2e/test_subquery_call_e2e.py`

**Checkpoint**: All acceptance scenarios from spec.md pass against live IRIS.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [X] T030 Run full regression: `python3 -m pytest tests/unit/ tests/e2e/ -q` — all 320+ existing tests pass
- [X] T031 Validate quickstart.md examples execute correctly against live IRIS
- [X] T032 [P] Update `docs/python/PYTHON_SDK.md` Cypher section with subquery syntax and examples

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies
- **Foundational (Phase 2)**: Depends on Phase 1 — BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Phase 2 — MVP
- **US2 (Phase 4)**: Depends on Phase 2 + Phase 3 (uses CTE infrastructure for scope isolation pattern)
- **US3 (Phase 5)**: Depends on Phase 2 only — can parallelize with Phase 3/4
- **Integration (Phase 5.5)**: Depends on Phases 3 + 4 completion
- **E2E (Phase 6)**: Depends on all implementation phases
- **Polish (Phase 7)**: Depends on Phase 6

### User Story Dependencies

- **US1 (P1)**: No dependencies after Phase 2 — can start immediately
- **US2 (P2)**: Depends on Phase 2 (AST/parser) — scalar subquery translation is independent of Phase 3's CTE path
- **US3 (P3)**: Independent of US1/US2 — parser-only feature

### Parallel Opportunities

- T009-T012 (US1 unit tests) can all run in parallel
- T015-T017 (US2 unit tests) can all run in parallel
- T020-T021 (US3 unit tests) can all run in parallel
- T025-T029 (all e2e tests) are independent and can run in parallel
- US3 (Phase 5) can run in parallel with US2 (Phase 4)

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Verify baseline
2. Complete Phase 2: AST + lexer + parser (T003-T008)
3. Complete Phase 3: Independent subquery → CTE (T009-T014)
4. **STOP and VALIDATE**: `CALL { MATCH ... RETURN ... } RETURN ...` produces CTE

### Incremental Delivery

1. Setup + Foundational → Parser handles `CALL { ... }`
2. Add US1 → Independent subquery → CTE works
3. Add US2 → Correlated subquery → scalar subquery works
4. Add US3 → IN TRANSACTIONS parsed (test-only)
5. Integration + E2E → All stories validated against live IRIS
6. Polish → Docs + full regression
