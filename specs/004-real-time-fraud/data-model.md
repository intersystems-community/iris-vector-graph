# Data Model: Real-Time Fraud Scoring MVP

**Feature**: 004-real-time-fraud
**Date**: 2025-10-02

## Overview

This document defines the data model for the fraud scoring feature, extending the existing IRIS Vector Graph schema with fraud-specific tables. The model supports real-time feature computation, event tracking, ground truth labels, and explainability.

---

## Entity Relationships Diagram

```
┌──────────────────┐
│ nodes (existing) │
│ ────────────────│
│ PK: node_id      │
└────────┬─────────┘
         │
         │ FK (multiple tables reference node_id)
         │
    ┌────┴─────────┬─────────────┬──────────────┬─────────────┐
    │              │             │              │             │
┌───▼───────┐  ┌──▼──────────┐ ┌▼────────────┐ ┌▼───────────┐  ┌──────────────────────┐
│ gs_events │  │ gs_features │ │  gs_labels  │ │kg_Node     │  │ rdf_edges (existing) │
│───────────│  │─────────────│ │─────────────│ │Embeddings  │  │──────────────────────│
│PK: entity │  │PK: entity   │ │PK: entity   │ │(existing,  │  │ Edges used for       │
│    _id,ts │  │    _id      │ │    _id,     │ │ extended)  │  │ risky neighbor       │
│           │  │             │ │    label_ts │ │            │  │ graph traversal      │
│device_id ─┼──┼─────────────┼─┼─────────────┼─┼────────────┤  └──────────────────────┘
│ip ────────┼──┼─────────────┼─┼─────────────┼─┼────────────┤
└───────────┘  └─────────────┘ └─────────────┘ └────────────┘
                                                      │
                                                      │ emb used for
                                                      ▼   fraud centroid sim
                                              ┌────────────────────┐
                                              │ gs_fraud_centroid  │
                                              │────────────────────│
                                              │ PK: centroid_id    │
                                              │ emb: VECTOR[768]   │
                                              └────────────────────┘
```

---

## Table Definitions

### gs_events (NEW)

**Purpose**: Append-only event log for transactions and activities. Source of truth for rolling feature computation.

**Schema**:
```sql
CREATE TABLE gs_events (
    entity_id      VARCHAR(256)    NOT NULL,  -- Payer account ID
    kind           VARCHAR(16)     NOT NULL,  -- Event type ('tx' for transaction)
    ts             TIMESTAMP       NOT NULL,  -- Event timestamp
    amount         NUMERIC(18,2),             -- Transaction amount (optional)
    device_id      VARCHAR(256),              -- Device identifier (optional)
    ip             VARCHAR(64),               -- IP address (optional)

    PRIMARY KEY (entity_id, ts),
    FOREIGN KEY (entity_id) REFERENCES nodes(node_id),

    INDEX idx_gs_events_ts (ts DESC),  -- For time-windowed queries
    INDEX idx_gs_events_entity_ts (entity_id, ts DESC)  -- For per-entity lookups
);
```

**Columns**:
- `entity_id`: References `nodes.node_id` (payer account)
- `kind`: Event type discriminator (MVP uses 'tx' only, future: 'login', 'withdrawal')
- `ts`: Event occurrence time (used for 24h rolling window)
- `amount`: Transaction amount in currency units (NULL if not applicable)
- `device_id`: Device fingerprint (links to device nodes via graph edges)
- `ip`: IP address (links to IP nodes via graph edges)

**Constraints**:
- Primary key on `(entity_id, ts)` ensures unique event per entity per timestamp
- Foreign key to `nodes` ensures referential integrity
- NOT NULL on `entity_id`, `kind`, `ts` (core event properties)

**Validation Rules** (from FR-006):
- `entity_id` MUST exist in `nodes` table
- `ts` MUST be valid timestamp
- `amount` MUST be >= 0 if present
- `kind` MUST be in allowed values ('tx')

**State Transitions**:
- INSERT only (append-only log)
- No UPDATEs or DELETEs (manual cleanup for GDPR/data retention)

**Performance Considerations**:
- Estimated row size: ~150 bytes
- Insertion rate: 500-1000 rows/sec (FR-007 requirement)
- Index on `(entity_id, ts DESC)` critical for trigger performance

---

### gs_features (NEW)

**Purpose**: Denormalized rolling features for each entity. Updated in real-time by trigger and hourly batch job.

