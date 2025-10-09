# Implementation Tasks: Interactive Biomedical Research Demo

**Feature**: 007-interactive-biomedical-research
**Branch**: `007-interactive-biomedical-research`
**Generated**: 2025-01-08

**Total Tasks**: 28
**Estimated Time**: 12-16 hours
**Parallel Opportunities**: 12 tasks can run in parallel (marked with [P])

---

## Task Execution Order

**Phase 1: Setup & Models** (T001-T004)
**Phase 2: Contract Tests - TDD Red** (T005-T008) [P]
**Phase 3: Services & Client** (T009-T012)
**Phase 4: API Routes Implementation** (T013-T017)
**Phase 5: Frontend Visualization** (T018-T021) [P]
**Phase 6: Integration & Testing** (T022-T025)
**Phase 7: Polish & Validation** (T026-T028) [P]

---

## Phase 1: Setup & Models (Sequential)

### T001: Create Pydantic models for biomedical domain [P] ✅
**File**: `src/iris_demo_server/models/biomedical.py` (new file)
**Dependencies**: None
**Effort**: 1.5 hours

**Description**:
Create all Pydantic models from data-model.md:
- `Protein`: protein_id, name, organism, sequence, function_description, vector_embedding
- `ProteinSearchQuery`: query_text, query_type (enum), top_k, filters
- `SimilaritySearchResult`: proteins, similarity_scores, search_method, performance_metrics
- `Interaction`: source_protein_id, target_protein_id, interaction_type, confidence_score, evidence
- `InteractionNetwork`: nodes, edges, layout_hints
- `PathwayQuery`: source_protein_id, target_protein_id, max_hops
- `PathwayResult`: path, intermediate_proteins, path_interactions, confidence

**Validation Rules**:
- vector_embedding must be 768-dimensional (if present)
- similarity_scores must be 0.0-1.0
- top_k must be 1-50
- max_hops must be 1-5
- All protein IDs in edges must exist in nodes

**Reference**: Plan lines 235-273, data-model.md

---

### T002: Extend QueryPerformanceMetrics for biomedical queries [P] ✅
**File**: `src/iris_demo_server/models/metrics.py` (extend existing)
**Dependencies**: None
**Effort**: 0.5 hours

**Description**:
Add biomedical query types to existing metrics model:
- Add "protein_search", "pathway_search", "network_expansion" to query_type enum
- Add "biomedical_api" and "cached_demo" to backend_used enum
- Ensure backward compatibility with fraud demo metrics

**Reference**: Plan line 271, data-model.md

---

### T003: Create biomedical API client skeleton with circuit breaker [P] ✅
**File**: `src/iris_demo_server/services/biomedical_client.py` (new file)
**Dependencies**: None
**Effort**: 1 hour

**Description**:
Create `BiomedicalAPIClient` class mirroring `FraudAPIClient` pattern:
- Initialize httpx.AsyncClient with HTTP/2, timeout 30s
- Create CircuitBreaker instance (5 failures, 60s recovery)
- Add placeholder methods: `search_proteins()`, `find_pathway()`, `get_network()`
- Add `_get_demo_*()` fallback methods for each operation
- Set base_url to "http://localhost:8300" (biomedical backend)

**Pattern**: Copy 95% of structure from `services/fraud_client.py:44-56,102-127`
**Reference**: Research.md "Biomedical Backend Integration", Plan lines 177-183

---

### T004: Create biomedical route handlers skeleton ✅
**File**: `src/iris_demo_server/routes/biomedical.py` (new file)
**Dependencies**: T001 (models needed for type hints)
**Effort**: 1 hour

**Description**:
Create `register_biomedical_routes(app)` function with route skeletons:
- `@app.get("/bio")` → bio_page() returning full HTML page
- `@app.post("/api/bio/search")` → search_proteins()
- `@app.post("/api/bio/pathway")` → find_pathway()
- `@app.get("/api/bio/network/{protein_id}")` → get_network()
- `@app.get("/api/bio/scenario/{scenario_name}")` → get_scenario()

All handlers should return placeholder responses (will implement in Phase 4)

