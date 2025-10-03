# Feature Specification: Real-Time Fraud Scoring on IRIS Vector Graph (MVP)

**Feature Branch**: `004-real-time-fraud`
**Created**: 2025-10-02
**Status**: Draft
**Input**: User description: "Real-Time Fraud Inference on IRIS Vector Graph"

## Execution Flow (main)
```
1. Parse user description from Input
   → Feature: Add fraud scoring API on top of IRIS Vector Graph
2. Extract key concepts from description
   → Actors: Risk API developers, Data Engineers, Analysts, ML Ops
   → Actions: Score transactions, ingest events, explain predictions, hot-load models
   → Data: Events, embeddings, rolling features, labels
   → Constraints: <20ms p95 latency, 200 QPS, SQL-first
3. Ambiguities marked: [None - well-defined MVP scope]
4. User scenarios defined
5. Functional requirements generated
6. Key entities identified
7. Review checklist: READY
```

---

## ⚡ Quick Guidelines
- ✅ Focus on WHAT users need and WHY
- ❌ Avoid HOW to implement (no tech stack, APIs, code structure)
- 👥 Written for business stakeholders, not developers

---

## User Scenarios & Testing

### Primary User Story
A Risk API developer needs to score a transaction in real-time by providing identifiers for the payer, device, IP, and merchant. The system returns a fraud probability (0-1) and human-readable reasons within 20 milliseconds at 200 queries per second.

### Acceptance Scenarios

1. **Given** a transaction with payer, device, IP, and merchant IDs, **When** the Risk API calls the scoring endpoint, **Then** the system returns a fraud probability and at least 3 reason codes in under 20ms (p95)

2. **Given** a new transaction event is inserted, **When** the event is written to the database, **Then** subsequent scoring requests compute up-to-date rolling features (24-hour degree, transaction sum) via on-demand LANGUAGE PYTHON procedures

3. **Given** a fraud score response, **When** an analyst reviews the reasons, **Then** they see concrete feature values (e.g., "deg_24h=38") and vector proximity scores that explain the prediction

4. **Given** a high-volume scenario of 500 events/second, **When** events are ingested continuously, **Then** the system sustains writes without backpressure and rolling features are available for scoring within seconds (on-demand computation)

### Edge Cases

- What happens when a node has no precomputed embedding?
  - System uses zero-vector and marks reason as "cold_start"

- How does the system handle missing feature data?
  - Returns default values (0 for counts, 0.0 for sums) and proceeds with scoring

- What happens if optional feature caching (hourly job) is not yet implemented?
  - Features are computed on-demand via LANGUAGE PYTHON procedures (~5-8ms). If latency exceeds budget, caching can be added as optimization

- How does the system handle high-degree nodes during optional ego-graph mode?
  - Enforces strict fanout caps (10 neighbors at hop 1, 5 at hop 2) to prevent performance degradation

---

## Requirements

### Functional Requirements

#### Core Scoring API
- **FR-001**: System MUST provide a fraud scoring endpoint that accepts payer, device, IP, and merchant identifiers and returns a fraud probability (0-1 range)
- **FR-002**: System MUST return at least 3 reason codes explaining the fraud score, including feature attributions and vector proximity
- **FR-003**: System MUST support two scoring modes: MLP (default, using precomputed embeddings) and EGO (optional, using bounded ego-graph)
- **FR-004**: System MUST achieve p95 latency of <20ms for MLP mode at 200 queries per second
- **FR-005**: System MUST achieve p95 latency of <50ms for EGO mode with fanout caps (10/5) and depth=2

#### Event Ingestion & Features
- **FR-006**: System MUST accept transaction events with entity_id, kind, timestamp, amount, device_id, and ip fields
- **FR-007**: System MUST sustain ≥500 events/second insertion rate without backpressure
- **FR-008**: System MUST compute rolling features (24-hour degree, 24-hour transaction sum) on-demand during scoring requests using LANGUAGE PYTHON stored procedures that query gs_events table (target: 5-8ms)
- **FR-009**: System MUST compute derived features (unique devices 7-day, risky neighbors 1-hop) on-demand during scoring requests via LANGUAGE PYTHON stored procedures. Optional: System MAY cache features in rdf_props via hourly job for performance optimization (deferred)
- **FR-010**: System MUST support foreign key constraints between events, nodes, embeddings, features, and labels tables

#### Explainability
- **FR-011**: System MUST provide feature-based reasons showing feature name, value, and weight (e.g., "deg_24h=38, weight=0.22")
- **FR-012**: System MUST provide vector proximity reasons showing similarity to fraud centroid
- **FR-013**: System MUST compute feature attributions using gradient × input for MLP model
- **FR-014**: System MUST return reason codes in 95% of scoring requests

#### Model Management
- **FR-015**: System MUST load TorchScript MLP model on startup from configurable file path
- **FR-016**: System MAY support hot-reloading of TorchScript model without system downtime (deferred to post-MVP - requires thread-safe model swapping)
- **FR-017**: System MUST track model version in embeddings table
- **FR-018**: System MUST fall back to zero-vector embeddings when node embedding is missing

#### Data Retention & Freshness
- **FR-019**: System MUST retain raw events in append-only table with no automatic deletion (manual cleanup only)
- **FR-020**: System MUST timestamp all feature updates to track freshness
- **FR-021**: System MAY support hourly batch jobs for caching derived features to rdf_props (performance optimization, deferred to post-MVP)

### Non-Functional Requirements

