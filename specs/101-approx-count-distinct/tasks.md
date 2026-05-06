# Tasks: Spec 101 — APPROX_COUNT_DISTINCT

## Phase 1: Failing Tests (RED)

- [ ] T001 Write `tests/e2e/test_approx_count_distinct.py`
  - SC-001: `approx_count_distinct(b)` on 2-hop KNOWS returns result within 10ms p50 on SF10
  - SC-002: Result is within 6.5% of exact `COUNT(DISTINCT b)` for 20 random persons
  - SC-003: `QueryMetadata.warnings` contains `std_error=6.5%` and `registers=256`
  - SC-004: `COUNT(DISTINCT b)` (exact) still works and returns exact value unchanged
  - SC-005: `approx_count_distinct` works for `*1..3` (3-hop) without crash

- [ ] T002 Run tests — confirm RED (CountDistinctKHop doesn't exist yet)

## Phase 2: ObjectScript — HLL Infrastructure

- [ ] T003 Add `EmptyHLL()` to GraphIndex.cls — returns $ListBuild of 256 zeros
- [ ] T004 Add `MergeHLL(ByRef merged, other)` to GraphIndex.cls — element-wise max of 256 registers
- [ ] T005 Add `EstimateHLL(hll)` to GraphIndex.cls — harmonic mean with alpha_256=0.7182
- [ ] T006 Add `UpdateStructuralHLL(sIdx, pIdx, oIdx)` to GraphIndex.cls — SHA1 hash oIdx integer, update `^NKG("$agg", sIdx, pIdx, "hll")`
- [ ] T007 Add `GetNodeIdx(nodeId)` and `GetLabelIdx(label)` lookup helpers to GraphIndex.cls (non-creating, return "" if not found)
- [ ] T008 Compile GraphIndex.cls on enterprise — zero errors

## Phase 3: ObjectScript — CountDistinctKHop

- [ ] T009 Add `CountDistinctKHop(srcId, predsJson, maxHops, direction)` to NKGAccel.cls
  - Expands frontier hop-by-hop via `^NKG(-1,...)` adjacency
  - Merges `^NKG("$agg", sIdx, pIdx, "hll")` for each frontier node
  - Returns JSON: `{"estimate": N, "registers": 256, "std_error": 0.065}`
- [ ] T010 Add `"count_distinct_khop": 1` to `Capabilities()` return in NKGAccel.cls
- [ ] T011 Compile NKGAccel.cls on enterprise — zero errors

## Phase 4: Write Path Integration

- [ ] T012 Add `UpdateStructuralHLL` call to `InsertIndex` in GraphIndex.cls (after existing ^NKG writes)
- [ ] T013 Add `UpdateStructuralHLL` call to `BuildNKG` in Traversal.cls (in the edge loop, after ^NKG(-1/-2/-3) writes)
- [ ] T014 Add `UpdateStructuralHLL` to `BulkIngestEdges` in EdgeScan.cls — uses `^NKG` gref directly in embedded Python
- [ ] T015 Compile all three classes on enterprise — zero errors

## Phase 5: Cypher Translator

- [ ] T016 Add `APPROX_COUNT_RE` regex to translator — detects `approx_count_distinct(x) AS col` in raw Cypher
- [ ] T017 Add `approx_count_distinct` to lexer/parser if needed (may parse as generic function call — verify)
- [ ] T018 Add unit test: `parse_query("MATCH (a)-[:K*1..2]-(b) RETURN approx_count_distinct(b) AS c")` doesn't raise

## Phase 6: Engine Routing

- [ ] T019 Add `approx_match` detection in `_execute_var_length_cypher` before `count_match` check
  - Detects from raw Cypher string (not SQL stub)
  - Calls `CountDistinctKHop` via `_call_classmethod`
  - Returns estimate with `QueryMetadata.warnings`
- [ ] T020 Handle case where `^NKG("$agg")` is empty (BuildNKG not yet run) — return `{"estimate": 0}` with warning `"HLL sketches not built — run BuildNKG"`

## Phase 7: Populate Sketches on SF10

- [ ] T021 Run `BuildNKG` on enterprise to populate `^NKG("$agg")` from existing SF10 edges
  - Estimated time: ~270s (54M edges × ~5μs per HLL update)
- [ ] T022 Verify sketch coverage: `^NKG("$agg")` node count ≥ 90% of `^NKG("$NI")` node count

## Phase 8: Validation (GREEN)

- [ ] T023 Run `IRIS_PORT=4972 pytest tests/e2e/test_approx_count_distinct.py -v` — ALL GREEN
- [ ] T024 Run full regression: `pytest tests/unit/ tests/contract/ -q` — no regressions
- [ ] T025 Benchmark: run 30 samples of `approx_count_distinct` on SF10, record p50/p95
- [ ] T026 Accuracy check: compare `approx_count_distinct` vs `COUNT(DISTINCT b)` on 20 persons, verify all within 6.5% ± margin

## Dependencies

```
T001-T002 (RED tests — required first)
    ↓
T003-T008 (HLL infrastructure — T003-T007 parallel, T008 after)
    ↓
T009-T011 (CountDistinctKHop — needs T003-T008)
T012-T015 (write paths — needs T006, parallel with T009-T011)
T016-T018 (translator — independent of ObjectScript)
    ↓
T019-T020 (engine routing — needs T009-T011 + T016-T018)
    ↓
T021-T022 (populate sketches — needs T012-T015 deployed)
    ↓
T023-T026 (validation — needs all above)
```

## Notes

- `GetNodeIdx` must be non-creating — unlike `InternNode` it should not create new entries
- BulkIngestEdges HLL update uses integer indices directly (no string interning needed — sIdx/pIdx must be looked up or created first if node not yet in ^NKG)
- The `approx_count_distinct` detection is on raw Cypher, not SQL stub — add the check before `translate_to_sql` is called or pass raw query through alongside the translated result
- HLL sketches survive `Kill ^KG` (which doesn't touch `^NKG`) but are killed by `Kill ^NKG` — document this in the method docstring
