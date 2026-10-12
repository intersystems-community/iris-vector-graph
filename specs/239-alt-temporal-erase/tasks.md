<!-- markdownlint-disable MD013 -->

# Tasks: Temporal globals have one writer

**Input**: specs/239-alt-temporal-erase/plan.md, spec.md. Test tasks come first. [x] = done.

## Phase 1: Tests first (no IRIS)

- [x] T001 Write `tests/unit/test_temporal_single_owner.py`: source-scan guard over `iris_src/src/**/*.cls`
  (comments stripped, string-aware), its regex self-tests, signature tests for the three new TemporalIndex methods,
  and Eraser delegation tests. Run before any ObjectScript change: 9 failed, 4 passed. The guard reported only
  `Graph/KG/Eraser.cls`.
- [x] T002 Update `tests/unit/test_eraser_inventory_coverage.py`: graph-scoped temporal stores are named in
  TemporalIndex (delegated by the Eraser), not in the Eraser. NOTE: this was edited before the ObjectScript change but
  NOT run red first; it was first run after the change.
- [x] T003 Write `tests/e2e/test_temporal_erase_owner.py` (marked e2e, uses
  `iris_connection`): graph erase, erase-all, node erase in both directions, and the
  other graph untouched. Collected (4 tests); not run (no IRIS).

**Checkpoint**: failing tests recorded in the report.

## Phase 2: Implementation

- [x] T004 `TemporalIndex.cls`: add `RemoveTemporalRow` (private); `DeleteTemporalEdge`
  uses it.
- [x] T005 `TemporalIndex.cls`: add `EraseGraph(pGraph) As %Status`.
- [x] T006 `TemporalIndex.cls`: add `EraseAll() As %Status`; `Purge` calls it.
- [x] T007 `TemporalIndex.cls`: add `EraseNodes(pGraph, ByRef pNodeIds) As %Status`.
- [x] T008 `Eraser.cls`: `EraseGraph` calls `TemporalIndex.EraseGraph`; no temporal
  kill left.
- [x] T009 `Eraser.cls`: `EraseAll` calls `TemporalIndex.EraseAll`; `labelset` stays.
- [x] T010 `Eraser.cls`: `EraseIdSet` builds `tNodeIds` and calls
  `TemporalIndex.EraseNodes` in the transaction.
- [x] T011 Unit tests pass: the source-scan and inventory guards, plus the neighbouring
  Eraser and subscript-layout guards (234 passed in total). `tests/unit/test_host.py`
  does not exist on this branch, so it was not run.

## Phase 3: Gate

- [ ] T012 Live e2e `tests/e2e/test_temporal_erase_owner.py`: NOT RUN by the package
  author (no IRIS). The lead runs it.
- [x] T013 Commit on `alt/temporal-erase` (no push, no attribution).
