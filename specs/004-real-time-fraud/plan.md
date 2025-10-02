# Implementation Plan: Real-Time Fraud Scoring on IRIS Vector Graph (MVP)

**Branch**: `004-real-time-fraud` | **Date**: 2025-10-02 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/004-real-time-fraud/spec.md`

## Execution Flow (/plan command scope)
```
1. Load feature spec from Input path
   → ✅ Loaded spec.md with 21 functional requirements, 8 non-functional requirements
2. Fill Technical Context (scan for NEEDS CLARIFICATION)
   → ✅ No NEEDS CLARIFICATION - well-defined MVP scope
   → Detected Project Type: single (IRIS-native fraud scoring extension)
   → Set Structure Decision: Extend existing IRIS Vector Graph codebase
3. Fill the Constitution Check section
   → ✅ Aligned with IRIS-Native Development, Test-First, Performance as Feature
4. Evaluate Constitution Check section
   → ✅ No violations, all requirements align with constitutional principles
   → Update Progress Tracking: Initial Constitution Check PASS
5. Execute Phase 0 → research.md
   → TorchScript model loading patterns in IRIS Embedded Python
   → SQL trigger design for real-time feature updates
   → Bounded CTE patterns for ego-graph extraction
6. Execute Phase 1 → contracts, data-model.md, quickstart.md
   → REST API contract for POST /fraud/score
   → SQL schema for gs_events, gs_features, gs_labels
   → Test scenarios for scoring, ingestion, explainability
7. Re-evaluate Constitution Check section
   → ✅ Design maintains constitutional compliance
   → Update Progress Tracking: Post-Design Constitution Check PASS
