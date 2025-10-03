-- fraud/schema.sql
-- SQL schema for fraud scoring MVP (IRIS-native format)
--
-- Tables:
-- 1. gs_events - Append-only event log
-- 2. gs_labels - Ground truth fraud labels
-- 3. gs_fraud_centroid - Precomputed fraud embedding centroid
--
-- NOTE: IRIS SQL doesn't use semicolons for iris.sql.exec()
-- NOTE: COMMENT ON is not supported in IRIS SQL
-- NOTE: CHECK constraints in PRIMARY KEY not supported

GO
CREATE TABLE gs_events (
    event_id BIGINT NOT NULL IDENTITY,
    entity_id VARCHAR(256) NOT NULL,
    kind VARCHAR(32) NOT NULL,
    ts TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    amount DOUBLE,
    device_id VARCHAR(128),
    ip VARCHAR(64),
    metadata VARCHAR(1024),
    PRIMARY KEY (event_id)
)

GO
CREATE INDEX idx_gs_events_entity_ts ON gs_events(entity_id, ts DESC)

GO
CREATE INDEX idx_gs_events_ts ON gs_events(ts DESC)

GO
CREATE TABLE gs_labels (
    entity_id VARCHAR(256) NOT NULL,
    label INT NOT NULL,
    label_ts TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    source VARCHAR(64),
    PRIMARY KEY (entity_id, label_ts)
)

GO
CREATE INDEX idx_gs_labels_label ON gs_labels(label, label_ts DESC)

-- Skipping gs_fraud_centroid table - requires Vector Search license
