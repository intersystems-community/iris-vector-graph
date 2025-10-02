# Quickstart: Real-Time Fraud Scoring on IRIS Vector Graph

**Feature**: 004-real-time-fraud
**Date**: 2025-10-02

## Overview

This quickstart guide demonstrates the fraud scoring MVP in 5 steps:
1. Verify IRIS database health
2. Load fraud schema and sample data
3. Insert a transaction event (triggers feature update)
4. Score a transaction via REST API
5. Validate explainability (reason codes)

**Prerequisites**:
- IRIS 2025.1+ running (Docker or local instance)
- Python 3.11+ with `uv` package manager
- TorchScript MLP model file (`models/mlp_current.torchscript`)

**Performance Expectations**:
- Event ingestion: <2ms
- Fraud scoring (MLP mode): <20ms p95
- End-to-end workflow: <5 seconds

---

## Step 1: Verify IRIS Database Health

### 1.1 Check IRIS is Running

```bash
# Default IRIS (Community Edition)
docker ps | grep iris

# ACORN-1 (licensed IRIS with HNSW)
docker ps | grep acorn
```

**Expected Output**:
```
CONTAINER ID   IMAGE                              PORTS
abc123def456   intersystemsdc/iris-community     0.0.0.0:1972->1972/tcp, 0.0.0.0:52773->52773/tcp
```

### 1.2 Test Database Connection

```python
# Run in Python REPL or Jupyter notebook
import iris

conn = iris.connect(
    host="localhost",
    port=1972,
    namespace="USER",
    username="_SYSTEM",
    password="SYS"
)

cursor = conn.cursor()
cursor.execute("SELECT 1")
result = cursor.fetchone()
print(f"✅ IRIS connection successful: {result}")

conn.close()
```

**Expected Output**:
```
✅ IRIS connection successful: (1,)
```

### 1.3 Verify Core Schema Exists

```sql
-- Run in IRIS SQL shell or Management Portal
SELECT COUNT(*) AS node_count FROM nodes;
SELECT COUNT(*) AS embedding_count FROM kg_NodeEmbeddings;
SELECT COUNT(*) AS edge_count FROM rdf_edges;
```

**Expected Output**:
```
node_count: [varies, depends on existing data]
embedding_count: [varies]
edge_count: [varies]
```

If tables don't exist, run:
```bash
uv run python scripts/setup_schema.py
```

---

## Step 2: Load Fraud Schema and Sample Data

### 2.1 Load Fraud-Specific SQL Schema

```bash
# Load fraud schema (gs_events, gs_features, gs_labels, gs_fraud_centroid)
uv run python scripts/fraud/load_fraud_schema.py
```

**Expected Output**:
```
✓ Created table: gs_events
✓ Created table: gs_features
✓ Created table: gs_labels
✓ Created table: gs_fraud_centroid
✓ Created trigger: gs_UpdateRollingFeatures
✓ Created procedure: gs_FetchServingRow
✓ Created procedure: gs_ScoreMLP
✓ Created procedure: gs_EgoGraph
✓ Created job: gs_RefreshDerivedFeatures
```

### 2.2 Load Sample Entities and Events

```bash
# Generate synthetic fraud events for testing
uv run python scripts/fraud/load_sample_events.py --num-entities 100 --num-events 1000
```

**Expected Output**:
```
✓ Inserted 100 entities into nodes table
  - 80 payer accounts (acct:*)
  - 10 devices (dev:*)
  - 5 IPs (ip:*)
  - 5 merchants (mer:*)

✓ Inserted 1000 events into gs_events table
  - Event types: 1000 transactions ('tx')
  - Trigger updated gs_features for 80 unique payers

✓ Inserted 10 fraud labels into gs_labels table
  - 8 fraud-labeled entities
  - 2 legit-labeled entities

✓ Computed fraud centroid (768-dim vector)
  - Inserted into gs_fraud_centroid (centroid_id=1)
```

### 2.3 Verify Sample Data Loaded