8. Plan Phase 2 → Task generation approach described below
9. ✅ STOP - Ready for /tasks command
```

## Summary

Add real-time fraud scoring capability to IRIS Vector Graph using precomputed node embeddings and a TorchScript MLP model loaded in IRIS Embedded Python. System accepts transaction identifiers (payer, device, IP, merchant) and returns fraud probability with explainable reason codes in <20ms p95 at 200 QPS.

**Technical Approach** (from research):
- **SQL-first architecture**: Extend existing `nodes`, `rdf_edges`, `kg_NodeEmbeddings` schema with fraud-specific tables (`gs_events`, `gs_features`, `gs_labels`)
- **IRIS Embedded Python**: Load TorchScript MLP model using `iris.cls` Python integration
- **Trigger-based features**: SQL AFTER INSERT trigger updates rolling 24h degree and transaction sums in same transaction
- **Hourly batch job**: SQL UPDATE statements for 7-day unique devices and risky neighbor counts
- **REST endpoint**: FastAPI POST /fraud/score leveraging existing `api/main.py` structure
- **Explainability**: Gradient × input for feature attributions, cosine similarity for vector proximity
- **Optional ego-graph**: Bounded 2-hop CTE with strict fanout caps (10/5) for GraphSAGE mode

## Technical Context

**Language/Version**: Python 3.11 (matches existing IRIS Vector Graph codebase)
**Primary Dependencies**:
- intersystems-irispython>=3.2.0 (IRIS database driver)
- FastAPI>=0.118.0 (existing REST API framework)
- PyTorch>=2.0.0 (TorchScript model loading in embedded Python)
- pydantic>=2.11.9 (request/response validation)

**Storage**: InterSystems IRIS 2025.1+ with Vector Search (HNSW), Embedded Python runtime
**Testing**: pytest with `@pytest.mark.requires_database` for integration tests against live IRIS
**Target Platform**: Linux server (IRIS Docker containers on ports 1972/52773 or 21972/252773 for ACORN-1)
**Project Type**: single (extends existing IRIS Vector Graph repository)
**Performance Goals**:
- MLP mode: <20ms p95 at 200 QPS
- EGO mode: <50ms p95 with fanout caps (10/5), depth=2
- Event ingestion: ≥500 events/sec sustained
- Feature freshness: ≤60s lag from event to feature update

**Constraints**:
- SQL-first (no external feature stores or caching layers)
- IRIS Embedded Python only (no separate Python service)
- Single endpoint MVP (no GraphQL/Cypher frontends)
- No multi-tenancy (deferred)
- TorchScript models externally trained (offline pipeline)

**Scale/Scope**:
- 4 new SQL tables (gs_events, gs_features, gs_labels, gs_fraud_centroid)
- 1 REST endpoint POST /fraud/score with 2 modes (MLP, EGO)
- 3 SQL stored procedures (gs_FetchServingRow, gs_ScoreMLP, gs_EgoGraph)
- 1 SQL trigger (gs_UpdateRollingFeatures)
- 1 hourly batch job (gs_RefreshDerivedFeatures)
- ~15 contract tests, ~10 integration tests, ~5 performance benchmarks

## Constitution Check
*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### I. IRIS-Native Development
- ✅ **PASS**: Leverages IRIS Embedded Python for TorchScript model inference
- ✅ **PASS**: Uses SQL procedures for feature computation (triggers + hourly jobs)
- ✅ **PASS**: Extends existing `kg_NodeEmbeddings` table with version tracking
- ✅ **PASS**: Bounded CTE for ego-graph extraction (pure SQL, no external dependencies)
- ✅ **PASS**: Foreign key constraints enforce referential integrity across fraud schema

### II. Test-First Development with Live Database Validation (NON-NEGOTIABLE)
- ✅ **PASS**: Contract tests MUST be written before implementing POST /fraud/score endpoint
- ✅ **PASS**: Integration tests MUST validate trigger behavior against live IRIS
- ✅ **PASS**: Performance tests MUST measure p95 latency with actual TorchScript model loaded
- ✅ **PASS**: All tests marked `@pytest.mark.requires_database` for live IRIS validation
- ✅ **PASS**: Quickstart includes IRIS health check before running fraud scoring example
- ✅ **PASS**: Red-Green-Refactor: Contract tests fail initially, pass after SQL + Python implementation

### III. Performance as a Feature
- ✅ **PASS**: NFR-001: PK lookups for embeddings/features ≤4ms p95 (indexed node_id, entity_id)
- ✅ **PASS**: NFR-002: MLP inference ≤8ms p95 (TorchScript CPU inference)
- ✅ **PASS**: NFR-003: Ego-graph CTE ≤25ms p95 (strict fanout caps 10/5, depth=2, indexed s,p,created_at)
- ✅ **PASS**: Performance benchmarks tracked in `docs/performance/fraud-scoring-mvp.md`
- ✅ **PASS**: FR-004/FR-005: SLOs defined for both MLP (<20ms) and EGO (<50ms) modes
- ✅ **PASS**: Load test validation at 200 QPS for 15 minutes required

### IV. Hybrid Search by Default
- ⚠️ **EXCEPTION**: Fraud scoring uses vector proximity + tabular features, NOT hybrid search (vector+text+graph RRF)
- **Justification**: Fraud detection requirements are domain-specific (transaction features + embeddings), not general search
- **Documented in Complexity Tracking**: This is a specialized inference use case, not a search feature

### V. Observability & Debuggability
- ✅ **PASS**: SQL procedures MUST log execution time for gs_FetchServingRow, gs_ScoreMLP, gs_EgoGraph
- ✅ **PASS**: Python functions MUST log model load events, inference latency, zero-vector fallbacks
- ✅ **PASS**: REST endpoint returns structured errors with fraud-specific trace info (entity IDs, feature values)
- ✅ **PASS**: Performance scripts output results to `docs/performance/` with timestamps
- ✅ **PASS**: FR-011/FR-012: Reason codes provide concrete feature values for debugging predictions

### VI. Modular Core Library
- ✅ **PASS**: Fraud scoring Python code isolated in `iris_vector_graph_core/fraud/` module
- ✅ **PASS**: TorchScript model loader abstracted (can work with any .torchscript file)
- ✅ **PASS**: Feature computation logic separable from IRIS-specific SQL syntax
- ✅ **PASS**: Explainability functions (gradient × input) reusable for other ML models

### VII. Explicit Error Handling (NON-NEGOTIABLE)
- ✅ **PASS**: FR-018: Missing embeddings → explicit zero-vector fallback with "cold_start" reason code
- ✅ **PASS**: NFR-008: Missing feature data → explicit default values (0 for counts, 0.0 for sums)
- ✅ **PASS**: Model load failures MUST raise clear exceptions with file path and error context
- ✅ **PASS**: SQL constraint violations MUST surface as actionable messages (e.g., invalid entity_id foreign key)
- ✅ **PASS**: No silent failures in scoring pipeline (every step logs success/failure)

### VIII. Standardized Database Interfaces
- ✅ **PASS**: Reuses existing `iris.connect()` pattern from IRIS Vector Graph codebase
- ✅ **PASS**: Follows established SQL procedure naming convention (`gs_*` prefix for fraud graph schemas)
- ✅ **PASS**: Uses parameterized queries to prevent SQL injection
- ✅ **PASS**: Contributes trigger pattern and CTE fanout cap pattern back to shared utilities

### Additional Constraints
- ✅ **Versioning**: MINOR version bump (new fraud scoring endpoint + schema tables)
- ✅ **Security**: Database credentials via `.env`, input validation for entity IDs, no arbitrary code execution
- ✅ **Documentation**: SQL procedures document parameters, return types, performance characteristics

**Overall Constitution Check**: ✅ **PASS** (1 documented exception for Hybrid Search requirement)

## Project Structure

### Documentation (this feature)
```
specs/004-real-time-fraud/
├── plan.md              # This file (/plan command output)
├── research.md          # Phase 0 output (/plan command)
├── data-model.md        # Phase 1 output (/plan command)
├── quickstart.md        # Phase 1 output (/plan command)
├── contracts/           # Phase 1 output (/plan command)
│   ├── fraud-score-api.openapi.yaml
│   └── test_fraud_score_contract.py
└── tasks.md             # Phase 2 output (/tasks command - NOT created by /plan)
```

### Source Code (repository root)
```
# Extend existing IRIS Vector Graph single-project structure

