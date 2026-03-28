# Tasks: NICHE Knowledge Graph Integer Index (^NKG)

**Input**: Design documents from `/specs/028-nkg-integer-index/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Required (SC-005: ≥6 unit tests, ≥3 e2e tests; Constitution Principle IV)

**Organization**: US1 (InsertIndex dual-write) and US2 (BuildKG batch pass) are both P1. US1 provides InternNode/InternLabel which US2 reuses. US3 (Delete/Update) is P2 and depends on US1.

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

- [X] T001 Verify all existing unit tests pass: `python3 -m pytest tests/unit/ -q`
- [X] T002 Verify existing ObjectScript classes compile on `iris-vector-graph-main`: docker cp + `$System.OBJ.Load`

**Checkpoint**: Baseline green.

---

## Phase 2: Foundational (Blocking Prerequisites)

- [X] T003 Add `InitStructuralLabels` classmethod to `iris_src/src/Graph/KG/GraphIndex.cls`: pre-populate `^NKG("$LS", 0)="out"`, `^NKG("$LI", "out")=0`, and same for `in` (1) and `deg` (2); set `^NKG("$meta", "labelCount")=3` if not already set
- [X] T004 Add `InternNode(id)` classmethod to `iris_src/src/Graph/KG/GraphIndex.cls`: fine-grained `Lock +^NKG("$NI", id)`, check existing, `$Increment(^NKG("$meta", "nodeCount"))`, set `^NKG("$ND", idx)` and `^NKG("$NI", id)`, unlock, return idx
- [X] T005 Add `InternLabel(label)` classmethod to `iris_src/src/Graph/KG/GraphIndex.cls`: same locking pattern as InternNode, call `InitStructuralLabels` on first use, `$Increment(^NKG("$meta", "labelCount"))`, set `^NKG("$LS", idx)` and `^NKG("$LI", label)`, return idx
- [X] T006 Deploy updated `GraphIndex.cls` to `iris-vector-graph-main` via docker cp + `$System.OBJ.Load` and verify compilation

**Checkpoint**: `InternNode` and `InternLabel` compile and are callable. Structural labels initialized.

---

## Phase 3: User Story 1 — Populate ^NKG on Edge Insert (Priority: P1) MVP

**Goal**: `InsertIndex` dual-writes `^KG` + `^NKG` with integer encoding on every SQL INSERT.

**Independent Test**: Insert edge via SQL, verify `^NKG` contains correct integer-subscripted entries.

### Tests for User Story 1

- [X] T007 [P] [US1] Unit test: `InternNode("TEST:A")` returns integer ≥0, calling again with same ID returns same integer in `tests/unit/test_nkg_index.py`
- [X] T008 [P] [US1] Unit test: `InternLabel("binds")` returns integer ≥3 (structural labels 0-2 reserved), calling again returns same integer in `tests/unit/test_nkg_index.py`
- [X] T009 [P] [US1] Unit test: structural labels pre-populated — `^NKG("$LS", 0)="out"`, `^NKG("$LS", 1)="in"`, `^NKG("$LS", 2)="deg"` after first InternLabel call in `tests/unit/test_nkg_index.py`
- [X] T010 [P] [US1] Unit test: encoding rule — label index N stored as -(N+1) subscript, verified by reading `^NKG(-1, ...)` after InsertIndex in `tests/unit/test_nkg_index.py`

### Implementation for User Story 1

- [X] T011 [US1] Update `InsertIndex` in `iris_src/src/Graph/KG/GraphIndex.cls`: after existing `^KG` writes, call `InternNode(s)`, `InternNode(o)`, `InternLabel(p)`, then set `^NKG(-1, sIdx, -(pIdx+1), oIdx) = weight`, `^NKG(-2, oIdx, -(pIdx+1), sIdx) = weight`, `$Increment(^NKG(-3, sIdx))`, `$Increment(^NKG("$meta", "version"))`
- [X] T012 [US1] Deploy updated `GraphIndex.cls` to container and verify InsertIndex fires on SQL INSERT

**Checkpoint**: Edge INSERT triggers both `^KG` and `^NKG` writes.

---

## Phase 4: User Story 2 — Batch Rebuild ^NKG from ^KG (Priority: P1)

**Goal**: `BuildKG()` includes a second pass that encodes `^NKG` from `^KG`.

**Independent Test**: Call `BuildKG()` on populated SQL data, verify `^NKG` metadata matches node/edge counts.

### Tests for User Story 2

- [X] T013 [P] [US2] Unit test: after `BuildKG()`, `^NKG("$meta", "nodeCount")` matches unique node count in `tests/unit/test_nkg_index.py`
- [X] T014 [P] [US2] Unit test: after `BuildKG()`, all edges in `^KG("out", ...)` have corresponding entries in `^NKG(-1, ...)` in `tests/unit/test_nkg_index.py`

### Implementation for User Story 2

- [X] T015 [US2] Add `^NKG` batch encoding pass to `BuildKG()` in `iris_src/src/Graph/KG/Traversal.cls`: after existing `^KG` population, `Kill ^NKG`, call `InitStructuralLabels`, iterate `^KG("out", src, pred, dst)`, intern all nodes/labels, write integer-encoded edges, set metadata counts
- [X] T016 [US2] Deploy updated `Traversal.cls` to container and verify `BuildKG()` populates both globals

**Checkpoint**: `BuildKG()` produces consistent `^KG` and `^NKG`.

---

## Phase 5: User Story 3 — Delete and Update Index Entries (Priority: P2)

**Goal**: `DeleteIndex` and `UpdateIndex` maintain `^NKG` alongside `^KG`.

### Tests for User Story 3

- [X] T017 [P] [US3] Unit test: after DeleteIndex, `^NKG(-1, sIdx, -(pIdx+1), oIdx)` is removed and version incremented in `tests/unit/test_nkg_index.py`
- [X] T018 [P] [US3] Unit test: after UpdateIndex, `^NKG` entry value changes and version incremented in `tests/unit/test_nkg_index.py`

### Implementation for User Story 3

- [X] T019 [US3] Update `DeleteIndex` in `iris_src/src/Graph/KG/GraphIndex.cls`: look up integer indices via `$Get(^NKG("$NI", s))` etc., `Kill ^NKG(-1, ...)` and `^NKG(-2, ...)`, decrement degree, increment version
- [X] T020 [US3] Update `UpdateIndex` in `iris_src/src/Graph/KG/GraphIndex.cls`: look up indices, update weight in `^NKG(-1, ...)` and `^NKG(-2, ...)`, increment version
- [X] T021 [US3] Update `PurgeIndex` in `iris_src/src/Graph/KG/GraphIndex.cls`: add `Kill ^NKG` alongside existing `Kill ^KG`

**Checkpoint**: All CRUD operations maintain `^NKG` consistency.

---

## Phase 5.5: Integration Tests

- [X] T022 [US1] Integration test: SQL INSERT INTO Graph_KG.rdf_edges triggers both `^KG` and `^NKG` writes, verified by reading globals via native API in `tests/integration/test_nkg_index_integration.py`
- [X] T023 [US2] Integration test: `BuildKG()` on 100-node graph produces `^NKG` with correct metadata counts in `tests/integration/test_nkg_index_integration.py`

**Checkpoint**: Functional index and batch rebuild verified at SQL layer.

---

## Phase 6: End-to-End Tests (IRIS — Principle IV, Non-Optional)

- [X] T024 [US1] E2e test: insert 3 edges via SQL, verify `^NKG` node dictionary has 4+ entries (nodes), label set has 4+ entries (out,in,deg + predicates), and out-edge subscripts use correct encoding in `tests/e2e/test_nkg_index_e2e.py`
- [X] T025 [US2] E2e test: load 100-node graph via create_node/create_edge, call BuildKG(), verify `^NKG("$meta", "nodeCount")` ≥ 100 and `^NKG("$meta", "version")` > 0 in `tests/e2e/test_nkg_index_e2e.py`
- [X] T026 [US3] E2e test: insert edge, delete it, verify `^NKG` out-edge entry is removed and degree decremented in `tests/e2e/test_nkg_index_e2e.py`
- [X] T027 E2e test: verify `^KG` backward compatibility — all existing graph operations (PPR, subgraph, etc.) still work after ^NKG dual-write is enabled in `tests/e2e/test_nkg_index_e2e.py`
- [X] T027a [US1] E2e test: spawn 5 concurrent edge inserts (different source nodes, same predicate) via Python threading, verify no duplicate integer indices in `^NKG("$NI")` and `^NKG("$meta", "nodeCount")` equals expected count in `tests/e2e/test_nkg_index_e2e.py`

**Checkpoint**: Full end-to-end validation against live IRIS.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [X] T028 Run full regression: `python3 -m pytest tests/unit/ tests/e2e/ -q` — all existing tests pass
- [X] T029 [P] Update `docs/architecture/ARCHITECTURE.md` with ^NKG global structure documentation

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies
- **Foundational (Phase 2)**: Depends on Phase 1 — BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Phase 2 (InternNode/InternLabel)
- **US2 (Phase 4)**: Depends on Phase 2 (InternNode/InternLabel) — can run in parallel with US1
- **US3 (Phase 5)**: Depends on Phase 2 — independent of US1/US2
- **Integration (Phase 5.5)**: Depends on US1 + US2
- **E2E (Phase 6)**: Depends on all implementation phases
- **Polish (Phase 7)**: Depends on Phase 6

### User Story Dependencies

- **US1 (P1)**: Depends on Phase 2 foundational methods
- **US2 (P1)**: Depends on Phase 2 — independent of US1 (reuses InternNode/InternLabel directly)
- **US3 (P2)**: Depends on Phase 2 — independent of US1/US2

### Parallel Opportunities

- T007-T010 (US1 unit tests) can all run in parallel
- T013-T014 (US2 unit tests) can run in parallel
- T017-T018 (US3 unit tests) can run in parallel
- US1, US2, US3 implementation can largely parallelize (different methods in same file)
- T024-T027 (e2e tests) are independent

---

## Implementation Strategy

### MVP First (User Story 1)

1. Phase 1: Baseline
2. Phase 2: InternNode + InternLabel + InitStructuralLabels (T003-T006)
3. Phase 3: InsertIndex dual-write (T007-T012)
4. **STOP and VALIDATE**: SQL INSERT produces both `^KG` and `^NKG` entries

### Incremental Delivery

1. Foundational → InternNode/InternLabel compile and work
2. US1 → InsertIndex dual-writes on every SQL INSERT
3. US2 → BuildKG() batch-encodes existing data to ^NKG
4. US3 → Delete/Update maintain ^NKG consistency
5. E2E → All stories validated against live IRIS
6. Polish → Docs + regression
