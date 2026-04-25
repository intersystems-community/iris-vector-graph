# Tasks: Rust-Accelerated BFS (Spec 079)

**Branch**: `079-rust-accelerated-bfs`
**Repos**: `arno` (Rust/ObjectScript) + `iris-vector-graph` (Python wiring)

## Phase 1: Failing tests (test-first)

- [ ] T001 [US-baseline] Measure and document BFS breakdown: run BFSFastJson under profiling to confirm ~40ms traversal / ~60ms JSON / ~28ms wire. Record in spec as confirmed baseline.
- [ ] T002 [US1] Write failing E2E test `test_bfs_arno_correctness` in `tests/unit/test_bfs_arno.py` — with Arno loaded, BFS 1..3 returns same node set as BFSFastJson (SC-002)
- [ ] T003 [P] [US1] Write failing E2E test `test_bfs_arno_perf` — BFS 1..3 on 10K/50K graph: p50 < 30ms (SC-001)
- [ ] T004 [P] [US3] Write failing E2E test `test_bfs_arno_predicate_filter` — `[:BINDS*1..3]` returns only BINDS-reachable nodes, not REGULATES-reachable (SC-003)
- [ ] T005 [P] [US1] Write failing E2E test `test_bfs_arno_fallback` — with Arno not available, BFS still returns correct results via BFSFastJson (SC-005)
- [ ] T006 [P] [US4] Write failing E2E test `test_bfs_arno_max_results` — `LIMIT 100` honored (SC-004)

**Gate**: All T001–T006 FAIL (not error) before proceeding to T007.

---

## Phase 2: Rust implementation (arno repo)

- [ ] T007 Add `read_nkg_adjacency_with_preds() -> Result<(Vec<String>, Vec<Vec<(usize, usize)>>), String>` to `arno/iris-integration/arno-callout/src/kg_ffi.rs` — walks `^NKG(-1, sIdx, -(pIdx+1), oIdx)` preserving `(oIdx, pIdx)` pairs
- [ ] T008 Add `resolve_pred_indices(predicates: &[String]) -> Vec<usize>` helper in `kg_ffi.rs` — uses `^NKG("$LI", pred)` callin lookup to map string predicates to integer indices
- [ ] T009 Add `ffi_kg_bfs_global(global_name, seed, predicates_json, max_hops, max_results)` to `kg_ffi.rs`:
  - If predicates empty: use `read_nkg_adjacency_auto()` + unfiltered BFS
  - If predicates non-empty: use `read_nkg_adjacency_with_preds()` + `resolve_pred_indices()` + filtered BFS
  - VecDeque BFS respecting max_hops and max_results
  - Returns `[{"s":..,"p":..,"o":..,"w":1.0,"step":N},...]` matching BFSFastJson format exactly
- [ ] T010 Add `#[rzf] pub fn kg_bfs_global(global_name: String, seed: String, predicates_json: String, max_hops: i64, max_results: i64) -> String` to `arno/iris-integration/arno-callout/src/lib.rs`
- [ ] T011 Run Arno unit tests: `cargo test -p arno-callout` — all pass

---

## Phase 3: ObjectScript shim (arno repo)

- [ ] T012 Add `BFSJson(seed, predicatesJson, maxHops, maxResults)` class method to `Graph.KG.NKGAccel.cls` — same `$ZF(-5)` pattern as `PPRNative`; function name `"kg_bfs_global"`
- [ ] T013 Add `"bfs": true` to `Capabilities()` JSON string in `Graph.KG.NKGAccel.cls`
- [ ] T014 Compile and test `Graph.KG.NKGAccel` in IRIS

---

## Phase 4: Python wiring (ivg repo)

- [ ] T015 In `iris_vector_graph/engine.py` `_execute_var_length_cypher`: after `_detect_arno()` check, add:
  ```python
  if self._arno_capabilities.get("bfs"):
      try:
          bfs_json = self._arno_call("Graph.KG.NKGAccel", "BFSJson",
              source_id, predicates_json, max_hops, vl.get("max_results", 0))
          bfs_results = _json.loads(str(bfs_json))
      except Exception as e:
          logger.warning(f"Arno BFSJson failed, falling back: {e}")
          # fall through to BFSFastJson
  ```
- [ ] T016 Verify T002–T006 tests pass with Arno loaded
- [ ] T017 Verify T005 (fallback) passes without Arno

---

## Phase 5: Benchmark validation

- [ ] T018 Run `tests/unit/test_cypher_benchmark_scale.py` with Arno loaded — BFS tests now show p50 < 30ms
- [ ] T019 Run `tests/unit/test_cypher_benchmark.py` (200-node suite) — all 12 still pass
- [ ] T020 Run full unit suite `tests/unit/` — 560+ pass, 0 regressions

---

## Phase 6: Polish

- [ ] T021 Update `README.md` — note Arno acceleration for BFS with performance numbers
- [ ] T022 Update `docs/architecture/rust-accelerator.md` — add BFS to the capability table
- [ ] T023 Commit, version bump (1.63.0), build, publish to PyPI

---

## Dependencies

```
T001 (baseline profiling — informational, non-blocking)
T002–T006 (failing tests, must fail before T007)
T007–T009 (kg_ffi.rs) → T010 (lib.rs) → T011 (cargo test)
T012–T014 (ObjectScript) — parallel with T007–T011
T015 (Python wiring) — needs T010 + T012 done
T016–T017 (test validation) — needs T015
T018–T020 (benchmark) — needs T016
T021–T023 (polish) — needs T018
```

## Parallel opportunities

- T007–T009 (kg_ffi.rs) and T012–T014 (ObjectScript) can be developed in parallel
- T002–T006 (test stubs) can all be written in one pass before any implementation

## Notes

- **arno** must be rebuilt and `libarno_callout.so` redeployed to the test container for Arno tests to run
- The `zf_global` feature flag in `arno-callout/Cargo.toml` must be enabled (it controls `^NKG` callin access)
- Test container for IVG does NOT load arno by default — T002/T003/T004 tests need `pytest -m arno` or a dedicated container with the `.so` loaded
