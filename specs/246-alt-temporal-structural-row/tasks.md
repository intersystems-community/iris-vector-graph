# Tasks: Temporal edges own a structural row (opt-in)

**Input**: `specs/246-alt-temporal-structural-row/plan.md`, `spec.md`
**Order**: test tasks first (rule 1 of the brief); each phase ends with its tests green.

## Phase 1: Tests first (must fail before Phase 2)

- [ ] T001 Write `tests/unit/test_temporal_structural_rows.py`: flag default off and no
      `rdf_edges` statement or `create_edge` call with no graph (US2.1)
- [ ] T002 Same file: flag on, no graph, one INSERT with marker `{"temporal": true}` (US1.1)
- [ ] T003 Same file: flag on, graph `"acme"` passed through, exactly one INSERT (US1.1)
- [ ] T004 Same file: flag on, existing row, no INSERT (US1.2)
- [ ] T005 Same file: flag on, strict ledger, no INSERT (US1.3)
- [ ] T006 Same file: flag on, store error, no INSERT (US1)
- [ ] T007 Same file: delete flag on, marked row, no survivors, DELETE runs (US3.1)
- [ ] T008 Same file: delete flag on, surviving sibling, no DELETE (US3.2)
- [ ] T009 Same file: delete flag on, window read error, no DELETE (US3.3)
- [ ] T010 Same file: delete flag on, unmarked row, no DELETE (US3.4)
- [ ] T011 Same file: delete flag off, no `rdf_edges` SELECT or DELETE (US3.5)
- [ ] T012 Same file: backfill dry run zero writes, row count unchanged (US4.1)
- [ ] T013 Same file: backfill idempotent, second run inserts 0 (US4.2)
- [ ] T014 Same file: backfill leaves existing unmarked rows alone (US4.3)
- [ ] T015 Same file: backfill passes `graph` through and marks inserted rows (FR-002, FR-003)
- [ ] T016 Write `tests/e2e/test_temporal_structural_row_live.py` (US5), marked e2e, not run here
- [ ] T017 Run the new unit file and confirm the failures are the expected ones (red)

## Phase 2: Implementation

- [ ] T018 Add `temporal_structural_rows` keyword to `IRISGraphEngine.__init__`
      (`iris_vector_graph/engine.py`)
- [ ] T019 Write path in `iris_vector_graph/_engine/temporal.py` `create_edge_temporal`
- [ ] T020 Delete path in `iris_vector_graph/_engine/temporal.py` `delete_edge_temporal`
- [ ] T021 Create `iris_vector_graph/migrations/temporal_structural_rows.py` with
      `backfill_structural_rows` and a report type
- [ ] T022 Run `tests/unit/test_temporal_structural_rows.py` (green)

## Phase 3: Regression

- [ ] T023 Run existing unit tests touching the temporal API (see report) and
      `tests/unit/test_host.py`
- [ ] T024 Commit with a plain-words message, no attribution lines, never push