sql/
├── schema.sql                     # Existing core schema (nodes, rdf_edges, kg_NodeEmbeddings)
├── operators.sql                  # Existing operators (kg_KNN_VEC, kg_RRF_FUSE, etc.)
└── fraud/                         # NEW: Fraud-specific SQL
    ├── schema_fraud.sql           # gs_events, gs_features, gs_labels, gs_fraud_centroid
    ├── trigger_update_features.sql # AFTER INSERT trigger on gs_events
    ├── proc_fetch_serving_row.sql  # gs_FetchServingRow(payer, device, ip, merchant)
    ├── proc_score_mlp.sql          # gs_ScoreMLP(tx_json) → {prob, reasons[]}
    ├── proc_ego_graph.sql          # gs_EgoGraph(seed, fanout1, fanout2, depth)
    └── job_refresh_derived_features.sql # Hourly UPDATE for 7d devices, risky neighbors

iris_vector_graph_core/
├── __init__.py                    # Existing core module
├── graph.py                       # Existing IRISGraphEngine
└── fraud/                         # NEW: Fraud scoring module
    ├── __init__.py
    ├── model_loader.py            # TorchScript model loading in embedded Python
    ├── explainability.py          # Gradient × input, vector proximity
    └── feature_utils.py           # Feature default handling, zero-vector fallback

api/
├── main.py                        # Existing FastAPI app (GraphQL + Cypher)
└── routers/
    ├── cypher.py                  # Existing Cypher router
    └── fraud.py                   # NEW: POST /fraud/score endpoint

tests/
├── contract/                      # NEW: Fraud API contract tests
│   └── test_fraud_score_contract.py
├── integration/                   # NEW: Fraud integration tests
│   ├── test_fraud_trigger.py      # Trigger updates features
│   ├── test_fraud_scoring.py      # End-to-end scoring workflow
│   └── test_fraud_explainability.py # Reason codes generation
└── e2e/                           # Existing e2e tests
    └── test_fraud_scoring_e2e.py  # NEW: Load test at 200 QPS