**Pattern**: Mirror `routes/fraud.py` structure
**Reference**: Plan lines 144, research.md "FastHTML + HTMX Integration"

---

## Phase 2: Contract Tests - TDD Red (Parallel)

### T005: Write contract test for POST /api/bio/search [P] ✅
**File**: `tests/demo/contract/test_bio_search.py` (new file)
**Dependencies**: T001 (models), T004 (routes)
**Effort**: 1 hour
**MUST FAIL**: This is TDD red phase

**Description**:
Create 5 contract tests from contracts/POST_api_bio_search.md:
1. `test_protein_search_request_schema()`: Validates ProteinSearchQuery acceptance
2. `test_protein_search_response_schema()`: Validates SimilaritySearchResult structure
3. `test_protein_search_validation_errors()`: Tests 400 for missing query_text
4. `test_protein_search_performance()`: Asserts response time <2s (FR-002)
5. `test_protein_search_similarity_scores()`: Validates scores 0.0-1.0

**Pattern**: Copy test structure from `tests/demo/contract/test_fraud_score.py`
**Reference**: Contracts/POST_api_bio_search.md, Plan lines 320-325

---

### T006: Write contract test for POST /api/bio/pathway [P] ✅
**File**: `tests/demo/contract/test_bio_pathway.py` (new file)
**Dependencies**: T001 (models), T004 (routes)
**Effort**: 1 hour
**MUST FAIL**: This is TDD red phase

**Description**:
Create 4 contract tests from contracts/POST_api_bio_pathway.md:
1. `test_pathway_request_schema()`: Validates PathwayQuery with source/target/max_hops
2. `test_pathway_response_schema()`: Validates PathwayResult structure (path, proteins, interactions)
3. `test_pathway_no_path_found()`: Tests 404 when no path exists
4. `test_pathway_confidence_scores()`: Validates confidence 0.0-1.0

**Reference**: Contracts/POST_api_bio_pathway.md, Plan lines 327-331

---

### T007: Write contract test for GET /api/bio/network/{protein_id} [P] ✅
**File**: `tests/demo/contract/test_bio_network.py` (new file)
**Dependencies**: T001 (models), T004 (routes)
**Effort**: 0.75 hours
**MUST FAIL**: This is TDD red phase

**Description**:
Create 3 contract tests from contracts/GET_api_bio_network.md:
1. `test_network_response_schema()`: Validates InteractionNetwork (nodes + edges)
2. `test_network_node_expansion()`: Tests expand_depth parameter changes node count
3. `test_network_size_limits()`: Asserts max 500 nodes returned (FR-018)

**Reference**: Contracts/GET_api_bio_network.md, Plan lines 333-336

---

### T008: Write contract test for GET /api/bio/scenario/{scenario_name} [P] ✅
**File**: `tests/demo/contract/test_bio_scenario.py` (new file)
**Dependencies**: T004 (routes)
**Effort**: 0.5 hours
**MUST FAIL**: This is TDD red phase

**Description**:
Create 2 contract tests:
1. `test_scenario_returns_html_form()`: Validates HTML form returned with pre-filled values
2. `test_scenario_invalid_name_404()`: Tests 404 for unknown scenario

Test all 3 scenarios: "cancer_protein", "metabolic_pathway", "drug_target"

**Reference**: Contracts/GET_api_bio_scenario.md

---

## Phase 3: Services & Client Implementation (Sequential)

### T009: Implement biomedical API client - search_proteins() ✅
**File**: `src/iris_demo_server/services/biomedical_client.py` (extend T003)
**Dependencies**: T003 (skeleton), T005 (test to make pass)
**Effort**: 1.5 hours

**Description**:
Implement `search_proteins(query: ProteinSearchQuery) -> SimilaritySearchResult`:
- Call `POST {base_url}/bio/search` with query JSON
- Handle circuit breaker: if open or demo_mode, call `_get_demo_proteins()`
- Parse response to SimilaritySearchResult
- Record success/failure on circuit breaker

**Demo Fallback**: `_get_demo_proteins()` returns 10 fixture proteins from research.md demo fixtures with heuristic similarity scores

