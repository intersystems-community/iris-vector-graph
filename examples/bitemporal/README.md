# Bitemporal Data Management for Financial Services

This example demonstrates **bitemporal data modeling** for fraud detection and financial data management, designed for **IDFS (InterSystems Data Fabric for Financial Services)** customers.

## What is Bitemporal Data?

Bitemporal data tracks **two independent timelines**:

1. **Valid Time** (Business Time): When the event **actually occurred** in the real world
   - Example: Transaction happened at 10:30 AM on Jan 15
   - Represents the "truth" about when things happened

2. **Transaction Time** (System Time): When we **learned about** or **recorded** the event
   - Example: We received the transaction at 2:00 PM on Jan 15 (3.5 hour delay)
   - Represents our knowledge timeline

## Why Bitemporal for Fraud Detection?

Financial services face unique challenges that bitemporal modeling solves:

### 1. **Late-Arriving Transactions**
- **Settlement delays**: Credit card transactions settle in batches (24-72 hours)
- **Cross-border delays**: International transfers can take days
- **Batch processing**: End-of-day reconciliation creates time gaps
- **Fraud detection**: Late arrivals might indicate fraudulent backdating

**Example**:
```
Transaction occurred: Jan 15, 10:30 AM (valid_from)
We learned about it:  Jan 16, 2:00 PM (system_from)
Delay: 27.5 hours → Suspicious pattern flagged
```

### 2. **Corrections and Amendments**
- **Chargebacks**: Customer disputes transaction weeks later
- **Fraud reversals**: Confirmed fraud requires updating historical records
- **Data corrections**: Fixing errors while preserving audit trail
- **Score updates**: ML models improve, requiring re-scoring

**Bitemporal Solution**: Create new version with updated data, preserving complete history.

### 3. **Regulatory Compliance**
- **SOX (Sarbanes-Oxley)**: Complete audit trail required
- **GDPR**: Right to rectification while maintaining history
- **MiFID II**: Transaction reporting with amendments
- **Basel III**: Historical risk reporting

**Key Query**: "Show me the exact state of our fraud detection system as it appeared on Dec 31 for year-end audit"

### 4. **Forensic Analysis**
- **Investigation**: "What did we know when we approved this transaction?"
- **Pattern detection**: Analyzing fraud ring behavior over time
- **Model performance**: Track how fraud scores evolved
- **Customer disputes**: Reconstruct complete timeline

## Schema Overview

### Core Tables

#### `bitemporal_fraud_events`
Main table storing all transaction versions:

```sql
CREATE TABLE bitemporal_fraud_events (
    -- Identity
    event_id VARCHAR(255),
    version_id INTEGER,

    -- VALID TIME (when it happened)
    valid_from TIMESTAMP,
    valid_to TIMESTAMP,       -- NULL = still valid

    -- TRANSACTION TIME (when we recorded it)
    system_from TIMESTAMP,
    system_to TIMESTAMP,      -- NULL = current version

    -- Transaction details
    transaction_id VARCHAR(255),
    amount DECIMAL(18, 2),
    payer VARCHAR(255),
    payee VARCHAR(255),

    -- Fraud detection
    fraud_score DECIMAL(5, 4),
    fraud_status VARCHAR(50),
    risk_level VARCHAR(20),

    -- Audit trail
    reason_for_change VARCHAR(500),
    changed_by VARCHAR(100),

    PRIMARY KEY (event_id, version_id)
);
```

#### `bitemporal_fraud_edges`
Temporal graph relationships:

```sql
CREATE TABLE bitemporal_fraud_edges (
    edge_id VARCHAR(255),
    version_id INTEGER,
    valid_from TIMESTAMP,
    valid_to TIMESTAMP,
    system_from TIMESTAMP,
    system_to TIMESTAMP,
    from_entity VARCHAR(255),
    to_entity VARCHAR(255),
    edge_type VARCHAR(100),
    PRIMARY KEY (edge_id, version_id)
);
```

### Key Views

- **`current_fraud_events`**: Only current versions (`system_to IS NULL`)
- **`valid_fraud_events`**: Currently valid transactions (`valid_from <= NOW < valid_to`)

## Common Query Patterns

### 1. Get Current State
```sql
SELECT * FROM current_fraud_events
WHERE payer = 'acct:customer123';
```

### 2. Time Travel (As-Of Query)
**"What did we know at 2PM yesterday?"**
```sql
SELECT * FROM bitemporal_fraud_events
WHERE system_from <= '2025-01-15 14:00:00'
  AND (system_to IS NULL OR system_to > '2025-01-15 14:00:00');
```

