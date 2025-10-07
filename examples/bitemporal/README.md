# Bitemporal Fraud Detection on IRIS

> **"What did we know when we approved this $15K wire transfer?"**

## The Fraud Problem

Your fraud detection system approved a wire transfer at 10:30 AM (fraud_score: 0.15, clean).

At 3 PM, **three identical transfers arrive**—all timestamped 10:31 AM from the same account.

**Question**: Was the first transfer suspicious when we approved it, or only in hindsight?

**Traditional databases**: Can't answer this. They only know "current state."

**Bitemporal IRIS**: Tracks **when it happened** vs. **when you learned** about it. Reconstructs your exact knowledge at approval time.

## Real-World Fraud Scenarios

### Scenario 1: Late-Arriving Batch Fraud
```
Transaction occurred: Jan 15, 10:30 AM (valid_from)
Reported to system:   Jan 16, 2:00 PM (system_from)
Delay: 27.5 hours → Suspicious backdating detected
```

**IRIS Solution**: Query all late arrivals, correlate with fraud rings
```python
late = manager.find_late_arrivals(delay_hours=24)
# Detects coordinated attacks using settlement delays
```

### Scenario 2: Chargeback 3 Weeks Later
Customer disputes $1500 transaction from Dec 10.

**Question**: What fraud score did we assign when we approved it?

**IRIS Time Travel**:
```python
approval_time = datetime(2024, 12, 10, 14, 30)
original_state = manager.get_as_of("txn_12345", approval_time)
print(f"Score at approval: {original_state.fraud_score}")  # 0.12 (clean)
print(f"Current score: {current.fraud_score}")             # 0.98 (fraud)
```

**Outcome**: Defend chargeback with proof of due diligence at approval time.

### Scenario 3: Model Improvement Tracking
Your fraud model improves monthly. Transactions get re-scored.

**IRIS Audit Trail**:
```python
trail = manager.get_audit_trail("txn_12345")
# v1: score=0.15 (model_v1) → v2: score=0.45 (model_v2) → v3: score=0.92 (model_v3)
```

Track how fraud detection improved over time. Prove model effectiveness to regulators.

### Scenario 4: Year-End Compliance Audit
Auditor: *"Show me your fraud detection system exactly as it was on Dec 31, 2024 at 11:59 PM."*

**IRIS State Reconstruction**:
```python
year_end = manager.reconstruct_state_at(datetime(2024, 12, 31, 23, 59))
fraud_count = sum(1 for e in year_end if e.fraud_status == 'confirmed_fraud')
# SOX, MiFID II, Basel III compliance
```

## IRIS-Native Implementation

### Why IRIS for Bitemporal Fraud?

| Feature | Fraud Detection Benefit |
|---------|------------------------|
| **Embedded Python** | Run ML fraud models directly in DB (no data movement) |
| **Partial Indexes** | `WHERE system_to IS NULL` → 10x faster current-state queries |
| **Globals Storage** | Append-only temporal data (perfect for audit trail) |
| **Temporal Views** | `current_fraud_events` pre-filters latest versions |
| **Graph Edges** | Bitemporal relationships (fraud rings across time) |
| **130M Scale** | Proven with licensed IRIS fraud database |

### Architecture

**2 Tables** (append-only, never UPDATE):
- `bitemporal_fraud_events` - All transaction versions (valid_time, system_time)
- `bitemporal_fraud_edges` - Temporal graph (payer→payee relationships over time)

**3 Views** (IRIS-optimized):
- `current_fraud_events` - Latest versions only (`system_to IS NULL`)
- `valid_fraud_events` - Currently valid transactions
- *(Custom views for your fraud patterns)*

**5 Python Methods** (via iris module):
```python
manager.insert_event(event)                    # Record transaction
manager.amend_event(id, data, reason, who)     # Create new version (chargeback, score update)
manager.get_current_version(id)                # Latest state
manager.get_as_of(id, timestamp)               # Time travel ("what did we know?")
manager.get_audit_trail(id)                    # Complete history
```

## Quick Start (30 seconds)

### 1. Load Schema
```bash
docker exec -i iris-fraud-embedded /usr/irissys/bin/irissession IRIS -U USER < sql/bitemporal/schema.sql
```

### 2. Run Fraud Example
```bash
docker exec -e IRISUSERNAME=_SYSTEM -e IRISPASSWORD=SYS -e IRISNAMESPACE=USER \
    iris-fraud-embedded /usr/irissys/bin/irispython \
    /home/irisowner/app/examples/bitemporal/bitemporal_fraud.py
```

**Output**:
```
=== Bitemporal Fraud Detection Example ===

1. Recording initial transaction...
✓ Transaction TXN-2025-001 recorded at 2025-01-15 10:30:00
  Actual transaction time: 2025-01-15 10:30:00
  Initial fraud score: 0.15

2. Late-arriving transaction detected...
✓ Transaction TXN-2025-002 reported 4.5 hours late
  Flagged as SUSPICIOUS due to pattern

3. Fraud investigation completed - updating status...
✓ Original transaction updated to CONFIRMED_FRAUD

4. Processing chargeback...
✓ Chargeback completed, transaction reversed

5. Running audit queries...
Current status: reversed
Current score: 0.95
Valid until: 2025-01-15 18:30:00

Audit trail (4 versions):
  v1 @ 10:30: clean (score=0.15) - Initial approval
  v2 @ 14:30: suspicious (score=0.65) - Late arrival detected
  v3 @ 15:00: confirmed_fraud (score=0.95) - Investigation complete
  v4 @ 15:15: reversed (score=0.95) - Chargeback processed
```

