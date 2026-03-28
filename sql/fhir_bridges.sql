-- Graph_KG.fhir_bridges: maps clinical codes (ICD-10, NDC, etc.) to KG node IDs
CREATE TABLE IF NOT EXISTS Graph_KG.fhir_bridges (
    fhir_code        VARCHAR(64) %EXACT NOT NULL,
    kg_node_id       VARCHAR(256) %EXACT NOT NULL,
    fhir_code_system VARCHAR(128) NOT NULL DEFAULT 'ICD10CM',
    bridge_type      VARCHAR(64) NOT NULL DEFAULT 'icd10_to_mesh',
    confidence       FLOAT DEFAULT 1.0,
    source_cui       VARCHAR(16),
    CONSTRAINT pk_bridge PRIMARY KEY (fhir_code, kg_node_id)
);

CREATE INDEX idx_bridges_code_type ON Graph_KG.fhir_bridges (fhir_code, bridge_type);
CREATE INDEX idx_bridges_kg_node ON Graph_KG.fhir_bridges (kg_node_id);
CREATE INDEX idx_bridges_type ON Graph_KG.fhir_bridges (bridge_type);
