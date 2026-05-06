# Tasks: Spec 099 — LDBC Full-Schema Loader

## Phase 1: Failing Tests

- [ ] T001 Write `tests/e2e/test_ldbc_full_loader.py` — SC-001 through SC-004: loader completes SF10, IC2/IC4/IC7/IC9/IC11 execute without error, node properties retrievable via Cypher, edge count matches CSV
- [ ] T002 Run tests — confirm RED (LDBCFullLoader class doesn't exist yet)

## Phase 2: Extract Remaining SF10 Files

- [ ] T003 Extract remaining needed files from SF10 tarball: `forum_hasMember_person_0_0.csv`, `comment_replyOf_post_0_0.csv`, `comment_replyOf_comment_0_0.csv`, `comment_hasTag_tag_0_0.csv` — add to `/tmp/sf10_out/`

## Phase 3: LDBCFullLoader Class

- [ ] T004 Create `tests/benchmarks/ldbc_full_loader.py` with `LDBCFullLoader` class
- [ ] T005 Implement `load_static_nodes()` — Tag (static/tag_0_0.csv), Organisation, Place via `engine.bulk_create_nodes()`
- [ ] T006 Implement `load_person_nodes()` — Person with properties (firstName, lastName, birthday, gender, locationIP) via `engine.bulk_create_nodes()`
- [ ] T007 Implement `load_content_nodes()` — Post + Comment with properties (creationDate, content, length) via `engine.bulk_create_nodes()` in batches of 50K
- [ ] T008 Implement `load_forum_nodes()` — Forum with title property via `engine.bulk_create_nodes()`
- [ ] T009 Implement `load_edges()` — all relationship CSV files via `BulkIngestEdges` (135K e/s): KNOWS, HAS_CREATOR, LIKES, HAS_TAG, INTERESTED_IN, WORKS_AT, STUDIED_AT, LOCATED_IN, HAS_MEMBER, REPLY_OF
- [ ] T010 Implement `build_indices()` — BuildKG, BuildNKG, InvalidateAdjCache, WarmAdjCache (knows subgraph only)
- [ ] T011 Implement `clear()` — delete all nodes/edges, kill ^KG/^NKG globals

## Phase 4: IC Query Functions (immediately implementable)

- [ ] T012 [P] `bench_ic2(engine, person_id)` — 20 most recent messages by friends (1-hop + date sort)
- [ ] T013 [P] `bench_ic4(engine, person_id, start_date, end_date)` — tags of friends' posts in date range
- [ ] T014 [P] `bench_ic7(engine, message_id)` — likes on a message + responders
- [ ] T015 [P] `bench_ic8(engine, person_id)` — comments replying to person's posts
- [ ] T016 [P] `bench_ic9(engine, person_id, date)` — posts/comments by friends before date
- [ ] T017 [P] `bench_ic11(engine, person_id, org_country, work_from)` — friends at company

## Phase 5: IC Benchmark Runner

- [ ] T018 Create `tests/benchmarks/ldbc_ic_benchmark.py` — runs all implemented IC functions, 200 random pairs each, outputs results JSON + comparison table vs GES SF100
- [ ] T019 Add placeholder stubs for IC3/IC5/IC6/IC12 (blocked on spec 100) that skip gracefully with a message

## Phase 6: Validation

- [ ] T020 Run `IRIS_PORT=4972 pytest tests/e2e/test_ldbc_full_loader.py -v` — ALL GREEN
- [ ] T021 Run full IC benchmark: `IRIS_PORT=4972 python tests/benchmarks/ldbc_ic_benchmark.py --sf sf10`
- [ ] T022 Record results for IC2/IC4/IC7/IC8/IC9/IC11 in `specs/099-ldbc-full-schema-loader/results.md`
- [ ] T023 Update `~/.agent/diagrams/ivg-query-inventory.html` with measured IC numbers

## Dependencies

```
T001-T002 (failing tests — RED required first)
    ↓
T003 (extract files)
    ↓
T004-T011 (loader — T004 first, then T005-T010 parallel, T011 parallel)
T012-T017 (IC functions — parallel, all independent)
    ↓
T018-T019 (benchmark runner — needs T012-T017)
    ↓
T020-T023 (validation — needs all above)
```

## Notes

- Comment nodes (21.9M) are the bottleneck — provide `--skip-comments` flag for fast iteration
- IC3/IC5/IC6/IC12 depend on spec 100 (variable-length path fix) — stub them out
- SF10 load time estimate: ~20 min full, ~5 min without comments
- Test against `iris-enterprise-2026` (port 4972, has embedded Python for BulkIngestEdges)
