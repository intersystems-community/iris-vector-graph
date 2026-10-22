<!-- markdownlint-disable MD013 -->

# Tasks: spec 255

Tests first in every phase; a phase closes when its E2E test passes.

## Phase 1: tests (fail first)

- [x] T001 Unit: `tests/unit/test_255_temporal_window_reader.py`: cursor paging and close (exhausted, `close()`,
      abandoned, error), spec JSON, FR-006 (`execute_temporal_window_query` sets `error`; `get_edges_in_window`
      raises), `get_latest_edges` chunking, `rebuild_temporal_latest`.
- [x] T002 Unit: `tlast` in the store guards (single owner, eraser inventory, `GraphStores`, snapshot plan).
- [x] T003 E2E US1: `tests/e2e/test_255_window_reader_us1_e2e.py`: hero query equals `QueryWindow` + `GetEdgeAttrs`
      in one round trip; source, list (union, once each), prefix, predicates, inbound; edge cases.
- [x] T004 E2E US2: `tests/e2e/test_255_window_reader_us2_e2e.py`: a window past the string cap read in pages equals
      a `^KG("tout")` walk; state footprint; `close()`; `get_edges_in_window` raises past the cap.
- [x] T005 E2E US3: `tests/e2e/test_255_window_reader_us3_e2e.py`: latest per source against brute force, with and
      without predicate and cutoff; absent sources; exact after delete, purge, node erase; pre-feature graph.

## Phase 2: implementation

- [x] T010 `Graph.KG.TemporalWindow`: `Open`, `Next`, `Close`, `State`, `Latest`.
- [x] T011 `TemporalIndex`: `tlast` maintenance in `WriteStores`, removals, migration, erase; `RebuildLatest`;
      FR-008 comments.
- [x] T012 Inventory: `GraphStores`, snapshot plan.
- [x] T013 Python: `TemporalWindowCursor`, `iter_edges_in_window`, `get_latest_edges`, `rebuild_temporal_latest`,
      `TemporalWindowError`, FR-006 in the store and the mixin; facade methods in `engine.py`.

## Phase 3: gates

- [x] T020 Unit suite and the temporal E2E files green on IVGTEST.
- [x] T021 SC measurements (small, shared container) recorded in the report.
