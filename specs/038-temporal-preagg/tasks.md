# Tasks: 038 Pre-Aggregated Temporal Analytics

**Branch**: `038-temporal-preagg`
**Rule**: Tests FIRST. No implementation task starts until its test task is written.

---

## Phase 1 — Unit tests (write first, run to confirm failure)

- [ ] T1: Add `TestTemporalPreAggUnit` class to `tests/unit/test_temporal_edges.py`
  - `test_get_temporal_aggregate_calls_classmethod`
  - `test_get_temporal_aggregate_count_returns_int`
  - `test_get_temporal_aggregate_avg_returns_float`
  - `test_get_temporal_aggregate_empty_avg_returns_none`
  - `test_get_temporal_aggregate_empty_count_returns_zero`
  - `test_get_bucket_groups_returns_list`
  - `test_get_distinct_count_calls_classmethod`

## Phase 2 — ObjectScript (TemporalIndex.cls)

- [ ] T2: Finalize `^KG("tagg")` writes in `InsertEdge` (already in skeleton — verify correctness)
- [ ] T3: Finalize `^KG("tagg")` writes in `BulkInsert` (already in skeleton — verify correctness)
- [ ] T4: Add MIN/MAX atomicity comment per FR-013
- [ ] T5: Add `GetDistinctCount` method (HLL merge + HarmonicMean, see plan.md §Phase 1)
- [ ] T6: Add HLL register update to `InsertEdge` (SHA1-based, 16-register $List)
- [ ] T7: Add HLL register update to `BulkInsert`
- [ ] T8: Verify `Purge` covers `^KG("tagg")` — already present, confirm

## Phase 3 — Python wrappers (engine.py)

- [ ] T9: Add `get_temporal_aggregate()` after `get_edge_attrs()`
- [ ] T10: Add `get_bucket_groups()`
- [ ] T11: Add `get_distinct_count()`

## Phase 4 — Run unit tests (must all pass)

- [ ] T12: `pytest tests/unit/test_temporal_edges.py -v -k "PreAgg"` — all green

## Phase 5 — E2E tests

- [ ] T13: Add `TestTemporalPreAggE2E` to `tests/unit/test_temporal_edges.py`
  - `test_aggregate_avg_correct`
  - `test_aggregate_count_correct`
  - `test_bucket_groups_all_sources`
  - `test_multi_bucket_aggregate`
  - `test_distinct_count_nonzero`
  - `test_purge_clears_tagg`

## Phase 6 — Compile and E2E run

- [ ] T14: Compile `TemporalIndex.cls` in `iris-vector-graph-main` container via `python scripts/deploy_objectscript.py` (verified: `scripts/compile_cls.py` does not exist; deploy_objectscript.py is the correct mechanism)
- [ ] T15: `pytest tests/unit/test_temporal_edges.py -v` — all green (unit + e2e)
- [ ] T16: `pytest tests/ -v` — full suite green

## Phase 7 — Benchmark (optional for this PR, required before v1.39.0 release notes)

- [ ] T17: Create `scripts/bench/bench_temporal_preagg.py`
- [ ] T18: Run on RE2-TT data, record ingest rate with tagg active
- [ ] T19: Record `get_temporal_aggregate` latency (1-bucket, 12-bucket, 288-bucket)
- [ ] T20: Update spec.md §8.4 with actual numbers

## Phase 8 — Ship

- [ ] T21: Bump `iris_vector_graph/__init__.py` → `1.39.0`
- [ ] T22: Update `README.md` changelog section
- [ ] T23: Update spec.md status → `Implemented`
- [ ] T24: Commit: `feat: v1.39.0 — Pre-aggregated temporal analytics (^KG("tagg"), HLL)`
- [ ] T25: Tag `v1.39.0`, build, publish via twine
