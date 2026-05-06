# Tasks: Spec 094 — Arno BFSJson Global-Buffer Transfer

## Phase 1: Rust — kg_ffi.rs

- [ ] T001 Add `read_nkg_adj_chunks_as_str(global_name: &str) -> Result<String, String>` to `native_algos` mod in `arno/iris-integration/arno-callout/src/kg_ffi.rs` — reads `^ArnoKG("KG","nkg_adj",1..N)` chunks, returns concatenated raw string
- [ ] T002 Modify `ffi_kg_bfs_global` in `arno/iris-integration/arno-callout/src/kg_ffi.rs` — replace `for line in adj_str.split('\n')` preamble with `read_nkg_adj_chunks_as_str(&global_name)` call; rest of BFS logic unchanged
- [ ] T003 Build arno for aarch64-unknown-linux-gnu: `cargo build --release --target aarch64-unknown-linux-gnu` in `arno/`
- [ ] T004 Deploy new `libarno_callout.so` to enterprise container: `docker cp target/aarch64-unknown-linux-gnu/release/libarno_callout.so iris-vector-graph-enterprise:/usr/irissys/mgr/libarno_callout.so`

## Phase 2: ObjectScript — NKGAccel.cls

- [ ] T005 Add `WriteAdjToGlobal(seed As %String, maxHops As %Integer, predsJson As %String)` private class method to `iris_src/src/Graph/KG/NKGAccel.cls` — BFS over `^NKG`, writes `\x1f`-delimited `srcId\x1fpredName\x1fdstId\n` lines to `^ArnoKG("KG","nkg_adj",N)` in 28KB chunks
- [ ] T006 Modify `BFSJson` in `iris_src/src/Graph/KG/NKGAccel.cls` — replace `ExportAdjacencyFromSeed` call + `$ZF(-5,...,adjStr,...)` with `WriteAdjToGlobal` + `$ZF(-5,...,"^ArnoKG",...)`; keep `BFSFastJson` fallback paths unchanged
- [ ] T007 Compile `Graph.KG.NKGAccel.cls` on `iris-vector-graph-enterprise` via iris-dev MCP (`iris_compile` target `Graph.KG.NKGAccel.cls`)
- [ ] T008 Verify compile clean — no errors, no warnings on NKGAccel

## Phase 3: Tests

- [ ] T009 [P] Write `tests/e2e/test_arno_bfs_global.py` — 5 test cases covering SC-001 through SC-005: no MAXSTRING on M, correctness vs BFSFastJson, predicate filter, fallback, S-scale no regression
- [ ] T010 Run `pytest tests/e2e/test_arno_bfs_global.py -v` against `iris-vector-graph-enterprise` (port 2972) — all 5 tests must pass

## Phase 4: Benchmark Validation

- [ ] T011 Fix `detect_arno()` in `tests/benchmarks/bench_utils.py` — already done (checks `bfs and nkg_data`)
- [ ] T012 Run `IRIS_PORT=2972 conda run -n py312 python tests/benchmarks/bench.py --datasets S M --runs 10 --warmup 3`
- [ ] T013 Confirm Q2/Q3/Q4 arno path returns results (no MAXSTRING), record actual p50 numbers
- [ ] T014 Update `specs/093-arno-acceleration-benchmark/results.md` with enterprise arno numbers
- [ ] T015 Mark SC-008/SC-009/SC-010 as PASS or document actual vs target in results.md

## Dependencies

```
T001 → T002 → T003 → T004   (Rust: sequential, must build before deploy)
T005 → T006 → T007 → T008   (ObjectScript: sequential)
T004 + T008 → T009 → T010   (Tests: need both Rust and ObjScript deployed)
T010 → T011 → T012 → T013 → T014 → T015  (Benchmark: needs tests passing)
```

T009 [P] can be written in parallel with T003/T004/T007/T008.