scripts/
├── setup_schema.py                # Existing schema setup
└── fraud/                         # NEW: Fraud-specific scripts
    ├── load_fraud_schema.py       # Load sql/fraud/*.sql
    ├── load_sample_events.py      # Generate synthetic fraud events
    └── performance/
        └── benchmark_fraud_scoring.py # 200 QPS load test

docs/performance/
└── fraud-scoring-mvp.md           # NEW: Performance benchmarks (p95 latency, QPS)

models/                            # NEW: TorchScript model artifacts (not in git)
├── mlp_current.torchscript        # Current MLP model (loaded on startup)
└── graphsage_current.torchscript  # Optional GraphSAGE model (EGO mode)
```

**Structure Decision**: Extend existing single-project IRIS Vector Graph structure with new `sql/fraud/`, `iris_vector_graph_core/fraud/`, `api/routers/fraud.py`, and fraud-specific tests. Maintains architectural consistency with existing GraphQL/Cypher implementation while isolating fraud-specific components.

## Phase 0: Outline & Research

**Status**: ✅ COMPLETE (proceeding to generate research.md)

**Research Tasks** (all resolved - no NEEDS CLARIFICATION in Technical Context):

1. **TorchScript Model Loading in IRIS Embedded Python**
   - Decision: Use `torch.jit.load()` in embedded Python via `iris.cls` Python gateway
   - Rationale: IRIS Embedded Python supports PyTorch, TorchScript provides fast CPU inference
   - Alternatives considered: ONNX Runtime (rejected - adds dependency), Pure Python model (rejected - too slow)

2. **SQL Trigger Design for Real-Time Rolling Features**
   - Decision: AFTER INSERT trigger on gs_events updates gs_features (deg_24h, tx_amt_sum_24h) in same transaction
   - Rationale: Ensures feature freshness without separate consumer process, meets FR-008 same-transaction requirement
   - Alternatives considered: Async message queue (rejected - adds complexity), Materialized view (rejected - IRIS doesn't support)

3. **Bounded CTE Patterns for Ego-Graph Extraction**
   - Decision: Recursive CTE with LIMIT at each hop level (LIMIT 10 at hop 1, LIMIT 5 at hop 2)
   - Rationale: Prevents unbounded traversal on high-degree nodes, meets NFR-003 <25ms p95 target
   - Alternatives considered: Breadth-first search in Python (rejected - slower), Neo4j Cypher (rejected - requires separate database)

4. **Feature Attribution Methods for MLP Explainability**
   - Decision: Gradient × input (simple saliency) for feature importance
   - Rationale: Fast (<3ms), interpretable, works with TorchScript autograd
   - Alternatives considered: SHAP (rejected - too slow for <20ms target), Integrated Gradients (rejected - overkill for MVP)

5. **Vector Proximity for Fraud Centroid**
   - Decision: Precompute fraud centroid offline (mean of fraud-labeled embeddings), use cosine similarity at inference
   - Rationale: O(1) lookup, no HNSW search required for single centroid
   - Alternatives considered: k-NN to fraud cluster (rejected - slower), HNSW search (rejected - unnecessary for single centroid)

6. **Hot-Reload Pattern for TorchScript Models**
   - Decision: Admin endpoint POST /admin/model/activate reloads model in-place using Python `importlib.reload()` pattern
   - Rationale: Zero downtime, meets FR-016 requirement
   - Alternatives considered: Process restart (rejected - downtime), Model versioning in DB (rejected - out of MVP scope)

**Output**: Proceeding to generate `research.md` with consolidated findings

## Phase 1: Design & Contracts
*Prerequisites: research.md complete*

**Status**: ✅ READY TO EXECUTE (proceeding to generate artifacts)

### 1. Data Model Extraction (→ `data-model.md`)

**Entities** (from spec.md Key Entities section):

- **Node** (existing): Pre-existing from IRIS Vector Graph core schema
- **kg_NodeEmbeddings** (existing, extended): Add `version` column if missing, track model version
- **gs_events** (new): entity_id, kind, ts, amount, device_id, ip
- **gs_features** (new): entity_id, ts_updated, deg_24h, tx_amt_sum_24h, uniq_devices_7d, risk_neighbors_1hop
- **gs_labels** (new): entity_id, label ('fraud'|'legit'), label_ts
- **gs_fraud_centroid** (new): centroid_id, emb VECTOR[768], label ('fraud'), updated_at

**Relationships**:
- gs_events.entity_id → nodes.node_id (FK)
- gs_features.entity_id → nodes.node_id (FK)
- gs_labels.entity_id → nodes.node_id (FK)
- gs_events.device_id, gs_events.ip → nodes.node_id (via graph edges in rdf_edges)

**State Transitions**:
- gs_features: INSERT on first event, UPDATE on subsequent events (trigger-driven)
- kg_NodeEmbeddings: version increments when model reloaded

### 2. API Contract Generation (→ `contracts/fraud-score-api.openapi.yaml`)

**Endpoint**: POST /fraud/score

**Request Schema** (from FR-001):
```yaml
POST /fraud/score
Request Body:
  mode: string (enum: ["MLP", "EGO"], default: "MLP")
  payer: string (entity_id)
  device: string (entity_id)
  ip: string (entity_id)
  merchant: string (entity_id)
  amount: number (optional)
  country: string (optional)
