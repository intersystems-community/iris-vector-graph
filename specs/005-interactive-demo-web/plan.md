# Implementation Plan: Interactive IRIS Demo Web Interface

**Branch**: `005-interactive-demo-web` | **Date**: 2025-01-06 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/005-interactive-demo-web/spec.md`

## Execution Flow (/plan command scope)
```
1. Load feature spec from Input path
   ✓ Loaded spec.md successfully
2. Fill Technical Context (scan for NEEDS CLARIFICATION)
   ✓ Project Type: Web application (demo server with interactive UI)
   ✓ Assumptions documented for 4 NEEDS CLARIFICATION items
3. Fill Constitution Check section
   ✓ Constitutional requirements evaluated
4. Evaluate Constitution Check section
   ✓ Initial check: PASS (web demo aligns with IRIS-native principles)
5. Execute Phase 0 → research.md
   ✓ Research complete (5 technology areas researched)
6. Execute Phase 1 → contracts, data-model.md, quickstart.md, CLAUDE.md
   ✓ data-model.md created (12 entities defined)
   ✓ contracts/openapi.yaml created (11 endpoints, 20+ schemas)
   ✓ quickstart.md created (10-step demo walkthrough)
   ✓ CLAUDE.md updated (demo server context added)
7. Re-evaluate Constitution Check
   ✓ Post-design check: PASS (no violations introduced)
8. Plan Phase 2 → Task generation approach (described, not executed)
   ✓ Task breakdown documented (39 tasks planned)
9. STOP - Ready for /tasks command
   ✓ COMPLETE