**Reference**: Research.md "Demo Mode Fallback Strategy", fraud_client.py:57-83

---

### T010: Implement biomedical API client - find_pathway() ✅
**File**: `src/iris_demo_server/services/biomedical_client.py` (extend T003)
**Dependencies**: T003 (skeleton), T006 (test to make pass)
**Effort**: 1 hour

**Description**:
Implement `find_pathway(query: PathwayQuery) -> PathwayResult`:
- Call `POST {base_url}/bio/pathway` with source/target/max_hops
- Handle 404 when no path found (return None or raise exception)
- Circuit breaker fallback: `_get_demo_pathway()` with hardcoded TP53→MDM2→CDKN1A path

**Reference**: Research.md demo fixtures, contracts/POST_api_bio_pathway.md

---

### T011: Implement biomedical API client - get_network() ✅
**File**: `src/iris_demo_server/services/biomedical_client.py` (extend T003)
**Dependencies**: T003 (skeleton), T007 (test to make pass)
**Effort**: 1 hour

**Description**:
Implement `get_network(protein_id: str, expand_depth: int = 1) -> InteractionNetwork`:
- Call `GET {base_url}/bio/network/{protein_id}?expand_depth={depth}`
- Handle 404 when protein not found
- Circuit breaker fallback: `_get_demo_network()` with 10-15 demo proteins + interactions
- Enforce 500 node hard limit (FR-018)

**Reference**: Research.md demo fixtures, contracts/GET_api_bio_network.md

---

### T012: Run contract tests to verify green phase
**File**: All contract tests (T005-T008)
**Dependencies**: T009, T010, T011 (implementations)
**Effort**: 0.5 hours

**Description**:
Run `pytest tests/demo/contract/test_bio_*.py` and verify ALL tests pass.
- Fix any failures in services/biomedical_client.py
- Ensure <2s performance requirement met
- Verify demo mode fallback works (test without backend)

This completes TDD green phase.

**Command**: `pytest tests/demo/contract/ -v`

---

## Phase 4: API Routes Implementation (Sequential)

### T013: Implement POST /api/bio/search route
**File**: `src/iris_demo_server/routes/biomedical.py` (extend T004)
**Dependencies**: T009 (client), T012 (tests passing)
**Effort**: 1.5 hours

**Description**:
Implement `search_proteins()` route handler:
1. Parse request body to ProteinSearchQuery (Pydantic validation)
2. Call `biomedical_client.search_proteins(query)`
3. Return FastHTML components with:
   - Table of proteins with similarity scores
   - Performance metrics display
   - Backend status indicator (live API vs demo mode)
4. Use HTMX `hx-target="#results"` for reactive swap

**Pattern**: Mirror `routes/fraud.py` POST /api/fraud/score handler
**Reference**: Research.md "FastHTML + HTMX Integration", quickstart.md scenario 1

---

### T014: Implement POST /api/bio/pathway route
**File**: `src/iris_demo_server/routes/biomedical.py` (extend T004)
**Dependencies**: T010 (client), T012 (tests passing)
**Effort**: 1.25 hours

**Description**:
Implement `find_pathway()` route handler:
1. Parse PathwayQuery from request
2. Call `biomedical_client.find_pathway(query)`
3. Return HTML showing:
   - Pathway as ordered list of proteins
   - Confidence score with visual indicator
   - Interaction types along path
4. Include D3.js script to highlight path in network graph (if visible)

**Reference**: Contracts/POST_api_bio_pathway.md, quickstart.md scenario 3

---

### T015: Implement GET /api/bio/network/{protein_id} route
**File**: `src/iris_demo_server/routes/biomedical.py` (extend T004)
**Dependencies**: T011 (client), T012 (tests passing)
**Effort**: 1 hour

**Description**:
Implement `get_network()` route handler:
1. Extract protein_id from path, expand_depth from query string
2. Call `biomedical_client.get_network(protein_id, expand_depth)`
3. Return JSON with InteractionNetwork (consumed by D3.js)
4. Handle 404 when protein not found

This endpoint returns JSON (not HTML) for D3.js consumption.

**Reference**: Contracts/GET_api_bio_network.md

