<!-- markdownlint-disable MD013 -->

# Tasks: Freshness module for Rebuilt stores

Test tasks come first. Tick each box when done.

## Phase 1: Unit tests first (red)

- [x] T001 Write `tests/unit/test_freshness.py` with the in-memory `FakeHost` and 16 tests (never stamped, stamp then
      current, token moves, re-stamp, rolled-back bump, per-store, stale order, empty, token unset = 0, host-only access).
- [x] T002 Run it and confirm red: collection fails with `No module named 'iris_vector_graph.freshness'`.

## Phase 1 gate

- [x] T003 Unit tests green after the module exists (16 passed).

## Phase 2: Live e2e test

- [x] T004 Write `tests/e2e/test_freshness_live.py` against `host_for(iris_connection)` (4 tests, marked `e2e`).
- [ ] T005 Run on the enterprise container (lead). Not run in the worktree. Blocked on `host.py` being on this branch
      (collection currently fails with `No module named 'iris_vector_graph.host'`).

## Phase 2 gate

- [ ] T006 e2e green on `ivg-iris-enterprise` (lead).

## Phase 3: Implementation

- [x] T007 `iris_vector_graph/freshness.py`: `Freshness` with `token`, `stamp`, `built_from`, `is_current`,
      `stale_stores`; constants `NKG`, `DEG2P`.
- [x] T008 Lint (`ruff check`) on the three new files.

## Phase 4: Report

- [x] T009 Replacement table for `_nkg_dirty` sites in the final report (no engine edits; see report).
