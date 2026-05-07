# Tasks: IC3 Exact 2-Hop COUNT

**Branch**: `152-ic3-exact-2hop-count`
**Spec**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md)

---

## Phase 1: Setup

- [x] T001 Verify `iris-enterprise-2026` container accessible and has LDBC SF10 data — use `IRISContainer.attach("iris-enterprise-2026")` to resolve port dynamically; skip benchmark tests if unavailable
- [x] T002 Verify `arno-builder` Docker image exists (`docker images | grep arno-builder`) and `libarno_callout.so` deployed to `/tmp/` on enterprise container
- [x] T003 [P] Create empty `tests/e2e/test_ic3_exact_count.py`

**Checkpoint**: Environment verified, test stub created.

---

## Phase 2: Test-First (Constitution Principle III)

> **Write BEFORE Rust/ObjectScript implementation. Must FAIL until Phase 3+4 complete.**

- [x] T004 [US1] Write `test_khop2_count_exact_matches_khop2_count` in `tests/e2e/test_ic3_exact_count.py` — after `Build2HopExactStats`, `KHop2CountExact(seed, 'KNOWS')` == `KHop2Count(seed, 'KNOWS')` for 3 seeds including `p_28587302384882`
- [x] T005 [US1] Write `test_khop2_count_exact_under_1ms` in `tests/e2e/test_ic3_exact_count.py` — after `Build2HopExactStats`, p50 < 1ms for `KHop2CountExact`
- [x] T006 [US1] Write `test_execute_cypher_2hop_count_uses_exact` in `tests/e2e/test_ic3_exact_count.py` — `execute_cypher('MATCH (s)-[:KNOWS*2]->(n) RETURN count(n)')` returns correct result in <1ms
- [x] T007 [US1] Write `test_khop2_count_exact_fallback` in `tests/e2e/test_ic3_exact_count.py` — when `^KG("deg2p_exact")` not populated, `KHop2CountExact` returns same result as `KHop2Count`
- [x] T008 [US2] Write `test_rebuild_nkg_under_30s` in `tests/e2e/test_ic3_exact_count.py` — `engine.rebuild_nkg()` completes in ≤30s on enterprise IRIS; use `IRISContainer.attach("iris-enterprise-2026")` to connect; skip if container unavailable or has no LDBC data
- [x] T009 Confirm T004-T008 all FAIL — T004/T005/T006/T007 fail because `KHop2CountExact` method does not exist (expect `<METHOD DOES NOT EXIST>` error or import error); T008 may error or skip

**Checkpoint**: 5 failing tests committed.

---

## Phase 3: Rust Implementation

- [x] T010 Add `ffi_kg_build_2hop_exact()` to `~/ws/arno/iris-integration/arno-callout/src/kg_ffi.rs` — reads `^KG("out",0,s,p,mid)` for each (s,p), builds HashSet H1 for hop-1, dedup HashSet for hop-2 (excluding H1 and src), writes `^KG("deg2p_exact",s,p) = exact_count`
- [x] T011 Register `kg_build_2hop_exact` wrapper in `~/ws/arno/iris-integration/arno-callout/src/lib.rs` under `#[cfg(feature = "zf_global")]`
- [x] T012 Cross-build `libarno_callout.so` using `arno-builder` Docker image per spec 094 procedure — `docker run --rm -v "<workspace_root>/..:/src" -w /src/<arno-callout-relative-path> arno-builder aarch64`; output at `~/ws/arno/target/aarch64-unknown-linux-gnu/release/libarno_callout.so`
- [x] T013 Verify `KG_BUILD_2HOP_EXACT_WRAPPER` exported: `nm -D ~/ws/arno/target/aarch64-unknown-linux-gnu/release/libarno_callout.so | grep BUILD_2HOP`
- [x] T014 Deploy new `.so` to enterprise container: `docker cp <path-to-so> iris-enterprise-2026:/tmp/libarno_callout.so` — path verified from T012 output

**Checkpoint**: Rust function built and deployed.

---