---

### T016: Implement GET /api/bio/scenario/{scenario_name} route
**File**: `src/iris_demo_server/routes/biomedical.py` (extend T004)
**Dependencies**: None (standalone)
**Effort**: 1 hour

**Description**:
Implement `get_scenario()` route handler with 3 scenarios:
1. **cancer_protein**: Pre-fill search form with "TP53", query_type="name", top_k=10
2. **metabolic_pathway**: Pre-fill pathway form with GAPDH → LDHA, max_hops=2
3. **drug_target**: Pre-fill search with "EGFR", filters={"organism": "Homo sapiens"}

Return HTML form that HTMX swaps into `#search-form` div.

**Pattern**: Mirror fraud demo GET /api/fraud/scenario/{name}
**Reference**: Contracts/GET_api_bio_scenario.md, quickstart.md scenarios

---

### T017: Implement GET /bio page route
**File**: `src/iris_demo_server/routes/biomedical.py` (extend T004)
**Dependencies**: T013-T016 (all routes functional)
**Effort**: 2 hours

**Description**:
Implement `bio_page()` full page render:
1. Create FastHTML page structure matching fraud demo styling
2. Include header with IRIS branding + statistics (demo protein count, avg query time)
3. Add scenario buttons (cancer_protein, metabolic_pathway, drug_target)
4. Create search form section (`id="search-form"`)
5. Create results section (`id="results"`)
6. Create network visualization section (`id="viz"`)
7. Include D3.js v7 script tag (already in app.py line 12)

**CSS**: Copy styling from fraud demo app.py lines 54-240, adapt colors/labels

**Reference**: Plan lines 46-343 (fraud demo page structure), quickstart.md

---

## Phase 5: Frontend Visualization (Parallel)

### T018: Create D3.js force-directed graph component [P]
**File**: `src/iris_demo_server/templates/biomedical.py` (new file) or inline in routes
**Dependencies**: T017 (page structure)
**Effort**: 2.5 hours

**Description**:
Create D3.js visualization script for protein interaction networks:
1. Create `renderProteinNetwork(data)` JavaScript function
2. Implement force-directed layout with:
   - `forceLink()` with distance=80
   - `forceManyBody()` with strength=-200
   - `forceCenter()` to center graph
   - `forceCollide()` with radius=30 for collision detection
3. Add zoom/pan controls (d3.zoom, scale 0.1-4)
4. Color nodes by protein type/organism
5. Label edges with interaction types
6. Make nodes draggable
7. Throttle simulation to 60fps (alphaDecay=0.02)

**Pattern**: Use D3.js v7 force simulation API
**Reference**: Research.md "Network Visualization Strategy", Plan lines 188-201

---

### T019: Add node expansion interaction (click to expand) [P]
**File**: Same as T018 (extend D3.js component)
**Dependencies**: T018 (base graph), T015 (network endpoint)
**Effort**: 1 hour

**Description**:
Add click handler to protein nodes:
1. On node click: fetch `/api/bio/network/{protein_id}?expand_depth=1`
2. Merge new nodes/edges into existing graph
3. Animate new nodes appearing
4. Update force simulation with new data
5. Highlight clicked node
6. Enforce 500 node limit (stop expansion if reached)

**Reference**: Quickstart.md scenario 2, Plan line FR-013

---

### T020: Style biomedical page to match fraud demo aesthetic [P]
**File**: `src/iris_demo_server/routes/biomedical.py` (CSS in Style tag)
**Dependencies**: T017 (page structure)
**Effort**: 1.5 hours

**Description**:
Create CSS matching fraud demo visual quality:
1. Copy base styles from fraud demo (app.py lines 54-240)
2. Change gradient colors to biomedical theme (greens/blues instead of purples)
3. Create `.protein-node`, `.interaction-edge` styles for D3.js
4. Add `.risk-badge` equivalent for similarity scores (high/medium/low)
5. Style scenario buttons with icons (💊, 🧬, 🎯)
6. Ensure responsive design (works on 1200px+ screens)

**Reference**: Plan line FR-031 (match fraud demo quality)

---