### 3. Find Late Arrivals
```sql
SELECT
    event_id,
    valid_from AS actual_time,
    system_from AS reported_time,
    TIMESTAMPDIFF(HOUR, valid_from, system_from) AS delay_hours
FROM bitemporal_fraud_events
WHERE TIMESTAMPDIFF(HOUR, valid_from, system_from) > 24;
```

### 4. Audit Trail
```sql
SELECT
    version_id,
    system_from,
    fraud_status,
    fraud_score,
    reason_for_change,
    changed_by
FROM bitemporal_fraud_events
WHERE event_id = 'evt_12345'
ORDER BY version_id;
```

### 5. End-of-Day Reconciliation
**"Show all transactions as they appeared on Dec 31"**
```sql
SELECT * FROM bitemporal_fraud_events
WHERE system_from <= '2024-12-31 23:59:59'
  AND (system_to IS NULL OR system_to > '2024-12-31 23:59:59')
  AND valid_from >= '2024-12-31 00:00:00'
  AND valid_from < '2025-01-01 00:00:00';
```

## Python API

### Basic Usage

```python
from bitemporal_fraud import BitemporalFraudManager, BitemporalEvent, FraudStatus

manager = BitemporalFraudManager()

# 1. Insert initial transaction
event = BitemporalEvent(
    event_id="evt_001",
    version_id=1,
    transaction_id="TXN-001",
    amount=1500.00,
    payer="acct:alice",
    payee="acct:bob",
    valid_from=datetime(2025, 1, 15, 10, 30),
    fraud_score=0.15,
    fraud_status=FraudStatus.CLEAN
)
manager.insert_event(event)

# 2. Amend after fraud confirmed
manager.amend_event(
    event_id="evt_001",
    new_data={
        'fraud_score': 0.95,
        'fraud_status': FraudStatus.CONFIRMED_FRAUD.value
    },
    reason="Fraud confirmed after investigation",
    changed_by="fraud_analyst"
)

# 3. Time travel query
past_state = manager.get_as_of(
    event_id="evt_001",
    as_of_time=datetime(2025, 1, 15, 12, 0)
)

# 4. Get complete audit trail
trail = manager.get_audit_trail("evt_001")
for version in trail:
    print(f"v{version.version_id}: {version.fraud_status} - {version.reason_for_change}")
```

### Advanced Operations

```python
# Find late-arriving transactions
late_arrivals = manager.find_late_arrivals(delay_hours=24)

# Find all amendments since yesterday
amendments = manager.find_amendments(since=datetime.now() - timedelta(days=1))

# Reconstruct complete database state as-of specific time
historical_state = manager.reconstruct_state_at(
    as_of_time=datetime(2024, 12, 31, 23, 59)
)
```

## Real-World Use Cases

### Use Case 1: Chargeback Analysis
**Scenario**: Customer disputes $1500 transaction from 3 weeks ago

**Bitemporal Workflow**:
1. Query original transaction state when approved
2. Review fraud score at approval time
3. Create amendment with chargeback status
4. Preserve complete audit trail for dispute resolution

```python
# What did we know when we approved it?
original = manager.get_as_of("evt_001", approval_time)
print(f"Score at approval: {original.fraud_score}")  # Was 0.15 (clean)

# Now mark as reversed
manager.amend_event(
    event_id="evt_001",
    new_data={'fraud_status': FraudStatus.REVERSED.value},
    reason="Customer chargeback - dispute resolved",
    changed_by="disputes_team"
)
```

### Use Case 2: Model Performance Tracking
**Scenario**: New fraud model deployed, need to compare old vs new scores

**Bitemporal Workflow**:
1. Keep original scores with original system_from timestamp
2. Re-score transactions with new model (new version)
3. Compare score evolution over time

```sql
-- Compare model versions
SELECT
    event_id,
    version_id,
    fraud_score,
    system_from AS model_timestamp,
    reason_for_change
FROM bitemporal_fraud_events
WHERE reason_for_change LIKE '%model update%'
ORDER BY event_id, version_id;
```

### Use Case 3: Regulatory Audit
**Scenario**: Auditor asks "Show me your fraud detection state on Dec 31, 2024"

**Bitemporal Workflow**:
1. Reconstruct complete database as-of Dec 31, 11:59 PM
2. Generate report exactly as it appeared then
3. Prove no retroactive changes without audit trail

```python
year_end_state = manager.reconstruct_state_at(
    as_of_time=datetime(2024, 12, 31, 23, 59)
)

# Generate compliance report
fraud_count = sum(1 for e in year_end_state if e.fraud_status == FraudStatus.CONFIRMED_FRAUD)
total_fraud_amount = sum(e.amount for e in year_end_state if e.fraud_status == FraudStatus.CONFIRMED_FRAUD)
```

