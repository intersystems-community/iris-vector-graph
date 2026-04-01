# Tasks: Edge Properties + NDJSON Import/Export

**Input**: Design documents from /specs/037-edgeprop-ndjson-ingest/
**Tests**: Required (SC-005: ≥8 unit, ≥4 e2e; Constitution Principle IV)

---

## Phase 1: Setup

- [X] T001 Verify baseline: python3 -m pytest tests/unit/ -q — 276 tests passing

---

## Phase 2: Foundational — edgeprop in TemporalIndex.cls

- [X] T002 Add edgeprop write loop to InsertEdge in iris_src/src/Graph/KG/TemporalIndex.cls: if attrs provided, iterate and write ^KG("edgeprop", ts, s, p, o, key) = value
- [X] T003 Add edgeprop write loop to BulkInsert in iris_src/src/Graph/KG/TemporalIndex.cls: each item may have optional "attrs" object
- [X] T004 Add GetEdgeAttrs(ts, source, predicate, target) classmethod to iris_src/src/Graph/KG/TemporalIndex.cls: $Order on ^KG("edgeprop", ts, s, p, o, *) and return JSON object
- [X] T005 Add ^KG("edgeprop") to Purge() in iris_src/src/Graph/KG/TemporalIndex.cls
- [X] T006 Deploy updated TemporalIndex.cls to test container

**Checkpoint**: edgeprop writes and reads work via ObjectScript.

---

## Phase 3: User Story 1 — Rich Edge Attributes (P1)

**Goal**: create_edge_temporal accepts attrs, get_edge_attrs returns them.

### Tests

- [X] T007 [P] [US1] Unit test: create_edge_temporal with attrs calls InsertEdge with attrs JSON in tests/unit/test_edgeprop_ndjson.py
- [X] T008 [P] [US1] Unit test: get_edge_attrs returns dict of stored attributes in tests/unit/test_edgeprop_ndjson.py
- [X] T009 [P] [US1] Unit test: get_edge_attrs for edge with no attrs returns empty dict in tests/unit/test_edgeprop_ndjson.py
- [X] T010 [P] [US1] Unit test: get_edges_in_window with include_attrs enriches results in tests/unit/test_edgeprop_ndjson.py

### Implementation

- [X] T011 [US1] Modify create_edge_temporal in iris_vector_graph/engine.py: pass attrs as JSON string to InsertEdge classMethodVoid
- [X] T012 [US1] Add get_edge_attrs(ts, source, predicate, target) to IRISGraphEngine in iris_vector_graph/engine.py

**Checkpoint**: Attrs round-trip: write→read→verify.

---

## Phase 4: User Story 2 — NDJSON Import (P1)

**Goal**: import_graph_ndjson reads NDJSON file, creates nodes + temporal edges with attrs.

### Tests

- [X] T013 [P] [US2] Unit test: import_graph_ndjson with 3-line file (2 nodes + 1 temporal edge) creates correct data in tests/unit/test_edgeprop_ndjson.py
- [X] T014 [P] [US2] Unit test: import_graph_ndjson upserts duplicate nodes without error in tests/unit/test_edgeprop_ndjson.py
- [X] T015 [P] [US2] Unit test: import_graph_ndjson skips unknown kind with warning in tests/unit/test_edgeprop_ndjson.py

### Implementation

- [X] T016 [US2] Add import_graph_ndjson(path, upsert_nodes, batch_size) to IRISGraphEngine in iris_vector_graph/engine.py: read lines, dispatch by kind, batch temporal edges via BulkInsert

**Checkpoint**: NDJSON file → nodes + temporal edges in Graph_KG.

---

## Phase 5: User Story 3 — NDJSON Export (P2)

**Goal**: export_graph_ndjson and export_temporal_edges_ndjson write NDJSON files.

### Tests

- [X] T017 [P] [US3] Unit test: export_graph_ndjson writes nodes then edges in tests/unit/test_edgeprop_ndjson.py

### Implementation

- [X] T018 [US3] Add export_graph_ndjson(path) to IRISGraphEngine in iris_vector_graph/engine.py: query all nodes, write node events, then query temporal edges, write temporal_edge events
- [X] T019 [US3] Add export_temporal_edges_ndjson(path, start, end, predicate) with window filter in iris_vector_graph/engine.py

---

## Phase 6: E2E Tests (Principle IV)

- [X] T020 [US1] E2e test: create_edge_temporal with attrs, get_edge_attrs round-trip against live IRIS in tests/e2e/test_edgeprop_ndjson_e2e.py
- [X] T021 [US2] E2e test: write NDJSON to temp file, import_graph_ndjson, verify nodes + edges exist in tests/e2e/test_edgeprop_ndjson_e2e.py
- [X] T022 [US3] E2e test: import then export NDJSON, verify round-trip fidelity in tests/e2e/test_edgeprop_ndjson_e2e.py
- [X] T023 [US2] E2e test: import 1000 temporal edges with attrs, verify attrs queryable in tests/e2e/test_edgeprop_ndjson_e2e.py

---

## Phase 7: Polish

- [X] T024 Run full regression: python3 -m pytest tests/unit/ tests/e2e/ -q — zero regressions

---

## Dependencies

- US1 (Phase 3): depends on Phase 2 (edgeprop in ObjectScript)
- US2 (Phase 4): depends on US1 (import writes attrs)
- US3 (Phase 5): depends on US1+US2 (export reads what import wrote)

## Implementation Strategy

MVP: Phase 1-4 (edgeprop + NDJSON import) = 16 tasks. Export is P2.