### T021: Update homepage to link to /bio [P]
**File**: `src/iris_demo_server/app.py` (extend existing homepage)
**Dependencies**: T017 (bio page functional)
**Effort**: 0.25 hours

**Description**:
Update homepage route (app.py lines 22-43):
- Ensure `/bio` link is active (currently returns 404)
- Verify link navigates to biomedical demo page
- Add subtitle: "Vector similarity search, pathway queries, network visualization"

This makes the homepage `/` → `/bio` navigation work.

**Reference**: App.py lines 38-40, Plan line FR-032

---

## Phase 6: Integration & Testing (Sequential)

### T022: Write integration test for protein similarity search scenario
**File**: `tests/demo/integration/test_bio_scenarios.py` (new file)
**Dependencies**: T013, T017, T018 (full search flow)
**Effort**: 1 hour

**Description**:
Create `test_scenario_protein_similarity_search()` integration test:
1. Navigate to `/bio`
2. Select "Cancer Protein Research" scenario
3. Verify form pre-filled with TP53
4. Submit search
5. Assert 10 results returned
6. Assert top result is TP53 with score=1.0
7. Assert response time <2s

Mirrors quickstart.md scenario 1.

**Reference**: Plan lines 340-344, quickstart.md scenario 1

---

### T023: Write integration test for network visualization scenario
**File**: `tests/demo/integration/test_bio_scenarios.py` (extend T022)
**Dependencies**: T018, T019 (graph rendering)
**Effort**: 0.75 hours

**Description**:
Create `test_scenario_network_visualization()`:
1. Perform TP53 search
2. Click first protein node
3. Assert D3.js graph renders (check for SVG elements)
4. Assert 5-10 nodes visible
5. Click MDM2 node
6. Assert graph expands (node count increases)

Mirrors quickstart.md scenario 2.

**Reference**: Quickstart.md scenario 2

---

### T024: Write integration test for pathway analysis scenario
**File**: `tests/demo/integration/test_bio_scenarios.py` (extend T022)
**Dependencies**: T014 (pathway route)
**Effort**: 0.75 hours

**Description**:
Create `test_scenario_pathway_analysis()`:
1. Select "Metabolic Pathway" scenario
2. Verify GAPDH → LDHA form
3. Submit pathway request
4. Assert path found with 2-3 hops
5. Assert confidence score 0.0-1.0
6. Assert pathway highlighted (if graph visible)

Mirrors quickstart.md scenario 3.

**Reference**: Quickstart.md scenario 3

---

### T025: Write integration test for hybrid search scenario
**File**: `tests/demo/integration/test_bio_scenarios.py` (extend T022)
**Dependencies**: T013 (search route with hybrid capability)
**Effort**: 0.5 hours

**Description**:
Create `test_scenario_hybrid_search()`:
1. Submit search: query_text="tumor suppressor", query_type="function"
2. Assert `search_method="hybrid"` in metrics
3. Assert results include known tumor suppressors (TP53, BRCA1, PTEN)
4. Assert similarity scores 0.0-1.0

Mirrors quickstart.md scenario 4.

**Reference**: Quickstart.md scenario 4, Plan line FR-025

---

## Phase 7: Polish & Validation (Parallel)

### T026: Add biomedical routes to app.py [P]
**File**: `src/iris_demo_server/app.py` (extend existing)
**Dependencies**: T017 (routes complete)
**Effort**: 0.25 hours

**Description**:
Register biomedical routes in main app:
1. Import: `from .routes.biomedical import register_biomedical_routes`
2. Call: `register_biomedical_routes(app)` after fraud routes (line 18)
3. Verify app starts without errors: `python src/iris_demo_server/app.py`

**Reference**: Plan lines 5-18

---

### T027: Run full quickstart validation [P]
**File**: Manual testing following quickstart.md
**Dependencies**: All previous tasks (T001-T026)
**Effort**: 0.5 hours

**Description**:
Execute all 7 quickstart scenarios:
1. Protein similarity search (<2s, 10 results)
2. Network visualization (graph renders, nodes expand)
3. Pathway analysis (path found, confidence shown)
4. Hybrid search (vector + text fusion)
5. Demo mode fallback (works without backend)
6. Real-time filtering (HTMX updates)
7. Performance validation (<2s for all operations)

