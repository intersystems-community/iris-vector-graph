# Tasks: TemporalIndex API Gaps

**Input**: Design documents from `/specs/204-temporal-index-gaps/`
**Branch**: `204-temporal-index-gaps`
**Version target**: 2.8.0

---

## Phase 1: Setup

**Purpose**: Verify container, confirm existing tests pass, establish baseline.

- [ ] T001 Verify `ivg-iris-enterprise` container name in `docker-compose.yml` (authoritative check before writing any test fixture)
- [ ] T002 Start enterprise container: `scripts/enterprise-container.sh up`
- [ ] T003 Run full test suite to confirm baseline: `IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 pytest`
- [ ] T004 Read `iris_src/src/Graph/KG/TemporalIndex.cls` fully before editing (required by CLAUDE.md ObjectScript rules)
- [ ] T005 Call `skill(action="describe")` for `objectscript-guardrails` and `objectscript-review` before writing any ObjectScript

**Checkpoint**: Baseline green, enterprise container running, guardrail skills loaded.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: ObjectScript skills loaded, test skeleton files created with correct fixtures and SKIP guard.

- [ ] T006 Create `tests/unit/test_temporal_index_gaps.py` with imports, mock fixture, and `SKIP_IRIS_TESTS` guard set to `"false"` default
- [ ] T007 Create `tests/integration/test_temporal_index_gaps_e2e.py` with `enterprise_iris_connection` fixture, `SKIP_IRIS_TESTS` guard, and `IVG_TEST_CONTAINER=ivg-iris-enterprise` env check
- [ ] T008 Bump version to `2.8.0` in `pyproject.toml`

**Checkpoint**: Both test skeleton files exist; `pytest tests/unit/test_temporal_index_gaps.py` collects 0 tests (no failures).

---

## Phase 3: User Story 1 — PurgeRawBefore (Priority: P1) 🎯 MVP

**Goal**: `TemporalIndex.PurgeRawBefore(tsEnd)` deletes raw edges (tout/tin/edgeprop) with ts < tsEnd, leaves aggregates untouched, returns deleted count. Python wrapper `purge_raw_before(ts_end)` delegates via `_call_classmethod`.

**Independent Test**: Insert edges at ts=100, 200, 300 with aggregates. Call `purge_raw_before(250)`. Assert: QueryWindow returns only ts=300; GetAggregate returns same values; return count == 2.

### Tests for User Story 1 (write FIRST — must FAIL before implementation)

- [ ] T009 [US1] Write unit tests for `purge_raw_before` in `tests/unit/test_temporal_index_gaps.py`: mock `_call_classmethod`, assert called with `"PurgeRawBefore"` and correct ts, assert return int cast; test zero-delete case
- [ ] T010 [US1] Write unit tests for `PurgeBefore` bucket-boundary fix: mock store, assert aggregate bucket at tsEnd boundary is NOT killed when tsEnd is mid-bucket
- [ ] T011 [US1] Write e2e test `test_purge_raw_before_preserves_aggregates` in `tests/integration/test_temporal_index_gaps_e2e.py`: insert 3 edges + confirm aggregates, call `purge_raw_before(250)`, assert raw gone and aggregates survive
- [ ] T012 [US1] Write e2e test `test_purge_raw_before_boundary_strict` in `tests/integration/test_temporal_index_gaps_e2e.py`: insert edge at ts=tsEnd, assert NOT deleted
- [ ] T013 [US1] Write e2e test `test_purge_raw_before_removes_edgeprop` in `tests/integration/test_temporal_index_gaps_e2e.py`: insert edge with attrs, purge, assert edgeprop entries gone for deleted edges

### Implementation for User Story 1

