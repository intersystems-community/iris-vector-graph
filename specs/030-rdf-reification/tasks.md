# Tasks: RDF 1.2 Reification for KBAC

**Input**: Design documents from `/specs/030-rdf-reification/`
**Tests**: Required (SC-004: ≥6 unit, ≥4 e2e; Constitution Principle IV)

**Organization**: US1 (reify) and US2 (query) are both P1 and tightly coupled. US3 (KBAC walk) is P2, test-only.

---

## Phase 1: Setup

- [X] T001 Verify all existing unit tests pass: `python3 -m pytest tests/unit/ -q`
- [X] T002 Verify IRIS container is running

**Checkpoint**: Baseline green.

---

## Phase 2: Foundational

- [X] T003 Create `sql/rdf_reifications.sql` with CREATE TABLE DDL per data-model.md (reifier_id %EXACT PK, edge_id BIGINT, FK constraints, idx_reif_edge index)
- [X] T004 Add `rdf_reifications` DDL to schema deployment in `iris_vector_graph/schema.py`
- [X] T005 Add `rdf_reifications` to `VALID_GRAPH_TABLES` in `iris_vector_graph/security.py`
- [X] T006 Deploy table to test container and verify: `SELECT COUNT(*) FROM Graph_KG.rdf_reifications`

**Checkpoint**: Table exists. All existing tests still pass.

---

## Phase 3: User Story 1 — Reify an Edge with Metadata (Priority: P1)

**Goal**: `reify_edge(edge_id, props={"confidence": "0.92"})` creates reifier node + junction row + properties.

### Tests

- [X] T007 [P] [US1] Unit test: `reify_edge` with mocked cursor creates node, label, junction row, and props in `tests/unit/test_reification.py`
- [X] T008 [P] [US1] Unit test: `reify_edge` with no custom reifier_id auto-generates `reif:<edge_id>` in `tests/unit/test_reification.py`
- [X] T009 [P] [US1] Unit test: `reify_edge` for nonexistent edge_id returns None in `tests/unit/test_reification.py`

### Implementation

- [X] T010 [US1] Add `reify_edge(edge_id, reifier_id, label, props)` to IRISGraphEngine in `iris_vector_graph/engine.py`: verify edge exists → create_node(reifier_id) → insert label → insert junction row → insert props → commit
- [X] T011 [US1] Verify `reify_edge` works against live IRIS

**Checkpoint**: Can reify an edge with metadata. Reifier node visible in `get_node()`.

---

## Phase 4: User Story 2 — Query and Delete Reifications (Priority: P1)

**Goal**: `get_reifications(edge_id)` returns reifier nodes with properties. `delete_reification(reifier_id)` cleans up.

### Tests

- [X] T012 [P] [US2] Unit test: `get_reifications` returns list of dicts with reifier_id and properties in `tests/unit/test_reification.py`
- [X] T013 [P] [US2] Unit test: `get_reifications` for edge with no reifications returns empty list in `tests/unit/test_reification.py`
- [X] T014 [P] [US2] Unit test: `delete_reification` removes junction row + reifier node in `tests/unit/test_reification.py`

### Implementation

- [X] T015 [US2] Add `get_reifications(edge_id)` to IRISGraphEngine in `iris_vector_graph/engine.py`: JOIN rdf_reifications with rdf_props on reifier_id, return list of {"reifier_id", "properties"} dicts
- [X] T016 [US2] Add `delete_reification(reifier_id)` to IRISGraphEngine in `iris_vector_graph/engine.py`: delete junction row → delete props → delete labels → delete node
- [X] T017 [US2] Add cascade cleanup to edge deletion path: when edges are deleted, find and remove associated reifications in `iris_vector_graph/engine.py`
- [X] T017a [US2] E2e test: deleting an edge via delete_node() cascade removes associated reification junction rows and reifier nodes in `tests/e2e/test_reification_e2e.py`

**Checkpoint**: Query and delete work. Cascade cleans up on edge deletion.

---

## Phase 5: User Story 3 — KBAC Access Check (Priority: P2)

**Goal**: Verify reifier nodes participate in graph traversal for access control patterns.

### Tests

- [X] T018 [P] [US3] Unit test: reifier node with accessPolicy property is discoverable via `get_node()` and `kg_NEIGHBORS()` in `tests/unit/test_reification.py`

**Checkpoint**: Reifiers are regular nodes visible to graph algorithms.

---

## Phase 5.5: Integration Tests (Principle IV)

- [X] T019 [US1] Integration test: INSERT into rdf_reifications and SELECT back verifies FK constraints work in `tests/integration/test_reification_integration.py`
- [X] T020 [US2] Integration test: get_reifications SQL JOIN returns correct properties in `tests/integration/test_reification_integration.py`

**Checkpoint**: SQL-layer behavior verified.

---

## Phase 6: End-to-End Tests (Principle IV, Non-Optional)

- [X] T021 [US1] E2e test: reify_edge creates reifier node, junction row, and properties in under 5ms against live IRIS in `tests/e2e/test_reification_e2e.py`
- [X] T022 [US1] E2e test: multiple reifications on same edge returns all reifiers in `tests/e2e/test_reification_e2e.py`
- [X] T023 [US2] E2e test: get_reifications returns correct properties in `tests/e2e/test_reification_e2e.py`
- [X] T024 [US2] E2e test: delete_reification removes reifier but preserves original edge in `tests/e2e/test_reification_e2e.py`
- [X] T025 [US3] E2e test: reifier node participates in kg_NEIGHBORS traversal in `tests/e2e/test_reification_e2e.py`

**Checkpoint**: All acceptance scenarios pass against live IRIS.

---

## Phase 7: Polish

- [X] T026 Run full regression: `python3 -m pytest tests/unit/ tests/e2e/ -q`
- [X] T027 [P] Update `docs/python/PYTHON_SDK.md` with reification API reference

---

## Dependencies & Execution Order

- **Phase 2**: BLOCKS all user stories (table must exist)
- **US1 (Phase 3)**: Independent after Phase 2
- **US2 (Phase 4)**: Depends on US1 (can't query without reifications existing)
- **US3 (Phase 5)**: Depends on US1 (test-only, no new code)
- **Integration + E2E**: Depend on US1 + US2

### Parallel Opportunities

- T007-T009 (US1 unit tests) in parallel
- T012-T014 (US2 unit tests) in parallel
- T021-T025 (e2e tests) independent

---

## Implementation Strategy

### MVP: US1 + US2 (Phase 2-4)

1. Create table + security allowlist
2. Implement reify_edge
3. Implement get_reifications + delete_reification + cascade
4. **VALIDATE**: Reify, query, delete round-trip works