Check off validation checklist in quickstart.md.

**Reference**: Quickstart.md entire document

---

### T028: Final constitution compliance check [P]
**File**: Review against `.specify/memory/constitution.md`
**Dependencies**: T027 (validation complete)
**Effort**: 0.5 hours

**Description**:
Verify all constitutional principles satisfied:
1. **IRIS-Native**: Uses biomedical backend (no direct IRIS in demo server) ✅
2. **Test-First**: Contract tests written before implementation ✅
3. **Performance**: <2s search (FR-002), HNSW backend ✅
4. **Hybrid Search**: RRF fusion demonstrated ✅
5. **Observability**: Metrics in all responses ✅
6. **Modular Core**: Demo server independent of backend ✅
7. **Explicit Errors**: Pydantic validation + circuit breaker ✅
8. **Standardized Interfaces**: Mirrors fraud demo pattern ✅

Run: `pytest tests/demo/ -v --cov=iris_demo_server`

**Reference**: Plan lines 72-116 (Constitution Check section)

---

## Parallel Execution Guide

### Batch 1: Models & Setup (run together)
```bash
# T001, T002, T003 can run in parallel (different files)
Task: "Create all Pydantic models in src/iris_demo_server/models/biomedical.py following data-model.md" &
Task: "Extend QueryPerformanceMetrics in src/iris_demo_server/models/metrics.py for biomedical queries" &
Task: "Create BiomedicalAPIClient skeleton in src/iris_demo_server/services/biomedical_client.py" &
wait
```

### Batch 2: Contract Tests (run together after T001-T004)
```bash
# T005, T006, T007, T008 are independent test files
pytest tests/demo/contract/test_bio_search.py &  # T005
pytest tests/demo/contract/test_bio_pathway.py &  # T006
pytest tests/demo/contract/test_bio_network.py &  # T007
pytest tests/demo/contract/test_bio_scenario.py &  # T008
wait
```

### Batch 3: Frontend Tasks (run together after T017)
```bash
# T018, T020, T021 modify different sections
Task: "Create D3.js force-directed graph component for protein networks" &
Task: "Style biomedical page with CSS matching fraud demo aesthetic" &
Task: "Update homepage app.py to link to /bio route" &
wait
```

### Batch 4: Final Validation (run together after T026)
```bash
# T027, T028 are independent validations
Task: "Execute all 7 quickstart.md validation scenarios" &
Task: "Verify constitution compliance for all 8 principles" &
wait
```

---

## Task Dependencies Graph

```
T001 [P] ─┬─→ T005 [P] ─┐
T002 [P] ─┤             │
T003 [P] ─┴─→ T006 [P] ─┤
T004     ───→ T007 [P] ─┼─→ T009 → T013 ─┐
          └─→ T008 [P] ─┘      → T014 ─┤
                         T010 → T015 ─┼─→ T017 ─┬─→ T018 [P] ─→ T019 → T022 ─┐
                         T011 ─→ T016 ─┘         ├─→ T020 [P] ──────→ T023 ─┤
                                T012 ────────────┴─→ T021 [P] ──────→ T024 ─┼─→ T026 ─┬─→ T027 [P]
                                                                     → T025 ─┘         └─→ T028 [P]
```

---

## Success Criteria

**Demo is complete when**:
- ✅ All 28 tasks completed
- ✅ All contract tests pass (pytest tests/demo/contract/)
- ✅ All integration tests pass (pytest tests/demo/integration/)
- ✅ Quickstart validation checklist 100% complete
- ✅ Constitution compliance verified (all 8 principles)
- ✅ Performance: protein search <2s, pathway <1s, network rendering <500ms
- ✅ Demo mode fallback works without backend
- ✅ Visual quality matches fraud demo (FR-031)

**Ready for**: Deployment to demo server for Life Sciences product demos

---

*Generated from plan.md, data-model.md, contracts/, quickstart.md*
*Based on Constitution v1.1.0 and TDD principles*