```sql
-- Check event count
SELECT COUNT(*) AS event_count FROM gs_events;

-- Check feature count (should match unique payers)
SELECT COUNT(*) AS feature_count FROM gs_features;

-- Check labels
SELECT label, COUNT(*) AS count FROM gs_labels GROUP BY label;

-- Check fraud centroid exists
SELECT centroid_id, updated_at FROM gs_fraud_centroid;
```

**Expected Output**:
```
event_count: 1000
feature_count: 80
label: fraud | count: 8
label: legit | count: 2
centroid_id: 1 | updated_at: 2025-10-02 10:30:00
```

---

## Step 3: Insert Transaction Event (Trigger Demo)

### 3.1 Check Initial Feature State

```python
import iris

conn = iris.connect("localhost", 1972, "USER", "_SYSTEM", "SYS")
cursor = conn.cursor()

# Query features for test payer before event
cursor.execute("""
    SELECT entity_id, deg_24h, tx_amt_sum_24h, ts_updated
    FROM gs_features
    WHERE entity_id = 'acct:quickstart_user1'
""")

result = cursor.fetchone()
print(f"Before event: {result}")
# Expected: None (entity doesn't exist yet) or (acct:quickstart_user1, 0, 0.00, <timestamp>)
```

### 3.2 Insert Transaction Event

```python
# Insert event (trigger will fire automatically)
cursor.execute("""
    INSERT INTO gs_events (entity_id, kind, ts, amount, device_id, ip)
    VALUES ('acct:quickstart_user1', 'tx', CURRENT_TIMESTAMP, 150.00, 'dev:laptop123', 'ip:192.168.1.100')
""")
conn.commit()

print("✓ Event inserted, trigger fired")
```

### 3.3 Verify Trigger Updated Features

```python
# Query features again (same transaction)
cursor.execute("""
    SELECT entity_id, deg_24h, tx_amt_sum_24h, ts_updated
    FROM gs_features
    WHERE entity_id = 'acct:quickstart_user1'
""")

result = cursor.fetchone()
print(f"After event: {result}")

# Expected output:
# ('acct:quickstart_user1', 1, 150.00, <current timestamp>)

# Verify timestamp is recent (within last 5 seconds)
import datetime
ts_updated = result[3]
now = datetime.datetime.now()
delta = (now - ts_updated).total_seconds()
assert delta < 5, f"Feature update not fresh (delta={delta}s)"

print(f"✅ Feature freshness: {delta:.2f}s (target: <60s)")

conn.close()
```

**Expected Output**:
```
Before event: None
✓ Event inserted, trigger fired
After event: ('acct:quickstart_user1', 1, 150.00, datetime.datetime(2025, 10, 2, 10, 35, 12))
✅ Feature freshness: 0.15s (target: <60s)
```

**Constitutional Compliance**:
- ✅ FR-008: Features updated in same transaction as event insert
- ✅ FR-020: `ts_updated` timestamp reflects fresh update

---

## Step 4: Score Transaction via REST API

### 4.1 Start FastAPI Server

```bash
# Terminal 1: Start FastAPI server
cd /Users/tdyar/ws/iris-vector-graph
uv run uvicorn api.main:app --reload --port 8000
```

**Expected Output**:
```
INFO:     ✓ IRIS Vector Graph API - Multi-Query-Engine Platform
INFO:       GraphQL endpoint: /graphql
INFO:       openCypher endpoint: /api/cypher
INFO:       Fraud Scoring endpoint: /fraud/score
INFO:     Connecting to IRIS at localhost:1972/USER
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

### 4.2 Call Fraud Scoring Endpoint (MLP Mode)

```bash
# Terminal 2: Score transaction via REST API
curl -X POST http://localhost:8000/fraud/score \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "MLP",
    "payer": "acct:quickstart_user1",
    "device": "dev:laptop123",
    "ip": "ip:192.168.1.100",
    "merchant": "mer:shop456",
    "amount": 150.00,
    "country": "US"
  }'
