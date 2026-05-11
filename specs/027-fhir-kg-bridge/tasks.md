# Tasks: FHIR-KG Clinical Bridge

**Branch**: `027-fhir-kg-bridge`
**Generated**: 2026-05-10
**Spec**: specs/027-fhir-kg-bridge/spec.md
**Plan**: specs/027-fhir-kg-bridge/plan.md

## Phase 1: Setup

- [ ] T001 Create iris_vector_graph/fhir_bridge.py with module imports (requests, json, logging, typing)
- [ ] T002 Create tests/unit/test_fhir_bridge.py with synthetic patient fixtures and test class structure
- [ ] T003 Create tests/e2e/test_fhir_bridge_e2e.py with iris_connection fixture and skip guard

## Phase 2: Foundational (blocking)

- [ ] T004 Insert synthetic bridge entries for 3 demo patients into fhir_bridges table via test fixture in tests/e2e/test_fhir_bridge_e2e.py
- [ ] T005 Insert synthetic KG nodes (mesh:D003924, mesh:D006973, mesh:D006333, mesh:D003866, mesh:D011247, mesh:D000740) via test fixture in tests/e2e/test_fhir_bridge_e2e.py

## Phase 3: User Story 1 — Load ICD-10 to MeSH Crosswalk (P1)

**Goal**: Bridge table populated with ICD-10→MeSH mappings from UMLS MRCONSO
**Test**: Load crosswalk, query known ICD-10 code J18.9, verify MeSH D011014 returned

- [ ] T006 [US1] Verify scripts/ingest/load_umls_bridges.py handles idempotent re-run correctly
- [ ] T007 [P] [US1] Write unit test: ingest script skips malformed rows (log + skip) in tests/unit/test_fhir_bridge.py
- [ ] T008 [P] [US1] Write e2e test: verify known ICD-10 J18.9 maps to MeSH D011014 after synthetic insert in tests/e2e/test_fhir_bridge_e2e.py

## Phase 4: User Story 2 — Query Patient KG Anchors (P1)

**Goal**: get_kg_anchors(engine, icd_codes) returns KG node IDs that exist in Graph_KG.nodes
**Test**: Call with ["E11.9", "I10"], verify mesh:D003924 and mesh:D006973 returned

- [ ] T009 [US2] Implement get_kg_anchors(engine, icd_codes) in iris_vector_graph/fhir_bridge.py
- [ ] T010 [P] [US2] Write unit test: get_kg_anchors with valid ICD codes returns expected node IDs in tests/unit/test_fhir_bridge.py
- [ ] T011 [P] [US2] Write unit test: get_kg_anchors with unknown codes returns empty list in tests/unit/test_fhir_bridge.py
- [ ] T012 [P] [US2] Write unit test: get_kg_anchors with empty fhir_bridges table returns empty + log warning in tests/unit/test_fhir_bridge.py
- [ ] T013 [US2] Write e2e test: get_kg_anchors against live IRIS returns correct anchors in tests/e2e/test_fhir_bridge_e2e.py
- [ ] T014 [US2] Write e2e test: get_kg_anchors filters to only nodes present in Graph_KG.nodes in tests/e2e/test_fhir_bridge_e2e.py

## Phase 5: User Story 3 — Unified Clinical Pipeline (P2)

**Goal**: unified_clinical_pipeline() chains FHIR search → anchors → PPR → results with provenance
**Test**: Run pipeline with mock FHIR returning maria-gonzalez-001, verify anchors + PPR results

- [ ] T015 [US3] Implement FHIR client helper (GET, BasicAuth, configurable 10s timeout) in iris_vector_graph/fhir_bridge.py
- [ ] T016 [US3] Implement ICD-10 code extraction from FHIR Condition resources in iris_vector_graph/fhir_bridge.py
- [ ] T017 [US3] Implement unified_clinical_pipeline(engine, query, fhir_base_url, fhir_auth, top_k, ppr_top_k, vector_search_param) in iris_vector_graph/fhir_bridge.py
- [ ] T018 [P] [US3] Write unit test: FHIR client handles BasicAuth and unauthenticated in tests/unit/test_fhir_bridge.py
- [ ] T019 [P] [US3] Write unit test: FHIR client returns error within timeout when unreachable in tests/unit/test_fhir_bridge.py
- [ ] T020 [P] [US3] Write unit test: ICD-10 extraction from Condition bundle returns correct codes in tests/unit/test_fhir_bridge.py
- [ ] T021 [P] [US3] Write unit test: pipeline returns status=anchors_resolved_but_no_graph_connectivity when PPR empty in tests/unit/test_fhir_bridge.py
- [ ] T022 [P] [US3] Write unit test: pipeline returns status=no_bridges_loaded when fhir_bridges empty in tests/unit/test_fhir_bridge.py
- [ ] T023 [US3] Write e2e test: pipeline with mocked FHIR + live IRIS returns ranked results in tests/e2e/test_fhir_bridge_e2e.py
- [ ] T024 [US3] Write e2e test: post-FHIR processing completes in under 500ms in tests/e2e/test_fhir_bridge_e2e.py

