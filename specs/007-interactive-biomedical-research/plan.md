# Implementation Plan: Interactive Biomedical Research Demo

**Branch**: `007-interactive-biomedical-research` | **Date**: 2025-01-08 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/Users/tdyar/ws/iris-vector-graph/specs/007-interactive-biomedical-research/spec.md`

## Execution Flow (/plan command scope)
```
1. Load feature spec from Input path
   ✓ Spec loaded successfully
2. Fill Technical Context (scan for NEEDS CLARIFICATION)
   ✓ Project Type: Web application (FastHTML server with D3.js frontend)
   ✓ Structure Decision: Extend existing demo server architecture
3. Fill Constitution Check section
   ✓ Based on IRIS Vector Graph Constitution v1.1.0
4. Evaluate Constitution Check section
   ✓ No violations - all principles aligned
   → Update Progress Tracking: Initial Constitution Check PASS
5. Execute Phase 0 → research.md
   ✓ Resolving clarifications with reasonable defaults
6. Execute Phase 1 → contracts, data-model.md, quickstart.md, CLAUDE.md
   → In progress
7. Re-evaluate Constitution Check section
   → Pending Phase 1 completion
8. Plan Phase 2 → Task generation approach
   → Pending
9. STOP - Ready for /tasks command
```

## Summary

Build an interactive biomedical research demonstration showcasing IRIS vector search and graph traversal capabilities for Life Sciences audiences. The demo will provide protein similarity search using HNSW-indexed embeddings, D3.js network visualization for protein interactions, and pathway analysis via graph queries. Architecture mirrors the proven fraud detection demo (FastHTML + HTMX + D3.js) with integration to existing `biomedical/biomedical_engine.py` backend.

**Primary Value**: Demonstrate sub-second protein queries combining vector similarity, text search, and graph traversal to Life Sciences product managers and biomedical researchers.

## Technical Context

**Language/Version**: Python 3.11+ (existing FastHTML server)
**Primary Dependencies**:
- FastHTML 0.6+ (server-side rendering)
- HTMX 2.0 (reactive updates)
- D3.js v7 (force-directed graphs)
- httpx (async HTTP for biomedical backend)
- Pydantic (data validation)

**Storage**: IRIS database with existing biomedical schema (`biomedical/biomedical_schema.py`)
**Testing**: pytest with TestClient for FastHTML routes
**Target Platform**: Web (browser-based demo accessed via localhost:8200/bio)
**Project Type**: Web application - FastHTML backend + D3.js frontend visualization
**Performance Goals**:
- Protein search <2 seconds (FR-002)
- Network rendering 50-500 nodes without lag (FR-018)
- Pathway queries <1 second for 3-hop traversal

**Constraints**:
- Must match fraud demo visual quality (FR-031)
- Leverage existing biomedical backend without migration (FR-005)
- Circuit breaker pattern for resilient API integration
- Demo mode fallback when biomedical backend unavailable

**Scale/Scope**:
- 10-15 sample proteins for demo scenarios
- Network visualization optimized for 50-500 node graphs
- 4-5 pre-configured demo queries (similar to fraud demo)
- Single-page interactive application

**Clarification Resolutions** (from spec line 18-20, 86):
1. **Data source**: Use existing biomedical backend with demo mode fallback (mirrors fraud demo pattern)
2. **Network size limits**: Hard limit 500 nodes with client-side filtering (FR-018 requirement)
3. **Embedding model**: Use existing biomedical_engine.py implementation (abstracted from demo)
4. **Clustering strategy**: No clustering - use D3.js zoom/pan controls for large graphs (FR-014)

## Constitution Check
*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

✅ **I. IRIS-Native Development**: Integrates with existing `biomedical/biomedical_engine.py` which uses IRIS vector search (HNSW) and graph queries. No new IRIS dependencies - leverages proven architecture.

✅ **II. Test-First Development with Live Database Validation**:
- Contract tests for `/api/bio/*` endpoints (mirrors `/api/fraud/*` pattern)
- Integration tests using TestClient with IRIS connection via biomedical backend
- Demo mode allows testing without live biomedical database
- Tests written before implementation (TDD red-green-refactor)

✅ **III. Performance as a Feature**:
- Vector search via HNSW indexing in biomedical backend (<2s requirement)
- Graph queries use existing bounded pathway algorithms
- Performance metrics displayed in UI (FR-030)
- Network rendering optimized with D3.js force simulation throttling

✅ **IV. Hybrid Search by Default**:
- FR-025 requires vector + text combination
- FR-026 specifies RRF fusion (existing in biomedical backend)
- Demo scenarios showcase hybrid capabilities

✅ **V. Observability & Debuggability**:
- Query performance metrics returned with all API responses
- Backend status indicator (live data vs demo mode)
- Structured logging following fraud demo pattern
- D3.js graph interaction events logged for debugging

✅ **VI. Modular Core Library**:
- Biomedical backend (`biomedical/biomedical_engine.py`) remains independent
- Demo server only consumes APIs, no direct IRIS coupling
- Circuit breaker pattern isolates failures

✅ **VII. Explicit Error Handling**:
- Pydantic models validate all requests
- Circuit breaker returns actionable demo fallback
- 404/400/500 errors with clear user messages
- No silent failures in pathway or search operations

✅ **VIII. Standardized Database Interfaces**:
- Uses existing `biomedical/biomedical_engine.py` interfaces
- No new direct IRIS queries - reuses proven patterns
- Extends fraud demo's client-service pattern

**Initial Constitution Check**: ✅ PASS (no violations)

## Project Structure

### Documentation (this feature)
```
specs/007-interactive-biomedical-research/
├── plan.md              # This file (/plan command output)
├── research.md          # Phase 0 output (/plan command)
├── data-model.md        # Phase 1 output (/plan command)
├── quickstart.md        # Phase 1 output (/plan command)
├── contracts/           # Phase 1 output (/plan command)
│   ├── POST_api_bio_search.md
│   ├── POST_api_bio_pathway.md
│   ├── GET_api_bio_scenario.md
│   └── GET_api_bio_network.md
└── tasks.md             # Phase 2 output (/tasks command - NOT created by /plan)
```

### Source Code (repository root)
```
src/iris_demo_server/
├── app.py                         # Main FastHTML app (add /bio route)
├── models/
│   ├── biomedical.py             # [NEW] Pydantic models for protein search, pathways
│   └── metrics.py                # [EXTEND] Add biomedical query types
├── routes/
│   ├── fraud.py                  # [EXISTING] Fraud demo routes
│   └── biomedical.py             # [NEW] Biomedical demo routes
├── services/
│   ├── fraud_client.py           # [EXISTING] Fraud API client with circuit breaker
│   └── biomedical_client.py      # [NEW] Biomedical API client with circuit breaker
└── templates/
    └── biomedical.py             # [NEW] FastHTML components for bio page

biomedical/                        # [EXISTING] Backend integration point
├── biomedical_engine.py          # [REUSE] Vector search, pathway queries
└── biomedical_schema.py          # [REUSE] IRIS schema

tests/demo/
├── contract/
│   ├── test_bio_search.py        # [NEW] Contract test for protein search
│   ├── test_bio_pathway.py       # [NEW] Contract test for pathway analysis
│   └── test_bio_network.py       # [NEW] Contract test for network data
└── integration/
    └── test_bio_scenarios.py     # [NEW] End-to-end demo scenarios
```

**Structure Decision**: Extends existing `src/iris_demo_server/` web application structure. Mirrors fraud demo architecture (routes, models, services, contracts) to maintain consistency. Reuses proven circuit breaker pattern and FastHTML + HTMX + D3.js stack.

## Phase 0: Outline & Research

**Unknowns Identified from Technical Context** (all resolved):
1. ✅ Biomedical backend API interface → Use `biomedical/biomedical_engine.py`
2. ✅ Network visualization performance strategy → D3.js force simulation with zoom controls
3. ✅ Demo mode fallback data structure → JSON fixtures matching protein schema
4. ✅ D3.js graph layout algorithm → Force-directed with collision detection

**Research Tasks Completed**:

### 1. Biomedical Backend Integration
**Decision**: Use `biomedical/biomedical_engine.py` as backend with async HTTP client wrapper

**Rationale**:
- Existing backend provides vector search, pathway queries, and protein metadata
- Circuit breaker pattern proven in fraud demo (95% code reuse)
- Async httpx client matches fraud_client.py architecture

**Alternatives Considered**:
- Direct IRIS access → Rejected: Violates modular core library principle
- New backend service → Rejected: Duplicates existing functionality

### 2. Network Visualization Strategy
**Decision**: D3.js force-directed graph with zoom, pan, and node-based filtering

**Rationale**:
- D3.js already integrated in fraud demo (line 12 of app.py)
- Force-directed layout optimal for protein interaction networks (biological literature standard)
- Zoom/pan handles 50-500 nodes without clustering complexity
- Client-side rendering reduces server load

**Alternatives Considered**:
- Cytoscape.js → Rejected: Additional 500KB dependency
- Server-side graph clustering → Rejected: Adds backend complexity for marginal UX gain
- Canvas rendering → Rejected: Loses D3.js interaction model

### 3. Demo Mode Fallback Strategy
**Decision**: JSON fixtures with 10-15 sample proteins, hardcoded interactions, heuristic similarity scores

**Rationale**:
- Matches fraud demo circuit breaker pattern (src/iris_demo_server/services/fraud_client.py:102-127)
- Enables demo without running biomedical backend
- Circuit breaker opens after 5 failures, falls back gracefully

**Alternatives Considered**:
- Embedded SQLite → Rejected: Adds dependency, violates IRIS-native principle
- Static page when backend down → Rejected: Poor UX, loses demo value

### 4. Performance Optimization Approach
**Decision**:
- Backend: HNSW vector index (existing in biomedical_engine.py)
- Frontend: D3.js force simulation throttling (60fps cap)
- Network: HTTP/2 connection pooling in httpx client
- Graph: Lazy node expansion (only fetch neighbors on click)

**Rationale**:
- Meets <2s search requirement (FR-002)
- Handles 50-500 nodes smoothly (FR-018)
- Lazy loading reduces initial render time
- Proven in fraud demo (similar metrics dashboard pattern)

**Output**: research.md generated with all clarifications resolved

## Phase 1: Design & Contracts

### 1. Data Model (data-model.md)

**Entities Extracted from Spec** (lines 140-158):

#### Protein
- **Fields**: protein_id (str), name (str), organism (str), sequence (str), function_description (str), vector_embedding (List[float])
- **Validation**: protein_id required, vector_embedding 768-dimensional
- **Source**: Biomedical backend response

#### ProteinSearchQuery
- **Fields**: query_text (str), query_type (enum: name|sequence|function), top_k (int, default=10), filters (dict)
- **Validation**: query_text non-empty, top_k 1-50, query_type in enum
- **Usage**: Request model for `/api/bio/search`

#### SimilaritySearchResult
- **Fields**: proteins (List[Protein]), similarity_scores (List[float]), search_method (str), performance_metrics (QueryPerformanceMetrics)
- **Validation**: len(proteins) == len(similarity_scores), scores 0.0-1.0
- **Usage**: Response model for `/api/bio/search`

#### Interaction
- **Fields**: source_protein_id (str), target_protein_id (str), interaction_type (str), confidence_score (float), evidence (str)
- **Validation**: confidence_score 0.0-1.0, protein_ids exist
- **Source**: Biomedical backend pathway queries

#### InteractionNetwork
- **Fields**: nodes (List[Protein]), edges (List[Interaction]), layout_hints (dict)
- **Validation**: All edge protein_ids exist in nodes
- **Usage**: Response model for `/api/bio/network`

#### PathwayQuery
- **Fields**: source_protein_id (str), target_protein_id (str), max_hops (int, default=3)
- **Validation**: protein_ids non-empty, max_hops 1-5
- **Usage**: Request model for `/api/bio/pathway`

#### PathwayResult
- **Fields**: path (List[str]), intermediate_proteins (List[Protein]), path_interactions (List[Interaction]), confidence (float)
- **Validation**: path length >= 2, confidence 0.0-1.0
- **Usage**: Response model for `/api/bio/pathway`

#### QueryPerformanceMetrics
- **Fields**: query_type (str), execution_time_ms (int), backend_used (enum: biomedical_api|cached_demo), result_count (int)
- **Validation**: execution_time_ms >= 0, backend_used in enum
- **Usage**: Embedded in all API responses

### 2. API Contracts (contracts/)

#### POST /api/bio/search
**Purpose**: Protein similarity search (FR-006, FR-007)
**Request**:
```json
{
  "query_text": "TP53",
  "query_type": "name",
  "top_k": 10,
  "filters": {"organism": "human"}
}
```
**Response**: SimilaritySearchResult
**Status Codes**: 200 OK, 400 Bad Request, 500 Internal Server Error

#### POST /api/bio/pathway
**Purpose**: Shortest pathway between proteins (FR-019, FR-020)
**Request**:
```json
{
  "source_protein_id": "ENSP00000269305",
  "target_protein_id": "ENSP00000 344548",
  "max_hops": 3
}
```
**Response**: PathwayResult
**Status Codes**: 200 OK, 400 Bad Request, 404 No Path Found

#### GET /api/bio/network/{protein_id}
**Purpose**: Interaction network for protein (FR-012, FR-013)
**Parameters**: protein_id (path), expand_depth (query, default=1)
**Response**: InteractionNetwork
**Status Codes**: 200 OK, 404 Not Found

#### GET /api/bio/scenario/{scenario_name}
**Purpose**: Pre-configured demo scenarios (FR-029)
**Parameters**: scenario_name (path: cancer_protein|metabolic_pathway|drug_target)
**Response**: HTML form with pre-filled values (HTMX swap)
**Status Codes**: 200 OK, 404 Not Found

### 3. Contract Tests (tests/demo/contract/)

All tests MUST FAIL until implementation complete (TDD red phase).

**test_bio_search.py**:
- `test_protein_search_request_schema()`: Validates request model
- `test_protein_search_response_schema()`: Validates response structure
- `test_protein_search_validation_errors()`: Tests 400 errors
- `test_protein_search_performance()`: Asserts <2s response time
- `test_protein_search_similarity_scores()`: Validates 0.0-1.0 range

**test_bio_pathway.py**:
- `test_pathway_request_schema()`: Validates pathway query
- `test_pathway_response_schema()`: Validates path structure
- `test_pathway_no_path_found()`: Tests 404 when disconnected
- `test_pathway_confidence_scores()`: Validates confidence 0.0-1.0

**test_bio_network.py**:
- `test_network_response_schema()`: Validates nodes + edges
- `test_network_node_expansion()`: Tests expand_depth parameter
- `test_network_size_limits()`: Asserts max 500 nodes

### 4. Integration Test Scenarios (tests/demo/integration/test_bio_scenarios.py)

Derived from user stories (spec lines 53-80):

- `test_scenario_protein_similarity_search()`: Story 1 - Search TP53
- `test_scenario_network_visualization()`: Story 4 - Display interaction graph
- `test_scenario_pathway_analysis()`: Story 7 - Find path between proteins
- `test_scenario_hybrid_search()`: Story 10 - Combine vector + text

### 5. Quickstart Validation (quickstart.md)

Step-by-step demo execution:
1. Start demo server: `python src/iris_demo_server/app.py`
2. Navigate to `http://localhost:8200/bio`
3. Select "Cancer Protein Research" scenario
4. Search for "TP53" → Verify top 10 results appear <2s
5. Click protein node → Verify network expands
6. Select two proteins → Request pathway → Verify path highlighted
7. Apply organism filter → Verify real-time update

**Success Criteria**: All 7 steps complete without errors, performance <2s

**Output**: data-model.md, contracts/*.md, contract tests (failing), quickstart.md

## Phase 2: Task Planning Approach
*This section describes what the /tasks command will do - DO NOT execute during /plan*

**Task Generation Strategy**:
1. Load `.specify/templates/tasks-template.md` as foundation
2. Extract tasks from Phase 1 artifacts:
   - data-model.md → Pydantic model creation tasks
   - contracts/*.md → Contract test tasks + implementation tasks
   - quickstart.md → Integration test tasks

**Ordering Strategy** (TDD + Dependency Order):
1. **[P] Setup Tasks** (parallel):
   - Create `models/biomedical.py` with Pydantic models
   - Create `services/biomedical_client.py` skeleton with circuit breaker
   - Create `routes/biomedical.py` FastHTML route handlers

2. **Contract Tests** (sequential, must fail first):
   - Write `test_bio_search.py` (MUST FAIL)
   - Write `test_bio_pathway.py` (MUST FAIL)
   - Write `test_bio_network.py` (MUST FAIL)

3. **[P] Implementation** (parallel where independent):
   - Implement biomedical_client.py API integration
   - Implement routes/biomedical.py endpoints
   - Create D3.js visualization components

4. **Make Tests Pass** (sequential):
   - Fix contract tests (green phase)
   - Add integration tests
   - Refactor for performance

5. **Frontend & Polish**:
   - Style biomedical page matching fraud demo
   - Add demo scenarios (cancer_protein, metabolic_pathway, drug_target)
   - Update homepage with /bio link

**Estimated Output**: 25-30 tasks in dependency order with [P] markers for parallelization

**Task Categories**:
- Models: 3 tasks (Protein, Query, Result models)
- Services: 4 tasks (client setup, circuit breaker, demo fallback, API methods)
- Routes: 5 tasks (search, pathway, network, scenario, page render)
- Tests: 8 tasks (contract tests for 4 endpoints, integration tests)
- Frontend: 6 tasks (D3.js graph, CSS styling, HTMX interactions, scenarios)
- Integration: 4 tasks (homepage link, navigation, metrics dashboard, quickstart validation)

**IMPORTANT**: This phase executed by /tasks command, NOT by /plan

## Phase 3+: Future Implementation
*These phases are beyond the scope of the /plan command*

**Phase 3**: Task execution (/tasks command creates tasks.md)
**Phase 4**: Implementation (execute tasks.md following TDD + constitutional principles)
**Phase 5**: Validation (run pytest, execute quickstart.md, verify <2s performance)

## Complexity Tracking
*No constitutional violations requiring justification*

This implementation has zero complexity deviations:
- Reuses fraud demo architecture (proven pattern)
- Extends existing FastHTML server (no new projects)
- Leverages existing biomedical backend (no new IRIS coupling)
- Follows TDD with live database testing
- Circuit breaker ensures resilience
- Performance optimized via HNSW + D3.js throttling

## Progress Tracking

**Phase Status**:
- [x] Phase 0: Research complete (/plan command)
- [x] Phase 1: Design complete (/plan command)
- [x] Phase 2: Task planning complete (/plan command - describe approach only)
- [ ] Phase 3: Tasks generated (/tasks command)
- [ ] Phase 4: Implementation complete
- [ ] Phase 5: Validation passed

**Gate Status**:
- [x] Initial Constitution Check: PASS
- [x] Post-Design Constitution Check: PASS
- [x] All NEEDS CLARIFICATION resolved (reasonable defaults applied)
- [x] Complexity deviations documented (NONE)

---
*Based on Constitution v1.1.0 - See `.specify/memory/constitution.md`*
