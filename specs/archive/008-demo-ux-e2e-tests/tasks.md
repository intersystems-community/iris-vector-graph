# Tasks: E2E UX Tests and Demo Enhancements

**Input**: Design documents from `/specs/008-demo-ux-e2e-tests/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: E2E tests ARE the deliverable for this feature. Each user story includes test implementation as core work.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **Tests**: `tests/e2e/`
- **Examples/Demos**: `examples/`
- **Domain Types**: `examples/domains/`
- **Sample Data**: `sql/`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: E2E test infrastructure and shared fixtures

- [x] T001 Create E2E test configuration file in tests/e2e/conftest.py with shared fixtures
- [x] T002 [P] Create demo utilities module in examples/demo_utils.py with progress indicators and error handling
- [x] T003 [P] Verify existing biomedical sample data loads correctly via sql/sample_data_768.sql

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**CRITICAL**: No user story work can begin until this phase is complete

- [x] T004 Create base DemoRunner class in examples/demo_utils.py with timing, progress, error handling
- [x] T005 [P] Create fraud domain package structure in examples/domains/fraud/__init__.py
- [x] T006 [P] Create fraud sample data SQL script in sql/fraud_sample_data.sql (75 accounts, 300 transactions, 25 alerts)
- [x] T007 Add fraud sample data loader function to scripts/setup/__init__.py

**Checkpoint**: Foundation ready - user story implementation can now begin in parallel

---

## Phase 3: User Story 1 - Biomedical Demo E2E Validation (Priority: P1)

**Goal**: Automated E2E tests for the Biomedical demo verifying protein queries, vector similarity, and graph traversal

**Independent Test**: Run `pytest tests/e2e/test_biomedical_demo.py -v` - all tests pass with loaded biomedical data

### E2E Tests for User Story 1

- [x] T008 [P] [US1] Create biomedical E2E test file in tests/e2e/test_biomedical_demo.py
- [x] T009 [US1] Implement test_database_connectivity in tests/e2e/test_biomedical_demo.py
- [x] T010 [US1] Implement test_protein_query_by_id in tests/e2e/test_biomedical_demo.py
- [x] T011 [US1] Implement test_protein_vector_similarity in tests/e2e/test_biomedical_demo.py
- [x] T012 [US1] Implement test_protein_interactions_graph_traversal in tests/e2e/test_biomedical_demo.py
- [x] T013 [US1] Implement test_graphql_playground_loads in tests/e2e/test_biomedical_demo.py
- [x] T014 [US1] Implement test_hybrid_search_rrf_fusion in tests/e2e/test_biomedical_demo.py

### Demo Script for User Story 1

- [x] T015 [US1] Create biomedical demo script in examples/demo_biomedical.py with 5+ progress steps
- [x] T016 [US1] Add database connection check with actionable error in examples/demo_biomedical.py
- [x] T017 [US1] Add sample data availability check in examples/demo_biomedical.py

**Checkpoint**: Biomedical E2E tests pass, demo script runs with clear output

---

## Phase 4: User Story 2 - Fraud Detection Demo E2E Validation (Priority: P2)

**Goal**: Create fraud detection domain types and E2E tests for pattern detection and graph traversal

**Independent Test**: Run `pytest tests/e2e/test_fraud_demo.py -v` - all tests pass with loaded fraud data

### Fraud Domain Types

- [x] T018 [P] [US2] Create Account GraphQL type in examples/domains/fraud/types.py
- [x] T019 [P] [US2] Create Transaction GraphQL type in examples/domains/fraud/types.py
- [x] T020 [P] [US2] Create Alert GraphQL type in examples/domains/fraud/types.py
- [x] T021 [US2] Create fraud DataLoaders in examples/domains/fraud/loaders.py
- [x] T022 [US2] Create fraud resolvers in examples/domains/fraud/resolver.py

### E2E Tests for User Story 2

- [x] T023 [P] [US2] Create fraud E2E test file in tests/e2e/test_fraud_demo.py
- [x] T024 [US2] Implement test_fraud_data_loaded in tests/e2e/test_fraud_demo.py
- [x] T025 [US2] Implement test_account_query_by_id in tests/e2e/test_fraud_demo.py
- [x] T026 [US2] Implement test_transaction_graph_traversal in tests/e2e/test_fraud_demo.py
- [x] T027 [US2] Implement test_ring_pattern_detection in tests/e2e/test_fraud_demo.py
- [x] T028 [US2] Implement test_mule_account_detection in tests/e2e/test_fraud_demo.py
- [x] T029 [US2] Implement test_vector_anomaly_detection in tests/e2e/test_fraud_demo.py

### Demo Script for User Story 2

- [x] T030 [US2] Create fraud detection demo script in examples/demo_fraud_detection.py
- [x] T031 [US2] Add ring pattern detection visualization in examples/demo_fraud_detection.py
- [x] T032 [US2] Add mule account detection in examples/demo_fraud_detection.py

**Checkpoint**: Fraud E2E tests pass, demo script runs with pattern detection output

---

## Phase 5: User Story 3 - Interactive Demo Experience Enhancement (Priority: P3)

**Goal**: Polish demo scripts and FastHTML UIs with better UX - progress bars, error messages, architecture diagrams

**Independent Test**: Run demo scripts and FastHTML apps, verify clear output, helpful errors, and architecture diagrams

### UX Enhancements

- [x] T033 [US3] Add rich progress indicators to DemoRunner in examples/demo_utils.py
- [x] T034 [P] [US3] Enhance error messages with "Next Steps" guidance in examples/demo_utils.py
- [x] T035 [P] [US3] Add auto-detection of database state (empty/populated) in examples/demo_utils.py
- [x] T036 [US3] Add architecture diagram popup to Biomedical FastHTML UI in src/iris_demo_server/app.py
- [x] T037 [US3] Add architecture diagram popup to Fraud Detection FastHTML UI in src/iris_fraud_server/app.py

### Graceful Degradation

- [x] T038 [US3] Add IRIS version detection and feature availability check in examples/demo_utils.py
- [x] T039 [US3] Add graceful degradation for missing VECTOR functions in demo scripts
- [x] T040 [US3] Enhance existing demo_working_system.py with new DemoRunner class

---

## Phase 6: UX E2E Testing & Polish (COMPLETE ✅)

**Purpose**: Agent-browser tests, documentation, and final validation

- [x] T041 Implement UX E2E tests for Biomedical FastHTML app in tests/e2e/test_biomedical_ui.py using agent-browser
- [x] T042 Implement UX E2E tests for Fraud FastHTML app in tests/e2e/test_fraud_ui.py using agent-browser
- [x] T043 [P] Update quickstart.md with final test commands and expected output
- [x] T044 [P] Update CLAUDE.md Active Technologies section for this feature
- [x] T045 Run full E2E test suite and verify <60s completion time
- [x] T046 Capture and link final UI screenshots in README.md
- [x] T047 Run linting and formatting (black, isort, flake8)

---

## Final Checkpoint: ALL USER STORIES COMPLETE ✅

The Multi-Query-Engine Platform is now fully validated with automated UX E2E tests and professional demo UIs.

**E2E Test Success**: 17/18 functional and UX tests passing (license limit hit on one concurrent test).
**Visual Assets**: 5 new screenshots in `docs/images/` including architecture popups.
**Architecture Diagrams**: In-app visualizations implemented for both Biomedical and Fraud domains.
**Infrastructure**: Automated setup, dynamic port mapping, and `iris-devtester` integration complete.
