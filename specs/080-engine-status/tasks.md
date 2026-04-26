# Tasks: Engine Status Snapshot (Spec 080)

**Branch**: `080-engine-status`

## Phase 1: Failing tests (test-first)

- [ ] T000 Create `tests/unit/test_engine_status.py` with imports + fixture wiring so collection succeeds
- [ ] T001 [US1] Write failing unit test `test_report_warns_kg_empty_with_edges` — mock: edges=5, kg_populated=False → report contains warning string
- [ ] T002 [US2] Write failing unit test `test_ready_for_bfs_requires_both` — kg_populated=True + edges=0 → False; both True → True
- [ ] T003 [P] [US3] Write failing unit test `test_report_contains_all_sections` — mock full EngineStatus, verify report has Tables/Adjacency/ObjectScript/Arno/Indexes sections
- [ ] T004 [P] [US4] Write failing unit test `test_errors_captured_not_raised` — probe raises → in errors, no exception surfaced
- [ ] T005 [P] [US1] Write failing E2E test `test_status_fresh_graph` — empty graph, all counts 0, no errors
- [ ] T006 [P] [US1] Write failing E2E test `test_status_after_create_edge` — create_edge() → edges>=1, kg_populated=True, ready_for_bfs=True
- [ ] T007 [P] [US3] Write failing E2E test `test_status_completes_under_500ms` — status() timing < 500ms
- [ ] T008 [P] [US4] Write failing E2E test `test_status_graceful_on_missing_tables` — IVF tables missing → empty list, no error raised
- [ ] T000-GATE Run `pytest tests/unit/test_engine_status.py` — all T001-T008 must FAIL (not ERROR) before proceeding

---

## Phase 2: ObjectScript — KGEdgeCount (blocks T011 engine probe)

- [ ] T009 Add `ClassMethod KGEdgeCount(maxCount As %Integer = 10000) As %Integer` to `iris_src/src/Graph/KG/Traversal.cls` — capped $Order over `^KG("out",0,...)`; returns 0 if ^KG empty; returns count up to maxCount
- [ ] T010 Add `ClassMethod NKGPopulated() As %Integer` to `Traversal.cls` — returns `($Data(^NKG) > 0)` (1 if any data, 0 if empty; `$Data` can return 0/1/10/11, `> 0` normalizes to boolean)

---

## Phase 3: status.py dataclasses

- [ ] T011 Create `iris_vector_graph/status.py` with all 6 dataclasses: `TableCounts`, `AdjacencyStatus`, `ObjectScriptStatus`, `ArnoStatus`, `IndexInventory`, `EngineStatus`
- [ ] T012 Add `EngineStatus.report() -> str` — formatted multi-line string with all sections; ⚠ warning when kg_populated=False and tables.edges>0
- [ ] T013 Add `EngineStatus.ready_for_bfs`, `.ready_for_vector_search`, `.ready_for_full_text` computed properties

**Gate**: T001-T004 unit tests pass after T011-T013.

---

## Phase 4: engine.py — status() method

- [ ] T014 Add `from iris_vector_graph.status import EngineStatus, ...` to `engine.py` imports
- [ ] T015 Implement `IRISGraphEngine.status() -> EngineStatus`:
  - Start timer
  - Probe table counts (6 SQL COUNT, wrapped in try/except each)
  - Probe ^KG via `Graph.KG.Traversal.KGEdgeCount(10000)` — fallback to `$Data` native if not deployed
  - Probe ^NKG via `Graph.KG.Traversal.NKGPopulated()` — fallback native
  - Probe ObjectScript class list via `%Dictionary.ClassDefinition` for 7 known classes
  - Probe Arno via `_detect_arno()` / `_arno_capabilities`
  - Probe HNSW via `COUNT(*) FROM kg_NodeEmbeddings_optimized`
  - Probe IVF/BM25/PLAID via catalog table queries
  - Stop timer, return `EngineStatus` with all fields populated

**Gate**: T005-T008 E2E tests pass after T015.

---

## Phase 5: Polish

- [ ] T016 Export `EngineStatus` from `iris_vector_graph/__init__.py` so callers can do `from iris_vector_graph import EngineStatus`
- [ ] T017 Add `engine.status()` to README.md — one-paragraph section with example output
- [ ] T018 Run full unit suite `pytest tests/unit/ -q` — 564+ pass, 0 regressions

---

## Phase 6: Commit and publish

- [ ] T019 Commit all changes with message `feat: engine.status() — structured runtime snapshot of all IVG components (spec 080)`
- [ ] T020 Bump to v1.63.3, build, publish to PyPI

---

## Dependencies

```
T000-T008 (failing tests) → all parallel
T009-T010 (ObjectScript KGEdgeCount/NKGPopulated) — parallel
T011-T013 (status.py dataclasses) — T001-T004 gate
T014-T015 (engine.py status()) — needs T009+T011
T016 (init.py export) — needs T011
T017 (README) — needs T015
T018 (full suite) — needs T015
T019-T020 (commit/publish) — needs T018
```

## Notes

- `status()` is NOT called in `__init__`, NOT called before queries — explicit only
- All probe failures → `errors: List[str]`, never raise to caller
- `kg_edge_count` reports exact count if ≤10,000, returns 10000 if at limit (caller interprets as "≥10,000")
- ObjectScript probed classes: `Graph.KG.Traversal`, `Graph.KG.PageRank`, `Graph.KG.IVFIndex`, `Graph.KG.BM25Index`, `Graph.KG.ArnoAccel`, `Graph.KG.Snapshot`, `Graph.KG.Dijkstra`
