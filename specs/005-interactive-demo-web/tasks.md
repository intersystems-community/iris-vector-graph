# Tasks: Interactive IRIS Demo Web Interface

**Input**: Design documents from `/specs/005-interactive-demo-web/`
**Prerequisites**: plan.md ✓, research.md ✓, data-model.md ✓, contracts/ ✓, quickstart.md ✓

**Tech Stack**: Python 3.11+, FastHTML, HTMX, D3.js, httpx, iris-devtools (ASGI registration)
**Deployment**: IRIS ASGI server (primary), uvicorn (development fallback)

## Format: `[ID] [P?] Description`
- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions

---

## Phase 3.1: Setup

- [x] **T001** Create project structure at `src/iris_demo_server/` with subdirectories: `routes/`, `services/`, `templates/`, `static/js/`, `static/css/`, `demo_data/`, and `tests/demo/` at repository root
- [x] **T002** Initialize Python project with `pyproject.toml` including dependencies: `fasthtml`, `httpx[http2]`, `faker`, `iris-devtools`, and dev dependencies: `pytest`, `playwright`, `pytest-asyncio`
- [x] **T003** [P] Configure linting tools in `pyproject.toml`: black, isort, flake8, mypy

---

## Phase 3.2: Tests First (TDD) ⚠️ MUST COMPLETE BEFORE 3.3

**CRITICAL: These tests MUST be written and MUST FAIL before ANY implementation**

### Contract Tests (API Endpoints)

- [x] **T004** [P] Contract test POST /api/fraud/score in `tests/demo/contract/test_fraud_score.py` - Assert request schema (payer, amount, device required), response schema (fraud_probability, risk_classification, metrics), 200 status
- [ ] **T005** [P] Contract test POST /api/fraud/bitemporal in `tests/demo/contract/test_fraud_bitemporal.py` - Assert temporal query schema (event_id, system_time required), response includes BitemporalResult with versions
- [ ] **T006** [P] Contract test GET /api/fraud/audit/{event_id} in `tests/demo/contract/test_fraud_audit.py` - Assert path parameter, response is array of versions with audit trail data
- [ ] **T007** [P] Contract test GET /api/fraud/late-arrivals in `tests/demo/contract/test_fraud_late_arrivals.py` - Assert query params (delay_threshold_hours, limit), response includes LateArrivalTransaction array
- [ ] **T008** [P] Contract test POST /api/bio/search in `tests/demo/contract/test_bio_search.py` - Assert ProteinQuery schema, response includes similarity-ranked results
- [ ] **T009** [P] Contract test POST /api/bio/pathway in `tests/demo/contract/test_bio_pathway.py` - Assert PathwayQuery schema (source/target proteins), response includes pathway array and network
- [ ] **T010** [P] Contract test POST /api/bio/hybrid-search in `tests/demo/contract/test_bio_hybrid.py` - Assert hybrid search schema (query_text required), response includes fusion_explanation
- [ ] **T011** [P] Contract test GET /api/bio/network/{protein_id}/expand in `tests/demo/contract/test_bio_expand.py` - Assert protein_id path param, max_neighbors query param, response includes neighbors and edges
- [ ] **T012** [P] Contract test POST /api/session/switch-mode in `tests/demo/contract/test_session_mode.py` - Assert mode enum validation, response confirms mode switch
- [ ] **T013** [P] Contract test GET /api/session/history in `tests/demo/contract/test_session_history.py` - Assert response includes query history array
- [ ] **T014** [P] Contract test POST /api/session/export in `tests/demo/contract/test_session_export.py` - Assert format enum (json/csv), response varies by format

### Integration Tests (End-to-End Scenarios from quickstart.md)

