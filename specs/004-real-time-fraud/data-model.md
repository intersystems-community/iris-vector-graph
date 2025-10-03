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
│ gs_events │  │ rdf_props   │ │  gs_labels  │ │kg_Node     │  │ rdf_edges (existing) │
│───────────│  │ (existing)  │ │─────────────│ │Embeddings  │  │──────────────────────│
│PK: entity │  │─────────────│ │PK: entity   │ │(existing,  │  │ Edges used for       │
│    _id,ts │  │ Node props: │ │    _id,     │ │ extended)  │  │ subgraph sampling    │
│           │  │ deg_24h,    │ │    label_ts │ │            │  │ (k-hop with fanout)  │
│device_id ─┼──┼─tx_amt_sum, │ │             │ │            │  └──────────────────────┘
│ip ────────┼──┼─device_count│ │             │ │            │
└───────────┘  │ (computed   │ └─────────────┘ └────────────┘
               │  on-demand) │                       │
               └─────────────┘                       │ emb used for
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

### rdf_props (EXISTING, EXTENDED)

**Purpose**: Node properties using existing RDF property table. Rolling features computed on-demand via CTE queries.

**Schema** (existing IRIS Vector Graph table):
```sql
-- Existing table structure
CREATE TABLE rdf_props (
    s         VARCHAR(256)  NOT NULL,  -- Subject (node_id)
    p         VARCHAR(256)  NOT NULL,  -- Property name
    o         VARCHAR(4096),           -- Property value (string)
    o_num     NUMERIC(18,2),           -- Property value (numeric)
    o_ts      TIMESTAMP,               -- Property value (timestamp)

    PRIMARY KEY (s, p),
    FOREIGN KEY (s) REFERENCES nodes(node_id),

    INDEX idx_rdf_props_sp (s, p)  -- For property lookups
);
```

**Fraud Feature Properties** (NEW usage pattern):
- `(entity_id, 'deg_24h', NULL, count, NULL)` - Event count in last 24h
- `(entity_id, 'tx_amt_sum_24h', NULL, sum, NULL)` - Transaction sum in last 24h
- `(entity_id, 'uniq_devices_7d', NULL, count, NULL)` - Unique devices in last 7 days
- `(entity_id, 'risk_neighbors_1hop', NULL, count, NULL)` - Risky neighbors at 1-hop
- `(entity_id, 'features_updated_at', NULL, NULL, timestamp)` - Last feature refresh timestamp

**On-Demand Feature Computation** (replaces trigger pattern):
```sql
-- Compute rolling features via CTE (called during scoring)
WITH rolling_features AS (
    SELECT
        :payer_id AS entity_id,
        COUNT(*) AS deg_24h,
        SUM(amount) AS tx_amt_sum_24h
    FROM gs_events
    WHERE entity_id = :payer_id
      AND ts >= CURRENT_TIMESTAMP - INTERVAL '24' HOUR
),
derived_features AS (
    SELECT
        :payer_id AS entity_id,
        COUNT(DISTINCT device_id) AS uniq_devices_7d,
        (SELECT COUNT(*)
         FROM rdf_edges e
         JOIN gs_labels l ON l.entity_id = e.o_id AND l.label = 'fraud'
         WHERE e.s = :payer_id) AS risk_neighbors_1hop
    FROM gs_events
    WHERE entity_id = :payer_id
      AND ts >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
)
SELECT * FROM rolling_features, derived_features;
```

**Cached Features** (optional optimization):
```sql
-- Optional: Materialize features as rdf_props for caching
MERGE INTO rdf_props AS target
USING (SELECT :entity_id AS s, 'deg_24h' AS p, deg_24h AS o_num FROM rolling_features) AS source
ON (target.s = source.s AND target.p = source.p)
WHEN MATCHED THEN UPDATE SET o_num = source.o_num
WHEN NOT MATCHED THEN INSERT (s, p, o_num) VALUES (source.s, source.p, source.o_num);
```

**Performance Considerations**:
- On-demand CTE: ~5-8ms for 24h window queries (acceptable for <20ms SLO)
- Optional caching via rdf_props: reduces to ~1ms property lookup
- Deferred optimization: Start with on-demand, cache if needed

