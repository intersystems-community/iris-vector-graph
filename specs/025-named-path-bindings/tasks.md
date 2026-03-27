# Tasks: Named Path Bindings

**Input**: Design documents from `/specs/025-named-path-bindings/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Required (SC-005: ≥10 unit tests, ≥5 e2e tests; Constitution Principle IV: e2e mandatory for IRIS-backend Cypher features)

**Organization**: Tasks grouped by user story. US1 and US2 are both P1 but US2 depends on Phase 2's AST/parser/context work, so they are sequenced.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup

**Purpose**: No new project setup needed — extending existing Cypher stack in-place.

- [X] T001 Verify all existing Cypher unit tests pass: `python3 -m pytest tests/unit/cypher/ tests/unit/test_cypher_*.py -q`
- [X] T002 Verify all existing e2e tests pass: `python3 -m pytest tests/e2e/ -q`

**Checkpoint**: Baseline green — zero regressions before any changes.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: AST and parser changes that ALL user stories depend on.

- [X] T003 Add `NamedPath` dataclass to `iris_vector_graph/cypher/ast.py` with fields `variable: str` and `pattern: GraphPattern`
- [X] T004 Extend `MatchClause` in `iris_vector_graph/cypher/ast.py` to add `named_paths: List[NamedPath] = field(default_factory=list)` (backward compatible — default empty list)
- [X] T005 Modify `parse_match_clause` in `iris_vector_graph/cypher/parser.py` to detect 2-token lookahead `IDENTIFIER EQUALS` before `LPAREN`, consume path variable name, then call `parse_graph_pattern()` and wrap result in `NamedPath`, appending to both `match.named_paths` and `match.patterns`
- [X] T006 Extend `TranslationContext` in `iris_vector_graph/cypher/translator.py` to add `named_paths: Dict[str, NamedPath]`, `path_node_aliases: Dict[str, List[str]]`, and `path_edge_aliases: Dict[str, List[str]]`
- [X] T007 Populate path registry during MATCH→JOIN translation in `iris_vector_graph/cypher/translator.py`: when processing a `MatchClause` with `named_paths`, record the ordered node SQL aliases and edge SQL aliases into `context.path_node_aliases` and `context.path_edge_aliases`

**Checkpoint**: Parser correctly parses `MATCH p = (a)-[r]->(b)` into AST with `NamedPath`. Translator context tracks path aliases. All existing tests still pass.

---

## Phase 3: User Story 1 — Bind and Return a Fixed-Length Path (Priority: P1) MVP

**Goal**: `MATCH p = (a)-[r]->(b) RETURN p` produces a JSON object with ordered nodes and rels.

**Independent Test**: Execute named path query against live IRIS, verify JSON result structure.

### Tests for User Story 1

- [X] T008 [P] [US1] Unit test: parse `MATCH p = (a)-[r]->(b) RETURN p` produces AST with `NamedPath(variable="p")` in `tests/unit/test_named_paths.py`
- [X] T009 [P] [US1] Unit test: parse `MATCH p = (a)-[r1]->(b)-[r2]->(c) RETURN p` produces 3-node `NamedPath` in `tests/unit/test_named_paths.py`
- [X] T009a [P] [US1] Unit test: parse `MATCH p = (a)-[r1]->(b)-[r2]->(c)-[r3]->(d) RETURN p` produces 4-node NamedPath in `tests/unit/test_named_paths.py`
- [X] T010 [P] [US1] Unit test: parse `MATCH (a)-[r]->(b) RETURN a` (no named path) still works unchanged in `tests/unit/test_named_paths.py`
- [X] T011 [P] [US1] Unit test: translate `RETURN p` emits `JSON_OBJECT('nodes': JSON_ARRAY(...), 'rels': JSON_ARRAY(...))` in `tests/unit/test_named_paths.py`

### Implementation for User Story 1

- [X] T012 [US1] Add path variable detection in `translate_return_clause` in `iris_vector_graph/cypher/translator.py`: when `ReturnItem.expression` is `Variable` and name is in `context.named_paths`, emit `JSON_OBJECT('nodes': JSON_ARRAY(node_aliases...), 'rels': JSON_ARRAY(edge_aliases...))` instead of node expansion
- [X] T013 [US1] Verify 1-hop named path `MATCH p = (a)-[r]->(b) RETURN p` produces correct SQL via `translate()` function

**Checkpoint**: `RETURN p` works for fixed-length patterns. Unit tests pass.

---

## Phase 4: User Story 2 — Path Functions: length, nodes, relationships (Priority: P1)

**Goal**: `length(p)`, `nodes(p)`, `relationships(p)` translate to correct SQL for fixed-length named paths.

**Independent Test**: Execute path function queries against live IRIS, verify integer/array results.

### Tests for User Story 2

- [X] T014 [P] [US2] Unit test: `length(p)` on 2-hop path translates to integer literal `2` in `tests/unit/test_named_paths.py`
- [X] T015 [P] [US2] Unit test: `nodes(p)` on 2-hop path translates to `JSON_ARRAY(n0.node_id, n1.node_id, n2.node_id)` in `tests/unit/test_named_paths.py`
- [X] T016 [P] [US2] Unit test: `relationships(p)` on 2-hop path translates to `JSON_ARRAY(e0.p, e1.p)` in `tests/unit/test_named_paths.py`
- [X] T017 [P] [US2] Unit test: `nodes(x)` where `x` is not a path raises `CypherTranslationError` in `tests/unit/test_named_paths.py`

### Implementation for User Story 2

- [X] T018 [US2] Add path function handling in `translate_expression` for `FunctionCall` in `iris_vector_graph/cypher/translator.py`: intercept `length`, `nodes`, `relationships` when argument is a `Variable` in `context.named_paths`; emit integer literal for `length`, `JSON_ARRAY(node_aliases)` for `nodes`, `JSON_ARRAY(edge_aliases)` for `relationships`
- [X] T019 [US2] Add error handling: when `length`/`nodes`/`relationships` argument is a `Variable` NOT in `context.named_paths`, raise `CypherTranslationError` with descriptive message in `iris_vector_graph/cypher/translator.py`

**Checkpoint**: All three path functions work. Error handling for invalid references verified. Unit tests pass.

---

## Phase 5: User Story 3 — Named Path with Property Filters (Priority: P2)

**Goal**: Named paths work correctly when combined with WHERE clause property filters.

**Independent Test**: Execute filtered named path queries, verify only matching paths returned with correct function values.

### Tests for User Story 3

- [X] T020 [P] [US3] Unit test: parse `MATCH p = (a)-[r]->(b) WHERE a.name = 'X' RETURN nodes(p)` produces correct AST in `tests/unit/test_named_paths.py`
- [X] T021 [P] [US3] Unit test: parse `MATCH p = (a)-[r:KNOWS]->(b) RETURN relationships(p)` with typed relationship in `tests/unit/test_named_paths.py`

### Implementation for User Story 3

- [X] T022 [US3] Verify WHERE clause filters apply correctly when named path is present — no code change expected (WHERE translation is independent of path binding), validate via tests only in `iris_vector_graph/cypher/translator.py`

**Checkpoint**: Property filters + named paths integrate correctly. No separate code change needed — validated by tests.

---

## Phase 5.5: Integration Tests (SQL Translation — Principle IV)

**Purpose**: Validate generated SQL correctness at the translation layer without live IRIS.

- [X] T022a [US1] Integration test: `translate()` for `MATCH p = (a)-[r]->(b) RETURN p` produces SQL containing `JSON_OBJECT` with `JSON_ARRAY` for nodes and rels in `tests/integration/test_named_paths_integration.py`
- [X] T022b [US2] Integration test: `translate()` for `RETURN length(p), nodes(p), relationships(p)` produces correct SQL fragments in `tests/integration/test_named_paths_integration.py`

**Checkpoint**: SQL translation verified structurally before live execution.

---

## Phase 6: End-to-End Tests (IRIS — Principle IV, Non-Optional)

**Purpose**: Validate all user stories against live `iris-vector-graph-main` IRIS container.

- [X] T023 [US1] E2e test: `MATCH p = (a)-[r]->(b) WHERE a.node_id = ? RETURN p` returns JSON with correct nodes and rels in `tests/e2e/test_named_paths_e2e.py`
- [X] T024 [US1] E2e test: `MATCH p = (a)-[r1]->(b)-[r2]->(c) RETURN p` returns 3 nodes and 2 rels in `tests/e2e/test_named_paths_e2e.py`
- [X] T024a [US1] E2e test: 3-hop named path `MATCH p = (a)-[r1]->(b)-[r2]->(c)-[r3]->(d) RETURN p` returns 4 nodes and 3 rels in `tests/e2e/test_named_paths_e2e.py`
- [X] T025 [US2] E2e test: `RETURN length(p)` returns correct integer for 1-hop and 2-hop paths in `tests/e2e/test_named_paths_e2e.py`
- [X] T026 [US2] E2e test: `RETURN nodes(p)` returns ordered JSON array of node IDs in `tests/e2e/test_named_paths_e2e.py`
- [X] T027 [US2] E2e test: `RETURN relationships(p)` returns ordered JSON array of predicate strings in `tests/e2e/test_named_paths_e2e.py`
- [X] T028 [US3] E2e test: named path with WHERE property filter returns only matching paths in `tests/e2e/test_named_paths_e2e.py`
- [X] T029 E2e test: no-match named path query returns empty result set in `tests/e2e/test_named_paths_e2e.py`

**Checkpoint**: All acceptance scenarios from spec.md pass against live IRIS.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [X] T030 Run full regression: `python3 -m pytest tests/unit/ tests/e2e/ -q` — all 301+ existing tests pass
- [X] T031 Validate quickstart.md examples execute correctly against live IRIS
- [X] T032 [P] Update `docs/python/PYTHON_SDK.md` Cypher section with named path syntax and path functions

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — baseline verification
- **Foundational (Phase 2)**: Depends on Phase 1 — BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Phase 2 — MVP
- **US2 (Phase 4)**: Depends on Phase 2 (uses same path registry) — can parallelize with US1 implementation if tests written first
- **US3 (Phase 5)**: Depends on Phase 2 — test-only validation, no new code
- **E2E (Phase 6)**: Depends on Phases 3 + 4 completion
- **Polish (Phase 7)**: Depends on Phase 6

### User Story Dependencies

- **US1 (P1)**: Can start after Phase 2 — no dependencies on other stories
- **US2 (P1)**: Can start after Phase 2 — shares path registry with US1 but implementation is in different code paths (RETURN vs FunctionCall)
- **US3 (P2)**: Can start after Phase 2 — test-only phase, validates existing WHERE integration

### Parallel Opportunities

- T008-T011 (US1 unit tests) can all run in parallel
- T014-T017 (US2 unit tests) can all run in parallel
- T020-T021 (US3 unit tests) can all run in parallel
- T023-T029 (all e2e tests) are independent and can run in parallel
- US1 and US2 implementation (T012-T013 and T018-T019) touch different code paths in translator.py but same file — sequential recommended

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Verify baseline
2. Complete Phase 2: AST + parser + context (T003-T007)
3. Complete Phase 3: `RETURN p` works (T008-T013)
4. **STOP and VALIDATE**: `MATCH p = (a)-[r]->(b) RETURN p` produces JSON

### Incremental Delivery

1. Setup + Foundational → Parser handles `p = (pattern)`
2. Add US1 → `RETURN p` works → JSON path objects
3. Add US2 → `length(p)`, `nodes(p)`, `relationships(p)` work
4. Add US3 → Verified with property filters (test-only)
5. E2E → All stories validated against live IRIS
6. Polish → Docs + full regression