**Schema**:
```sql
CREATE TABLE gs_features (
    entity_id           VARCHAR(256)    PRIMARY KEY,  -- Entity (payer account)
    ts_updated          TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,  -- Last feature update

    -- Real-time features (trigger-updated)
    deg_24h             INT             DEFAULT 0,   -- Count of events in last 24h
    tx_amt_sum_24h      NUMERIC(18,2)   DEFAULT 0,   -- Sum of transaction amounts in last 24h

    -- Batch features (hourly job-updated)
    uniq_devices_7d     INT             DEFAULT 0,   -- Count of unique devices in last 7 days
    risk_neighbors_1hop INT             DEFAULT 0,   -- Count of risky neighbors at 1-hop distance

    FOREIGN KEY (entity_id) REFERENCES nodes(node_id),

    INDEX idx_gs_features_updated (ts_updated DESC)  -- For freshness monitoring
);
```

**Columns**:
- `entity_id`: References `nodes.node_id` (payer account)
- `ts_updated`: Timestamp of last feature update (for freshness tracking)
- `deg_24h`: Rolling 24-hour event count (incremented by trigger)
- `tx_amt_sum_24h`: Rolling 24-hour transaction sum (incremented by trigger)
- `uniq_devices_7d`: Count of distinct devices used in last 7 days (updated hourly)
- `risk_neighbors_1hop`: Count of neighbors labeled 'fraud' (updated hourly)

**Constraints**:
- Primary key on `entity_id` (one feature row per entity)
- Foreign key to `nodes` ensures referential integrity
- DEFAULT values ensure graceful handling of missing features (FR-008)

**Validation Rules** (from FR-020):
- `ts_updated` MUST be refreshed on every feature change
- `deg_24h`, `tx_amt_sum_24h` MUST be >= 0
- `uniq_devices_7d`, `risk_neighbors_1hop` MUST be >= 0

**State Transitions**:
- **INSERT**: On first event for entity (trigger)
- **UPDATE**: On subsequent events (trigger for real-time features, hourly job for batch features)
- **DELETE**: Manual only (GDPR compliance)

**Update Patterns**:
```sql
-- Trigger pattern (R002)
UPDATE gs_features
SET deg_24h = deg_24h + 1,
    tx_amt_sum_24h = tx_amt_sum_24h + COALESCE(new_amount, 0),
    ts_updated = CURRENT_TIMESTAMP
WHERE entity_id = ?;

-- Hourly job pattern (R002)
UPDATE gs_features f
SET uniq_devices_7d = (
    SELECT COUNT(DISTINCT device_id)
    FROM gs_events
    WHERE entity_id = f.entity_id AND ts >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
),
risk_neighbors_1hop = (
    SELECT COUNT(*)
    FROM rdf_edges e
    JOIN gs_labels l ON l.entity_id = e.o_id AND l.label = 'fraud'
    WHERE e.s = f.entity_id AND e.p IN ('uses_device', 'uses_ip', 'transacts_with')
),
ts_updated = CURRENT_TIMESTAMP;
```

**Performance Considerations**:
- Row size: ~100 bytes
- Estimated row count: 1-10M entities
- Trigger update: ~0.5-1ms per event (single row UPDATE)
- Hourly job: ~5min for 1M entities (batched UPDATEs)

---

### gs_labels (NEW)

**Purpose**: Ground truth labels for training and computing risky neighbor features.

**Schema**:
```sql
CREATE TABLE gs_labels (
    entity_id   VARCHAR(256)  NOT NULL,  -- Entity being labeled
    label       VARCHAR(16)   NOT NULL,  -- Label value ('fraud' or 'legit')
    label_ts    TIMESTAMP     NOT NULL,  -- When label was assigned

    PRIMARY KEY (entity_id, label_ts),
    FOREIGN KEY (entity_id) REFERENCES nodes(node_id),

    INDEX idx_gs_labels_label (label),  -- For filtering by label type
    INDEX idx_gs_labels_ts (label_ts DESC)  -- For time-based queries
);
```

**Columns**:
- `entity_id`: References `nodes.node_id` (labeled entity)
- `label`: Label value ('fraud' or 'legit')
- `label_ts`: Timestamp when label was assigned (for temporal analysis)

**Constraints**:
- Primary key on `(entity_id, label_ts)` allows multiple labels over time
- Foreign key to `nodes` ensures referential integrity
- `label` MUST be in allowed values ('fraud', 'legit')

**Validation Rules**:
- `entity_id` MUST exist in `nodes` table
- `label_ts` MUST be >= entity creation time

**State Transitions**:
- INSERT only (immutable audit trail)
- No UPDATEs or DELETEs (preserve label history)

**Usage**:
1. **Training**: Offline model training pipeline queries labels to build training set
2. **Risky Neighbors**: Hourly job counts neighbors with `label='fraud'`

**Performance Considerations**:
- Row size: ~80 bytes
- Estimated row count: 100K-1M labeled entities
- Index on `label` critical for hourly risky neighbor computation

