<!-- markdownlint-disable MD013 -->

# Tasks: IVG Engine Critical Fixes (206)

**Branch**: `206-engine-critical-fixes`
**Input**: `specs/206-engine-critical-fixes/`
**Prerequisites**: plan.md ✓ spec.md ✓ research.md ✓ data-model.md ✓

**User Stories**: US1 = A9 BuildKG preserves temporal data (P1) | US2 = A12 bulk_delete_nodes index speed (P1) | US3 = A8.3 PurgeBucketRange aggregate expiry (P2)

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: US1 / US2 / US3
- All paths relative to repo root

---

## Phase 1: Setup

**Purpose**: Branch verified, container confirmed, test files scaffolded

- [ ] T001 Confirm on branch `206-engine-critical-fixes` (`git branch --show-current`)
- [ ] T002 Confirm `ivg-iris-enterprise` running (`docker ps --filter name=ivg-iris-enterprise`)
- [ ] T003 [P] Read `iris_src/src/Graph/KG/TraversalBuild.cls` — locate `Kill ^KG` line
- [ ] T004 [P] Read `iris_vector_graph/_engine/nodes_edges.py` — locate `bulk_delete_nodes` OR query
- [ ] T005 [P] Read `iris_src/src/Graph/KG/TemporalIndex.cls` — confirm `PurgeBucketRange` absent

**Checkpoint**: Source locations confirmed, container up

---

## Phase 2: Foundational

**Purpose**: Shared test infrastructure (fixtures, conftest imports) used by all three stories

- [ ] T006 Verify `tests/unit/test_engine_critical_fixes.py` does not exist (will create)
- [ ] T007 Verify `tests/integration/test_engine_critical_fixes_e2e.py` does not exist (will create)
- [ ] T008 Confirm `tests/conftest.py` exports `iris_conn` / `engine` fixtures used by new E2E file

**Checkpoint**: Ready for story phases

---

## Phase 3: User Story 1 — BuildKG preserves temporal data (Priority: P1) 🎯 MVP

**Goal**: Replace `Kill ^KG` with five explicit adjacency kills so temporal globals survive `sync()`

**Independent Test**: Write temporal edges, call `engine.sync()`, assert edges still in `^KG("tout")`

### Tests for US1 (write FIRST — must FAIL before implementation)

- [ ] T009 [US1] Write unit test `test_build_kg_preserves_temporal_globals` in `tests/unit/test_engine_critical_fixes.py`
  - Mock `classMethodVoid("Graph.KG.TraversalBuild", "BuildKG")`
  - Assert test scaffolding runs; implementation will make it meaningful post-fix
  - Verify `Kill ^KG` pattern absent from ObjectScript source (grep assertion)
- [ ] T010 [US1] Write E2E test `test_sync_preserves_temporal_edges` in `tests/integration/test_engine_critical_fixes_e2e.py`
  - Insert 5 temporal edges via `engine` fixture
  - Call `engine.sync()`
  - Assert all 5 edges present in `^KG("tout")` via `engine.get_edges_in_window()`

**Phase gate**: Run `pytest tests/unit/test_engine_critical_fixes.py::test_build_kg_preserves_temporal_globals` — must exist (may pass vacuously before fix)

### Implementation for US1

- [ ] T011 [US1] Edit `iris_src/src/Graph/KG/TraversalBuild.cls` — replace `Kill ^KG` with:
  `Kill ^KG("label"), ^KG("prop"), ^KG("out"), ^KG("in"), ^KG("deg")`
- [ ] T012 [US1] Push and compile: `scripts/enterprise-container.sh compile-all` (uses irispython)
- [ ] T013 [US1] Run E2E phase gate: `pytest tests/integration/test_engine_critical_fixes_e2e.py::test_sync_preserves_temporal_edges` — must PASS

**Checkpoint**: `sync()` safe with temporal data. One-line ObjectScript change confirmed.

---

## Phase 4: User Story 2 — bulk_delete_nodes index speed (Priority: P1)

**Goal**: Split the OR DELETE into two single-column DELETEs so IRIS uses per-column indexes

**Independent Test**: Mock cursor; assert two separate DELETE statements issued per batch (never `OR`)

### Tests for US2 (write FIRST — must FAIL before implementation)

- [ ] T014 [US2] Write unit test `test_bulk_delete_nodes_no_or_query` in `tests/unit/test_engine_critical_fixes.py`
  - Patch `iris_vector_graph._engine.nodes_edges` cursor
  - Call `engine.bulk_delete_nodes(["n1", "n2", "n3"])`
  - Assert `cursor.execute` called ≥4 times (2 for rdf_reifications, 2 for rdf_edges)
  - Assert no call contains `" OR "` in the SQL string
- [ ] T015 [US2] Write unit test `test_bulk_delete_nodes_functional_equivalence` — insert nodes/edges into in-memory mock, verify all matching rows removed by split DELETEs

### Implementation for US2

- [ ] T016 [US2] Read `iris_vector_graph/_engine/nodes_edges.py` lines around `bulk_delete_nodes` OR query
- [ ] T017 [US2] Edit `iris_vector_graph/_engine/nodes_edges.py` — split into 4 DELETEs per plan.md Phase 0:
  1. `DELETE FROM rdf_reifications WHERE edge_id IN (SELECT edge_id FROM rdf_edges WHERE s IN (...))`
  2. `DELETE FROM rdf_reifications WHERE edge_id IN (SELECT edge_id FROM rdf_edges WHERE o_id IN (...))`
  3. `DELETE FROM rdf_edges WHERE s IN (...)`
  4. `DELETE FROM rdf_edges WHERE o_id IN (...)`