### Use Case 4: Fraud Ring Detection
**Scenario**: Multiple transactions from same device reported hours later

**Bitemporal Workflow**:
1. Detect late arrivals with similar patterns
2. Flag suspicious coordinated timing
3. Create amendments as investigation progresses

```sql
-- Find coordinated attacks via late arrivals
SELECT
    device,
    COUNT(*) AS txn_count,
    AVG(TIMESTAMPDIFF(HOUR, valid_from, system_from)) AS avg_delay,
    SUM(amount) AS total_amount
FROM bitemporal_fraud_events
WHERE system_to IS NULL
  AND TIMESTAMPDIFF(HOUR, valid_from, system_from) > 12
GROUP BY device
HAVING COUNT(*) > 5
ORDER BY total_amount DESC;
```

## Performance Considerations

### Indexes

Critical indexes for bitemporal queries:

```sql
-- Current versions (most common)
CREATE INDEX idx_bitemporal_current
    ON bitemporal_fraud_events(event_id)
    WHERE system_to IS NULL;

-- Valid time range queries
CREATE INDEX idx_bitemporal_valid_time
    ON bitemporal_fraud_events(valid_from, valid_to);

-- Transaction time (as-of queries)
CREATE INDEX idx_bitemporal_system_time
    ON bitemporal_fraud_events(system_from, system_to);
```

### Query Optimization

**DO**:
- ✓ Filter `system_to IS NULL` for current versions (uses index)
- ✓ Use views (`current_fraud_events`, `valid_fraud_events`)
- ✓ Add index hints for as-of queries
- ✓ Partition by time range if >100M rows

**DON'T**:
- ✗ Scan all versions for simple current-state queries
- ✗ Join without time filters
- ✗ Use `SELECT *` for large temporal ranges

## Integration with Existing Fraud System

### Migration Strategy

1. **Phase 1**: Add bitemporal columns to existing tables
   ```sql
   ALTER TABLE fraud_events ADD COLUMN version_id INTEGER DEFAULT 1;
   ALTER TABLE fraud_events ADD COLUMN system_from TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
   ALTER TABLE fraud_events ADD COLUMN system_to TIMESTAMP DEFAULT NULL;
   ```

2. **Phase 2**: Create current_version view
   ```sql
   CREATE VIEW current_fraud_events AS
   SELECT * FROM fraud_events WHERE system_to IS NULL;
   ```

3. **Phase 3**: Update application code to use views
   ```python
   # Old: SELECT * FROM fraud_events WHERE payer = ?
   # New: SELECT * FROM current_fraud_events WHERE payer = ?
   ```

4. **Phase 4**: Implement amendment workflow
   ```python
   # Instead of UPDATE, create new version
   manager.amend_event(event_id, new_data, reason, changed_by)
   ```

## Files in This Example

- **`schema.sql`**: Complete bitemporal schema with indexes and views
- **`example_queries.sql`**: 17 query patterns covering all use cases
- **`bitemporal_fraud.py`**: Python API with full CRUD and temporal operations
- **`README.md`**: This documentation

## Running the Example

### 1. Create Schema
```bash
# Load bitemporal schema
docker exec -i iris-fraud-embedded /usr/irissys/bin/irissession IRIS -U USER < sql/bitemporal/schema.sql
```

### 2. Run Python Example
```bash
# Run complete workflow demonstration
docker exec -e IRISUSERNAME=_SYSTEM -e IRISPASSWORD=SYS -e IRISNAMESPACE=USER \
    iris-fraud-embedded /usr/irissys/bin/irispython \
    /home/irisowner/app/examples/bitemporal/bitemporal_fraud.py
```

### 3. Try Example Queries
```bash
# Execute query examples
docker exec -i iris-fraud-embedded /usr/irissys/bin/irissession IRIS -U USER < sql/bitemporal/example_queries.sql
```

## Further Reading

- **Martin Fowler**: [Bitemporal History](https://martinfowler.com/articles/bitemporal-history.html)
- **Snodgrass**: *Developing Time-Oriented Database Applications in SQL*
- **Airweave**: Bitemporal data management patterns
- **ISO SQL:2011**: Temporal features specification

## Support

For IDFS customers:
- InterSystems WRC: [support.intersystems.com](https://support.intersystems.com)
- Financial Services Team: Contact your account manager
- Community: [InterSystems Community](https://community.intersystems.com)

---

**Version**: 1.0
**Last Updated**: January 2025
**Compatibility**: InterSystems IRIS 2024.1+