- [ ] **T015** [P] Integration test fraud scoring in `tests/demo/integration/test_fraud_scoring.py` - Submit transaction via fraud API client, assert <2s response, fraud score 0.0-1.0, risk classification valid, metrics included (Scenario 1)
- [ ] **T016** [P] Integration test bitemporal time-travel in `tests/demo/integration/test_bitemporal.py` - Query historical state with system_time, assert correct version returned, temporal context preserved (Scenario 2)
- [ ] **T017** [P] Integration test late arrivals in `tests/demo/integration/test_late_arrivals.py` - Query transactions with >24h delay, assert delay_hours calculated correctly, suspicion flags present (Scenario 3)
- [ ] **T018** [P] Integration test audit trail in `tests/demo/integration/test_audit_trail.py` - Retrieve complete version history, assert all versions present, chargeback workflow visible (Scenario 4)
- [ ] **T019** [P] Integration test protein search in `tests/demo/integration/test_protein_search.py` - Search for protein via biomedical client, assert top-k results, similarity scores ranked (Scenario 5)
- [ ] **T020** [P] Integration test pathway visualization in `tests/demo/integration/test_pathway.py` - Find pathway between two proteins, assert shortest path computed, network structure valid (Scenario 6)
- [ ] **T021** [P] Integration test hybrid search in `tests/demo/integration/test_hybrid_search.py` - Execute vector+text+RRF search, assert fusion explanation present, results combined correctly (Scenario 7)
- [ ] **T022** [P] Integration test network expansion in `tests/demo/integration/test_network_expansion.py` - Expand protein node, assert neighbors loaded, edges include interaction types (Scenario 8)

---

## Phase 3.3: Core Implementation (ONLY after tests are failing)

### Data Models (from data-model.md)

- [x] **T023** [P] DemoSession model in `src/iris_demo_server/models/session.py` - Fields: session_id (UUID), mode (Enum), query_history (List), visualization_state (Dict), created_at, last_activity. Validation: mode in {fraud, biomedical}, 30min timeout
- [x] **T024** [P] Fraud query/result models in `src/iris_demo_server/models/fraud.py` - FraudTransactionQuery (payer, amount, device validation), FraudScoringResult (probability 0-1, risk_classification enum), BitemporalQuery/Result, LateArrivalTransaction (delay_hours >24)
- [ ] **T025** [P] Biomedical query/result models in `src/iris_demo_server/models/biomedical.py` - ProteinQuery (search_mode enum, top_k 1-100), ProteinSearchResult (similarity_score 0-1 ranked), PathwayQuery, InteractionNetwork (nodes/edges structure)
- [x] **T026** [P] QueryPerformanceMetrics model in `src/iris_demo_server/models/metrics.py` - Fields: query_type, execution_time_ms, backend_used, result_count, search_methods, timestamp. Used for FR-019 observability

### HTTP Clients (Backend Integration)

- [x] **T027** [P] Fraud API client in `src/iris_demo_server/services/fraud_client.py` - ResilientAPIClient with httpx.AsyncClient, circuit breaker (5 failures → 60s open), exponential backoff, connection pooling (max_connections=100), HTTP/2 enabled, fallback to demo_data/ on circuit open
- [ ] **T028** [P] Biomedical graph client in `src/iris_demo_server/services/bio_client.py` - IRIS REST API client or iris module integration for vector search, pathway queries, network expansion. Same resilience pattern as fraud client
- [ ] **T029** [P] Demo state manager in `src/iris_demo_server/services/demo_state.py` - Session storage using FastHTML signed cookies, mode switching with context preservation, query history management (max 100 queries), visualization state persistence
- [ ] **T030** [P] Demo data generator in `src/iris_demo_server/services/demo_data.py` - Faker-based synthetic data (seed=42 for reproducibility), custom biomedical providers (protein names, pathways), pre-generated datasets in `demo_data/`, DEMO_MODE environment toggle, PII-free guarantees

### API Routes (FastHTML Endpoints)

- [x] **T031** Fraud demo routes in `src/iris_demo_server/routes/fraud.py` - Implement POST /api/fraud/score (complete), POST /api/fraud/bitemporal, GET /api/fraud/audit/{event_id}, GET /api/fraud/late-arrivals using fraud_client. Return FT components for HTMX swap
- [ ] **T032** Biomedical demo routes in `src/iris_demo_server/routes/biomedical.py` - Implement POST /api/bio/search, POST /api/bio/pathway, POST /api/bio/hybrid-search, GET /api/bio/network/{protein_id}/expand using bio_client. Return network data + HTMX fragments
- [ ] **T033** Session management routes in `src/iris_demo_server/routes/session.py` - Implement POST /api/session/switch-mode (preserve query history), GET /api/session/history, POST /api/session/export (JSON/CSV formats). Use demo_state manager
- [x] **T034** FastHTML app entry point in `src/iris_demo_server/app.py` - Create FastHTML() app, configure signed cookie sessions, register routes (fraud, biomedical, session), add HTMX headers, setup CORS for development
- [ ] **T035** Error handling middleware in `src/iris_demo_server/app.py` - Catch httpx exceptions → return 503 with fallback_mode notice, validation errors → 400 with helpful messages, log all errors with trace IDs for debugging
- [ ] **T036** Performance metrics logging in `src/iris_demo_server/services/metrics_logger.py` - Log query_type, execution_time, backend_used, result_count for all requests. Output to stdout (JSON format) for monitoring

