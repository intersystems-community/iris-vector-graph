-- Bitemporal Query Examples for Financial Services
--
-- These queries demonstrate common bitemporal patterns used in
-- fraud detection, regulatory compliance, and audit scenarios.

-- ============================================================
-- SECTION 1: BASIC TEMPORAL QUERIES
-- ============================================================

-- Query 1: Get current version of all events
-- Use case: Real-time fraud dashboard
SELECT
    event_id,
    transaction_id,
    payer,
    amount,
    fraud_score,
    fraud_status,
    valid_from,
    system_from
FROM current_fraud_events
ORDER BY valid_from DESC
LIMIT 100;

-- Query 2: Get all versions of a specific transaction
-- Use case: Audit trail for disputed transaction
SELECT
    version_id,
    valid_from,
    valid_to,
    system_from,
    system_to,
    fraud_score,
    fraud_status,
    reason_for_change,
    changed_by
FROM bitemporal_fraud_events
WHERE event_id = 'txn_12345'
ORDER BY version_id;

-- Query 3: Find currently valid transactions (happening right now)
-- Use case: Real-time monitoring
SELECT
    event_id,
    transaction_id,
    payer,
    payee,
    amount,
    fraud_score,
    TIMESTAMPDIFF(MINUTE, valid_from, CURRENT_TIMESTAMP) AS minutes_ago
FROM valid_fraud_events
WHERE fraud_status IN ('suspicious', 'confirmed_fraud')
ORDER BY fraud_score DESC
LIMIT 50;

-- ============================================================
-- SECTION 2: AS-OF QUERIES (Time Travel)
-- ============================================================

-- Query 4: What did we know about fraud at 2PM yesterday?
-- Use case: Regulatory reporting, compliance audit
SELECT
    event_id,
    transaction_id,
    fraud_score,
    fraud_status,
    system_from
FROM bitemporal_fraud_events
WHERE system_from <= DATEADD(day, -1, CURRENT_TIMESTAMP)  -- As of yesterday 2PM
  AND (system_to IS NULL OR system_to > DATEADD(day, -1, CURRENT_TIMESTAMP))
  AND fraud_status != 'clean'
ORDER BY fraud_score DESC;

-- Query 5: Historical view of a customer's risk profile
-- Use case: Customer investigation, pattern analysis
WITH customer_history AS (
    SELECT
        valid_from,
        system_from,
        fraud_score,
        fraud_status,
        amount,
        version_id,
        ROW_NUMBER() OVER (PARTITION BY event_id ORDER BY version_id DESC) AS rn
    FROM bitemporal_fraud_events
    WHERE payer = 'acct:customer123'
      AND system_from <= '2025-01-01 00:00:00'
      AND (system_to IS NULL OR system_to > '2025-01-01 00:00:00')
)
SELECT * FROM customer_history
WHERE rn = 1  -- Most recent version as of Jan 1
ORDER BY valid_from DESC;

-- ============================================================
-- SECTION 3: LATE-ARRIVING TRANSACTIONS
-- ============================================================

-- Query 6: Find late-arriving transactions (delayed reporting)
-- Use case: Settlement delays, batch processing, cross-border transactions
SELECT
    event_id,
    transaction_id,
    valid_from AS actual_transaction_time,
    system_from AS when_we_learned,
    TIMESTAMPDIFF(HOUR, valid_from, system_from) AS hours_delay,
    amount,
    payer,
    fraud_score
FROM bitemporal_fraud_events
WHERE system_to IS NULL  -- Current version
  AND TIMESTAMPDIFF(HOUR, valid_from, system_from) > 24  -- More than 24h delay
ORDER BY hours_delay DESC;

-- Query 7: Detect backdated transactions (fraud indicator)
-- Use case: Fraud detection, compliance monitoring
SELECT
    event_id,
    transaction_id,
    valid_from,
    system_from,
    TIMESTAMPDIFF(DAY, valid_from, system_from) AS days_backdated,
    amount,
    payer,
    merchant,
    fraud_status