- [ ] T018 [US2] Write E2E test `test_bulk_delete_nodes_removes_all_matching` in `tests/integration/test_engine_critical_fixes_e2e.py`
  - Create nodes where some are source-only, some target-only, some both
  - Call `bulk_delete_nodes`
  - Assert all edges referencing deleted nodes are gone
- [ ] T019 [US2] Run E2E phase gate: `pytest tests/integration/test_engine_critical_fixes_e2e.py::test_bulk_delete_nodes_removes_all_matching` — must PASS
- [ ] T020 [US2] Run unit phase gate: `pytest tests/unit/test_engine_critical_fixes.py -k "bulk_delete"` — must PASS

**Checkpoint**: OR query gone, 4 indexed DELETEs confirmed by test assertions.

---

## Phase 5: User Story 3 — PurgeBucketRange aggregate expiry (Priority: P2)

**Goal**: Add `PurgeBucketRange(bucketStart, bucketEnd)` ObjectScript classmethod + Python wrapper

**Independent Test**: Insert edges + aggregates, call `purge_bucket_range`, assert targeted `^KG("tagg")` buckets gone, raw `^KG("tout"/"tin")` entries intact

### Tests for US3 (write FIRST — must FAIL before implementation)

- [ ] T021 [US3] Write unit test `test_purge_bucket_range_wrapper` in `tests/unit/test_engine_critical_fixes.py`
  - Mock `classMethodValue("Graph.KG.TemporalIndex", "PurgeBucketRange", ...)`
  - Assert wrapper passes `bucket_start`, `bucket_end` correctly
  - Assert returns `int`
- [ ] T022 [US3] Write unit test `test_purge_bucket_range_invalid_range` — `bucket_start > bucket_end` → returns 0, no side effects
- [ ] T023 [US3] Write E2E test `test_purge_bucket_range_clears_tagg_keeps_raw` in `tests/integration/test_engine_critical_fixes_e2e.py`
  - Insert edges with known timestamps → known bucket numbers
  - Call `purge_bucket_range(b_start, b_end)` covering those buckets
  - Assert return count matches bucket count deleted
  - Assert `get_edges_in_window()` still returns raw edges
  - Assert `^KG("tagg", bucket)` entries in range are absent (via `classMethodValue` query or global inspection)

### Implementation for US3

- [ ] T024 [US3] Read `iris_src/src/Graph/KG/TemporalIndex.cls` — confirm location to insert new classmethod (after `PurgeRawBefore`)
- [ ] T025 [US3] Edit `iris_src/src/Graph/KG/TemporalIndex.cls` — add `PurgeBucketRange` classmethod per plan.md Phase 0 snippet (ASCII-only, no `{}` in `///` comments)
- [ ] T026 [US3] Push and compile: `scripts/enterprise-container.sh compile-all`
- [ ] T027 [P] [US3] Read `iris_vector_graph/_engine/temporal.py` — confirm `TemporalMixin` location
- [ ] T028 [US3] Edit `iris_vector_graph/_engine/temporal.py` — add `purge_bucket_range(self, bucket_start, bucket_end) -> int` wrapper
- [ ] T029 [US3] Run E2E phase gate: `pytest tests/integration/test_engine_critical_fixes_e2e.py::test_purge_bucket_range_clears_tagg_keeps_raw` — must PASS
- [ ] T030 [US3] Run unit phase gate: `pytest tests/unit/test_engine_critical_fixes.py -k "purge_bucket"` — must PASS

**Checkpoint**: PurgeBucketRange ships. Raw edges survive aggregate expiry confirmed.

---

## Phase 6: Polish & Cross-Cutting

- [ ] T031 Run full unit suite: `pytest tests/unit/ -x` — 0 failures
- [ ] T032 Run full integration suite: `pytest tests/integration/ -x` — 0 failures
- [ ] T033 [P] Run linting: `ruff check . && black --check . && mypy iris_vector_graph/`
- [ ] T034 Update `CHANGELOG.md` — add v2.10.0 entry with A9, A12, A8.3 items
- [ ] T035 Bump `pyproject.toml` version to `2.10.0`
- [ ] T036 Run `markdownlint-cli2 --fix CHANGELOG.md && prettier --write CHANGELOG.md`

**Checkpoint**: All tests green, lint clean, version bumped.

---

## Dependencies

```text
Phase 1 → Phase 2 → Phase 3 (US1) → Phase 4 (US2) → Phase 5 (US3) → Phase 6
                  ↘               ↗
                    Phases 3-5 share conftest fixtures (Phase 2)
```

US2 and US3 are independent of each other after Phase 2 — can implement in parallel if desired.

## Parallel opportunities

- T003, T004, T005 (Phase 1 reads) — fully parallel
- T009 unit test + T010 E2E test scaffold (Phase 3) — parallel
- T014, T015 unit tests (Phase 4) — parallel
- T021, T022, T023 unit+E2E tests (Phase 5) — parallel
- T027 read + T025 ObjectScript edit (Phase 5) — parallel if T024 done

## Implementation strategy

**MVP**: Phase 3 (A9) alone — one-line fix, highest severity, ships independently.
**Full v2.10.0**: All three phases complete.

Total tasks: 36 | US1: 5 | US2: 7 | US3: 10 | Foundational: 5 | Polish: 6 | Setup: 3