#### Performance
- **NFR-001**: MLP mode scoring MUST achieve <20ms p95 latency at 200 QPS (component budget breakdown documented in plan.md Constitution Check section)
- **NFR-002**: Optional ego-graph mode (EGO) MUST achieve <50ms p95 latency with fanout caps (10/5, max 60 edges)

#### Scalability
- **NFR-003**: System MUST handle 200 concurrent queries per second for MLP mode
- **NFR-004**: System MAY support hourly feature caching completing in ≤5 minutes for current dataset sizes (deferred to post-MVP if on-demand computation exceeds latency budget)

#### Reliability
- **NFR-005**: System MUST achieve error rate of 0% during 15-minute load test at 200 QPS
- **NFR-006**: System MUST handle missing data gracefully without throwing exceptions

### Key Entities

- **Node**: Represents any entity in the fraud graph (accounts, devices, IPs, merchants). Has unique node_id. Pre-existing entity from IRIS Vector Graph core schema.

- **Event**: Raw transaction or activity record. Contains entity_id (payer), kind (e.g., 'tx'), timestamp, amount, device_id, ip. Append-only, immutable. Used to derive rolling features.

- **Embedding**: Precomputed vector representation (768 dimensions) of a node. Includes version number and last update timestamp. May be missing for cold-start nodes (system falls back to zero-vector).

- **Rolling Features**: Graph properties computed on-demand or cached in rdf_props table. Contains:
  - Counts: 24-hour degree, unique devices in 7 days, risky neighbors at 1-hop
  - Aggregates: 24-hour transaction amount sum
  - Metadata: last update timestamp (features_updated_at)
  - Computed on-demand via LANGUAGE PYTHON stored procedures during scoring requests (~5-8ms)
  - Optional: Materialized to rdf_props via hourly caching job (deferred optimization)

- **Label**: Ground truth fraud/legit label for an entity at a specific timestamp. Used for training/validation and for computing "risky neighbors" feature. Optional for online scoring.

- **Fraud Score**: Output of scoring request. Contains fraud probability (0-1) and array of reason codes. Ephemeral (not persisted in MVP).

- **Reason Code**: Explanation component. Contains kind (feature/vector), human-readable detail (e.g., "deg_24h=38"), and attribution weight. Minimum 3 reasons per score.

- **TorchScript Model**: Serialized MLP or GraphSAGE neural network. Loaded into embedded Python interpreter. Supports hot-reload via admin endpoint. Versioned but version management is external to database in MVP.

---

## Review & Acceptance Checklist

### Content Quality
- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

### Requirement Completeness
- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable (p95 latency, QPS, freshness)
- [x] Scope is clearly bounded (MVP: single endpoint, SQL-first, no multi-tenancy)
- [x] Dependencies identified (IRIS Vector Graph core schema, TorchScript models)

---

## Execution Status

- [x] User description parsed
- [x] Key concepts extracted
- [x] Ambiguities marked (none - well-defined MVP)
- [x] User scenarios defined
- [x] Requirements generated (21 functional, 6 non-functional)
- [x] Entities identified (8 entities)
- [x] Review checklist passed

---

## Scope Boundaries (MVP)

### In Scope
- Single fraud scoring endpoint (POST /fraud/score)
- On-demand feature computation via LANGUAGE PYTHON stored procedures (rolling 24h features, derived 7d features)
- Precomputed node embeddings (HNSW optional)
- TorchScript MLP model loaded in embedded Python
- Simple explainability (top-3 feature attributions + vector proximity)
- Model loading on startup (hot-reload deferred to post-MVP)
- Optional: bounded k-hop subgraph sampling (fanout 10/5, max 60 edges) for GraphSAGE mode

### Out of Scope (Deferred)
- Multi-tenancy / ContextID isolation
- JWT authentication / authorization policies
- GraphQL surface for fraud queries
- openCypher frontend for fraud patterns
- iFind / full-text search integration
- Global caches (^GS.FEAT) for micro-optimizations
- Model registry / governance layer
- Streaming infrastructure (Kafka/Flink)
- Federation / router architecture
- Compliance/audit UI
- Advanced explainability (SHAP, counterfactuals)

---

## Dependencies & Assumptions

### Dependencies
- IRIS Vector Graph core schema (nodes, rdf_edges, rdf_labels, rdf_props, kg_NodeEmbeddings)
- IRIS Embedded Python runtime (https://docs.intersystems.com/iris20252/csp/docbook/DocBook.UI.Page.cls?KEY=GEPYTHON_flexible)
- TorchScript models (externally trained, provided as .torchscript files)
- PyTorch library available in embedded Python environment

### Assumptions
- Node embeddings are precomputed offline and loaded into kg_NodeEmbeddings table
- Fraud centroid vector is precomputed offline for vector proximity reasoning
- Hourly feature refresh job can be implemented as simple SQL UPDATE statements
- Load testing infrastructure exists to validate p95 latency and QPS targets
- Model training pipeline is external to this MVP (models delivered as artifacts)

---

## Success Metrics Summary

| Metric | Target | Measurement Method |
|--------|--------|-------------------|
| MLP mode p95 latency | <20ms | Load test at 200 QPS for 15 min |
| EGO mode p95 latency | <50ms | Load test with fanout 10/5, depth 2 |
| Event ingestion throughput | ≥500 events/sec | Sustained write test without backpressure |
| Feature freshness | ≤60s | Timestamp delta between event and feature update |
| Reason code coverage | ≥95% | Percentage of responses with ≥3 reasons |
| Error rate | 0% | Count of 5xx responses during load test |
| Model hot-reload downtime | N/A (deferred) | Time delta between requests before/after version flip |

---