```

**Expected Response** (HTTP 200 OK):
```json
{
  "prob": 0.12,
  "reasons": [
    {
      "kind": "feature",
      "detail": "deg_24h=1",
      "weight": 0.05
    },
    {
      "kind": "feature",
      "detail": "tx_amt_sum_24h=150.00",
      "weight": 0.03
    },
    {
      "kind": "vector",
      "detail": "sim_to_fraud=0.24",
      "weight": 0.04
    }
  ]
}
```

**Interpretation**:
- **prob=0.12**: Low fraud probability (12%)
- **Reason 1**: Low 24h event count (deg_24h=1)
- **Reason 2**: Moderate transaction amount (tx_amt_sum_24h=150.00)
- **Reason 3**: Low similarity to fraud centroid (sim_to_fraud=0.24)

### 4.3 Validate Response Time

```bash
# Measure response time (10 consecutive requests)
for i in {1..10}; do
  time curl -s -X POST http://localhost:8000/fraud/score \
    -H "Content-Type: application/json" \
    -d '{
      "mode": "MLP",
      "payer": "acct:quickstart_user1",
      "device": "dev:laptop123",
      "ip": "ip:192.168.1.100",
      "merchant": "mer:shop456",
      "amount": 150.00
    }' > /dev/null
done
```

**Expected Output**:
```
real    0m0.018s  # <20ms (meets FR-004 SLO)
real    0m0.015s
real    0m0.017s
...
```

**Constitutional Compliance**:
- ✅ FR-004: MLP mode p95 latency <20ms
- ✅ NFR-001: PK lookups complete in ≤4ms
- ✅ NFR-002: MLP inference completes in ≤8ms

---

## Step 5: Validate Explainability (Reason Codes)

### 5.1 Verify Minimum 3 Reason Codes

```python
import requests

response = requests.post(
    "http://localhost:8000/fraud/score",
    json={
        "mode": "MLP",
        "payer": "acct:quickstart_user1",
        "device": "dev:laptop123",
        "ip": "ip:192.168.1.100",
        "merchant": "mer:shop456",
        "amount": 150.00
    }
)

assert response.status_code == 200, f"Expected 200, got {response.status_code}"

data = response.json()
reasons = data["reasons"]

# FR-002: Minimum 3 reason codes
assert len(reasons) >= 3, f"Expected >=3 reasons, got {len(reasons)}"
print(f"✅ Reason code count: {len(reasons)} (target: >=3)")

# FR-011: Feature reasons show concrete values
feature_reasons = [r for r in reasons if r["kind"] == "feature"]
for reason in feature_reasons:
    assert "=" in reason["detail"], f"Feature reason missing value: {reason['detail']}"
    print(f"✅ Feature reason: {reason['detail']} (weight={reason['weight']:.2f})")

# FR-012: Vector proximity reason exists
vector_reasons = [r for r in reasons if r["kind"] == "vector"]
assert len(vector_reasons) > 0, "Expected at least 1 vector proximity reason"
print(f"✅ Vector proximity: {vector_reasons[0]['detail']} (weight={vector_reasons[0]['weight']:.2f})")
```

**Expected Output**:
```
✅ Reason code count: 3 (target: >=3)
✅ Feature reason: deg_24h=1 (weight=0.05)
✅ Feature reason: tx_amt_sum_24h=150.00 (weight=0.03)
✅ Vector proximity: sim_to_fraud=0.24 (weight=0.04)
```

### 5.2 Test Cold-Start Scenario (Missing Embedding)

```python
# Create entity without embedding
conn = iris.connect("localhost", 1972, "USER", "_SYSTEM", "SYS")
cursor = conn.cursor()

cursor.execute("INSERT INTO nodes (node_id) VALUES ('acct:cold_start_user')")
cursor.execute("""
    INSERT INTO gs_events (entity_id, kind, ts, amount, device_id, ip)
    VALUES ('acct:cold_start_user', 'tx', CURRENT_TIMESTAMP, 500.00, 'dev:laptop123', 'ip:192.168.1.100')
""")
conn.commit()
conn.close()

