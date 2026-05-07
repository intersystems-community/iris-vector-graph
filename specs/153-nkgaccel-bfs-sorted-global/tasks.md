# Tasks: NKGAccel BFS Unified Output via Sorted Global

**Branch**: `153-nkgaccel-bfs-sorted-global`
**Spec**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md)

---

## Phase 1: Setup

- [ ] T001 Verify `iris_vector_graph` community container accessible via `IRISContainer.attach("iris_vector_graph")` — no hardcoded ports
- [ ] T002 Verify `iris-enterprise-2026` accessible via `IRISContainer.attach("iris-enterprise-2026")` with arno Rust BFS active
- [ ] T003 [P] Create `tests/e2e/test_arno_bfs_unified.py` stub

**Checkpoint**: Environment verified, test stub created.

---

## Phase 2: Test-First (Constitution Principle III)

> **Write BEFORE NKGAccel.cls change. Must FAIL until Phase 3 complete.**

- [ ] T004 [US1] Write `test_rust_bfs_output_format_is_sorted` — with arno Rust active, call `NKGAccel.BFSJson` directly; assert return value starts with `"SORTED:"` not `"CHUNKED:"`
- [ ] T005 [US1] Write `test_rust_bfs_result_matches_objectscript` — compare result count from arno path vs ObjectScript fallback for same seed/hops on enterprise
- [ ] T006 [US1] Write `test_unbounded_rust_bfs_no_maxstring` — unbounded VL path on high-degree seed with Rust active; assert completes without error
- [ ] T007 [US2] Write `test_engine_chunked_branch_removed` — grep `engine.py` for `"CHUNKED:"` at line ~1575 (the `BFSFastJsonChunked` branch); assert it is absent after T014
- [ ] T008 Confirm T004 FAILS (`NKGAccel.BFSJson` currently returns assembled JSON, not `"SORTED:"`)

**Checkpoint**: 4 failing tests committed.

---

## Phase 3: NKGAccel.cls Change

- [ ] T009 Read `NKGAccel.BFSJson` current implementation in `iris_src/src/Graph/KG/NKGAccel.cls` lines 453-595
- [ ] T010a **BENCHMARK GATE**: time `NKGAccel.BFSJson` on enterprise with 15K-result BFS BEFORE change; record baseline. If ObjectScript conversion (T010) adds >50% overhead, stop and assess Rust alternative.
- [ ] T010 Update `BFSJson` — after assembling chunks + killing `^ArnoKG("bfs_result")`: iterate JSON via `%DynamicArray.%FromJSON()`, write `^ArnoKG("bfs_r", tag, step, o) = $LB(s,p,w)`, return `"SORTED:tag"`
- [ ] T010b **BENCHMARK GATE**: time same 15K-result BFS AFTER T010; verify within 20% of baseline (SC-002). If not, rollback T010 and open Rust alternative spec.
- [ ] T011 Compile `Graph.KG.NKGAccel.cls` on all containers — zero errors
- [ ] T012 Verify T004 now PASSES (`NKGAccel.BFSJson` returns `"SORTED:"`)

**Checkpoint**: NKGAccel returns sorted global format.

---

## Phase 4: Engine Change

- [ ] T013 Locate engine handling: `NKGAccel.BFSJson` return hits `engine.py:1539` (`_json.loads(str(bfs_json))`) — update this line to detect `"SORTED:"` prefix and route to `ReadBFSResults`/`_bfs_stream_pages` instead of direct parse
- [ ] T014 Remove stale `"CHUNKED:"` branch at `engine.py:1575` (the `BFSFastJsonChunked` legacy path); add deprecation `logger.warning` guard
- [ ] T015 Verify T007 PASSES (grep confirms `"CHUNKED:"` branch removed)

**Checkpoint**: Engine has single unified `"SORTED:"` path.

---

## Phase 5: End-to-End Validation (Constitution Principle IV — Non-Optional)

- [ ] T016 Run `pytest tests/e2e/test_arno_bfs_unified.py -v` — all 4 tests pass
- [ ] T017 [P] Run `pytest tests/e2e/test_cypher_vl_path_bfs.py -q` — 8/8 pass (regression)
- [ ] T018 [P] Run `pytest tests/e2e/test_streaming_bfs.py -q` — 5/5 pass (regression)
- [ ] T019 Run `pytest tests/unit/test_cypher_parser.py tests/unit/test_cypher_translator.py -q` — pass

**Checkpoint**: Zero regressions, all acceptance scenarios pass.

---

## Phase 6: Polish

- [ ] T020 Update `ENGINEERING_DEBT.md` — mark NKGAccel BFS sorted global resolved
- [ ] T021 Bump version to `1.89.0` and publish

---

## Dependencies

- Phase 1 → Phase 2 (tests before impl) → Phase 3 (NKGAccel) → Phase 4 (engine) → Phase 5 → Phase 6
- T008 must confirm T004 fails before Phase 3 begins
- T017 and T018 can run in parallel with T019

## Notes

- Constitution Principle III: T008 confirms T004 fails before Phase 3 begins.
- Constitution Principle IV: All e2e tests use `IRISContainer.attach()` — no hardcoded ports.
- Constitution Principle VI: Container names from docker-compose files only.
- **I1 fix**: The `"CHUNKED:"` at `engine.py:1575` is from `BFSFastJsonChunked` (legacy ObjectScript fallback), NOT from the Rust path. T014 removes this stale branch. T013 updates the Rust path routing at line 1539.
- **T010a/T010b benchmark gates**: ObjectScript `%DynamicArray.%FromJSON()` + 15K `$Set` operations may add significant overhead. Must benchmark before committing. If >20% slower, open a separate Rust-side spec.
- Tag format: `$Job _ "_bfs"` — scoped per IRIS process, sequential calls safe.