### UI Templates (FastHTML Components)

- [ ] **T037** [P] Base layout in `src/iris_demo_server/templates/base.py` - FT components: base HTML structure, tab navigation (fraud/biomedical), HTMX script includes, demo mode banner if DEMO_MODE=true, educational tooltips container
- [ ] **T038** [P] Fraud demo templates in `src/iris_demo_server/templates/fraud/` - scoring_form.py (transaction input with validation), bitemporal_query.py (dual timestamp pickers), audit_trail.py (timeline component with event markers), results_table.py (fraud scores display)
- [ ] **T039** [P] Biomedical demo templates in `src/iris_demo_server/templates/biomedical/` - protein_search.py (search form with mode selector), pathway_query.py (source/target protein inputs), results_table.py (similarity scores ranked), network_viz_container.py (canvas + SVG overlay)
- [ ] **T040** [P] D3.js network visualization in `src/iris_demo_server/static/js/network_viz.js` - Canvas rendering with Barnes-Hut force simulation, quadtree spatial indexing for hit detection, hierarchical clustering at 500+ nodes, zoom/pan/drag interactions, HTMX integration for node expansion
- [ ] **T041** [P] Demo helper utilities in `src/iris_demo_server/static/js/demo_helpers.js` - HTMX event listeners, form validation helpers, tooltip triggers, timestamp formatters, performance metrics display
- [ ] **T041b** [P] Guided tour implementation in `src/iris_demo_server/templates/guided_tour.py` - Sample query buttons for first-time users (FR-020), feature walkthrough tooltips, interactive onboarding flow, skip/dismiss controls

---

## Phase 3.4: Integration & Polish

### End-to-End Tests (Playwright)

- [ ] **T042** E2E test fraud demo workflow in `tests/demo/e2e/test_fraud_e2e.py` - Using Playwright: load homepage, submit transaction, verify fraud score displayed <2s, perform bitemporal query, check audit trail timeline, verify all HTMX swaps work
- [ ] **T043** E2E test biomedical demo workflow in `tests/demo/e2e/test_bio_e2e.py` - Using Playwright: switch to biomedical tab (verify context preserved), search protein, verify results table, run pathway query, interact with D3 graph (click node expansion), verify canvas rendering

### Deployment (IRIS ASGI + Docker)

- [ ] **T044** IRIS ASGI registration script in `src/iris_demo_server/register_asgi.py` - Use iris-devtools to register FastHTML app with IRIS web server, configure route prefix `/demo`, enable embedded Python ASGI support, handle IRIS credentials from environment
- [ ] **T045** Dockerfile for demo server in `docker/Dockerfile.demo` - Base: IRIS licensed image with embedded Python, COPY src/iris_demo_server/, install dependencies via uv, COPY demo_data/, register ASGI app on startup, EXPOSE 52773 (IRIS web server port)
- [ ] **T046** Docker Compose for demo in `docker-compose.demo.yml` - Service: iris-demo-server using Dockerfile.demo, depends_on: iris-fraud-embedded (fraud API), iris-acorn-1 (biomedical graph), environment: DEMO_MODE=false (use real data), ports: 52773:52773, volumes: demo session persistence if needed

### Documentation & Validation

- [ ] **T047** [P] Update quickstart.md with deployment commands - Add IRIS ASGI deployment section, document uvicorn dev mode alternative (`uvicorn app:app --reload`), update API validation curl commands to use `:52773/demo` prefix
- [ ] **T048** Run complete quickstart.md walkthrough - Execute all 10 test steps from quickstart.md, verify all success criteria (15 functional requirements), capture screenshots for demo documentation, validate <2s query responses
- [ ] **T049** Performance validation in `scripts/performance/test_demo_performance.py` - Benchmark fraud scoring (<10ms backend call), protein search (<200ms with HNSW), HTMX swap latency (<100ms), D3 graph rendering (60 FPS with 500 nodes), log results to docs/performance/

---

## Dependencies

**Setup blocks all**:
- T001-T003 must complete before any other tasks

**Tests block implementation**:
- T004-T022 (all tests) MUST be failing before T023-T041 (implementation)

**Models block services**:
- T023-T026 (models) → T027-T030 (services)

**Services block routes**:
- T027-T030 (services) → T031-T036 (routes)