```

## Summary

Build an interactive web-based demo server showcasing IRIS capabilities across two domains: **Financial Services (fraud detection)** and **Biomedical Research (protein networks)**. The demo integrates with existing fraud detection API (`:8100`) and biomedical graph backend, providing live queries, bitemporal time-travel, network visualization, and hybrid search demonstrations. Target audience: IDFS sales engineers and Life Sciences product managers conducting prospect demos.

**Technical Approach** (from research): FastHTML + HTMX for reactive UI, direct integration with existing IRIS backends, D3.js for graph visualization, session-based demo state preservation.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: FastHTML, HTMX, D3.js (client), httpx (async API client), iris module (embedded Python)
**Storage**: Session-based (in-memory state), no persistent demo database required
**Testing**: pytest, FastHTML test client, Playwright (E2E for interactive visualizations)
**Target Platform**: Linux/macOS server (Docker container), modern browsers (Chrome 90+, Firefox 88+, Safari 14+)
**Project Type**: Web application (single-page demo with dual modes)
**Performance Goals**: <2s query response, <100ms UI updates (HTMX swap), live fraud scoring <10ms (existing API)
**Constraints**: Zero data migration (use existing APIs), demo data for external customers (sanitized subset), graceful degradation if backends unavailable
**Scale/Scope**: Internal demo tool, ~10-20 concurrent users max, 2 demo modes (fraud, biomedical), ~8 interactive features

**Assumptions for [NEEDS CLARIFICATION] items**:
1. **Deployment target**: Internal only (first iteration). External customer demos use same server with sanitized demo data toggle.
2. **Authentication**: None for initial version (internal network). Add basic auth if deploying externally.
3. **Data privacy**: Use existing 130M fraud database for internal demos. Provide "demo mode" toggle to use synthetic/sanitized data for external prospects.
4. **Graph visualization limits**: Auto-cluster when >500 nodes, provide "show more" expansion with warning. Use force-directed layout with collision detection.
5. **Export formats**: JSON (for technical users), CSV (for data analysis). PDF export deferred to future iteration.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### I. IRIS-Native Development
✅ **PASS** - Demo integrates with existing IRIS-backed APIs:
- Fraud detection API (`:8100`) uses licensed IRIS with embedded Python
- Biomedical graph uses IRIS vector search (HNSW), RRF fusion, graph traversal
- Bitemporal queries execute against IRIS SQL (valid_time, system_time indexes)
- No external databases introduced

### II. Test-First Development with Live Database Validation
✅ **PASS** - Testing strategy:
- Contract tests for demo API endpoints (must fail before implementation)
- Integration tests using live IRIS backends (fraud API, biomedical graph)
- E2E tests with Playwright for interactive visualizations
- Performance tests verify <2s response time requirement

### III. Performance as a Feature
✅ **PASS** - Performance requirements explicit:
- FR-002: Query results within 2 seconds
- FR-019: Display performance metrics (query time, method used)
- Leverage existing optimized backends (fraud API <10ms, HNSW vector search <10ms)

### IV. Hybrid Search by Default
✅ **PASS** - Biomedical demo showcases hybrid search:
- FR-017: Demonstrate hybrid search (vector + text + RRF fusion)
- Educational tooltips explain how RRF combines search methods

### V. Observability & Debuggability
✅ **PASS** - Observability built in:
- FR-019: Display query performance metrics to users
- Structured logging for demo requests (query type, response time, errors)
- Error handling with user-friendly messages (FR-004: explanatory text)

### VI. Modular Core Library
✅ **PASS** - Demo does NOT modify core libraries:
- Uses existing `iris_fraud_server` (FastAPI fraud scoring)
- Uses existing `iris_vector_graph_core` (biomedical graph queries)
- Demo layer is independent web application

### VII. Explicit Error Handling
✅ **PASS** - Error handling requirements:
- Edge cases documented in spec (API unavailable → cached demo data)
- No results → helpful suggestions
- Invalid input → validation errors with date range guidance

### VIII. Standardized Database Interfaces
✅ **PASS** - Uses existing standardized interfaces:
- Fraud API calls via HTTP client (no direct DB access)
- Biomedical queries via IRIS REST API or iris module
- No ad-hoc database queries in demo layer

**Initial Constitution Check**: ✅ **PASS**

## Project Structure

### Documentation (this feature)
```
specs/005-interactive-demo-web/
├── plan.md              # This file (/plan command output)
├── research.md          # Phase 0 output (/plan command)
├── data-model.md        # Phase 1 output (/plan command)
├── quickstart.md        # Phase 1 output (/plan command)
├── contracts/           # Phase 1 output (/plan command)
│   ├── openapi.yaml     # API contract for demo endpoints
│   └── schemas/         # Request/response schemas
└── tasks.md             # Phase 2 output (/tasks command - NOT created by /plan)
```

### Source Code (repository root)
```
src/iris_demo_server/     # Demo web application
├── __init__.py
├── app.py                # FastHTML application entry point
├── routes/
│   ├── fraud.py          # Financial services demo routes
│   ├── biomedical.py     # Biomedical research demo routes
│   └── api.py            # JSON API endpoints (for HTMX)
├── services/
│   ├── fraud_client.py   # HTTP client for fraud API (:8100)
│   ├── bio_client.py     # Client for biomedical graph queries
│   └── demo_state.py     # Session state management
├── templates/
│   ├── base.html         # Base layout with tab navigation
│   ├── fraud/            # Fraud demo components
│   │   ├── scoring_form.html
│   │   ├── bitemporal_query.html
│   │   └── audit_trail.html
│   └── biomedical/       # Biomedical demo components
│       ├── protein_search.html
│       ├── network_viz.html
│       └── pathway_query.html
└── static/
    ├── js/
    │   ├── network_viz.js  # D3.js graph visualization
    │   └── demo_helpers.js # UI utilities
    └── css/
        └── demo.css

tests/demo/
├── test_fraud_routes.py       # Contract tests for fraud endpoints
├── test_bio_routes.py         # Contract tests for biomedical endpoints
├── test_integration.py        # Integration tests with live backends
└── test_e2e_viz.py           # Playwright tests for visualizations

