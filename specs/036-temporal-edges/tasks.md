# Tasks: Temporal Edge Indexing

**Input**: Design documents from `/specs/036-temporal-edges/`
**Tests**: Required (SC-006: ≥8 unit, ≥4 e2e; Constitution Principle IV)

**Organization**: US1-3 are all P1 and sequential (each builds on the previous). US4 (Cypher) is P2, deferred.

---

## Phase 1: Setup

- [X] T001 Verify baseline: `python3 -m pytest tests/unit/ -q` — 264 tests passing

---

## Phase 2: Foundational — TemporalIndex.cls Skeleton

- [X] T002 Create `iris_src/src/Graph/KG/TemporalIndex.cls` with class declaration and empty ClassMethod signatures: InsertEdge, BulkInsert, QueryWindow, GetVelocity, FindBursts, Purge
- [X] T003 Add `BUCKET_SIZE = 300` constant and `_WriteBucket` private helper that increments `^KG("bucket", floor(ts/300), source)` via `$Increment` in `iris_src/src/Graph/KG/TemporalIndex.cls`
- [X] T004 Deploy TemporalIndex.cls to test container and verify compilation

**Checkpoint**: TemporalIndex.cls compiles. All existing tests still pass.

---

## Phase 3: User Story 1 — Write Timestamped Edges at Ingest Speed (P1)

**Goal**: InsertEdge writes all 5 global keys. BulkInsert achieves ≥50K edges/sec.

**Independent Test**: Insert 10K edges via BulkInsert, verify all 5 global keys populated, measure throughput.

### Tests

- [X] T005 [P] [US1] Unit test: `create_edge_temporal("A","REL","B",1712000000)` calls `TemporalIndex.InsertEdge` via classMethodVoid in `tests/unit/test_temporal_edges.py`
- [X] T006 [P] [US1] Unit test: `bulk_create_edges_temporal([...])` calls `TemporalIndex.BulkInsert` with JSON batch via classMethodValue in `tests/unit/test_temporal_edges.py`
- [X] T007 [P] [US1] Unit test: `create_edge_temporal` with `timestamp=None` auto-assigns current time (not zero) in `tests/unit/test_temporal_edges.py`

### Implementation

- [X] T008 [US1] Implement `TemporalIndex.InsertEdge(source, predicate, target, timestamp, weight)` in `iris_src/src/Graph/KG/TemporalIndex.cls`: write `^KG("tout",ts,s,p,o)`, `^KG("tin",ts,o,p,s)`, call `_WriteBucket`, PLUS `^KG("out",s,p,o)`, `^KG("in",o,p,s)`, `^KG("deg",s)` for backward compat
- [X] T009 [US1] Implement `TemporalIndex.BulkInsert(batchJSON As %String) As %Integer` in `iris_src/src/Graph/KG/TemporalIndex.cls`: parse `%DynamicArray`, loop calling InsertEdge internals directly (no method call overhead), return count
- [X] T010 [US1] Add `create_edge_temporal(source, predicate, target, timestamp, weight)` to IRISGraphEngine in `iris_vector_graph/engine.py`: calls `TemporalIndex.InsertEdge` via `_iris_obj().classMethodVoid`
- [X] T011 [US1] Add `bulk_create_edges_temporal(edges)` to IRISGraphEngine in `iris_vector_graph/engine.py`: JSON-serialize, single `classMethodValue("BulkInsert", batchJSON)` call
- [X] T012 [US1] Benchmark: insert 100K edges, verify ≥50K edges/sec, document in `tests/e2e/test_temporal_edges_e2e.py`

**Checkpoint**: 100K edges inserted in <2s. All 5 global keys populated per edge.

---

## Phase 4: User Story 2 — Query Edges Within a Time Window (P1)

**Goal**: `get_edges_in_window()` uses `$Order` range scan, returns in O(results) time.

### Tests

- [X] T013 [P] [US2] Unit test: `get_edges_in_window` result contains only edges with timestamps in [start, end] in `tests/unit/test_temporal_edges.py`
- [X] T014 [P] [US2] Unit test: `get_edges_in_window` with empty window returns `[]` in `tests/unit/test_temporal_edges.py`

### Implementation

- [X] T015 [US2] Implement `TemporalIndex.QueryWindow(source, predicate, tsStart, tsEnd) As %String` in `iris_src/src/Graph/KG/TemporalIndex.cls`: nested `$Order` range scan on `^KG("tout", tsStart..tsEnd, source, ...)`
- [X] T016 [US2] Add `get_edges_in_window(source, predicate, start, end)` to IRISGraphEngine in `iris_vector_graph/engine.py`