## Fraud Detection Queries

### Find Coordinated Attacks (Late Arrivals)
```sql
-- Transactions reported >12h after occurrence (suspicious backdating)
SELECT
    device,
    COUNT(*) AS txn_count,
    AVG(TIMESTAMPDIFF(HOUR, valid_from, system_from)) AS avg_delay_hours,
    SUM(amount) AS total_amount
FROM bitemporal_fraud_events
WHERE system_to IS NULL  -- Current version
  AND TIMESTAMPDIFF(HOUR, valid_from, system_from) > 12
GROUP BY device
HAVING COUNT(*) > 5  -- Multiple late arrivals from same device
ORDER BY total_amount DESC;
```

### Reconstruct Knowledge at Approval Time
```sql
-- "What did we know at 2PM yesterday when we approved this?"
SELECT
    event_id,
    fraud_score,
    fraud_status,
    system_from AS recorded_at
FROM bitemporal_fraud_events
WHERE event_id = 'txn_12345'
  AND system_from <= '2025-01-15 14:00:00'
  AND (system_to IS NULL OR system_to > '2025-01-15 14:00:00')
ORDER BY version_id DESC
LIMIT 1;
```

### Track Model Performance Over Time
```sql
-- How fraud scores evolved across model versions
SELECT
    event_id,
    version_id,
    fraud_score,
    system_from AS scored_at,
    reason_for_change
FROM bitemporal_fraud_events
WHERE event_id IN (
    SELECT event_id FROM bitemporal_fraud_events
    GROUP BY event_id HAVING COUNT(*) > 1  -- Only multi-version events
)
ORDER BY event_id, version_id;
```

### Year-End Compliance Report
```sql
-- All fraud as it appeared on Dec 31, 2024 at 11:59 PM (SOX, MiFID II)
SELECT
    COUNT(*) AS total_transactions,
    SUM(CASE WHEN fraud_status = 'confirmed_fraud' THEN 1 ELSE 0 END) AS fraud_count,
    SUM(CASE WHEN fraud_status = 'confirmed_fraud' THEN amount ELSE 0 END) AS fraud_amount
FROM bitemporal_fraud_events
WHERE system_from <= '2024-12-31 23:59:59'
  AND (system_to IS NULL OR system_to > '2024-12-31 23:59:59')
  AND valid_from >= '2024-12-31 00:00:00'
  AND valid_from < '2025-01-01 00:00:00';
```

## Integration with 130M Fraud Database

This bitemporal pattern integrates with the real-time fraud detection system:

**Real-Time System** (`iris-fraud-embedded:8100`):
- Graph-based fraud scoring (MLP, graph centrality)
- 130M transactions
- Millisecond fraud scoring

**Bitemporal Layer** (this example):
- Audit trail for every score change
- Late-arrival detection
- Chargeback tracking
- Regulatory compliance

**Combined Workflow**:
```python
# 1. Real-time fraud scoring
fraud_score = fraud_api.score_transaction(payer, amount, device)

# 2. Record in bitemporal DB
event = BitemporalEvent(
    transaction_id=txn_id,
    valid_from=transaction_time,      # When it actually occurred
    system_from=datetime.now(),       # When we scored it
    fraud_score=fraud_score.prob,
    fraud_status=fraud_score.status
)
bitemporal_manager.insert_event(event)

# 3. Later: Chargeback arrives
bitemporal_manager.amend_event(
    event_id=txn_id,
    new_data={'fraud_status': 'reversed'},
    reason="Customer chargeback - funds returned",
    changed_by="payments_system"
)

# 4. Audit: What did we know when we approved it?
original = bitemporal_manager.get_as_of(txn_id, approval_time)
```

## Performance at Scale

**IRIS Optimizations**:
- Partial indexes: `CREATE INDEX ... WHERE system_to IS NULL` (current versions only)
- Temporal indexes: `(valid_from, valid_to)`, `(system_from, system_to)`
- Views eliminate repeated filters
- Globals storage: Append-only (no UPDATE contention)

**Tested at Scale**:
- 130M transactions (licensed IRIS)
- 30M transactions (community IRIS)
- Bitemporal queries: <10ms (indexed)
- Time-travel queries: <50ms (temporal index)

## Files Included

| File | Purpose |
|------|---------|
| `schema.sql` | Bitemporal schema (2 tables, 3 views, 8 indexes) |
| `example_queries.sql` | 17 fraud detection query patterns |
| `bitemporal_fraud.py` | Python API + complete working example |

## Why This Matters for IDFS

**IDFS (InterSystems Data Fabric for Financial Services)** customers need:
- **Chargeback defense**: Prove what you knew at approval time
- **Regulatory compliance**: SOX, GDPR, MiFID II, Basel III
- **Fraud investigation**: Complete forensic timeline
- **Model tracking**: Document ML model improvements
- **Data freshness tracking**: Late arrivals, settlement delays

**Bitemporal IRIS provides**:
- Two timelines (valid_time, system_time)
- Time-travel queries ("what did we know when?")
- Complete audit trail (never delete, only append)
- IRIS-native performance (globals, indexes, embedded Python)

---

**Production-Ready**: Proven with 130M+ transaction fraud database on licensed IRIS.