docker/
└── Dockerfile.demo            # Demo server container
```

**Structure Decision**: Web application structure chosen. Demo server is a standalone FastHTML application that integrates with existing IRIS backends (fraud API, biomedical graph). No backend/frontend split needed—FastHTML provides server-rendered components with HTMX reactivity.

## Phase 0: Outline & Research

### Research Tasks

1. **FastHTML + HTMX Integration Patterns**
   - Best practices for session state management in FastHTML
   - HTMX patterns for partial page updates (swap strategies)
   - Form handling with validation and error display

2. **D3.js Force-Directed Graphs**
   - Optimal layout algorithms for protein interaction networks
   - Performance with 500+ nodes (clustering strategies)
   - Interactive node expansion (load on demand)

3. **Bitemporal Query UI Patterns**
   - Time-travel UI/UX (timestamp picker, historical views)
   - Diff visualization (original vs. current state)
   - Audit trail display (timeline component)

4. **Demo Data Management**
   - Synthetic data generation for external demos
   - Demo mode toggle implementation
   - Fallback handling when backends unavailable

5. **HTTP Client Best Practices**
   - Async HTTP client patterns (httpx)
   - Connection pooling for repeated API calls
   - Timeout and retry strategies

### Research Output

**File**: `research.md`

**Format**:
```markdown
## Decision: [Technology/Pattern Choice]
**Rationale**: [Why chosen]
**Alternatives considered**: [What else evaluated]
**Implementation notes**: [Key details for Phase 1]
```

**Coverage**:
- FastHTML session management approach
- HTMX swap strategies for each demo feature
- D3.js graph visualization architecture
- Demo data toggle mechanism
- API client configuration

## Phase 1: Design & Contracts

*Prerequisites: research.md complete*

### 1. Data Model (`data-model.md`)

Extract entities from spec (section "Key Entities"):

**Entities**:
- `DemoSession`: mode (fraud/biomedical), query_history[], visualization_state{}
- `FraudTransactionQuery`: payer, payee, amount, device, merchant, ip, timestamp
- `FraudScoringResult`: fraud_probability, risk_classification, contributing_factors[], scoring_timestamp
- `BitemporalQuery`: event_id, historical_timestamp
- `BitemporalResult`: versions[], fraud_score, status, changed_by, change_reason
- `ProteinQuery`: protein_identifier, search_criteria{}
- `ProteinSearchResult`: proteins[], similarity_scores[], interaction_counts[], metadata{}
- `InteractionNetwork`: nodes[], edges[], visualization_state{}
- `QueryPerformanceMetrics`: execution_time_ms, backend_used, result_count, search_methods[]

**Validation Rules** (from requirements):
- FR-002: Query response time <2000ms
- FR-007: Risk classification in {low, medium, high, critical}
- FR-010: Late arrival = timestamp diff >24h
- FR-014: Similarity scores 0.0-1.0, ranked descending

**State Transitions**:
- DemoSession: fraud <-> biomedical (FR-021: preserve context)
- FraudStatus: clean -> suspicious -> confirmed_fraud -> reversed (FR-011: chargeback workflow)

### 2. API Contracts (`contracts/openapi.yaml`)

Map functional requirements to endpoints:

**Fraud Endpoints**:
- `POST /api/fraud/score` (FR-006: submit transaction for scoring)
- `POST /api/fraud/bitemporal` (FR-008: time-travel query)
- `GET /api/fraud/audit/{event_id}` (FR-009: audit trail)
- `GET /api/fraud/late-arrivals` (FR-010: late arrivals dashboard)

**Biomedical Endpoints**:
- `POST /api/bio/search` (FR-013: protein search)
- `POST /api/bio/pathway` (FR-016: pathway query)
- `POST /api/bio/hybrid-search` (FR-017: vector + text + graph)
- `GET /api/bio/network/{protein_id}/expand` (FR-018: expand node)

**Session Endpoints**:
- `POST /api/session/switch-mode` (FR-021: switch financial/biomedical)
- `GET /api/session/history` (FR-003: query history)
- `POST /api/session/export` (FR-023: export demo results)

**Contract Tests** (must fail):
- `test_contracts/test_fraud_endpoints.py`
- `test_contracts/test_bio_endpoints.py`
- `test_contracts/test_session_endpoints.py`

### 3. Integration Test Scenarios

Extract from spec "Acceptance Scenarios":

**Financial Services** (4 scenarios):
1. `test_fraud_scoring_live_query` (Scenario 1: transaction scoring <2s)
2. `test_bitemporal_time_travel` (Scenario 2: "what did we know at approval time?")
3. `test_late_arrival_detection` (Scenario 3: settlement delays >24h)
4. `test_audit_trail_preservation` (Scenario 4: complete version history)

**Biomedical** (4 scenarios):
5. `test_protein_similarity_search` (Scenario 5: top 10 similar proteins)
6. `test_pathway_visualization` (Scenario 6: shortest path between proteins)
7. `test_hybrid_search_fusion` (Scenario 7: vector + text + RRF)
8. `test_network_node_expansion` (Scenario 8: click protein -> show partners)

### 4. Quickstart Validation (`quickstart.md`)

User flow for demo walkthrough:

1. Start demo server: `docker-compose -f docker-compose.demo.yml up -d`
2. Access UI: http://localhost:8200
3. **Fraud Tab**: Submit sample transaction → verify fraud score <2s
4. **Fraud Tab**: Select transaction → time-travel query → verify historical state
5. **Biomedical Tab**: Search "TP53" → verify top 10 similar proteins
6. **Biomedical Tab**: Select two proteins → verify pathway visualization
7. Switch tabs → verify session context preserved
8. Export results → verify JSON download

### 5. Update CLAUDE.md

Run `.specify/scripts/bash/update-agent-context.sh claude` to add:

```markdown
## Demo Server (005-interactive-demo-web)