FROM bitemporal_fraud_events
WHERE system_to IS NULL
  AND valid_from < system_from
  AND TIMESTAMPDIFF(DAY, valid_from, system_from) > 7  -- More than 7 days back
  AND fraud_status != 'clean'
ORDER BY days_backdated DESC;

-- ============================================================
-- SECTION 4: CORRECTIONS AND AMENDMENTS
-- ============================================================

-- Query 8: Find all corrected/amended transactions
-- Use case: Chargeback analysis, fraud reversals
SELECT
    e1.event_id,
    e1.transaction_id,
    e1.version_id AS original_version,
    e1.fraud_status AS original_status,
    e1.fraud_score AS original_score,
    e2.version_id AS latest_version,
    e2.fraud_status AS current_status,
    e2.fraud_score AS current_score,
    e2.reason_for_change,
    e2.changed_by
FROM bitemporal_fraud_events e1
JOIN bitemporal_fraud_events e2
    ON e1.event_id = e2.event_id
    AND e2.system_to IS NULL  -- Latest version
WHERE e1.version_id = 1  -- Original version
  AND e2.version_id > 1  -- Has been amended
  AND e1.fraud_status != e2.fraud_status;

-- Query 9: Track fraud score evolution over time
-- Use case: Model performance analysis, ML debugging
SELECT
    event_id,
    version_id,
    system_from AS score_timestamp,
    fraud_score,
    fraud_status,
    reason_for_change,
    LAG(fraud_score) OVER (PARTITION BY event_id ORDER BY version_id) AS previous_score,
    fraud_score - LAG(fraud_score) OVER (PARTITION BY event_id ORDER BY version_id) AS score_change
FROM bitemporal_fraud_events
WHERE event_id IN (
    SELECT event_id
    FROM bitemporal_fraud_events
    GROUP BY event_id
    HAVING COUNT(*) > 1  -- Only events with multiple versions
)
ORDER BY event_id, version_id;

-- ============================================================
-- SECTION 5: REGULATORY AND COMPLIANCE QUERIES
-- ============================================================

-- Query 10: End-of-day reconciliation report
-- Use case: Daily regulatory reporting, SOX compliance
SELECT
    DATE(valid_from) AS transaction_date,
    COUNT(DISTINCT event_id) AS total_transactions,
    SUM(amount) AS total_amount,
    COUNT(CASE WHEN fraud_status = 'confirmed_fraud' THEN 1 END) AS fraud_count,
    SUM(CASE WHEN fraud_status = 'confirmed_fraud' THEN amount ELSE 0 END) AS fraud_amount,
    AVG(fraud_score) AS avg_fraud_score
FROM bitemporal_fraud_events
WHERE system_from <= '2025-01-31 23:59:59'  -- As of end of Jan 31
  AND (system_to IS NULL OR system_to > '2025-01-31 23:59:59')
  AND valid_from >= '2025-01-31 00:00:00'
  AND valid_from < '2025-02-01 00:00:00'
GROUP BY DATE(valid_from);

-- Query 11: Audit trail for specific transaction
-- Use case: Investigation, dispute resolution, compliance
SELECT
    version_id,
    valid_from AS transaction_time,
    system_from AS recorded_at,
    fraud_score,
    fraud_status,
    risk_level,
    reason_for_change,
    changed_by,
    CASE
        WHEN system_to IS NULL THEN 'CURRENT'
        ELSE CAST(system_to AS VARCHAR)
    END AS valid_until
FROM bitemporal_fraud_events
WHERE transaction_id = 'TXN-2025-001234'
ORDER BY version_id;

-- ============================================================
-- SECTION 6: FRAUD PATTERN DETECTION (Bitemporal Graph)
-- ============================================================

