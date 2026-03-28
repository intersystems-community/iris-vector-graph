# Tasks: FHIR-to-KG Bridge Layer

**Input**: Design documents from `/specs/027-fhir-kg-bridge/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Required (SC-005: ≥6 unit tests, ≥3 e2e tests; Constitution Principle IV: integration + e2e mandatory for schema changes)

**Organization**: US1 (crosswalk ingest) and US2 (anchor extraction) are both P1 and independent after Phase 2. US3 (unified pipeline) is P2 and depends on US1+US2.

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

- [X] T001 Verify all existing unit tests pass: `python3 -m pytest tests/unit/ -q`
- [X] T002 Verify all existing e2e tests pass: `python3 -m pytest tests/e2e/ -q`

**Checkpoint**: Baseline green — zero regressions before any changes.

---

## Phase 2: Foundational (Blocking Prerequisites)

- [X] T003 Create `sql/fhir_bridges.sql` with CREATE TABLE IF NOT EXISTS DDL for `Graph_KG.fhir_bridges` per data-model.md schema (fhir_code, kg_node_id, fhir_code_system, bridge_type, confidence, source_cui, composite PK)
- [X] T004 Add `fhir_bridges` table creation to schema deployment in `iris_vector_graph/schema.py` — add the DDL to `get_schema_sql_list()` or equivalent schema init function
- [X] T005 Deploy `fhir_bridges` table to live IRIS container and verify it exists: `SELECT COUNT(*) FROM Graph_KG.fhir_bridges`

**Checkpoint**: `Graph_KG.fhir_bridges` table exists in IRIS. All existing tests still pass.

---

## Phase 3: User Story 1 — Load ICD-10→MeSH Crosswalk (Priority: P1)

**Goal**: UMLS MRCONSO parser loads ICD-10-CM→MeSH mappings into `fhir_bridges`.

**Independent Test**: Run ingest script with MRCONSO file, verify ≥50K rows with correct ICD→MeSH mappings.

### Tests for User Story 1

- [X] T006 [P] [US1] Unit test: MRCONSO line parser extracts CUI, SAB, CODE, STR from pipe-delimited line in `tests/unit/test_fhir_bridges.py`
- [X] T007 [P] [US1] Unit test: CUI join logic produces correct ICD10CM→MeSH pairs from two dictionaries in `tests/unit/test_fhir_bridges.py`
- [X] T008 [P] [US1] Unit test: MeSH descriptor IDs are prefixed with `MeSH:` to match KG node_id format in `tests/unit/test_fhir_bridges.py`
- [X] T009 [P] [US1] Unit test: malformed MRCONSO lines are skipped with warning (not abort) in `tests/unit/test_fhir_bridges.py`
- [X] T009a [P] [US1] Unit test: inserting duplicate (fhir_code, kg_node_id) pair is silently skipped (idempotent — no error, no duplicate row) in `tests/unit/test_fhir_bridges.py`

### Implementation for User Story 1

- [X] T010 [US1] Create `scripts/ingest/load_umls_bridges.py` with two-pass MRCONSO parser: pass 1 collects CUI→ICD10CM (SAB='ICD10CM'), pass 2 collects CUI→MeSH (SAB='MSH', TTY='MH'), join on CUI, INSERT OR IGNORE into `Graph_KG.fhir_bridges` with `bridge_type='icd10_to_mesh'` and `MeSH:` prefix on descriptor IDs
- [X] T011 [US1] Add CLI argument parsing to `load_umls_bridges.py`: `--mrconso PATH` (required), `--container NAME` (default iris-vector-graph-main), `--dry-run` flag for validation without insert

**Checkpoint**: Ingest script loads MRCONSO data into `fhir_bridges`. Unit tests pass.

---

## Phase 4: User Story 2 — Query Patient KG Anchors (Priority: P1)

**Goal**: `get_kg_anchors(icd_codes)` returns KG node IDs linked through `fhir_bridges`, filtered to nodes existing in `Graph_KG.nodes`.

**Independent Test**: Insert test bridge rows, call `get_kg_anchors()`, verify returned node IDs.

### Tests for User Story 2

- [X] T012 [P] [US2] Unit test: `get_kg_anchors(["J18.9"])` with mocked cursor returns expected MeSH node IDs in `tests/unit/test_fhir_bridges.py`
- [X] T013 [P] [US2] Unit test: `get_kg_anchors([])` returns empty list (no error) in `tests/unit/test_fhir_bridges.py`
- [X] T014 [P] [US2] Unit test: `get_kg_anchors()` filters to only nodes present in `Graph_KG.nodes` (mock returns bridge row for non-existent node → filtered out) in `tests/unit/test_fhir_bridges.py`

### Implementation for User Story 2

- [X] T015 [US2] Add `get_kg_anchors(icd_codes, bridge_type='icd10_to_mesh')` method to `IRISGraphEngine` in `iris_vector_graph/engine.py`: parameterized SQL JOIN between `fhir_bridges` and `nodes` table, returns distinct `kg_node_id` list

**Checkpoint**: `get_kg_anchors()` works against live IRIS. Unit tests pass.

---

## Phase 5: User Story 3 — Unified Clinical-to-Literature Pipeline (Priority: P2)

**Goal**: Demo script chains FHIR vector search → anchor extraction → PPR walk → literature retrieval.

**Independent Test**: Run pipeline with known clinical query against pre-loaded data, verify ranked results with provenance.

### Implementation for User Story 3

- [X] T016 [US3] Create `scripts/demo/unified_pipeline.py` with 6-step pipeline: (1) FHIR vector search via HTTP, (2) extract ICD codes from FHIR Condition results, (3) `get_kg_anchors(icd_codes)`, (4) `kg_PAGERANK(seed_entities=anchors)`, (5) `kg_KNN_VEC()` for literature, (6) RRF score fusion
- [X] T017 [US3] Add graceful fallback: if FHIR endpoint unavailable, skip steps 1-2 and accept seed ICD codes as CLI argument for KG-only search in `scripts/demo/unified_pipeline.py`
- [X] T018 [US3] Add provenance tracking: each result includes chain `[icd_code → mesh_term → kg_mechanism → paper_id]` in `scripts/demo/unified_pipeline.py`

**Checkpoint**: Unified pipeline executes end-to-end. Provenance chains visible in output.

---

## Phase 5.5: Integration Tests (SQL Layer — Principle IV)

- [X] T019 [US1] Integration test: INSERT into `fhir_bridges` and SELECT back verifies round-trip with correct column values in `tests/integration/test_fhir_bridges_integration.py`
- [X] T020 [US2] Integration test: `get_kg_anchors()` SQL JOIN produces correct results with test data in both `fhir_bridges` and `Graph_KG.nodes` in `tests/integration/test_fhir_bridges_integration.py`

**Checkpoint**: SQL-layer behavior verified before live execution.

---

## Phase 6: End-to-End Tests (IRIS — Principle IV, Non-Optional)

- [X] T021 [US1] E2e test: insert 10 ICD→MeSH bridge rows into live IRIS, verify they persist and are queryable via SELECT in `tests/e2e/test_fhir_bridges_e2e.py`
- [X] T022 [US1] E2e test: idempotent insert — re-inserting same bridge rows does not create duplicates in `tests/e2e/test_fhir_bridges_e2e.py`
- [X] T023 [US2] E2e test: `get_kg_anchors(["J18.9", "E11.9"])` returns only MeSH node IDs that exist in `Graph_KG.nodes` against live IRIS in `tests/e2e/test_fhir_bridges_e2e.py`
- [X] T024 [US2] E2e test: `get_kg_anchors([])` returns empty list against live IRIS in `tests/e2e/test_fhir_bridges_e2e.py`
- [X] T025 [US2] E2e test: `get_kg_anchors()` with ICD codes that have no bridge mapping returns empty list in `tests/e2e/test_fhir_bridges_e2e.py`

**Checkpoint**: All acceptance scenarios from spec.md pass against live IRIS.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [X] T026 Run full regression: `python3 -m pytest tests/unit/ tests/e2e/ -q` — all 336+ existing tests pass
- [X] T027 [P] Update `docs/python/PYTHON_SDK.md` with `get_kg_anchors()` API reference and bridge table documentation
- [X] T028 [P] Update README.md changelog with v1.18.0 entry for FHIR-to-KG bridge

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies
- **Foundational (Phase 2)**: Depends on Phase 1 — creates the table that all stories need
- **US1 (Phase 3)**: Depends on Phase 2 — needs `fhir_bridges` table
- **US2 (Phase 4)**: Depends on Phase 2 — needs `fhir_bridges` table (independent of US1)
- **US3 (Phase 5)**: Depends on US1 + US2 — needs bridge data loaded AND anchor function
- **Integration (Phase 5.5)**: Depends on Phase 2
- **E2E (Phase 6)**: Depends on Phases 3 + 4 completion
- **Polish (Phase 7)**: Depends on Phase 6

### User Story Dependencies

- **US1 (P1)**: Independent after Phase 2 — ingest script only needs the table
- **US2 (P1)**: Independent after Phase 2 — `get_kg_anchors()` only needs the table
- **US3 (P2)**: Depends on US1 (data loaded) + US2 (anchor function exists)

### Parallel Opportunities

- T006-T009 (US1 unit tests) can all run in parallel
- T012-T014 (US2 unit tests) can all run in parallel
- US1 (Phase 3) and US2 (Phase 4) can run in parallel after Phase 2
- T021-T025 (all e2e tests) are independent and can run in parallel
- T027-T028 (docs) can run in parallel

---

## Implementation Strategy

### MVP First (User Story 1 + 2)

1. Complete Phase 1: Verify baseline
2. Complete Phase 2: Create `fhir_bridges` table (T003-T005)
3. Complete Phase 3 + 4 in parallel: Ingest script + `get_kg_anchors()`
4. **STOP and VALIDATE**: Bridge data loads, anchors resolve correctly

### Incremental Delivery

1. Setup + Foundational → `fhir_bridges` table exists
2. Add US1 → MRCONSO ingest works
3. Add US2 → `get_kg_anchors()` works
4. Add US3 → Unified pipeline orchestrates the full demo
5. Integration + E2E → All stories validated against live IRIS
6. Polish → Docs + full regression