**Advantages over gs_features table**:
- ✅ Graph-native (aligns with AWS GraphStorm pattern)
- ✅ Simpler schema (reuses existing rdf_props table)
- ✅ Flexible (add new features without schema migration)
- ✅ No trigger complexity (features computed when needed)

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

-- gs_labels
gs_labels.entity_id → nodes.node_id

-- rdf_props (existing)
rdf_props.s → nodes.node_id
```

**Rationale**:
- Direct FK on `entity_id` ensures referential integrity
- Features stored as `rdf_props` rows (graph-native pattern)
- `device_id` and `ip` linked via `rdf_edges` (not FK)

### Graph Edges (Subgraph Sampling Pattern)

**Subgraph Extraction for GNN Inference** (from R001A):
```sql
-- K-hop sampling with fanout limits (10/5)
WITH hop1 AS (
    SELECT e.s, e.o_id, e.p, e.created_at
    FROM rdf_edges e
    WHERE e.s = :target_transaction_id
    ORDER BY e.created_at DESC
    LIMIT 10  -- Fanout cap for hop 1
),
hop2 AS (
    SELECT e.s, e.o_id, e.p, e.created_at
    FROM rdf_edges e
    WHERE e.s IN (SELECT o_id FROM hop1)
    ORDER BY e.s, e.created_at DESC
    LIMIT 50  -- Fanout cap for hop 2 (10 × 5)
)
SELECT s, o_id, p FROM hop1
UNION ALL
SELECT s, o_id, p FROM hop2;
```

**Edge Types** (for fraud context):
- `uses_device`: Payer → Device
- `uses_ip`: Payer → IP
- `transacts_with`: Payer → Merchant
- `shares_device`: Payer → Payer (implicit, via device)

**Risky Neighbor Count** (computed on-demand):
```sql
-- Count fraud-labeled neighbors at 1-hop
SELECT COUNT(*)
FROM rdf_edges e
JOIN gs_labels l ON l.entity_id = e.o_id AND l.label = 'fraud'
WHERE e.s = :payer_id
  AND e.p IN ('uses_device', 'uses_ip', 'transacts_with');
```

---

## Data Flow

### Event Ingestion Flow (Simplified)
```
1. INSERT INTO gs_events (entity_id, kind, ts, amount, device_id, ip)
   ↓
2. COMMIT (single-table insert, no trigger overhead)
```

**No Trigger Required**: Features computed on-demand during scoring, not materialized

### Scoring Flow (MLP Mode)
```
1. Compute rolling features via CTE (deg_24h, tx_amt_sum_24h, uniq_devices_7d, risk_neighbors_1hop)
   ↓ ~5-8ms
2. Query kg_NodeEmbeddings WHERE id = payer_id
   ↓ ~1ms
3. Query gs_fraud_centroid WHERE centroid_id = 1
   ↓ ~0.5ms
4. Combine features + payer_emb → TorchScript MLP inference
   ↓ ~8ms
5. Compute gradient × input (feature attributions)
   ↓ ~3ms
6. Compute cosine_similarity(payer_emb, fraud_centroid)
   ↓ ~0.5ms
7. Return {prob, reasons[]} JSON
   ↓
Total: ~18.5ms (meets <20ms SLO)
```

### Scoring Flow (EGO Mode - Optional)
```
1. Extract k-hop subgraph via bounded CTE (fanout 10/5, max 60 edges)
   ↓ ~21-31ms
2. Query kg_NodeEmbeddings for all subgraph nodes
   ↓ ~2-3ms
3. Build subgraph JSON payload (nodes + edges + features)
   ↓ ~1-2ms
4. TorchScript GraphSAGE inference (if implemented)
   ↓ ~8-12ms
5. Feature attributions + vector proximity
   ↓ ~3ms
6. Return {prob, reasons[]} JSON
   ↓
Total: ~35-51ms (meets <50ms SLO)
```

### Optional Feature Caching Flow
```
-- If on-demand CTE exceeds latency budget, materialize to rdf_props
1. Hourly job: Compute features for active entities (ts_updated < NOW - 1 HOUR)
   ↓
