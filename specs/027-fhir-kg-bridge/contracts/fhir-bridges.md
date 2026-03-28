# Contracts: FHIR-to-KG Bridge Layer

**Feature**: 027-fhir-kg-bridge | **Date**: 2026-03-27

## Contract 1: Schema — fhir_bridges DDL

```sql
CREATE TABLE IF NOT EXISTS Graph_KG.fhir_bridges (
    fhir_code        VARCHAR(64) NOT NULL,
    kg_node_id       VARCHAR(256) NOT NULL,
    fhir_code_system VARCHAR(128) NOT NULL DEFAULT 'ICD10CM',
    bridge_type      VARCHAR(64) NOT NULL DEFAULT 'icd10_to_mesh',
    confidence       FLOAT DEFAULT 1.0,
    source_cui       VARCHAR(16),
    CONSTRAINT pk_bridge PRIMARY KEY (fhir_code, kg_node_id)
);
```

## Contract 2: Library — get_kg_anchors()

**Input**: `icd_codes: List[str]`, optional `bridge_type: str = "icd10_to_mesh"`

**Output**: `List[str]` — KG node IDs that exist in both `fhir_bridges` and `Graph_KG.nodes`

**Behavior**:
1. Query `fhir_bridges` WHERE `fhir_code IN (codes)` AND `bridge_type = type`
2. JOIN with `Graph_KG.nodes` to filter to nodes that actually exist in the KG
3. Return distinct `kg_node_id` list

**Error handling**: Empty `icd_codes` → returns `[]`. SQL errors → raises.

## Contract 3: Ingest — load_umls_bridges.py

**Input**: Path to MRCONSO.RRF file

**Behavior**:
1. First pass: collect CUI → ICD10CM code mappings (SAB='ICD10CM')
2. Second pass: collect CUI → MeSH descriptor ID mappings (SAB='MSH', TTY='MH')
3. Join on CUI to produce (icd10_code, mesh_id) pairs
4. Prefix MeSH IDs with `MeSH:` to match KG node_id format
5. INSERT OR IGNORE into `Graph_KG.fhir_bridges`
6. Log: total rows parsed, mappings found, rows inserted, rows skipped (malformed)

**Idempotency**: Uses INSERT with PK conflict handling — re-runs don't duplicate.

## Contract 4: Demo — unified_pipeline.py

**Input**: Clinical query string (e.g., "ARDS COVID dexamethasone")

**Output**: Ranked list of `{paper_id, title, score, provenance: [condition→mesh→mechanism→paper]}`

**Pipeline steps**:
1. FHIR vector search via HTTP → DocumentReference results
2. Extract ICD codes from patient Conditions (via FHIR)
3. `get_kg_anchors(icd_codes)` → KG seed nodes
4. `kg_PAGERANK(seed_entities=anchors)` → ranked KG nodes
5. `kg_KNN_VEC(top_kg_nodes)` → ranked PubMed articles
6. RRF fusion of FHIR scores + KG scores → final ranking

**Fallback**: If FHIR endpoint unavailable, skip step 1-2, use KG-only search from step 3.
