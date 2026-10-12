<!-- markdownlint-disable MD013 MD060 -->

# Tasks: One writer for the adjacency stores

**Input**: `specs/245-alt-adjacency-owner/plan.md`, `spec.md`. Test tasks come first.

## Phase 1: Tests first (must FAIL before Phase 2)

- [x] T001 Write `tests/unit/test_adjacency_single_writer.py`: source scan of `iris_src/src/**/*.cls` (comments stripped)
      for Set/Kill/Merge/`$Increment` of `^KG("out"`/`^KG("in"`; only `Adjacency.cls` plus the allow-list (with reasons).
      Include a self-test of the scanner on known write and read snippets.
- [x] T002 Add delegation tests to the same file: `EdgeScan` WriteAdjacency/DeleteAdjacency/WriteAdjacencyShadow/
      WriteAdjacencyKeyed/DeleteAdjacencyKeyed call `Graph.KG.Adjacency` and contain no `Set`/`Kill` of out/in; `Adjacency.cls`
      defines Put, PutKeyed, Remove, RemoveKeyed, PutShadow, Reweight with the signatures in plan.md.
- [x] T003 Run both new tests with the pytest command from the brief; record the failures (14 failed, 19 scanner self-tests passed, before any `.cls` edit).
- [x] T004 Write `tests/e2e/test_adjacency_owner_live.py` (marked e2e, not run here): ledger apply, loader and
      EdgeScan path, compare out/in/deg/degp/`__version` to the recorded pre-change values; Loader asserts graph 0.

## Phase 2: Implementation

- [x] T005 Create `iris_src/src/Graph/KG/Adjacency.cls` (uncompiled): Put, PutShadow, PutKeyed, Remove, RemoveKeyed,
      Reweight, private SetLeafFromLowest and OtherEdgeKey, bodies lifted from EdgeScan.
- [x] T006 Replace the EdgeScan primitives with one-line delegations; delete the moved private helpers from EdgeScan.
- [x] T007 Route LedgerApply (OpCreateRel both branches, PersistQuals, DeleteRel) through Adjacency; no duplicate version bump.
- [x] T008 Route Loader.Load through `Adjacency.Put(0, ...)`; drop its deg/degp increments; update its header comment.
- [x] T009 Route BenchFormat.Seed through `Adjacency.Put(0, ...)`; walkers read graph key 0; drop the explicit deg Set.
- [x] T010 Repoint the counter-ownership tests in `tests/unit/test_adjacency_counter_ownership.py` that read
      EdgeScan's WriteAdjacency/DeleteAdjacency bodies to the Adjacency Put/Remove bodies. Explain in the report.
- [ ] T011 Run the T001-T003 tests until green, plus the neighbouring source-scan tests. Partly done: the new tests and
      the ownership tests are green. Not green, and outside this package's files: `test_234_multigraph_sql.py` (pins the
      EdgeScan writer text and version-bump regex) and `test_objectscript_coverage_gate.py` (class list in
      `docs/objectscript-coverage-floor.json`). Reported to the lead; not edited here. `test_module_size.py` fails on
      files this package does not touch.

## Phase 3: Commit

- [ ] T012 Commit specs (`git add -f specs/245-alt-adjacency-owner`) and code in logical commits. No attribution lines. No push.
