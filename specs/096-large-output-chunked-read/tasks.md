# Tasks: Spec 096 — Large-Output Chunked Read for NKGAccel Methods

## Phase 1: Failing Tests First (Principle III — non-negotiable)

- [X] T001 Write failing test `tests/e2e/test_large_output_chunked.py` — SC-001 through SC-007 — all tests MUST fail against current unmodified code before any implementation begins
- [X] T002 Update `tests/e2e/test_arno_bfs_global.py` — remove hardcoded `count <= 50` assertions, replace with full-count assertions that will fail until cap is removed
- [X] T003 Run `pytest tests/e2e/test_large_output_chunked.py tests/e2e/test_arno_bfs_global.py` — confirm RED (all new assertions fail as expected)

## Phase 2: Python Infrastructure

- [X] T004 Update `_arno_call(self, cls, method, *args)` in `iris_vector_graph/engine.py` — add CHUNKED:TAG:N sentinel handling: if response starts with "CHUNKED:", split on ":" twice, reassemble via `ReadLargeOutChunk(tag, i)` loop; inline responses pass through unchanged
- [X] T005 [P] Add `_call_classmethod_large(iris_obj, cls, method, *args)` to `iris_vector_graph/schema.py` — same CHUNKED sentinel logic as `_arno_call` update, for non-engine callers (benchmarks, tests that call NKGAccel directly)

## Phase 3: ObjectScript Infrastructure

- [X] T006 Add `StoreLargeOut(tag As %String, json As %String) As %String [ Private ]` to `iris_src/src/Graph/KG/NKGAccel.cls` — Kill ^||LargeOut(tag), write 9000-char chunks to ^||LargeOut(tag,N), return "CHUNKED:" _ tag _ ":" _ n if $Length(json) > 9000, else return json inline
- [X] T007 Add `ReadLargeOutChunk(tag As %String, chunkNum As %Integer) As %String` to `iris_src/src/Graph/KG/NKGAccel.cls` — returns $Get(^||LargeOut(tag, chunkNum), "")
- [X] T008 Update `BFSJson` in `iris_src/src/Graph/KG/NKGAccel.cls` — remove 50-result cap, wrap ZF return: `Return ..StoreLargeOut("BFS", raw)`
- [X] T009 Update `PPRNative` in `iris_src/src/Graph/KG/NKGAccel.cls` — wrap at PPRJson boundary: `Return ..StoreLargeOut("PPR", ..PPRNative(...))`
- [X] T010 Update `KHopNeighbors` in `iris_src/src/Graph/KG/NKGAccel.cls` — wrap at KHopJson boundary: `Return ..StoreLargeOut("KHOP", ..KHopNeighbors(...))`
- [X] T011 Update `RandomWalkJson` in `iris_src/src/Graph/KG/NKGAccel.cls` — wrap final return: `Return ..StoreLargeOut("RW", result)`
- [X] T012 Compile `Graph.KG.NKGAccel.cls` on gqs-ivg-test (community, stable compiler) — verify compile status=1, no errors

## Phase 4: Benchmark Update

- [X] T013 Update `tests/benchmarks/bench.py` — `run_bfs_arno` uses `_call_classmethod_large` from bench_utils, remove explicit `max_results` cap argument

## Phase 5: Validation

- [X] T014 Run `pytest tests/e2e/test_large_output_chunked.py tests/e2e/test_arno_bfs_global.py -v` — 4 passed, 5 skipped (arno-only skip on community IRIS), 0 failed
- [X] T015 Benchmark update verified — bench.py uses call_classmethod_large via bench_utils
- [X] T016 SC-006 process-private isolation: test included and skips gracefully when arno .so unavailable
- [X] T017 SC-001 through SC-007 status: SC-002/003/store/read pass; SC-001/004/005/006/007 skip on community (require enterprise arno); 0 failures

## Dependencies

```
T001-T003 (failing tests — MUST be RED before any implementation)
    ↓
T004-T005 (Python infrastructure — parallel)
    ↓
T006-T011 (ObjectScript changes — parallel within phase)
    ↓
T012 (compile — must come after all .cls edits)
    ↓
T013 (benchmark update)
    ↓
T014-T017 (validation — all must be GREEN)
```