**Routes block templates**:
- T031-T036 (routes) → T037-T041 (templates)

**Implementation blocks E2E**:
- T023-T041 (all implementation) → T042-T043 (E2E tests)

**E2E blocks deployment**:
- T042-T043 (E2E passing) → T044-T046 (deployment)

**Deployment blocks validation**:
- T044-T046 (deployed) → T047-T049 (final validation)

---

## Parallel Execution Examples

### Batch 1: Contract Tests (after setup complete)
```bash
# Launch T004-T014 together (11 contract tests, all independent files):
Task: "Contract test POST /api/fraud/score in tests/demo/contract/test_fraud_score.py"
Task: "Contract test POST /api/fraud/bitemporal in tests/demo/contract/test_fraud_bitemporal.py"
Task: "Contract test GET /api/fraud/audit/{event_id} in tests/demo/contract/test_fraud_audit.py"
# ... all 11 contract tests in parallel
```

### Batch 2: Integration Tests (after contract tests complete)
```bash
# Launch T015-T022 together (8 integration tests, all independent):
Task: "Integration test fraud scoring in tests/demo/integration/test_fraud_scoring.py"
Task: "Integration test bitemporal time-travel in tests/demo/integration/test_bitemporal.py"
# ... all 8 integration tests in parallel
```

### Batch 3: Data Models (after tests failing)
```bash
# Launch T023-T026 together (4 model files, all independent):
Task: "DemoSession model in src/iris_demo_server/models/session.py"
Task: "Fraud models in src/iris_demo_server/models/fraud.py"
Task: "Biomedical models in src/iris_demo_server/models/biomedical.py"
Task: "QueryPerformanceMetrics model in src/iris_demo_server/models/metrics.py"
```

### Batch 4: HTTP Clients (after models complete)
```bash
# Launch T027-T030 together (4 service files, all independent):
Task: "Fraud API client in src/iris_demo_server/services/fraud_client.py"
Task: "Biomedical graph client in src/iris_demo_server/services/bio_client.py"
Task: "Demo state manager in src/iris_demo_server/services/demo_state.py"
Task: "Demo data generator in src/iris_demo_server/services/demo_data.py"
```

### Batch 5: UI Templates (after routes complete)
```bash
# Launch T037-T041 together (5 template/static files, all independent):
Task: "Base layout in src/iris_demo_server/templates/base.py"
Task: "Fraud templates in src/iris_demo_server/templates/fraud/"
Task: "Biomedical templates in src/iris_demo_server/templates/biomedical/"
Task: "D3.js network viz in src/iris_demo_server/static/js/network_viz.js"
Task: "Demo helpers in src/iris_demo_server/static/js/demo_helpers.js"
```

---

## Notes

- **[P] tasks** = different files, no dependencies, safe for parallel execution
- **Verify tests fail** before implementing (TDD requirement)
- **IRIS ASGI deployment** is primary method, demonstrates embedded Python capability
- **uvicorn fallback** available for local dev: `uv run uvicorn app:app --reload --port 8200`
- **DEMO_MODE toggle** via environment variable (true = synthetic data, false = real 130M fraud DB)
- **Circuit breaker** ensures demos never fail (fallback to cached data)
- **Performance targets**: <2s queries (FR-002), <100ms HTMX swaps, 60 FPS D3 graphs
- **Commit after each task** to track progress

---

## Validation Checklist

**Completeness**:
- [x] All 11 API endpoints have contract tests (T004-T014)
- [x] All 12 entities have model tasks (T023-T026)
- [x] All 8 acceptance scenarios have integration tests (T015-T022)
- [x] All tests come before implementation (Phase 3.2 before 3.3)
- [x] Parallel tasks are truly independent (verified file paths)
- [x] Each task specifies exact file path
- [x] No [P] task modifies same file as another [P] task

**Coverage**:
- Contract tests: 11/11 endpoints ✓
- Integration tests: 8/8 scenarios ✓
- Data models: 4/4 model files ✓
- Services: 4/4 clients ✓
- Routes: 3/3 route files ✓
- Templates: 5/5 template files ✓
- E2E tests: 2/2 workflows ✓
- Deployment: 3/3 (ASGI registration, Dockerfile, docker-compose) ✓

**Total Tasks**: 49 (10 setup/tests, 19 implementation, 5 templates, 2 E2E, 3 deployment, 3 validation)

---

**Tasks generation complete. Ready for /implement or manual execution.**
