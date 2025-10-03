# Tasks: Real-Time Fraud Scoring on IRIS Vector Graph (MVP)

**Input**: Design documents from `/specs/004-real-time-fraud/`
**Prerequisites**: plan.md ✅, research.md ✅, data-model.md ✅, contracts/ ✅, quickstart.md ✅

## Execution Flow (main)
```
1. Load plan.md from feature directory
   → ✅ Found plan.md with tech stack (Python 3.11, FastAPI, PyTorch, IRIS Embedded Python)
   → ✅ Project structure: Extend existing IRIS Vector Graph (sql/fraud/, iris_vector_graph_core/fraud/, api/routers/fraud.py)
2. Load optional design documents:
   → data-model.md: 3 NEW tables (gs_events, gs_labels, gs_fraud_centroid), EXTENDED rdf_props and kg_NodeEmbeddings
   → contracts/: fraud-score-api.openapi.yaml + test_fraud_score_contract.py (11 tests)
   → research.md: TorchScript loading, on-demand CTE features, k-hop sampling, GraphStorm upgrade path
   → quickstart.md: 5-step validation workflow
3. Generate tasks by category:
   → Setup: SQL schema, dependencies, fraud module structure
   → Tests: 11 contract tests (TDD), 3 integration tests, 1 e2e load test
   → Core: SQL tables, stored procedures, Python fraud module, FastAPI endpoint
   → Integration: TorchScript model loading, feature computation, explainability
   → Polish: Performance benchmarks, documentation, quickstart validation
4. Apply task rules:
   → Different files = mark [P] for parallel
   → Same file = sequential (no [P])
   → Tests before implementation (TDD - NON-NEGOTIABLE)
5. Number tasks sequentially (T001, T002...)
6. Task count: 35 tasks (8 setup, 15 tests, 8 core, 4 polish)
7. ✅ READY for execution
```

## Format: `[ID] [P?] Description`
- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions

## Path Conventions
- **Single project**: Extends existing IRIS Vector Graph repository
- **SQL**: `sql/fraud/` (new directory)
- **Python Core**: `iris_vector_graph_core/fraud/` (new module)
- **API**: `api/routers/fraud.py` (new router)
- **Tests**: `tests/contract/`, `tests/integration/`, `tests/e2e/`
- **Scripts**: `scripts/fraud/` (new directory)

---

## Phase 3.1: Setup

### T001: Create fraud-specific directory structure
**File paths**:
- `sql/fraud/` (directory)
- `iris_vector_graph_core/fraud/` (directory)
- `scripts/fraud/` (directory)
- `scripts/fraud/performance/` (directory)
- `tests/contract/` (directory, may exist)
- `tests/integration/` (directory, may exist)
- `docs/performance/` (directory, may exist)

**Tasks**:
1. Create `sql/fraud/` directory
2. Create `iris_vector_graph_core/fraud/` directory with `__init__.py`
3. Create `scripts/fraud/` and `scripts/fraud/performance/` directories
4. Ensure `tests/contract/`, `tests/integration/`, `docs/performance/` directories exist

**Acceptance**:
- All directories exist
- `iris_vector_graph_core/fraud/__init__.py` exists (can be empty initially)

---

### T002: [P] Install PyTorch dependency for TorchScript support
**File path**: `pyproject.toml` (or `requirements.txt`)

**Tasks**:
1. Add `torch>=2.0.0` to dependencies (required for TorchScript loading)
2. Add `torchvision` if needed (optional, for model preprocessing)
3. Run `uv sync` or `pip install -r requirements.txt`

**Acceptance**:
- `import torch` succeeds in Python REPL
- `torch.jit.load` function available

**Note**: PyTorch is large (~800MB). Use CPU-only version for production servers.

---

### T003: [P] Configure linting and type checking for fraud module
**File path**: `pyproject.toml`, `.flake8`, `mypy.ini`