- [ ] T014 [US1] Edit `iris_src/src/Graph/KG/TemporalIndex.cls`: add `ClassMethod PurgeRawBefore(tsEnd As %Integer) As %Integer` — loops `^KG("tout")` for ts < tsEnd, kills tout/tin/edgeprop, returns count; does NOT touch tagg/bucket/out/in/deg/labelset
- [ ] T015 [US1] Edit `iris_src/src/Graph/KG/TemporalIndex.cls`: fix `PurgeBefore` bucket kill — compute `maxSafeBucket = (tsEnd \ ..#BUCKET) - 1`, only kill `^KG("tagg", bucket)` / `^KG("bucket", bucket)` when `bucket <= maxSafeBucket`
- [ ] T016 [US1] Push and compile TemporalIndex.cls to ivg-iris-enterprise via `iris_doc(mode="put", compile=True)`
- [ ] T017 [US1] Add `purge_raw_before(self, ts_end: int) -> int` store method in `iris_vector_graph/stores/iris_sql_store.py` following the `write_temporal_edge` pattern — calls `_call_classmethod("Graph.KG.TemporalIndex", "PurgeRawBefore", str(ts_end))`, casts to int
- [ ] T018 [US1] Add `purge_raw_before(self, ts_end: int) -> int: ...` to `GraphStore` protocol in `iris_vector_graph/store_protocol.py`
- [ ] T019 [US1] Add `purge_raw_before(self, ts_end: int) -> int` method in `iris_vector_graph/_engine/temporal.py` (TemporalMixin), delegating to `self._store.purge_raw_before(ts_end)`

**Phase Gate — run e2e tests T011–T013. ALL must pass before advancing to Phase 4.**

---

## Phase 4: User Story 2 — suppressReverseIndex (Priority: P2)

**Goal**: `InsertEdge(..., suppressReverseIndex=0)` skips `^KG("tin")` write when flag=1. Same flag on `BulkInsert` items. Python wrappers accept `suppress_reverse_index: bool = False`.

**Independent Test**: Insert edge with `suppress_reverse_index=True`. Assert: `QueryWindow(source, ...)` returns it. Assert: `QueryWindowInbound(target, ...)` returns empty.

### Tests for User Story 2 (write FIRST — must FAIL before implementation)

- [ ] T020 [US2] Write unit test `test_create_edge_temporal_suppress_reverse` in `tests/unit/test_temporal_index_gaps.py`: mock `write_temporal_edge`, assert called with `suppress_reverse_index=True` when engine method called with flag
- [ ] T021 [US2] Write unit test `test_write_temporal_edge_suppress_flag_passed` in `tests/unit/test_temporal_index_gaps.py`: mock `_call_classmethod`, assert InsertEdge receives `"1"` as 8th argument when suppress=True, `"0"` when False
- [ ] T022 [US2] Write e2e test `test_suppress_reverse_index_outbound_visible` in `tests/integration/test_temporal_index_gaps_e2e.py`: insert with suppress=True, assert QueryWindow returns edge
- [ ] T023 [US2] Write e2e test `test_suppress_reverse_index_inbound_invisible` in `tests/integration/test_temporal_index_gaps_e2e.py`: insert with suppress=True, assert QueryWindowInbound returns empty
- [ ] T024 [US2] Write e2e test `test_suppress_reverse_default_writes_both` in `tests/integration/test_temporal_index_gaps_e2e.py`: insert without suppress flag, assert both QueryWindow and QueryWindowInbound return edge

### Implementation for User Story 2

- [ ] T025 [US2] Edit `iris_src/src/Graph/KG/TemporalIndex.cls`: add `suppressReverseIndex As %Boolean = 0` as final parameter to `InsertEdge`; wrap `Set ^KG("tin", ...)` in `If 'suppressReverseIndex { ... }`
- [ ] T026 [US2] Edit `iris_src/src/Graph/KG/TemporalIndex.cls`: update `BulkInsert` to read `suppress_reverse` from each batch item object; apply suppression per-item on `^KG("tin")` write
- [ ] T027 [US2] Push and compile TemporalIndex.cls to ivg-iris-enterprise via `iris_doc(mode="put", compile=True)`
- [ ] T028 [US2] Edit `iris_vector_graph/stores/iris_sql_store.py`: add `suppress_reverse_index: bool = False` to `write_temporal_edge`; pass `str(int(suppress_reverse_index))` as 8th arg to InsertEdge
- [ ] T029 [US2] Edit `iris_vector_graph/stores/iris_sql_store.py`: add `suppress_reverse_index: bool = False` to `bulk_write_temporal_edges`; forward to each `write_temporal_edge` call
- [ ] T030 [US2] Edit `iris_vector_graph/_engine/temporal.py`: add `suppress_reverse_index: bool = False` to `create_edge_temporal`; forward to `self._store.write_temporal_edge`
- [ ] T031 [US2] Edit `iris_vector_graph/_engine/temporal.py`: add `suppress_reverse_index: bool = False` to `bulk_create_edges_temporal`; forward to `self._store.bulk_write_temporal_edges`