2. MERGE INTO rdf_props (deg_24h, tx_amt_sum_24h, uniq_devices_7d, risk_neighbors_1hop)
   ↓
3. Scoring reads from rdf_props (~1ms) instead of CTE (~5-8ms)
```

---

## Indexing Strategy

### Critical Indexes (required for performance)

```sql
-- gs_events (for on-demand feature CTE queries)
CREATE INDEX idx_gs_events_entity_ts ON gs_events(entity_id, ts DESC);  -- Rolling window queries
CREATE INDEX idx_gs_events_ts ON gs_events(ts DESC);  -- Global time-based queries

-- gs_labels (for risky neighbor computation)
CREATE INDEX idx_gs_labels_label ON gs_labels(label);  -- Filter by label='fraud'
CREATE INDEX idx_gs_labels_ts ON gs_labels(label_ts DESC);  -- Temporal queries

-- gs_fraud_centroid (for staleness monitoring)
CREATE INDEX idx_gs_centroid_updated ON gs_fraud_centroid(updated_at DESC);

-- rdf_edges (CRITICAL for subgraph sampling - R001A)
CREATE INDEX rdf_edges_spc_idx ON rdf_edges(s, p, created_at DESC);  -- K-hop fanout queries

-- rdf_props (optional, for cached features)
-- Primary key (s, p) already provides efficient lookups
```

**Index Rationale**:
| Index | Purpose | Performance Impact |
|-------|---------|-------------------|
| `idx_gs_events_entity_ts` | On-demand CTE feature computation (24h/7d windows) | 5-8ms for rolling features |
| `rdf_edges_spc_idx` | K-hop subgraph sampling (fanout 10/5) | Reduces from ~200ms to ~21-31ms |
| `idx_gs_labels_label` | Risky neighbor JOIN (fraud-labeled nodes) | Reduces neighbor count from ~50ms to ~5ms |
| `rdf_props (s,p) PK` | Cached feature lookups (if materialized) | ~1ms property access |

---

## Data Retention & Cleanup

### gs_events (Append-Only)
- **Retention Policy**: Manual cleanup only (FR-019)
- **GDPR Compliance**: Provide DELETE script for specific entity_id
```sql
-- Manual cleanup script (run quarterly)
DELETE FROM gs_events WHERE ts < CURRENT_TIMESTAMP - INTERVAL '365' DAY;
```

### rdf_props (Cached Features - Optional)
- **Retention Policy**: No cleanup required (features computed on-demand)
- **Optional Cleanup**: Purge stale cached features if materialized
```sql
-- Optional: Remove cached features older than 30 days
DELETE FROM rdf_props
WHERE s IN (SELECT entity_id FROM gs_events WHERE ts < CURRENT_TIMESTAMP - INTERVAL '30' DAY)
  AND p IN ('deg_24h', 'tx_amt_sum_24h', 'uniq_devices_7d', 'risk_neighbors_1hop');
```

### gs_labels (Immutable Audit Trail)
- **Retention Policy**: Never delete (audit trail)
- **GDPR Exception**: Labels may be pseudonymized but not deleted

---

## Validation Checklist

- [x] All tables have PRIMARY KEY constraints
- [x] All foreign keys properly reference nodes.node_id
- [x] Indexes defined for critical queries (on-demand CTE, subgraph sampling)
- [x] DEFAULT values handle missing data gracefully (FR-008, NFR-008)
- [x] On-demand feature computation pattern documented (5-8ms CTE)
- [x] Subgraph sampling pattern with fanout limits (10/5, max 60 edges)
- [x] Validation rules align with functional requirements (FR-006, FR-008, FR-010)
- [x] State transitions documented for all tables
- [x] Performance characteristics estimated (meets <20ms MLP, <50ms EGO SLOs)
- [x] Graph-native pattern aligns with AWS GraphStorm (features as properties)

---

**Status**: ✅ Data model simplified and aligned with AWS GraphStorm pattern (Phase 1)