---

### gs_fraud_centroid (NEW)

**Purpose**: Precomputed fraud embedding centroid for vector proximity reasoning.

**Schema**:
```sql
CREATE TABLE gs_fraud_centroid (
    centroid_id  INT          PRIMARY KEY,  -- Centroid identifier (1 for MVP)
    emb          VECTOR[768]  NOT NULL,     -- Fraud centroid vector
    label        VARCHAR(16)  DEFAULT 'fraud',  -- Always 'fraud' for MVP
    updated_at   TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,  -- Last centroid update

    INDEX idx_gs_centroid_updated (updated_at DESC)  -- For staleness monitoring
);
```

**Columns**:
- `centroid_id`: Unique identifier (1 for MVP, future: multiple centroids for clustering)
- `emb`: 768-dimensional fraud centroid vector (normalized)
- `label`: Always 'fraud' for MVP (future: 'legit' centroid for contrast)
- `updated_at`: Timestamp of last centroid recomputation

**Constraints**:
- Primary key on `centroid_id`
- `emb` MUST be normalized (L2 norm = 1.0)

**Validation Rules** (from R005):
- `emb` MUST have 768 dimensions
- `emb` MUST NOT contain NaN or Inf values

**State Transitions**:
- **INSERT**: On first centroid computation (offline job)
- **UPDATE**: Daily recomputation (offline job updates `emb` and `updated_at`)

**Computation Pattern** (offline):
```python
# Offline centroid computation
fraud_embeddings = kg_NodeEmbeddings.join(gs_labels, on='node_id')
                                    .filter(label='fraud')
                                    .select('emb')
centroid = np.mean(fraud_embeddings, axis=0)
centroid = centroid / np.linalg.norm(centroid)  # Normalize

# Insert/update in IRIS
cursor.execute("""
    MERGE INTO gs_fraud_centroid AS target
    USING (SELECT 1 AS centroid_id) AS source
    ON (target.centroid_id = source.centroid_id)
    WHEN MATCHED THEN UPDATE SET emb = ?, updated_at = CURRENT_TIMESTAMP
    WHEN NOT MATCHED THEN INSERT (centroid_id, emb, updated_at) VALUES (1, ?, CURRENT_TIMESTAMP)
""", (centroid, centroid))
```

**Performance Considerations**:
- Single row (1 centroid for MVP)
- Centroid update frequency: Daily (low write load)
- Cosine similarity lookup: ~0.5ms (R005)

---

### kg_NodeEmbeddings (EXISTING, EXTENDED)

**Purpose**: Precomputed node embeddings. Extended with `version` column for model tracking.

**Schema** (extension):
```sql
-- Existing columns
id          VARCHAR(256)  PRIMARY KEY REFERENCES nodes(node_id),
emb         VECTOR[768]   NOT NULL,
updated_at  TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,

-- NEW column
version     INT           DEFAULT 1  -- Model version that generated this embedding
```

**Extension Rationale** (from FR-017):
- Track which model version generated each embedding
- Support gradual rollout (e.g., recompute 10% of embeddings with new model daily)
- Enable A/B testing (compare predictions with v1 vs v2 embeddings)

**Migration Pattern**:
```sql
-- Add version column to existing table
ALTER TABLE kg_NodeEmbeddings ADD COLUMN version INT DEFAULT 1;

-- Update existing rows to version 1
UPDATE kg_NodeEmbeddings SET version = 1 WHERE version IS NULL;
```

**Performance Considerations**:
- No index on `version` required (not used in hot path)
- Minimal storage overhead (+4 bytes per row)

---

## Relationships

### Foreign Key Constraints

```sql
-- gs_events
gs_events.entity_id → nodes.node_id
gs_events.device_id → nodes.node_id (via graph edges, not FK)
gs_events.ip → nodes.node_id (via graph edges, not FK)

-- gs_features
gs_features.entity_id → nodes.node_id

-- gs_labels
gs_labels.entity_id → nodes.node_id
```

**Rationale**:
- Direct FK on `entity_id` ensures referential integrity for primary entity
- `device_id` and `ip` are linked via graph edges (flexible, allows NULL values)

### Graph Edges

**Risky Neighbor Relationships** (computed by hourly job):
```sql
-- Example edge patterns for risk_neighbors_1hop
rdf_edges WHERE s = payer_id AND p = 'uses_device' AND o_id IN (SELECT entity_id FROM gs_labels WHERE label='fraud')
rdf_edges WHERE s = payer_id AND p = 'uses_ip' AND o_id IN (SELECT entity_id FROM gs_labels WHERE label='fraud')
rdf_edges WHERE s = payer_id AND p = 'transacts_with' AND o_id IN (SELECT entity_id FROM gs_labels WHERE label='fraud')
```