## Phase 6: User Story 4 — FHIR Search Tool (P2)

**Goal**: MCP-compatible FHIRSearchTool wraps FHIR REST API
**Test**: Tool searches Conditions for demo patient, returns structured summary with ICD codes

- [ ] T025 [US4] Implement FHIRSearchTool class (MCP-compatible interface) in iris_vector_graph/fhir_bridge.py
- [ ] T026 [P] [US4] Write unit test: FHIRSearchTool returns structured condition list in tests/unit/test_fhir_bridge.py
- [ ] T027 [P] [US4] Write unit test: FHIRSearchTool handles auth failure gracefully in tests/unit/test_fhir_bridge.py

## Phase 7: User Story 5 — Patient Graph Neighborhood Tool (P2)

**Goal**: MCP-compatible GetPatientKGNeighborhoodTool chains patient→conditions→anchors→PPR
**Test**: Tool with maria-gonzalez-001 returns ranked KG concepts

- [ ] T028 [US5] Implement GetPatientKGNeighborhoodTool class in iris_vector_graph/fhir_bridge.py
- [ ] T029 [P] [US5] Write unit test: tool returns neighborhood dict with anchors + ppr_results in tests/unit/test_fhir_bridge.py
- [ ] T030 [P] [US5] Write unit test: tool returns empty neighborhood for patient with no conditions in tests/unit/test_fhir_bridge.py
- [ ] T031 [US5] Write e2e test: tool against live IRIS with synthetic patient data in tests/e2e/test_fhir_bridge_e2e.py

## Phase 8: User Story 6 — Patient Anchors in Cypher (P3)

**Goal**: /api/cypher with fhir_patient_id parameter auto-resolves anchors
**Test**: Cypher query with patient_anchors parameter returns graph results

- [ ] T032 [US6] Add fhir_patient_id parameter handling in iris_vector_graph/cypher_api.py
- [ ] T033 [US6] Implement patient_anchors resolution (patient_id → FHIR → ICD → get_kg_anchors) in iris_vector_graph/fhir_bridge.py
- [ ] T034 [P] [US6] Write unit test: Cypher without fhir_patient_id unchanged in tests/unit/test_fhir_bridge.py
- [ ] T035 [US6] Write e2e test: Cypher with fhir_patient_id resolves anchors in tests/e2e/test_fhir_bridge_e2e.py

## Phase 9: Polish

- [ ] T036 Export get_kg_anchors, unified_clinical_pipeline, FHIRSearchTool, GetPatientKGNeighborhoodTool from iris_vector_graph/__init__.py
- [ ] T037 Add fhir_bridge module to docs/python/PYTHON_SDK.md with usage examples
- [ ] T038 Run full test suite (unit + e2e) and verify 0 regressions

## Dependencies

```
US1 (crosswalk) ─┐
                  ├─→ US2 (anchors) ─┬─→ US3 (pipeline) ─→ US5 (neighborhood tool)
                  │                   └─→ US6 (cypher hint)
                  └─→ US4 (FHIR tool, independent)
```

## Parallel Execution

- Phase 4 (US2): T010, T011, T012 in parallel
- Phase 5 (US3): T018, T019, T020, T021, T022 in parallel
- Phase 6 (US4): T026, T027 in parallel
- Phase 7 (US5): T029, T030 in parallel
- Cross-phase: US4 has no dependency on US2/US3

## Implementation Strategy

**MVP**: Phase 1–4 (Setup + US1 + US2) — get_kg_anchors() working end-to-end
**V1**: + Phase 5 (US3) — unified pipeline matching CareConnect ObjectScript
**V2**: + Phase 6–7 (US4 + US5) — MCP tools for Mindwalk/fhiragent
**V3**: + Phase 8 (US6) — Cypher API integration