**Checkpoint**: Window query returns correct edges in <5ms for 1-minute windows.

---

## Phase 5: User Story 3 — Burst Detection (P1)

**Goal**: Velocity check uses bucket index O(1). `find_burst_nodes` identifies high-velocity nodes.

### Tests

- [X] T017 [P] [US3] Unit test: `get_edge_velocity` returns correct count from bucket index in `tests/unit/test_temporal_edges.py`
- [X] T018 [P] [US3] Unit test: `find_burst_nodes` returns nodes above threshold, excludes nodes below in `tests/unit/test_temporal_edges.py`

### Implementation

- [X] T019 [US3] Implement `TemporalIndex.GetVelocity(nodeId, windowSec) As %Integer` in `iris_src/src/Graph/KG/TemporalIndex.cls`: sum `^KG("bucket",...)` entries covering the time window
- [X] T020 [US3] Implement `TemporalIndex.FindBursts(label, predicate, windowSec, threshold) As %String` in `iris_src/src/Graph/KG/TemporalIndex.cls`: scan nodes with given label, filter by velocity ≥ threshold
- [X] T021 [US3] Add `get_edge_velocity(node_id, window_seconds)` and `find_burst_nodes(label, predicate, window_seconds, threshold)` to IRISGraphEngine in `iris_vector_graph/engine.py`

---

## Phase 5.5: Cleanup (Principle IV + constitution)

- [X] T022 [US3] Implement `TemporalIndex.Purge()` in `iris_src/src/Graph/KG/TemporalIndex.cls`: `Kill ^KG("tout")`, `Kill ^KG("tin")`, `Kill ^KG("bucket")`
- [X] T023 Add temporal cleanup to `delete_node()` cascade in `iris_vector_graph/engine.py`: kill `^KG("tout", ..., node_id, ...)` entries when node is deleted

---

## Phase 6: Integration + E2E Tests (Principle IV, Non-Optional)

- [X] T024 [US1] Integration test: InsertEdge writes all 5 global keys, verify via native API in `tests/integration/test_temporal_integration.py`
- [X] T025 [US1] E2e test: bulk_create_edges_temporal 10K edges, verify `^KG("tout")` and `^KG("in")` populated, measure throughput ≥50K/sec in `tests/e2e/test_temporal_edges_e2e.py`
- [X] T026 [US2] E2e test: insert edges spanning 1 hour, query 1-minute window, verify correct edges returned in <5ms in `tests/e2e/test_temporal_edges_e2e.py`
- [X] T027 [US3] E2e test: insert burst pattern (100 edges in 60s for one node), verify `find_burst_nodes` detects it in `tests/e2e/test_temporal_edges_e2e.py`
- [X] T028 E2e test: backward compat — `kg_NEIGHBORS` and `kg_PAGERANK` still work on temporal-edge-enriched graph in `tests/e2e/test_temporal_edges_e2e.py`

---

## Phase 7: Polish

- [X] T029 Run full regression: `python3 -m pytest tests/unit/ tests/e2e/ -q` — 264+ existing tests pass, 0 regressions
- [X] T030 [P] Update `docs/python/PYTHON_SDK.md` with temporal edge API section

---

## Dependencies

- **Phase 2**: Blocks all user stories (TemporalIndex.cls skeleton)
- **US1 (Phase 3)**: Independent after Phase 2 — write path
- **US2 (Phase 4)**: Depends on US1 (needs edges to query)
- **US3 (Phase 5)**: Depends on US1 (needs bucket counts populated)
- **Phase 6 E2E**: Depends on US1+2+3

### Parallel Opportunities

- T005-T007 (unit tests) in parallel
- T013-T014 (US2 unit tests) in parallel
- T017-T018 (US3 unit tests) in parallel
- T025-T028 (all e2e tests) independent

---

## Implementation Strategy

### MVP (US1 only — 12 tasks)

1. TemporalIndex.cls skeleton (T002-T004)
2. Write tests first (T005-T007)
3. InsertEdge + BulkInsert (T008-T009)
4. Python wrappers (T010-T011)
5. Benchmark (T012)

**STOP and VALIDATE**: 100K edges in <2s, all 5 global keys populated, 264 existing tests pass.
