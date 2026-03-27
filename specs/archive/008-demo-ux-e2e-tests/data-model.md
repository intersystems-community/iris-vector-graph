# Data Model: Fraud Detection Domain

**Feature**: 008-demo-ux-e2e-tests  
**Date**: 2025-01-18

## Overview

This document defines the data model for the Fraud Detection demo domain. It follows the same generic graph schema used by the Biomedical domain, with domain-specific entity types layered on top.

## Core Schema (Existing)

The Fraud Detection domain uses the existing generic graph schema:

```sql
-- Nodes (entity identities)
nodes (
    node_id VARCHAR(255) PRIMARY KEY,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)

-- Labels (entity types)
rdf_labels (
    s VARCHAR(255),     -- node_id
    label VARCHAR(100)  -- e.g., "Account", "Transaction", "Alert"
)

-- Properties (entity attributes)
rdf_props (
    s VARCHAR(255),     -- node_id
    p VARCHAR(100),     -- property name
    o TEXT,             -- property value (JSON for complex types)
    lang VARCHAR(10),   -- optional language tag
    datatype VARCHAR(50) -- XSD datatype
)

-- Edges (relationships)
rdf_edges (
    s VARCHAR(255),     -- source node_id
    p VARCHAR(100),     -- predicate (relationship type)
    o_id VARCHAR(255),  -- target node_id
    confidence FLOAT    -- optional confidence score
)

-- Vector Embeddings
kg_NodeEmbeddings (
    id VARCHAR(255) PRIMARY KEY,
    emb VECTOR(768)     -- 768-dimensional embeddings
)
```

## Domain Entities

### Account

Represents a financial account in the fraud detection network.

| Property | Type | Description | Required |
|----------|------|-------------|----------|
| id | VARCHAR(255) | Unique identifier (e.g., "ACCOUNT:A12345") | Yes |
| labels | ["Account"] | Entity type | Yes |
| account_type | VARCHAR(50) | "checking", "savings", "credit", "crypto" | Yes |
| status | VARCHAR(20) | "active", "suspended", "closed" | Yes |
| risk_score | FLOAT | 0.0-1.0 risk assessment | No |
| created_date | DATE | Account creation date | No |
| holder_name | VARCHAR(255) | Account holder (anonymized for demo) | No |

**Relationships**:
- `SENT_TO` → Transaction (outgoing money)
- `RECEIVED_FROM` → Transaction (incoming money)
- `USED_BY` → Device (login devices)

---

### Transaction

Represents a financial transaction between accounts.

| Property | Type | Description | Required |
|----------|------|-------------|----------|
| id | VARCHAR(255) | Unique identifier (e.g., "TXN:T78901") | Yes |
| labels | ["Transaction"] | Entity type | Yes |
| amount | FLOAT | Transaction amount | Yes |
| currency | VARCHAR(3) | ISO 4217 currency code | Yes |
| transaction_type | VARCHAR(50) | "transfer", "payment", "withdrawal", "deposit" | Yes |
| timestamp | TIMESTAMP | Transaction time | Yes |
| status | VARCHAR(20) | "completed", "pending", "failed", "reversed" | Yes |
| description | TEXT | Transaction description | No |

**Relationships**:
- `FROM_ACCOUNT` → Account (source)
- `TO_ACCOUNT` → Account (destination)
- `TRIGGERED` → Alert (if suspicious)

---

### Alert

Represents a fraud detection alert.

| Property | Type | Description | Required |
|----------|------|-------------|----------|
| id | VARCHAR(255) | Unique identifier (e.g., "ALERT:AL456") | Yes |
| labels | ["Alert"] | Entity type | Yes |
| alert_type | VARCHAR(50) | "velocity", "pattern", "anomaly", "threshold" | Yes |
| severity | VARCHAR(20) | "low", "medium", "high", "critical" | Yes |
| confidence | FLOAT | 0.0-1.0 detection confidence | Yes |
| triggered_at | TIMESTAMP | Alert timestamp | Yes |
| status | VARCHAR(20) | "open", "investigating", "resolved", "false_positive" | Yes |
| description | TEXT | Human-readable alert description | No |

**Relationships**:
- `RELATED_TO` → Transaction (triggering transaction)
- `INVOLVES` → Account (suspicious account)

---

## Fraud Detection Patterns

### Ring Pattern (Money Laundering)

A cycle of accounts where money flows in a closed loop:

```
Account A → Transaction → Account B
     ↑                        ↓
Account D ← Transaction ← Account C
```

**Detection**: Graph query finding cycles of length 3-5 with rapid transaction timing.

### Star Pattern (Mule Account)

A central account receiving/sending to many peripheral accounts:

```
      Account X
          ↓
Account A → Account B (Mule) → Account C
          ↑
      Account Y
```

**Detection**: Find accounts with degree > threshold (e.g., 10+ unique counterparties).

### Velocity Pattern

Unusual number of transactions in short timeframe:

**Detection**: Count transactions per account per hour, flag if > threshold.

---

## Vector Embeddings

Account and Transaction embeddings are 768-dimensional vectors encoding:

- **Account embeddings**: Transaction patterns, counterparty network, temporal behavior
- **Transaction embeddings**: Amount, timing, description text (NLP-derived)

**Use Cases**:
- Anomaly detection: Find transactions with low similarity to historical patterns
- Cluster analysis: Group similar accounts for pattern recognition
- Nearest neighbor: Find accounts similar to known fraudsters

---

## Sample Data Summary

| Entity | Count | Notes |
|--------|-------|-------|
| Account | 75 | 10 flagged as suspicious |
| Transaction | 300 | 50 in fraud patterns |
| Alert | 25 | Various severity levels |
| Embeddings | 375 | All accounts + transactions |

**Patterns Included**:
- 3 ring patterns (4-5 nodes each)
- 2 star patterns (1 hub + 8-12 spokes)
- 10 velocity violations