**Phase Gate — run e2e tests T022–T024. ALL must pass before advancing to Phase 5.**

---

## Phase 5: User Story 3 — InternLabelSet / ResolveLabelSet (Priority: P3)

**Goal**: `InternLabelSet(attrsJSON)` canonicalizes (sorted keys, no whitespace), SHA1-hashes, stores under `^KG("labelset", hash)` once, returns hex hash. `ResolveLabelSet(hash)` returns canonical JSON or `""`. Python wrappers on engine.

**Independent Test**: Call `intern_label_set({"b":2,"a":1})` and `intern_label_set({"a":1,"b":2})` → same hash. `resolve_label_set(hash)` → `'{"a":1,"b":2}'`. `resolve_label_set("unknown")` → `""`.

### Tests for User Story 3 (write FIRST — must FAIL before implementation)

- [ ] T032 [US3] Write unit test `test_intern_label_set_delegates` in `tests/unit/test_temporal_index_gaps.py`: mock `intern_label_set` store, assert called with JSON string, assert return value is hash string
- [ ] T033 [US3] Write unit test `test_resolve_label_set_delegates` in `tests/unit/test_temporal_index_gaps.py`: mock `resolve_label_set` store, assert return value forwarded
- [ ] T034 [US3] Write e2e test `test_intern_label_set_key_order_invariant` in `tests/integration/test_temporal_index_gaps_e2e.py`: assert `{"b":2,"a":1}` and `{"a":1,"b":2}` produce same hash
- [ ] T035 [US3] Write e2e test `test_intern_label_set_idempotent` in `tests/integration/test_temporal_index_gaps_e2e.py`: call 1000 times with 10 distinct label sets (random key order); assert exactly 10 distinct storage entries via ResolveLabelSet
- [ ] T036 [US3] Write e2e test `test_resolve_label_set_canonical_form` in `tests/integration/test_temporal_index_gaps_e2e.py`: assert resolved JSON has keys sorted and no extra whitespace
- [ ] T037 [US3] Write e2e test `test_resolve_label_set_unknown_returns_empty` in `tests/integration/test_temporal_index_gaps_e2e.py`: assert `resolve_label_set("deadbeef")` returns `""`
- [ ] T038 [US3] Write e2e test `test_purge_raw_before_does_not_touch_labelset` in `tests/integration/test_temporal_index_gaps_e2e.py`: intern label, purge raw edges, assert label still resolves (FR-016)

### Implementation for User Story 3

- [ ] T039 [US3] Edit `iris_src/src/Graph/KG/TemporalIndex.cls`: add `ClassMethod InternLabelSet(attrsJSON As %String) As %String` — parse as %DynamicObject, sort keys, emit compact JSON, SHA1Hash → lowercase hex, write `^KG("labelset", hash)` if absent, return hash; return `""` on parse error
- [ ] T040 [US3] Edit `iris_src/src/Graph/KG/TemporalIndex.cls`: add `ClassMethod ResolveLabelSet(hash As %String) As %String` — return `$Get(^KG("labelset", hash), "")`
- [ ] T041 [US3] Push and compile TemporalIndex.cls to ivg-iris-enterprise via `iris_doc(mode="put", compile=True)`
- [ ] T042 [US3] Add `intern_label_set(self, attrs_json: str) -> str` and `resolve_label_set(self, hash_hex: str) -> str` to `iris_vector_graph/stores/iris_sql_store.py` following `_call_classmethod` pattern
- [ ] T043 [US3] Add `intern_label_set` and `resolve_label_set` to `GraphStore` protocol in `iris_vector_graph/store_protocol.py`
- [ ] T044 [US3] Add `intern_label_set(self, attrs: dict) -> str` and `resolve_label_set(self, hash_hex: str) -> str` to TemporalMixin in `iris_vector_graph/_engine/temporal.py`; `intern_label_set` serializes dict to JSON before delegating