# Score without embedding
response = requests.post(
    "http://localhost:8000/fraud/score",
    json={
        "mode": "MLP",
        "payer": "acct:cold_start_user",
        "device": "dev:laptop123",
        "ip": "ip:192.168.1.100",
        "merchant": "mer:shop456",
        "amount": 500.00
    }
)

data = response.json()
reasons = data["reasons"]

# FR-018: Zero-vector fallback with "cold_start" reason
vector_reasons = [r for r in reasons if r["kind"] == "vector"]
assert any("cold_start" in r["detail"] for r in vector_reasons), "Expected cold_start reason"
print(f"✅ Cold-start handled gracefully: {vector_reasons[0]['detail']}")
```

**Expected Output**:
```
✅ Cold-start handled gracefully: cold_start (weight=0.00)
```

**Constitutional Compliance**:
- ✅ FR-018: Missing embeddings fall back to zero-vector with explicit reason code
- ✅ NFR-008: Missing data handled gracefully without exceptions

---

## Cleanup

### Remove Test Entities

```python
import iris

conn = iris.connect("localhost", 1972, "USER", "_SYSTEM", "SYS")
cursor = conn.cursor()

# Delete test events
cursor.execute("DELETE FROM gs_events WHERE entity_id LIKE 'acct:quickstart%'")
cursor.execute("DELETE FROM gs_events WHERE entity_id = 'acct:cold_start_user'")

# Delete test features
cursor.execute("DELETE FROM gs_features WHERE entity_id LIKE 'acct:quickstart%'")
cursor.execute("DELETE FROM gs_features WHERE entity_id = 'acct:cold_start_user'")

# Delete test nodes
cursor.execute("DELETE FROM nodes WHERE node_id LIKE 'acct:quickstart%'")
cursor.execute("DELETE FROM nodes WHERE node_id = 'acct:cold_start_user'")

conn.commit()
conn.close()

print("✓ Cleanup complete")
```

---

## Validation Checklist

- [x] IRIS database health verified (Step 1)
- [x] Fraud schema loaded (Step 2.1)
- [x] Sample data inserted (Step 2.2)
- [x] Event ingestion triggers feature update (Step 3)
- [x] Fraud scoring endpoint returns valid response (Step 4)
- [x] Response time <20ms p95 (Step 4.3)
- [x] Minimum 3 reason codes returned (Step 5.1)
- [x] Feature reasons show concrete values (Step 5.1)
- [x] Vector proximity reason included (Step 5.1)
- [x] Cold-start scenario handled gracefully (Step 5.2)

**Constitutional Compliance**:
- ✅ Test-First: Quickstart validates all acceptance scenarios from spec.md
- ✅ IRIS-Native: All operations use IRIS SQL + embedded Python (no external services)
- ✅ Performance as Feature: Response time validated against SLOs
- ✅ Explicit Error Handling: Cold-start fallback tested
- ✅ Observability: Reason codes provide debugging context

---

## Next Steps

1. **Run Contract Tests**: Validate OpenAPI contract compliance
   ```bash
   pytest specs/004-real-time-fraud/contracts/test_fraud_score_contract.py -v
   ```

2. **Run Performance Benchmark**: Load test at 200 QPS
   ```bash
   uv run python scripts/fraud/performance/benchmark_fraud_scoring.py
   ```

3. **Explore EGO Mode**: Test optional ego-graph + GraphSAGE scoring
   ```bash
   curl -X POST http://localhost:8000/fraud/score \
     -H "Content-Type: application/json" \
     -d '{"mode": "EGO", "payer": "acct:quickstart_user1", ...}'
   ```

4. **Hot-Reload Model**: Test TorchScript model hot-reload
   ```bash
   curl -X POST http://localhost:8000/admin/model/activate \
     -H "Content-Type: application/json" \
     -d '{"model_path": "models/mlp_v2.torchscript"}'
   ```

---

**Quickstart Complete**: ✅ All acceptance scenarios validated in <5 minutes
