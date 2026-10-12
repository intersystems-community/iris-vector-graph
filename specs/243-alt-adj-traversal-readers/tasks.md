<!-- markdownlint-disable MD013 -->

# Tasks: Traversal readers stop reading the stored degree counters

**Input**: [plan.md](plan.md), [spec.md](spec.md)
**Tests**: required by project rule. Test tasks come first in every phase.

## Phase 1: Tests first

- [x] T001 [US1,US2,US3] Write source scan `tests/unit/test_traversal_readers_no_stored_counter.py` (fails before the change)
- [x] T002 [US1,US2] Write live e2e `tests/e2e/test_traversal_readers_live.py` (uncompiled, lead runs against IRIS)
- [x] T003 Run T001 and record which tests failed before any source edit

## Phase 2: Implementation

- [x] T004 [US1] TraversalBFS.cls: two pruning sites use `^KG("out", ...)`
- [x] T005 [US2] TraversalKHop.cls: delete the stored-counter shortcut in KHopCount
- [x] T006 [US2] TraversalBuild.cls: inline `^NKG(-3,s)` accumulation in BuildNKG
- [x] T007 [US2] TraversalBuild.cls: one-pass `deg2p` in Build2HopStats
- [x] T008 [US3] ArnoAccel.cls: sources via `Graph.KG.Adj.NextSource`
- [x] T009 [US3] NKGAccelAdjacency.cls: delete the `^KG` fallback
- [x] T010 Run unit scan and the existing unit tests that pin these classes (206 pass). `tests/unit/test_host.py` is not in this worktree, so it was not run

## Phase 3: Polish

- [x] T011 Report: uncompiled ObjectScript, deviations, needs-from-lead
