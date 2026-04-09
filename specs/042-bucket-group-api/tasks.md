# Tasks: Bucket Group API Enhancements

**Input**: Design documents from `/specs/042-bucket-group-api/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, quickstart.md

**Organization**: Tasks are grouped by user story. US1 (sourcePrefix) and US3 (docstring) touch different parts of the same methods, so US3 is absorbed into US1. US2 (GetBucketGroupTargets) is fully independent. All changes live in `iris_src/src/Graph/KG/TemporalIndex.cls` and `iris_vector_graph/engine.py`, with tests in `tests/unit/test_temporal_edges.py`.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (US1, US2)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm existing test infrastructure is wired correctly before writing new tests.

- [X] T001 Verify `tests/unit/test_temporal_edges.py` has `SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"` at top (line 9) and `iris_test_container` fixture is reachable — no changes needed if already present

**Checkpoint**: `pytest tests/unit/test_temporal_edges.py --collect-only` succeeds

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: No shared infrastructure needed beyond Phase 1. All changes are additive to existing methods.

*(No foundational tasks — changes are fully contained within US1 and US2 phases.)*

---

## Phase 3: User Story 1 — sourcePrefix Filter + Docstring (Priority: P1)

**Goal**: `GetBucketGroups` accepts an optional `sourcePrefix` parameter and filters results server-side. `get_bucket_groups()` Python wrapper gains `source_prefix` kwarg and a full return-schema docstring.

**Independent Test**: Ingest edges for two distinct source prefixes, call `get_bucket_groups(..., source_prefix="Prefix1:")`, assert only Prefix1 rows returned and Prefix2 rows absent.

### Tests for User Story 1

- [X] T002 [US1] Add unit test class `TestGetBucketGroupsSourcePrefix` to `tests/unit/test_temporal_edges.py` with mocked `_iris_obj()`
- [X] T003 [US1] Add live-container test class `TestGetBucketGroupsSourcePrefixE2E` to `tests/unit/test_temporal_edges.py` under `@pytest.mark.skipif(SKIP_IRIS_TESTS, ...)` — use `iris_connection` fixture from `conftest.py`
- [X] T004 [P] [US1] Add unit test `test_get_bucket_groups_docstring` to `tests/unit/test_temporal_edges.py`

### Implementation for User Story 1

- [X] T005 [US1] Modify `GetBucketGroups` in `iris_src/src/Graph/KG/TemporalIndex.cls` — add `sourcePrefix As %String = ""` param + `$Extract` filter in inner loop
- [X] T006 [P] [US1] Modify `get_bucket_groups()` in `iris_vector_graph/engine.py` — add `source_prefix=""` kwarg, pass as fourth arg, add full docstring

**Checkpoint**: `pytest tests/unit/test_temporal_edges.py::TestGetBucketGroupsSourcePrefix tests/unit/test_temporal_edges.py::test_get_bucket_groups_docstring -v` — all green ✓

---

## Phase 4: User Story 2 — GetBucketGroupTargets (Priority: P2)

**Goal**: New `GetBucketGroupTargets(source, predicate, tsStart, tsEnd)` ObjectScript method + `get_bucket_group_targets()` Python wrapper. Returns distinct target node IDs for a source+predicate over a time window by scanning the existing `^KG("tin")` reverse index.

**Independent Test**: Ingest `ProcessHL7 → G1`, `ProcessHL7 → G2`, `OtherRoutine → G3`. Call `get_bucket_group_targets("...ProcessHL7", "CALLED_BY", ts0, ts1)`. Assert G1 and G2 returned, G3 absent.

### Tests for User Story 2

- [X] T007 [US2] Add unit test class `TestGetBucketGroupTargets` to `tests/unit/test_temporal_edges.py` with mocked `_iris_obj()`
- [X] T008 [US2] Add live-container test class `TestGetBucketGroupTargetsE2E` to `tests/unit/test_temporal_edges.py` — use `iris_connection` fixture; covers deduplication and window boundary (SC-003)

### Implementation for User Story 2

- [X] T009 [US2] Add `GetBucketGroupTargets` class method to `iris_src/src/Graph/KG/TemporalIndex.cls` — scans `^KG("tin")` reverse index, deduplicates with local `dedup` array
- [X] T010 [P] [US2] Add `get_bucket_group_targets()` to `iris_vector_graph/engine.py`

**Checkpoint**: `pytest tests/unit/test_temporal_edges.py::TestGetBucketGroupTargets -v` — all green ✓

---

## Phase 5: Polish & Cross-Cutting Concerns

- [X] T011 [P] Full unit test suite passes: 22 passed, 21 skipped — zero regressions
- [X] T012 [P] Backward compat verified: all existing callers pass 3 positional args; `source_prefix=""` default is non-breaking
- [X] T013 Update `specs/042-bucket-group-api/checklists/requirements.md` — mark all items complete
- [X] T014 [P] **Out-of-scope tracking**: SC-002 ("RoutineTimelineStore idempotency produces no false positives in multi-tenant namespace") is validated in the opsreview follow-on commit on branch `006-rtn-correlation`, not in this IVG branch. Confirm the follow-on task is recorded in opsreview before closing this branch.

---

## Dependency Graph

```
T001 (setup)
  └── T002, T003, T004 (US1 tests — parallel)
        └── T005, T006 (US1 impl — parallel)
              └── T007, T008 (US2 tests — parallel with US1 impl)
                    └── T009, T010 (US2 impl — parallel)
                          └── T011, T012, T013 (polish — parallel)
```

## Implementation Strategy

**MVP = Phase 3 (US1) only** — `sourcePrefix` filter fixes the multi-tenant correctness bug and is the highest-value change. US2 adds new capability. US3 (docstring) is folded into T006.