-- Query 12: Find suspicious patterns in transaction timing
-- Use case: Fraud ring detection, coordinated attacks
WITH suspicious_timing AS (
    SELECT
        payer,
        COUNT(DISTINCT event_id) AS txn_count,
        MIN(valid_from) AS first_txn,
        MAX(valid_from) AS last_txn,
        MIN(system_from) AS first_reported,
        MAX(system_from) AS last_reported,
        AVG(TIMESTAMPDIFF(HOUR, valid_from, system_from)) AS avg_delay_hours
    FROM bitemporal_fraud_events
    WHERE system_to IS NULL
      AND valid_from >= DATEADD(day, -7, CURRENT_TIMESTAMP)
    GROUP BY payer
    HAVING COUNT(DISTINCT event_id) > 10
      AND AVG(TIMESTAMPDIFF(HOUR, valid_from, system_from)) > 12
)
SELECT
    s.payer,
    s.txn_count,
    s.avg_delay_hours,
    COUNT(CASE WHEN e.fraud_status != 'clean' THEN 1 END) AS flagged_count,
    AVG(e.fraud_score) AS avg_fraud_score
FROM suspicious_timing s
JOIN bitemporal_fraud_events e ON s.payer = e.payer
WHERE e.system_to IS NULL
GROUP BY s.payer, s.txn_count, s.avg_delay_hours
HAVING AVG(e.fraud_score) > 0.5
ORDER BY avg_fraud_score DESC;

-- Query 13: Cross-reference merchant and device combinations
-- Use case: Compromised device detection, merchant fraud
SELECT
    merchant,
    device,
    COUNT(DISTINCT payer) AS unique_payers,
    COUNT(DISTINCT event_id) AS txn_count,
    SUM(amount) AS total_amount,
    AVG(fraud_score) AS avg_fraud_score,
    COUNT(CASE WHEN fraud_status = 'confirmed_fraud' THEN 1 END) AS confirmed_fraud_count
FROM bitemporal_fraud_events
WHERE system_to IS NULL  -- Current version
  AND valid_from >= DATEADD(day, -30, CURRENT_TIMESTAMP)
GROUP BY merchant, device
HAVING COUNT(DISTINCT payer) > 5  -- Multiple users on same device
  AND AVG(fraud_score) > 0.4
ORDER BY avg_fraud_score DESC, confirmed_fraud_count DESC;

-- ============================================================
-- SECTION 7: PERFORMANCE OPTIMIZATION EXAMPLES
-- ============================================================

-- Query 14: Efficient current state query using index
-- Use case: High-frequency API queries
SELECT /*+ INDEX(bitemporal_fraud_events idx_bitemporal_current) */
    event_id,
    transaction_id,
    fraud_score,
    fraud_status
FROM bitemporal_fraud_events
WHERE system_to IS NULL
  AND payer = 'acct:vip_customer'
ORDER BY valid_from DESC;

-- Query 15: Time-range query with proper index usage
-- Use case: Historical analysis, reporting
SELECT /*+ INDEX(bitemporal_fraud_events idx_bitemporal_valid_time) */
    DATE(valid_from) AS date,
    COUNT(*) AS transactions,
    AVG(fraud_score) AS avg_score
FROM bitemporal_fraud_events
WHERE system_to IS NULL
  AND valid_from >= '2025-01-01'
  AND valid_from < '2025-02-01'
GROUP BY DATE(valid_from)
ORDER BY date;

-- ============================================================
-- SECTION 8: ADVANCED BITEMPORAL OPERATIONS
-- ============================================================

-- Query 16: Reconstruct state at any point in time
-- Use case: Forensic analysis, regulatory audit
CREATE PROCEDURE reconstruct_state_at(
    IN p_as_of_time TIMESTAMP
)
LANGUAGE SQL
BEGIN
    SELECT
        event_id,
        version_id,
        transaction_id,
        payer,
        payee,
        amount,
        fraud_score,
        fraud_status,
        valid_from,
        system_from
    FROM bitemporal_fraud_events
    WHERE system_from <= p_as_of_time
      AND (system_to IS NULL OR system_to > p_as_of_time)
    ORDER BY valid_from DESC;
END;

-- Query 17: Find all changes made in last 24 hours
-- Use case: Change monitoring, audit log
SELECT
    event_id,
    version_id,
    system_from AS change_time,
    reason_for_change,
    changed_by,
    fraud_status,
    fraud_score
FROM bitemporal_fraud_events
WHERE system_from >= DATEADD(hour, -24, CURRENT_TIMESTAMP)
  AND version_id > 1  -- Exclude initial inserts
ORDER BY system_from DESC;