```

**Response Schema** (from FR-002, FR-011, FR-012):
```yaml
200 OK:
  prob: number (0.0-1.0)
  reasons: array of objects
    - kind: string ("feature" | "vector")
      detail: string (e.g., "deg_24h=38")
      weight: number (attribution score)
```

**Error Responses**:
```yaml
400 Bad Request: Invalid entity_id format
404 Not Found: Entity not found in nodes table
500 Internal Server Error: Model load failure, SQL error
```

### 3. Contract Test Generation (→ `contracts/test_fraud_score_contract.py`)

One test per requirement:
- `test_fraud_score_mlp_mode_success` (FR-001, FR-002)
- `test_fraud_score_ego_mode_success` (FR-003)
- `test_fraud_score_returns_min_3_reasons` (FR-002, FR-014)
- `test_fraud_score_invalid_entity_id_400` (input validation)
- `test_fraud_score_entity_not_found_404` (error handling)

### 4. Integration Test Scenarios (from user stories)

**Story 1** → `test_fraud_scoring_latency_200qps` (FR-004)
**Story 2** → `test_event_insert_updates_features_same_tx` (FR-008)
**Story 3** → `test_fraud_score_reason_codes_concrete` (FR-011, FR-012)
**Story 4** → `test_model_hot_reload_no_downtime` (FR-016)
**Story 5** → `test_event_ingestion_500_per_sec` (FR-007, FR-009)

### 5. Update CLAUDE.md (agent-specific guidance)

Will run `.specify/scripts/bash/update-agent-context.sh claude` to add:
- New fraud scoring technical context (TorchScript, triggers, CTE patterns)
- SQL procedure patterns for gs_* prefix
- Performance targets (20ms MLP, 50ms EGO, 500 events/sec)
- Testing requirements (live IRIS, contract tests before implementation)

**Output**: data-model.md, /contracts/fraud-score-api.openapi.yaml, /contracts/test_fraud_score_contract.py, quickstart.md, CLAUDE.md updated

## Phase 2: Task Planning Approach
*This section describes what the /tasks command will do - DO NOT execute during /plan*

**Task Generation Strategy**:

1. **Load tasks-template.md** as base structure
2. **Generate SQL schema tasks** from data-model.md:
   - Task: Write gs_events table DDL [P]
   - Task: Write gs_features table DDL [P]
   - Task: Write gs_labels table DDL [P]
   - Task: Write gs_fraud_centroid table DDL [P]
   - Task: Write trigger gs_UpdateRollingFeatures (AFTER INSERT on gs_events)
   - Task: Write job gs_RefreshDerivedFeatures (hourly UPDATE)

3. **Generate SQL procedure tasks** from contracts:
   - Task: Write gs_FetchServingRow stored procedure
   - Task: Write gs_ScoreMLP stored procedure (stub returning fixed score initially)
   - Task: Write gs_EgoGraph stored procedure (optional, bounded CTE)

4. **Generate Python module tasks** from data-model.md:
   - Task: Implement model_loader.py (TorchScript load/reload) [P]
   - Task: Implement explainability.py (gradient × input, cosine sim) [P]
   - Task: Implement feature_utils.py (defaults, zero-vector fallback) [P]

5. **Generate REST API tasks** from contracts:
   - Task: Write contract tests in test_fraud_score_contract.py (MUST FAIL initially)
   - Task: Implement POST /fraud/score endpoint in api/routers/fraud.py
   - Task: Wire fraud router to api/main.py

6. **Generate integration test tasks** from user stories:
   - Task: Write test_fraud_trigger.py (validate trigger updates features)
   - Task: Write test_fraud_scoring.py (end-to-end MLP mode)
   - Task: Write test_fraud_explainability.py (validate reason codes)
   - Task: Write test_fraud_scoring_e2e.py (200 QPS load test)

7. **Generate quickstart validation task**:
   - Task: Write quickstart.md with 5-step fraud scoring example
   - Task: Validate quickstart.md runs end-to-end on clean IRIS instance

8. **Generate performance benchmark tasks**:
   - Task: Write benchmark_fraud_scoring.py (15-min load test at 200 QPS)
   - Task: Run benchmark and document results in docs/performance/fraud-scoring-mvp.md

**Ordering Strategy** (TDD + Dependency order):

1. **Phase 1: Schema** (parallel tasks [P])
   - SQL DDL for gs_events, gs_features, gs_labels, gs_fraud_centroid
   - Trigger and hourly job SQL

2. **Phase 2: Contract Tests** (TDD - MUST FAIL)
   - Write contract tests for POST /fraud/score
   - Write integration tests for trigger, scoring, explainability

3. **Phase 3: Implementation** (make tests pass)
   - Python modules (model_loader, explainability, feature_utils)
   - SQL procedures (gs_FetchServingRow, gs_ScoreMLP, gs_EgoGraph)
   - REST endpoint (api/routers/fraud.py)

4. **Phase 4: Validation**
   - Quickstart.md execution
   - Load test at 200 QPS
   - Performance benchmark documentation

**Estimated Output**: 30-35 numbered, ordered tasks in tasks.md

**Parallel Execution Markers [P]**:
- All SQL DDL tasks (independent tables)
- All Python module tasks (independent files)

**IMPORTANT**: This phase is executed by the /tasks command, NOT by /plan

## Phase 3+: Future Implementation
*These phases are beyond the scope of the /plan command*

**Phase 3**: Task execution (/tasks command creates tasks.md)
**Phase 4**: Implementation (execute tasks.md following constitutional principles)
**Phase 5**: Validation (run tests, execute quickstart.md, performance validation)

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| **IV. Hybrid Search by Default** | Fraud scoring is inference (vector + tabular features), not search | Hybrid search (vector+text+graph RRF) not applicable to fraud detection use case. Requirement is domain-specific prediction, not information retrieval. |

**Justification**: This is a specialized ML inference feature, not a general search capability. The constitution's Hybrid Search principle applies to search/retrieval features, not to all uses of vector embeddings. Fraud scoring combines vectors with transactional features (degree counts, amounts) using a supervised MLP model, which is architecturally distinct from unsupervised RRF fusion.

## Progress Tracking
*This checklist is updated during execution flow*

**Phase Status**:
- [x] Phase 0: Research complete (/plan command)
- [x] Phase 1: Design complete (/plan command) - proceeding to generate artifacts
- [x] Phase 2: Task planning complete (/plan command - describe approach only)
- [ ] Phase 3: Tasks generated (/tasks command)
- [ ] Phase 4: Implementation complete
- [ ] Phase 5: Validation passed

**Gate Status**:
- [x] Initial Constitution Check: PASS (1 documented exception)
- [x] Post-Design Constitution Check: PASS
- [x] All NEEDS CLARIFICATION resolved (none present)
- [x] Complexity deviations documented (Hybrid Search exception justified)

---
*Based on Constitution v1.1.0 - See `.specify/memory/constitution.md`*