**Phase Gate — run e2e tests T034–T038. ALL must pass before advancing to Phase 6.**

---

## Phase 6: User Story 4 — Bucket Unit Fix / TSUNIT (Priority: P2)

**Goal**: `Parameter TSUNIT = "ms"` makes all bucket arithmetic use `BUCKETMS = BUCKET * 1000`. `GetAggregate` and `GetBucketGroups` scan correct 5-minute buckets for ms timestamps. `PurgeBefore` and `PurgeRawBefore` use TSUNIT-aware BUCKETDIV.

**Independent Test**: With `TSUNIT="ms"`, insert edge at ts=1,000,000 ms. Assert bucket = 1,000,000 ÷ 300,000 = 3. Assert `GetAggregate` over window 900,000–1,100,000 returns count=1.

### Tests for User Story 4 (write FIRST — must FAIL before implementation)

- [ ] T045 [US4] Write unit test `test_tsunit_ms_bucket_calculation` in `tests/unit/test_temporal_index_gaps.py`: mock store, assert that when TSUNIT="ms" a ts=1,000,000 call produces bucket=3 (not 3333)
- [ ] T046 [US4] Write e2e test `test_tsunit_ms_correct_bucket` in `tests/integration/test_temporal_index_gaps_e2e.py`: use a test subclass with TSUNIT="ms", insert edge at ts=1_000_000, assert GetAggregate(900_000, 1_100_000) returns count=1
- [ ] T047 [US4] Write e2e test `test_tsunit_ms_purge_uses_correct_divisor` in `tests/integration/test_temporal_index_gaps_e2e.py`: with TSUNIT="ms", insert edges, call PurgeRawBefore with ms tsEnd, assert correct edges deleted
- [ ] T048 [US4] Write e2e test `test_tsunit_default_unchanged` in `tests/integration/test_temporal_index_gaps_e2e.py`: with default TSUNIT="", behavior identical to current second-precision (regression guard)

### Implementation for User Story 4

- [ ] T049 [US4] Edit `iris_src/src/Graph/KG/TemporalIndex.cls`: add `Parameter TSUNIT As %String = ""` and `Parameter BUCKETMS As %Integer = 300000`
- [ ] T050 [US4] Edit `iris_src/src/Graph/KG/TemporalIndex.cls`: replace all `\ ..#BUCKET` occurrences in `InsertEdge`, `BulkInsert`, `GetDistinctCount`, `GetVelocity`, `FindBursts`, `GetAggregate`, `GetBucketGroups` with `$Select(..#TSUNIT="ms": ts \ ..#BUCKETMS, 1: ts \ ..#BUCKET)` (or introduce local variable `tBucketDiv` at top of each method)
- [ ] T051 [US4] Edit `iris_src/src/Graph/KG/TemporalIndex.cls`: apply same BUCKETDIV logic to `PurgeBefore` and `PurgeRawBefore` bucket-kill arithmetic
- [ ] T052 [US4] Push and compile TemporalIndex.cls to ivg-iris-enterprise via `iris_doc(mode="put", compile=True)`

**Phase Gate — run e2e tests T046–T048. ALL must pass before advancing to Polish phase.**

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Version bump, CHANGELOG, regression run, SC-006 grep.