**Edge Types**:
- `uses_device`: Payer → Device
- `uses_ip`: Payer → IP
- `transacts_with`: Payer → Merchant

---

## Data Flow

### Event Ingestion Flow
```
1. INSERT INTO gs_events (entity_id, kind, ts, amount, device_id, ip)
   ↓
2. AFTER INSERT trigger fires
   ↓
3. MERGE INTO gs_features (deg_24h++, tx_amt_sum_24h+=amount, ts_updated=NOW)
   ↓
4. COMMIT (both gs_events row and gs_features update in same transaction)
```

### Feature Refresh Flow (Hourly Job)
```
1. SELECT entity_id FROM gs_features WHERE ts_updated < NOW - INTERVAL '1 HOUR'
   ↓
2. For each entity_id:
     - Compute uniq_devices_7d (COUNT DISTINCT device_id from gs_events)
     - Compute risk_neighbors_1hop (COUNT fraud-labeled neighbors from rdf_edges + gs_labels)
   ↓
3. UPDATE gs_features SET uniq_devices_7d=?, risk_neighbors_1hop=?, ts_updated=NOW
```

### Scoring Flow (Inference)
```
1. Query gs_features WHERE entity_id = payer_id
   ↓
2. Query kg_NodeEmbeddings WHERE id IN (payer_id, device_id, ip_id, merchant_id)
   ↓
3. Query gs_fraud_centroid WHERE centroid_id = 1
   ↓
4. Combine features + embeddings → pass to TorchScript MLP
   ↓
5. Compute gradient × input (feature attributions)
   ↓
6. Compute cosine_similarity(payer_emb, fraud_centroid)
   ↓
7. Return {prob, reasons[]} JSON
```

---

## Indexing Strategy

### Critical Indexes (required for performance)

```sql
-- gs_events (for trigger and time-windowed queries)
CREATE INDEX idx_gs_events_entity_ts ON gs_events(entity_id, ts DESC);
CREATE INDEX idx_gs_events_ts ON gs_events(ts DESC);

-- gs_features (for freshness monitoring)
CREATE INDEX idx_gs_features_updated ON gs_features(ts_updated DESC);

-- gs_labels (for risky neighbor computation)
CREATE INDEX idx_gs_labels_label ON gs_labels(label);
CREATE INDEX idx_gs_labels_ts ON gs_labels(label_ts DESC);

-- gs_fraud_centroid (for staleness monitoring)
CREATE INDEX idx_gs_centroid_updated ON gs_fraud_centroid(updated_at DESC);

-- rdf_edges (CRITICAL for ego-graph performance - R003)
CREATE INDEX rdf_edges_spc_idx ON rdf_edges(s, p, created_at DESC);
```

**Index Rationale**:
| Index | Purpose | Performance Impact |
|-------|---------|-------------------|
| `idx_gs_events_entity_ts` | Trigger MERGE lookup, time-windowed queries | Reduces trigger overhead from ~5ms to ~0.5ms |
| `rdf_edges_spc_idx` | Ego-graph CTE traversal | Reduces ego-graph query from ~200ms to ~20ms |
| `idx_gs_labels_label` | Risky neighbor JOIN | Reduces hourly job from ~30min to ~5min |

---

## Data Retention & Cleanup

### gs_events (Append-Only)
- **Retention Policy**: Manual cleanup only (FR-019)
- **GDPR Compliance**: Provide DELETE script for specific entity_id
```sql
-- Manual cleanup script (run quarterly)
DELETE FROM gs_events WHERE ts < CURRENT_TIMESTAMP - INTERVAL '365' DAY;
```

### gs_features (Updated)
- **Retention Policy**: Keep latest features indefinitely
- **Cleanup**: DELETE only when entity is deactivated

### gs_labels (Immutable Audit Trail)
- **Retention Policy**: Never delete (audit trail)
- **GDPR Exception**: Labels may be pseudonymized but not deleted

---

## Validation Checklist

- [x] All tables have PRIMARY KEY constraints
- [x] All foreign keys properly reference nodes.node_id
- [x] Indexes defined for critical queries (trigger, hourly job, ego-graph)
- [x] DEFAULT values handle missing data gracefully (FR-008, NFR-008)
- [x] Timestamp columns for freshness tracking (ts_updated)
- [x] Validation rules align with functional requirements (FR-006, FR-008, FR-010)
- [x] State transitions documented for all tables
- [x] Performance characteristics estimated

---

**Status**: ✅ Data model complete, ready for contract generation (Phase 1)
