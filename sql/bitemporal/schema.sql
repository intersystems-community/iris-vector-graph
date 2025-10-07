-- Bitemporal Fraud Event Schema for Financial Services
--
-- This schema demonstrates bitemporal data modeling for fraud detection
-- and financial data management, critical for regulatory compliance and audit.
--
-- TWO TEMPORAL DIMENSIONS:
-- 1. VALID TIME (aka "Business Time"): When the transaction actually occurred
--    - valid_from: When the transaction started being valid in the real world
--    - valid_to: When the transaction stopped being valid (NULL = still valid)
--
-- 2. TRANSACTION TIME (aka "System Time"): When we learned about it
--    - system_from: When this row was inserted into the database
--    - system_to: When this row was superseded (NULL = current version)
--
-- USE CASES:
-- - Late-arriving transactions (settlement delays, batch processing)
-- - Corrections/amendments (chargebacks, refunds, fraud reversals)
-- - Audit queries ("What did we know at 2PM yesterday?")
-- - Regulatory reporting ("Show me all transactions as they appeared on Dec 31")
-- - Fraud investigation ("When did we first flag this transaction?")

-- Main bitemporal fraud events table
CREATE TABLE bitemporal_fraud_events (
    -- Primary key (unique across all versions)
    event_id VARCHAR(255) NOT NULL,

    -- Version tracking (multiple rows per event_id)
    version_id INTEGER NOT NULL,

    -- VALID TIME: When transaction actually occurred
    valid_from TIMESTAMP NOT NULL,
    valid_to TIMESTAMP,  -- NULL = still valid

    -- TRANSACTION TIME: When we recorded this version
    system_from TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    system_to TIMESTAMP,  -- NULL = current version

    -- Transaction details
    transaction_id VARCHAR(255) NOT NULL,
    amount DECIMAL(18, 2) NOT NULL,
    currency VARCHAR(3) DEFAULT 'USD',

    -- Graph entities
    payer VARCHAR(255) NOT NULL,
    payee VARCHAR(255) NOT NULL,
    merchant VARCHAR(255),
    device VARCHAR(255),
    ip_address VARCHAR(255),

    -- Fraud scoring
    fraud_score DECIMAL(5, 4),  -- 0.0000 to 1.0000
    fraud_status VARCHAR(50),   -- 'clean', 'suspicious', 'confirmed_fraud', 'reversed'
    risk_level VARCHAR(20),     -- 'low', 'medium', 'high', 'critical'

    -- Audit metadata
    reason_for_change VARCHAR(500),  -- Why this version was created
    changed_by VARCHAR(100),         -- Who made the change

    -- Transaction metadata
    channel VARCHAR(50),             -- 'web', 'mobile', 'pos', 'atm'
    location_country VARCHAR(2),
    metadata VARCHAR(5000),          -- JSON for additional fields

    PRIMARY KEY (event_id, version_id)
);

-- Index for current versions (most common query)
CREATE INDEX idx_bitemporal_current
    ON bitemporal_fraud_events(event_id)
    WHERE system_to IS NULL;

-- Index for valid time queries
CREATE INDEX idx_bitemporal_valid_time
    ON bitemporal_fraud_events(valid_from, valid_to);

-- Index for transaction time queries
CREATE INDEX idx_bitemporal_system_time
    ON bitemporal_fraud_events(system_from, system_to);

-- Index for fraud investigations
CREATE INDEX idx_bitemporal_fraud_status
    ON bitemporal_fraud_events(fraud_status, risk_level, valid_from);

-- Index for entity graph queries
CREATE INDEX idx_bitemporal_payer
    ON bitemporal_fraud_events(payer, valid_from);
CREATE INDEX idx_bitemporal_payee
    ON bitemporal_fraud_events(payee, valid_from);
CREATE INDEX idx_bitemporal_merchant
    ON bitemporal_fraud_events(merchant, valid_from);

-- Temporal graph edges (bitemporal relationships)
CREATE TABLE bitemporal_fraud_edges (
    edge_id VARCHAR(255) NOT NULL,
    version_id INTEGER NOT NULL,

    -- VALID TIME
    valid_from TIMESTAMP NOT NULL,
    valid_to TIMESTAMP,

    -- TRANSACTION TIME
    system_from TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    system_to TIMESTAMP,

    -- Graph relationship
    from_entity VARCHAR(255) NOT NULL,
    to_entity VARCHAR(255) NOT NULL,
    edge_type VARCHAR(100) NOT NULL,  -- 'TRANSACTED_WITH', 'PAID', 'USED_DEVICE', etc.

    -- Relationship metadata
    transaction_count INTEGER DEFAULT 1,
    total_amount DECIMAL(18, 2),
    avg_amount DECIMAL(18, 2),
    risk_score DECIMAL(5, 4),

    -- Audit
    reason_for_change VARCHAR(500),
    changed_by VARCHAR(100),

    PRIMARY KEY (edge_id, version_id)
);

CREATE INDEX idx_bitemporal_edges_current
    ON bitemporal_fraud_edges(edge_id)
    WHERE system_to IS NULL;

CREATE INDEX idx_bitemporal_edges_from
    ON bitemporal_fraud_edges(from_entity, edge_type, valid_from);

CREATE INDEX idx_bitemporal_edges_to
    ON bitemporal_fraud_edges(to_entity, edge_type, valid_from);

-- View: Current versions only (most common use case)
CREATE VIEW current_fraud_events AS
SELECT
    event_id,
    version_id,
    valid_from,
    valid_to,
    system_from,
    transaction_id,
    amount,
    currency,
    payer,
    payee,
    merchant,
    device,
    ip_address,
    fraud_score,
    fraud_status,
    risk_level,
    channel,
    location_country,
    reason_for_change,
    changed_by
FROM bitemporal_fraud_events
WHERE system_to IS NULL;

-- View: Currently valid transactions (valid now, current version)
CREATE VIEW valid_fraud_events AS
SELECT
    event_id,
    version_id,
    valid_from,
    valid_to,
    system_from,
    transaction_id,
    amount,
    currency,
    payer,
    payee,
    merchant,
    fraud_score,
    fraud_status,
    risk_level
FROM bitemporal_fraud_events
WHERE system_to IS NULL
  AND valid_from <= CURRENT_TIMESTAMP
  AND (valid_to IS NULL OR valid_to > CURRENT_TIMESTAMP);

-- Stored procedure: Get event as-of specific time
CREATE PROCEDURE get_event_as_of(
    IN p_event_id VARCHAR(255),
    IN p_as_of_time TIMESTAMP,
    OUT p_result VARCHAR(10000)
)
LANGUAGE OBJECTSCRIPT
{
    -- Return the version that was current at p_as_of_time
    SELECT TOP 1 *
    INTO :p_result
    FROM bitemporal_fraud_events
    WHERE event_id = p_event_id
      AND system_from <= p_as_of_time
      AND (system_to IS NULL OR system_to > p_as_of_time)
    ORDER BY version_id DESC
}

-- Stored procedure: Get valid events during time range
CREATE PROCEDURE get_valid_events_during(
    IN p_valid_start TIMESTAMP,
    IN p_valid_end TIMESTAMP,
    OUT p_count INTEGER
)
LANGUAGE OBJECTSCRIPT
{
    -- Return count of events valid during the time range
    SELECT COUNT(DISTINCT event_id)
    INTO :p_count
    FROM bitemporal_fraud_events
    WHERE system_to IS NULL  -- Current version only
      AND valid_from < p_valid_end
      AND (valid_to IS NULL OR valid_to > p_valid_start)
}