## Phase 4: ObjectScript Implementation

- [x] T015 Add `KHop2CountExact(srcId, pred)` to `iris_src/src/Graph/KG/Traversal.cls` — `$Get(^KG("deg2p_exact", srcId, pred), -1)` ≥0 → return; else → `KHop2Count(srcId, pred)`
- [x] T016 Add `Build2HopExactStats()` to `Traversal.cls` — tries `ArnoAccel.IsAvailable()` + `kg_build_2hop_exact` DLL call; falls back to ObjectScript dedup scan (slow)
- [x] T017 Update `BuildNKG` in `Traversal.cls` — call `Do ..Build2HopExactStats()` after `Do ..Build2HopStats()`
- [x] T018 Compile `Graph.KG.Traversal.cls` on `iris-enterprise-2026` and `iris_vector_graph` containers — zero errors
- [x] T019 Run `Build2HopExactStats` manually on enterprise, verify `^KG("deg2p_exact", "p_28587302384882", "KNOWS")` = 37276

**Checkpoint**: ObjectScript compiled, exact count verified.

---

## Phase 5: Engine Wiring

- [x] T020 Add `khop2_count_exact(node_id, pred)` to `iris_vector_graph/engine.py` — `KHop2Input(node_id=node_id)` + `classMethodValue("Graph.KG.Traversal", "KHop2CountExact", ...)`
- [x] T021 Add `backfill_deg2p_exact()` to engine — calls `Build2HopExactStats` (returns count)
- [x] T022 Update `rebuild_nkg()` to call `Build2HopExactStats` after `BuildNKG` (already calls `Build2HopStats`)
- [x] T023 Update `_try_khop_fast_path` `_2HOP_COUNT_RE` in engine — change from `KHop2Count` to `KHop2CountExact`

**Checkpoint**: Engine wired.

---

## Phase 6: End-to-End Validation (Constitution Principle IV — Non-Optional)

- [x] T024 Run `pytest tests/e2e/test_ic3_exact_count.py -v` — all 5 tests pass
- [x] T025 [P] Run `pytest tests/e2e/test_cypher_vl_path_bfs.py -q` — 8/8 pass (regression)
- [x] T026 [P] Run `pytest tests/unit/test_validation.py tests/unit/test_ivgresult.py -q` — pass
- [x] T027 Benchmark IC3 before/after:
  - Before: `KHop2Count` p50 ~70ms
  - After: `KHop2CountExact` p50 <1ms, result matches exactly

**Checkpoint**: All tests pass, <1ms confirmed.

---

## Phase 7: Polish

- [x] T028 Update `ENGINEERING_DEBT.md` — mark IC3 exact 2-hop COUNT resolved
- [x] T029 Update benchmark table in `docs/performance/BENCHMARKS.md` with new IC3 COUNT number
- [x] T030 Bump version to `1.88.0` and publish

---

## Dependencies

- Phase 1 → Phase 2 (tests before impl) → Phase 3 (Rust) → Phase 4 (ObjScript) → Phase 5 → Phase 6 → Phase 7
- T010-T011 (write Rust) can start in parallel with T015-T016 (write ObjectScript)
- T012-T014 (build+deploy Rust) must complete before T018-T019 (compile+verify ObjScript calling Rust)
- T009 must confirm tests fail before Phase 3/4 begin

## Notes

- Constitution Principle III: T009 confirms tests fail (method not found or assertion error) before Phase 3 begins.
- Constitution Principle IV: All e2e tests use `IRISContainer.attach("iris_vector_graph")` for community tests; enterprise benchmark tests use `IRISContainer.attach("iris-enterprise-2026")` and skip gracefully if unavailable.
- Constitution Principle VI: No hardcoded ports. Container name `iris_vector_graph` from `docker-compose.yml`. Arno build path resolved at runtime from T012 output, not hardcoded from local machine path.
- `ffi_kg_build_2hop_exact` writes to `^KG("deg2p_exact")` — a new subscript key, not conflicting with `deg2p` (upper bound) or `degp` (1-hop).