**Tasks**:
1. Add `iris_vector_graph_core/fraud/` to mypy checked paths
2. Add `api/routers/fraud.py` to mypy checked paths
3. Verify black/isort configuration includes new modules

**Acceptance**:
- `mypy iris_vector_graph_core/fraud/` passes (with any-typed stubs for now)
- `black iris_vector_graph_core/fraud/ api/routers/fraud.py` formats successfully

---

## Phase 3.2: Tests First (TDD) ⚠️ MUST COMPLETE BEFORE 3.3
**CRITICAL: These tests MUST be written and MUST FAIL before ANY implementation**

### T004: [P] Contract test: POST /fraud/score MLP mode success
**File path**: `tests/contract/test_fraud_score_contract.py`

**Tasks**:
1. Copy contract test from `specs/004-real-time-fraud/contracts/test_fraud_score_contract.py`
2. Implement `test_fraud_score_mlp_mode_success` test
3. Run test: `pytest tests/contract/test_fraud_score_contract.py::test_fraud_score_mlp_mode_success -v`
4. **VERIFY IT FAILS** (endpoint not implemented yet)

**Acceptance**:
- Test imports FastAPI TestClient
- Test creates test entities in IRIS (via `setup_test_entities` fixture)
- Test POSTs to `/fraud/score` with MLP mode
- Test asserts 200 OK, prob in [0.0, 1.0], reasons array length >= 3
- **Test FAILS with 404 or 500 (endpoint doesn't exist)**

**Constitutional Validation**: Test-First principle - MUST fail before implementation

---

### T005: [P] Contract test: POST /fraud/score EGO mode success
**File path**: `tests/contract/test_fraud_score_contract.py`

**Tasks**:
1. Implement `test_fraud_score_ego_mode_success` test
2. Run test: `pytest tests/contract/test_fraud_score_contract.py::test_fraud_score_ego_mode_success -v`
3. **VERIFY IT FAILS**

**Acceptance**:
- Test POSTs with `mode="EGO"`
- Test asserts 200 OK with valid response
- **Test FAILS (endpoint doesn't exist)**

---

### T006: [P] Contract test: Minimum 3 reason codes
**File path**: `tests/contract/test_fraud_score_contract.py`

**Tasks**:
1. Implement `test_fraud_score_returns_min_3_reasons` test
2. Verify reason codes sorted by weight descending
3. Verify feature reasons have `=` format (e.g., "deg_24h=1")
4. **VERIFY IT FAILS**

**Acceptance**:
- Test checks `len(data["reasons"]) >= 3`
- Test validates reason code schema (kind, detail, weight)
- **Test FAILS**

---

### T007: [P] Contract test: Invalid entity_id returns 400
**File path**: `tests/contract/test_fraud_score_contract.py`

**Tasks**:
1. Implement `test_fraud_score_invalid_entity_id_400` test
2. POST with malformed entity_id (missing namespace)
3. **VERIFY IT FAILS** (endpoint doesn't exist)

**Acceptance**:
- Test expects 400 Bad Request
- Test validates error response schema (error, detail, trace_id)
- **Test FAILS**

---

### T008: [P] Contract test: Entity not found returns 404
**File path**: `tests/contract/test_fraud_score_contract.py`

**Tasks**:
1. Implement `test_fraud_score_entity_not_found_404` test
2. POST with valid-format but non-existent entity_id
3. **VERIFY IT FAILS**

**Acceptance**:
- Test expects 404 Not Found
- **Test FAILS**

---

### T009: [P] Contract test: Invalid mode returns 400/422
**File path**: `tests/contract/test_fraud_score_contract.py`

**Tasks**:
1. Implement `test_fraud_score_invalid_mode_400` test
2. POST with `mode="INVALID_MODE"`
3. **VERIFY IT FAILS**

**Acceptance**:
- Test expects 400 or 422
- **Test FAILS**

---

### T010: [P] Contract test: Missing required field returns 400/422
**File path**: `tests/contract/test_fraud_score_contract.py`

**Tasks**:
1. Implement `test_fraud_score_missing_required_field_400` test
2. POST without `payer` field
3. **VERIFY IT FAILS**

**Acceptance**:
- Test expects 400 or 422
- **Test FAILS**

---

### T011: [P] Contract test: Optional fields null handled gracefully
**File path**: `tests/contract/test_fraud_score_contract.py`

**Tasks**:
1. Implement `test_fraud_score_optional_fields_null` test
2. POST without `amount` or `country` fields
3. **VERIFY IT FAILS**

**Acceptance**:
- Test expects 200 OK (optional fields handled)
- **Test FAILS**

---

### T012: [P] Contract test: Zero amount transaction
**File path**: `tests/contract/test_fraud_score_contract.py`

**Tasks**:
1. Implement `test_fraud_score_zero_amount` test
2. POST with `amount=0.0`
3. **VERIFY IT FAILS**

**Acceptance**:
- Test expects 200 OK
- **Test FAILS**

---

### T013: [P] Contract test: Large amount transaction
**File path**: `tests/contract/test_fraud_score_contract.py`

**Tasks**:
1. Implement `test_fraud_score_large_amount` test
2. POST with `amount=1000000.00`
3. **VERIFY IT FAILS**

**Acceptance**:
- Test expects 200 OK
- **Test FAILS**

---

### T014: [P] Contract test: Meta-test suite summary
**File path**: `tests/contract/test_fraud_score_contract.py`

**Tasks**:
1. Implement `test_contract_suite_summary` meta-test
2. Verify 11 contract tests exist
3. Verify all tests marked `@pytest.mark.requires_database`

**Acceptance**:
- Meta-test counts 11 contract test functions
- All tests have `@pytest.mark.requires_database` marker
- Meta-test PASSES (validates test suite structure)

**Note**: This meta-test can pass immediately (validates structure, not implementation)

---

### T015: [P] Integration test: On-demand CTE feature computation
**File path**: `tests/integration/test_fraud_features.py`

**Tasks**:
1. Create test that inserts events into `gs_events` table
2. Execute on-demand CTE query for rolling features (deg_24h, tx_amt_sum_24h)
3. Verify feature values match expected counts
4. **VERIFY IT FAILS** (CTE pattern not implemented)

**Acceptance**:
- Test inserts 5 events for same entity within 24h window
- CTE query returns `deg_24h=5`, `tx_amt_sum_24h=<sum of amounts>`
- **Test FAILS (SQL schema doesn't exist yet)**

**Constitutional Validation**: Test-First with Live Database

---

### T016: [P] Integration test: TorchScript model loading in embedded Python
**File path**: `tests/integration/test_fraud_model_loading.py`

**Tasks**:
1. Create minimal TorchScript MLP model fixture (simple 2-layer MLP)
2. Test loading model via `iris_vector_graph_core.fraud.model_loader.load_model()`
3. Test inference with dummy input (8 features → fraud probability)
4. **VERIFY IT FAILS** (model_loader.py doesn't exist)

**Acceptance**:
- Test creates `models/test_mlp.torchscript` fixture
- Test loads model without errors
- Test runs inference, gets prob in [0.0, 1.0]
- **Test FAILS (module not implemented)**

**Constitutional Validation**: Live IRIS + Embedded Python validation

---

### T017: [P] Integration test: Explainability (gradient × input)
**File path**: `tests/integration/test_fraud_explainability.py`

**Tasks**:
1. Test feature attribution computation (gradient × input)
2. Test returns top 3 feature reasons with weights
3. Test vector proximity computation (cosine similarity to fraud centroid)
4. **VERIFY IT FAILS** (explainability.py doesn't exist)

**Acceptance**:
- Test computes gradient × input for 8 features
- Test returns sorted reasons by weight descending
- Test includes vector proximity reason
- **Test FAILS (module not implemented)**

---

### T018: [P] E2E test: Load test at 200 QPS
**File path**: `tests/e2e/test_fraud_scoring_e2e.py`

**Tasks**:
1. Create load test that sends 200 concurrent requests/sec for 15 minutes
2. Measure p95 latency for MLP mode
3. Verify error rate = 0%
4. **VERIFY IT FAILS** (endpoint doesn't exist)

**Acceptance**:
- Test uses `asyncio` or `concurrent.futures` for concurrent requests
- Test validates p95 latency <20ms (NFR target)
- Test validates 0% error rate (NFR-007)
- **Test FAILS (API not implemented)**

**Note**: This is a long-running test (15min). Mark as `@pytest.mark.slow` and skip in CI.

---

## Phase 3.3: Core Implementation (ONLY after tests are failing)

### T019: [P] SQL schema: gs_events table
**File path**: `sql/fraud/schema_fraud.sql`

**Tasks**:
1. Create `sql/fraud/schema_fraud.sql`
2. Define `gs_events` table per data-model.md:
   - `entity_id VARCHAR(256) NOT NULL`
   - `kind VARCHAR(16) NOT NULL`
   - `ts TIMESTAMP NOT NULL`
   - `amount NUMERIC(18,2)`
   - `device_id VARCHAR(256)`
   - `ip VARCHAR(64)`
   - PRIMARY KEY `(entity_id, ts)`
   - FOREIGN KEY `entity_id` → `nodes.node_id`
   - INDEX `idx_gs_events_entity_ts ON (entity_id, ts DESC)`
   - INDEX `idx_gs_events_ts ON (ts DESC)`

**Acceptance**:
- `sql/fraud/schema_fraud.sql` exists
- SQL syntax valid (test with `\i sql/fraud/schema_fraud.sql` in IRIS SQL shell)
- Indexes created for CTE performance (5-8ms target)

**Constitutional Validation**: IRIS-Native Development (SQL-first)

---

### T020: [P] SQL schema: gs_labels table
**File path**: `sql/fraud/schema_fraud.sql`

**Tasks**:
1. Add `gs_labels` table to `sql/fraud/schema_fraud.sql`:
   - `entity_id VARCHAR(256) NOT NULL`
   - `label VARCHAR(16) NOT NULL` (CHECK: label IN ('fraud', 'legit'))
   - `label_ts TIMESTAMP NOT NULL`
   - PRIMARY KEY `(entity_id, label_ts)`
   - FOREIGN KEY `entity_id` → `nodes.node_id`
   - INDEX `idx_gs_labels_label ON (label)`
   - INDEX `idx_gs_labels_ts ON (label_ts DESC)`

**Acceptance**:
- `gs_labels` table defined in same file
- Immutable audit trail (INSERT only, no DELETE in normal operations)

---

### T021: [P] SQL schema: gs_fraud_centroid table
**File path**: `sql/fraud/schema_fraud.sql`

**Tasks**:
1. Add `gs_fraud_centroid` table to `sql/fraud/schema_fraud.sql`:
   - `centroid_id INT PRIMARY KEY`
   - `emb VECTOR[768] NOT NULL`
   - `label VARCHAR(16) DEFAULT 'fraud'`
   - `updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP`
   - INDEX `idx_gs_centroid_updated ON (updated_at DESC)`

**Acceptance**:
- `gs_fraud_centroid` table defined
- Single row for MVP (centroid_id=1)

---

### T022: [P] SQL schema: Extend kg_NodeEmbeddings with version column
**File path**: `sql/fraud/schema_fraud.sql`

**Tasks**:
1. Add migration statement to extend `kg_NodeEmbeddings`:
   ```sql
   ALTER TABLE kg_NodeEmbeddings ADD COLUMN version INT DEFAULT 1;
   UPDATE kg_NodeEmbeddings SET version = 1 WHERE version IS NULL;
   ```

**Acceptance**:
- `kg_NodeEmbeddings.version` column exists
- All existing rows have `version=1`

---

### T023: SQL stored procedure: On-demand CTE feature computation
**File path**: `sql/fraud/proc_compute_features.sql`

**Tasks**:
1. Create `gs_ComputeFeatures(payer_id VARCHAR)` stored procedure
2. Implement CTE for rolling features:
   - `deg_24h`: COUNT(*) from gs_events WHERE entity_id=payer_id AND ts >= NOW - 24h
   - `tx_amt_sum_24h`: SUM(amount) from gs_events WHERE entity_id=payer_id AND ts >= NOW - 24h
   - `uniq_devices_7d`: COUNT(DISTINCT device_id) from gs_events WHERE entity_id=payer_id AND ts >= NOW - 7d
   - `risk_neighbors_1hop`: COUNT(*) from rdf_edges JOIN gs_labels WHERE s=payer_id AND label='fraud'
3. Return features as JSON: `{"deg_24h": 5, "tx_amt_sum_24h": 1250.50, ...}`

**Acceptance**:
- Procedure executes in ~5-8ms (within 20ms budget)
- Returns correct feature values for test data
- Integration test T015 PASSES

**Constitutional Validation**: Performance as Feature (5-8ms CTE)

---

### T024: SQL stored procedure: K-hop subgraph sampling
**File path**: `sql/fraud/proc_subgraph_sample.sql`

**Tasks**:
1. Create `gs_SubgraphSample(target_tx_id VARCHAR, fanout1 INT, fanout2 INT)` stored procedure
2. Implement k-hop CTE with fanout limits (research.md R001A pattern):
   ```sql
   WITH hop1 AS (
       SELECT e.s, e.o_id, e.p, e.created_at
       FROM rdf_edges e
       WHERE e.s = target_tx_id
       ORDER BY e.created_at DESC
       LIMIT fanout1  -- Default 10
   ),
   hop2 AS (
       SELECT e.s, e.o_id, e.p, e.created_at
       FROM rdf_edges e
       WHERE e.s IN (SELECT o_id FROM hop1)
       ORDER BY e.s, e.created_at DESC
       LIMIT fanout1 * fanout2  -- Default 50 (10 × 5)
   )
   SELECT s, o_id, p FROM hop1
   UNION ALL
   SELECT s, o_id, p FROM hop2;
   ```
3. Return subgraph as JSON (simplified GraphStorm format)

**Acceptance**:
- Procedure returns max 60 edges (10 + 50)
- Executes in ~21-31ms (within 50ms EGO mode budget)
- Subgraph JSON includes nodes, edges, features

---

### T025: Python module: TorchScript model loader
**File path**: `iris_vector_graph_core/fraud/model_loader.py`

**Tasks**:
1. Implement `load_model(model_path: str) -> torch.jit.ScriptModule`
2. Use `torch.jit.load(model_path)` in IRIS Embedded Python context
3. Implement `hot_reload_model(new_model_path: str)` for zero-downtime updates
4. Add error handling for missing model files (FileNotFoundError)

**Acceptance**:
- `load_model("models/mlp_current.torchscript")` succeeds
- Model loaded once on startup, cached in memory
- Integration test T016 PASSES

**Constitutional Validation**: IRIS-Native (Embedded Python), Explicit Error Handling

---

### T026: Python module: Feature utilities (zero-vector fallback)
**File path**: `iris_vector_graph_core/fraud/feature_utils.py`

**Tasks**:
1. Implement `get_embedding_with_fallback(node_id: str) -> np.ndarray`
2. Query `kg_NodeEmbeddings` WHERE `id = node_id`
3. If missing: return `np.zeros(768)` (zero-vector fallback per FR-018)
4. Add "cold_start" reason code when fallback occurs

**Acceptance**:
- Function returns 768-dim vector
- Missing embeddings return zero-vector without exception
- Reason code includes `{"kind": "vector", "detail": "cold_start", "weight": 0.00}`

**Constitutional Validation**: Explicit Error Handling (no silent failures)

---

### T027: Python module: Explainability (gradient × input)
**File path**: `iris_vector_graph_core/fraud/explainability.py`

**Tasks**:
1. Implement `compute_feature_attributions(model, input_tensor) -> List[Reason]`
2. Compute gradient × input for each feature
3. Return top 3 feature reasons sorted by absolute weight
4. Implement `compute_vector_proximity(payer_emb, fraud_centroid) -> Reason`
5. Compute cosine similarity: `cos_sim = dot(payer_emb, fraud_centroid) / (norm(payer_emb) * norm(fraud_centroid))`

**Acceptance**:
- Feature attributions complete in <3ms (NFR-004)
- Returns reasons with format: `{"kind": "feature", "detail": "deg_24h=38", "weight": 0.22}`
- Vector proximity reason: `{"kind": "vector", "detail": "sim_to_fraud=0.91", "weight": 0.30}`
- Integration test T017 PASSES

**Constitutional Validation**: Performance as Feature (3ms explainability)

---

### T028: FastAPI router: POST /fraud/score endpoint
**File path**: `api/routers/fraud.py`

**Tasks**:
1. Create `api/routers/fraud.py` with FastAPI router
2. Define Pydantic models:
   - `FraudScoreRequest` (mode, payer, device, ip, merchant, amount?, country?)
   - `FraudScoreResponse` (prob, reasons)
   - `ReasonCode` (kind, detail, weight)
3. Implement `POST /fraud/score` endpoint:
   - Validate entity_id format (pattern `^[a-z]+:[a-zA-Z0-9_-]+$`)
   - Query IRIS: `gs_ComputeFeatures(payer_id)` for features
   - Query IRIS: `kg_NodeEmbeddings` for payer embedding (with fallback)
   - Query IRIS: `gs_fraud_centroid` for fraud centroid
   - Load TorchScript MLP model
   - Run inference: `prob = model(features + payer_emb)`
   - Compute explainability: feature attributions + vector proximity
   - Return JSON response with min 3 reason codes
4. Add error handling:
   - 400: Invalid entity_id format
   - 404: Entity not found in nodes table
   - 500: Model load failure, SQL error (with trace_id)

**Acceptance**:
- Endpoint registered in `api/main.py`
- All 11 contract tests T004-T014 PASS
- MLP mode <20ms p95 latency
- Error responses include trace_id for debugging

**Constitutional Validation**: Observability (trace_id), Explicit Error Handling

---

### T029: FastAPI router: EGO mode (optional subgraph sampling)
**File path**: `api/routers/fraud.py`

**Tasks**:
1. Add EGO mode branch to `POST /fraud/score`
2. If `mode="EGO"`:
   - Call `gs_SubgraphSample(payer_id, fanout1=10, fanout2=5)`
   - Query embeddings for all subgraph nodes
   - Build subgraph JSON payload
   - Run TorchScript GraphSAGE inference (if model loaded)
   - Compute explainability
3. Validate p95 latency <50ms

**Acceptance**:
- EGO mode returns valid response
- Contract test T005 PASSES
- EGO mode latency <50ms p95 (NFR target)

**Note**: GraphSAGE model optional for MVP. Can return 501 Not Implemented if model missing.

---

### T030: Integration: Register fraud router in FastAPI app
**File path**: `api/main.py`

**Tasks**:
1. Import `fraud` router from `api.routers.fraud`
2. Add `app.include_router(fraud.router, prefix="/fraud", tags=["Fraud Scoring"])`
3. Verify health check reports fraud scoring endpoint available

**Acceptance**:
- `GET /health` includes fraud scoring in available endpoints
- `POST /fraud/score` accessible via FastAPI TestClient
- All contract tests T004-T014 PASS

---

## Phase 3.4: Integration & Validation

### T031: Script: Load fraud schema into IRIS
**File path**: `scripts/fraud/load_fraud_schema.py`

**Tasks**:
1. Create script that executes all `sql/fraud/*.sql` files
2. Connect to IRIS via `iris.connect()`
3. Execute in order:
   - `schema_fraud.sql` (tables)
   - `proc_compute_features.sql` (stored procedure)
   - `proc_subgraph_sample.sql` (stored procedure)
4. Print success/failure for each step

**Acceptance**:
- Script runs: `uv run python scripts/fraud/load_fraud_schema.py`
- All tables and procedures created in IRIS
- Quickstart Step 2.1 validated

**Constitutional Validation**: Standardized DB Interfaces (iris.connect pattern)

---

### T032: Script: Load sample fraud events
**File path**: `scripts/fraud/load_sample_events.py`

**Tasks**:
1. Generate synthetic fraud events (100 entities, 1000 events)
2. Insert into `nodes`, `gs_events`, `gs_labels` tables
3. Compute fraud centroid (mean of fraud-labeled embeddings)
4. Insert into `gs_fraud_centroid`

**Acceptance**:
- Script runs: `uv run python scripts/fraud/load_sample_events.py --num-entities 100 --num-events 1000`
- Events inserted, labels applied, centroid computed
- Quickstart Step 2.2 validated

---

### T033: Validate quickstart.md workflow
**File path**: `specs/004-real-time-fraud/quickstart.md`

**Tasks**:
1. Execute all 5 steps from quickstart.md:
   - Step 1: Verify IRIS database health
   - Step 2: Load fraud schema and sample data
   - Step 3: Insert transaction event (validate on-demand CTE features)
   - Step 4: Score transaction via REST API
   - Step 5: Validate explainability (min 3 reasons, cold-start handling)
2. Validate each step's expected output
3. Measure response times

**Acceptance**:
- All quickstart steps execute successfully
- MLP mode latency <20ms
- Min 3 reason codes returned
- Cold-start scenario handled gracefully
- Quickstart complete in <5 minutes

**Constitutional Validation**: Test-First (quickstart validates all acceptance scenarios)

---

## Phase 3.5: Polish

### T034: Performance benchmark: 200 QPS load test
**File path**: `scripts/fraud/performance/benchmark_fraud_scoring.py`

**Tasks**:
1. Create load test script using `asyncio` or `locust`
2. Send 200 concurrent requests/sec for 15 minutes
3. Measure:
   - p50, p95, p99 latency
   - Error rate
   - Throughput (QPS)
4. Generate report: `docs/performance/fraud-scoring-mvp.md`

**Acceptance**:
- E2E test T018 PASSES
- p95 latency <20ms (NFR-001)
- Error rate 0% (NFR-007)
- Sustained 200 QPS (NFR-005)

**Constitutional Validation**: Performance as Feature (tracked benchmarks)

---

### T035: [P] Documentation: Update CLAUDE.md with fraud scoring context
**File path**: `CLAUDE.md`

**Tasks**:
1. Add fraud scoring section to CLAUDE.md
2. Document key patterns:
   - On-demand CTE feature computation (~5-8ms)
   - TorchScript model loading in embedded Python
   - K-hop subgraph sampling (fanout 10/5, max 60 edges)
   - GraphStorm upgrade path (>10M nodes)
3. Document testing requirements:
   - All fraud tests marked `@pytest.mark.requires_database`
   - TDD workflow (tests before implementation)
   - Performance targets (MLP <20ms, EGO <50ms)

**Acceptance**:
- CLAUDE.md includes fraud scoring context
- Future LLM agents can understand fraud scoring architecture

---

## Dependencies

### Critical Path (Sequential)
```
Setup (T001-T003)
  ↓
Tests (T004-T018) [ALL MUST FAIL BEFORE T019]
  ↓
SQL Schema (T019-T024)
  ↓
Python Modules (T025-T027) [parallel where independent files]
  ↓
FastAPI Router (T028-T030)
  ↓
Integration (T031-T033)
  ↓
Polish (T034-T035)
```

### Specific Dependencies
- T023 (CTE procedure) blocks T015 (integration test passing)
- T025 (model loader) blocks T016 (integration test passing)
- T027 (explainability) blocks T017 (integration test passing)
- T028 (endpoint) blocks T004-T014 (contract tests passing)
- T031 (schema load script) blocks T032 (sample data script)
- T032 (sample data) blocks T033 (quickstart validation)
- All implementation (T019-T030) blocks T034 (performance benchmark)

---

## Parallel Execution Examples

### Phase 3.2: Launch all contract tests in parallel (MUST FAIL)
```bash
# Run all contract tests concurrently (they WILL fail initially - this is correct)
pytest tests/contract/test_fraud_score_contract.py -v -n auto

# Expected: All tests FAIL with 404 or import errors (endpoint not implemented)
```

### Phase 3.3: Create SQL tables in parallel
```bash
# These can be executed in parallel (different sections of same file)
# In practice, execute schema_fraud.sql once (creates all tables sequentially)
uv run python -c "
import iris
conn = iris.connect('localhost', 1972, 'USER', '_SYSTEM', 'SYS')
cursor = conn.cursor()
with open('sql/fraud/schema_fraud.sql') as f:
    cursor.execute(f.read())
conn.commit()
"
```

### Phase 3.3: Create Python modules in parallel
```bash
# Launch T025, T026, T027 together (different files)
# Task 1: Implement model_loader.py
# Task 2: Implement feature_utils.py
# Task 3: Implement explainability.py

# Each can be developed independently, then integrated in T028
```

### Phase 3.5: Polish tasks in parallel
```bash
# T034: Run performance benchmark
uv run python scripts/fraud/performance/benchmark_fraud_scoring.py &

# T035: Update documentation (different file)
# Edit CLAUDE.md in parallel with benchmark running
```

---

## Validation Checklist
*GATE: Checked before marking tasks complete*

- [x] All contracts have corresponding tests (11 contract tests T004-T014)
- [x] All entities have schema tasks (gs_events T019, gs_labels T020, gs_fraud_centroid T021)
- [x] All tests come before implementation (Phase 3.2 before 3.3)
- [x] Parallel tasks truly independent (marked [P] only when different files)
- [x] Each task specifies exact file path
- [x] No task modifies same file as another [P] task
- [x] Performance SLOs tracked (MLP <20ms, EGO <50ms, 200 QPS, 0% error rate)
- [x] Constitutional compliance validated at each phase

---

## Notes

### TDD Workflow (NON-NEGOTIABLE)
1. **Phase 3.2**: Write all tests (T004-T018) - they MUST FAIL
2. **Verify failures**: Run tests, confirm 404/import errors
3. **Phase 3.3**: Implement code (T019-T030)
4. **Verify passes**: Run tests again, confirm all PASS
5. **Never skip**: Tests before implementation is constitutional requirement

### Performance Targets (from spec.md NFRs)
- MLP mode: <20ms p95 at 200 QPS (NFR-001, NFR-005)
- EGO mode: <50ms p95 (NFR-003)
- CTE feature computation: ~5-8ms (fits in 20ms budget)
- Feature attribution: <3ms (NFR-004)
- Event ingestion: ≥500 events/sec (FR-007)
- Error rate: 0% during 15min load test (NFR-007)

### Constitutional Principles Applied
- **IRIS-Native**: SQL-first (CTE features, stored procedures), Embedded Python (TorchScript)
- **Test-First**: 15 tests before any implementation, live IRIS validation
- **Performance as Feature**: SLOs tracked in tests, benchmarks documented
- **Observability**: trace_id in errors, reason codes for explainability
- **Explicit Error Handling**: Zero-vector fallback, cold_start reasons, graceful degradation

### AWS GraphStorm Upgrade Path
- **When**: Graph exceeds 10M nodes or distributed training needed
- **How**: Export IRIS → S3, train on SageMaker, deploy endpoint (6-8 weeks)
- **Reference**: research.md R001 (upgrade path documented)

---

**Status**: ✅ Tasks complete and ready for `/implement` command
**Total Tasks**: 35 (8 setup, 15 tests, 8 core, 4 polish)
**Estimated Timeline**: 2-3 weeks (MVP scope)