- [ ] T053 [P] Add `v2.8.0` entry to `CHANGELOG.md` covering all 4 gaps
- [ ] T054 [P] Verify `pyproject.toml` version is `2.8.0` (set in T008; confirm no drift)
- [ ] T055 Run full test suite: `IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 pytest` — all tests green
- [ ] T056 [P] Grep opsreview for remaining direct `^KG` references: `grep -rn '\^KG' ~/ws/opsreview/iris/src/OpsReview/Monitor/` — document count for SC-006 tracking (migration of opsreview itself is out of scope for this spec)
- [ ] T057 Commit all changes on `204-temporal-index-gaps` branch

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately
- **Phase 2 (Foundational)**: Depends on Phase 1 — blocks all user stories
- **Phase 3 (US1 — PurgeRawBefore)**: Depends on Phase 2 — P1 priority, implement first
- **Phase 4 (US2 — suppressReverseIndex)**: Depends on Phase 2 — may start after Phase 3 e2e gate
- **Phase 5 (US3 — InternLabelSet)**: Depends on Phase 2 — start after Phase 4 e2e gate; FR-016 test (T038) depends on Phase 3 PurgeRawBefore being live
- **Phase 6 (US4 — TSUNIT)**: Depends on Phase 2 — start after Phase 5 e2e gate; edits same `.cls` file so sequential is safest
- **Phase 7 (Polish)**: Depends on all user story phase gates passing

### User Story Dependencies

- **US1 (P1)**: No cross-story dependencies. Implement first — unblocks US3 test T038.
- **US2 (P2)**: No cross-story dependencies. Can start after Phase 2.
- **US3 (P3)**: T038 depends on US1 `PurgeRawBefore` being live. Start US3 after US1 phase gate.
- **US4 (P2)**: Edits same TemporalIndex.cls — start after US3 to avoid merge conflicts.

### Within Each User Story

- Unit tests written and confirmed failing → ObjectScript implementation → compile → store layer → protocol → mixin layer → e2e tests pass
- Every `.cls` edit must be followed by `iris_doc(mode="put", compile=True)` before running e2e tests

### Parallel Opportunities

- T006 and T007 (test skeleton files) can be written in parallel [P]
- T009–T013 (US1 unit + e2e tests) can be written in parallel before any implementation
- T014 and T017 (ObjectScript + store layer for US1) can start in parallel once ObjectScript implementation is written
- T053 and T054 (CHANGELOG + version confirm) are independent [P]

---

## Parallel Example: User Story 1

```bash
# All unit + e2e test stubs for US1 (write before any implementation):
pytest tests/unit/test_temporal_index_gaps.py -k "purge_raw" --collect-only   # 0 tests → add them
pytest tests/integration/test_temporal_index_gaps_e2e.py -k "purge_raw" --collect-only

# After T014–T019 implementation:
IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 \
  pytest tests/unit/test_temporal_index_gaps.py tests/integration/test_temporal_index_gaps_e2e.py -k "us1 or purge_raw" -v
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Phase 1: Setup + baseline
2. Phase 2: Test skeletons + version bump
3. Phase 3: PurgeRawBefore (P1 — largest opsreview impact)
4. **STOP**: US1 e2e gate passes → opsreview `MetricPurge.cls` can migrate

### Incremental Delivery

1. US1 (PurgeRawBefore) → opsreview MetricPurge migrated
2. US2 (suppressReverseIndex) → opsreview MetricIngest ^KG("tin") kill removed
3. US3 (InternLabelSet) → opsreview MetricIngest ^KG("labelset") writes moved
4. US4 (TSUNIT fix) → ms-timestamp bucket correctness restored
5. Polish → release 2.8.0

---

## Notes

- `[P]` = different files, no dependencies
- `[US1/US2/US3/US4]` = maps to user story from spec.md
- Every ObjectScript edit requires `iris_doc(mode="put", compile=True)` before e2e tests
- Never use community container (ivg-iris, port 21972) — MaxServerConn=1 causes license failures
- `SKIP_IRIS_TESTS` defaults `"false"` — do not hardcode `True` in test fixtures
- Total tasks: 57 | Unit tests: T009–T010, T020–T021, T032–T033, T045 | E2E tests: T011–T013, T022–T024, T034–T038, T046–T048 | Implementation: T014–T019, T025–T031, T039–T044, T049–T052 | Phase gates: T013 (US1), T024 (US2), T038 (US3), T048 (US4)
