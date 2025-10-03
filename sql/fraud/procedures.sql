-- fraud/procedures.sql
-- OBJECTSCRIPT stored procedures for fraud scoring
--
-- NOTE: IRIS SQL doesn't use semicolons for iris.sql.exec()
-- NOTE: Use GO separators between statements
-- NOTE: Keep it simple - OBJECTSCRIPT only, no Python inline code

GO
CREATE OR REPLACE PROCEDURE gs_ComputeFeatures(
    IN payer_id VARCHAR(256),
    OUT deg_24h INT,
    OUT tx_amt_sum_24h DOUBLE,
    OUT uniq_devices_7d INT,
    OUT risk_neighbors_1hop INT
)
LANGUAGE OBJECTSCRIPT
{
    // Simple stub - return zeros for now
    SET deg_24h = 0
    SET tx_amt_sum_24h = 0.0
    SET uniq_devices_7d = 0
    SET risk_neighbors_1hop = 0
    QUIT
}

GO
CREATE OR REPLACE PROCEDURE gs_SubgraphSample(
    IN target_tx_id VARCHAR(256),
    IN fanout1 INT,
    IN fanout2 INT,
    OUT edge_count INT
)
LANGUAGE OBJECTSCRIPT
{
    // Simple stub - return zero edges
    SET edge_count = 0
    QUIT
}
