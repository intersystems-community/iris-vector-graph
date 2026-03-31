# Tasks: Cypher CAST Functions + COUNT(DISTINCT)

**Input**: Design documents from `/specs/032-cypher-cast-coerce/`
**Tests**: Required (SC-004: ≥6 unit, ≥2 e2e; Constitution Principle IV)

**Organization**: US1 (CAST fixes) and US2 (COUNT DISTINCT verification) are both P1 and independent.

---

## Phase 1: Setup

- [X] T001 Verify baseline: `python3 -m pytest tests/unit/ -q` — 230 tests passing

**Checkpoint**: Baseline green.

---

## Phase 2: Foundational

No foundational work needed — this is a pure translator fix.

---

## Phase 3: User Story 1 — CAST Functions Fixed (Priority: P1)

**Goal**: `toInteger()`, `toFloat()`, `toString()`, `toBoolean()` emit correct SQL.

**Independent Test**: Run `MATCH (n) WHERE toInteger(n.val) = 7 RETURN n` against live IRIS — must return rows.

### Tests (write first — TDD)

- [X] T002 [P] [US1] Unit test: `toInteger(n.x)` translates to `CAST(n_props.val AS INTEGER)` in `tests/unit/test_cypher_functions.py`
- [X] T003 [P] [US1] Unit test: `toFloat(n.x)` translates to `CAST(n_props.val AS DOUBLE)` in `tests/unit/test_cypher_functions.py`
- [X] T004 [P] [US1] Unit test: `toString(n.x)` translates to `CAST(n_props.val AS VARCHAR(4096))` in `tests/unit/test_cypher_functions.py`
- [X] T005 [P] [US1] Unit test: `toBoolean('true')` translates to `CASE WHEN LOWER(...) IN ('true','1','yes','y')` in `tests/unit/test_cypher_functions.py`
- [X] T006 [P] [US1] Unit test: `toBoolean('TRUE')` (uppercase) also maps to LOWER form in `tests/unit/test_cypher_functions.py`

### Implementation

- [X] T007 [US1] Add 4 explicit CAST/CASE returns before generic `sql_fn` emit at line ~988 in `iris_vector_graph/cypher/translator.py`

**Checkpoint**: 5 unit tests pass. CAST functions emit correct SQL.

---

## Phase 4: User Story 2 — COUNT(DISTINCT) Verified (Priority: P1)

**Goal**: `COUNT(DISTINCT expr)` emits `COUNT(DISTINCT ...)` in SQL — verify it works or fix it.

### Tests

- [X] T008 [P] [US2] Unit test: `COUNT(DISTINCT n.name)` SQL contains `COUNT(DISTINCT` substring in `tests/unit/test_cypher_functions.py`
- [X] T009 [P] [US2] E2e test: `MATCH (n:Label) RETURN COUNT(DISTINCT n.name) AS cnt` returns correct deduplicated count against live IRIS in `tests/e2e/test_cypher_coerce_e2e.py`

### Implementation

- [X] T010 [US2] Verify `AggregationFunction.distinct` flag is parsed and emitted; fix if broken in `iris_vector_graph/cypher/translator.py`

**Checkpoint**: COUNT(DISTINCT) returns correct deduplicated counts.

---

## Phase 5: E2E Validation

- [X] T011 [US1] E2e test: `MATCH (n:Gene) WHERE toInteger(n.chromosome) = 7 RETURN n.name` returns results against live IRIS in `tests/e2e/test_cypher_coerce_e2e.py`
- [X] T012 [US1] E2e test: `MATCH (d:Drug) WHERE toFloat(d.confidence) > 0.5 RETURN d.name` works against live IRIS in `tests/e2e/test_cypher_coerce_e2e.py`

**Checkpoint**: All 4 coercion functions work against live IRIS.

---

## Phase 6: Polish

- [X] T013 Run full regression: `python3 -m pytest tests/unit/ tests/e2e/ -q` — zero regressions

---

## Dependencies

- T002-T006 (unit tests): all parallel, independent of each other
- T007 (implementation): depends on T002-T006 (TDD — tests first)
- T008 (COUNT DISTINCT unit test): independent of T002-T007
- T009, T011, T012 (e2e): all need T007 done

## Implementation Strategy

**MVP**: T001 → T002-T006 (write tests) → T007 (4-line fix) → T008-T009 (COUNT DISTINCT) = **10 tasks, < 1 hour**