**Tech Stack**: FastHTML, HTMX, D3.js
**Port**: `:8200` (demo server)
**Integrations**:
  - Fraud API (`:8100/fraud/score`)
  - IRIS biomedical graph (vector search, RRF fusion)

**Running Demo**:
```bash
docker-compose -f docker-compose.demo.yml up -d
# Access: http://localhost:8200
```

**Testing**:
```bash
pytest tests/demo/
playwright test tests/demo/test_e2e_viz.py
```

**Recent Changes** (2025-01-06):
- Added interactive demo server for IDFS and Life Sciences teams
- Fraud demo: live scoring, bitemporal queries, audit trails
- Biomedical demo: protein search, network viz, hybrid search
```

## Phase 2: Task Planning Approach

*This section describes what the /tasks command will do - DO NOT execute during /plan*

**Task Generation Strategy**:
1. Load `.specify/templates/tasks-template.md` as base
2. Generate contract test tasks from `contracts/openapi.yaml`
3. Generate model tasks from `data-model.md` entities
4. Generate integration test tasks from acceptance scenarios
5. Generate implementation tasks (routes, services, templates, visualizations)
6. Add deployment tasks (Dockerfile, docker-compose.demo.yml)

**Task Breakdown**:

**Contract Tests** (8 tasks, [P] parallel):
- [P] Task 1: Write failing test for `/api/fraud/score` endpoint
- [P] Task 2: Write failing test for `/api/fraud/bitemporal` endpoint
- [P] Task 3: Write failing test for `/api/fraud/audit/{id}` endpoint
- [P] Task 4: Write failing test for `/api/fraud/late-arrivals` endpoint
- [P] Task 5: Write failing test for `/api/bio/search` endpoint
- [P] Task 6: Write failing test for `/api/bio/pathway` endpoint
- [P] Task 7: Write failing test for `/api/bio/hybrid-search` endpoint
- [P] Task 8: Write failing test for `/api/bio/network/{id}/expand` endpoint

**Data Models** (3 tasks, [P] parallel):
- [P] Task 9: Create session state models (DemoSession, query history)
- [P] Task 10: Create fraud query/result models with validation
- [P] Task 11: Create biomedical query/result models with validation

**Integration Tests** (8 tasks, sequential - depends on models):
- Task 12: Integration test for fraud scoring (Scenario 1)
- Task 13: Integration test for bitemporal queries (Scenario 2)
- Task 14: Integration test for late arrivals (Scenario 3)
- Task 15: Integration test for audit trails (Scenario 4)
- Task 16: Integration test for protein search (Scenario 5)
- Task 17: Integration test for pathway visualization (Scenario 6)
- Task 18: Integration test for hybrid search (Scenario 7)
- Task 19: Integration test for network expansion (Scenario 8)

**Services** (4 tasks, [P] parallel - depends on models):
- [P] Task 20: Implement fraud API client (httpx, connection pooling)
- [P] Task 21: Implement biomedical graph client (IRIS REST API or iris module)
- [P] Task 22: Implement demo state manager (session storage)
- [P] Task 23: Implement demo data generator (synthetic data for external demos)

**Routes** (6 tasks, depends on services):
- Task 24: Implement fraud demo routes (scoring, bitemporal, audit)
- Task 25: Implement biomedical demo routes (search, pathway, hybrid)
- Task 26: Implement session management routes (switch mode, history, export)
- Task 27: Create FastHTML app entry point with tab navigation
- Task 28: Add error handling and fallback for unavailable backends
- Task 29: Add performance metrics logging

**UI Templates** (5 tasks, [P] parallel - depends on routes):
- [P] Task 30: Create base layout with fraud/biomedical tabs
- [P] Task 31: Create fraud demo templates (forms, results tables)
- [P] Task 32: Create biomedical demo templates (search forms, results)
- [P] Task 33: Implement D3.js network visualization component
- [P] Task 34: Add educational tooltips (IRIS features explanations)

**E2E Tests** (2 tasks, depends on UI):
- Task 35: Playwright test for fraud demo workflow
- Task 36: Playwright test for biomedical demo workflow (with network viz)

**Deployment** (3 tasks, final):
- Task 37: Create Dockerfile.demo (FastHTML server)
- Task 38: Create docker-compose.demo.yml (demo server + dependencies)
- Task 39: Update quickstart.md with deployment instructions

**Ordering Strategy**:
- TDD order: Contract tests (1-8) → Models (9-11) → Integration tests (12-19) → Implementation (20-36)
- Mark [P] for tasks that can run in parallel (independent files/modules)
- Final tasks (37-39) depend on everything else

**Estimated Output**: ~39 numbered, ordered tasks in tasks.md

**IMPORTANT**: This phase is executed by the /tasks command, NOT by /plan

## Phase 3+: Future Implementation

*These phases are beyond the scope of the /plan command*

**Phase 3**: Task execution (/tasks command creates tasks.md)
**Phase 4**: Implementation (execute tasks.md following TDD and constitutional principles)
**Phase 5**: Validation (run tests, execute quickstart.md walkthrough, performance checks)

**Implementation Notes**:
- Use existing fraud API (`:8100`) and biomedical graph backend (no schema changes)
- Demo server runs on `:8200` (avoid port conflicts)
- Session state in-memory (no persistent storage needed for demo)
- Graceful degradation if backends unavailable (cached demo data)

## Complexity Tracking

*Fill ONLY if Constitution Check has violations that must be justified*

**No constitutional violations detected.** Demo integrates with existing IRIS backends, follows TDD workflow, uses standardized interfaces, includes performance requirements, and provides observability.

## Progress Tracking

*This checklist is updated during execution flow*

**Phase Status**:
- [x] Phase 0: Research complete (/plan command)
- [x] Phase 1: Design complete (/plan command)
- [x] Phase 2: Task planning complete (/plan command - describe approach only)
- [x] Phase 3: Tasks generated (/tasks command) - 49 tasks created
- [ ] Phase 4: Implementation complete
- [ ] Phase 5: Validation passed

**Gate Status**:
- [x] Initial Constitution Check: PASS
- [x] Post-Design Constitution Check: PASS
- [x] All NEEDS CLARIFICATION resolved (documented assumptions in Technical Context)
- [x] Complexity deviations documented (none - no violations)

---
*Based on Constitution v1.1.0 - See `.specify/memory/constitution.md`*
