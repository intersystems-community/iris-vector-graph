# Research: FHIR-to-KG Bridge Layer

**Feature**: 027-fhir-kg-bridge | **Date**: 2026-03-27

## R1: UMLS MRCONSO Format for ICD-10-CM → MeSH Extraction

**Decision**: Parse MRCONSO.RRF to extract rows where SAB='ICD10CM' and SAB='MSH' share the same CUI (Concept Unique Identifier). This produces ICD-10-CM code → MeSH descriptor ID mappings.

**Rationale**: UMLS is the authoritative medical terminology integration system. MRCONSO links all source vocabularies via CUIs. By finding CUIs that have both an ICD10CM atom and a MSH atom, we get the official NLM-curated mapping. Expected yield: 50K-80K mappings.

**MRCONSO.RRF format** (pipe-delimited, no header):
```
CUI|LAT|TS|LUI|STT|SUI|ISPREF|AUI|SAUI|SCUI|SDUI|SAB|TTY|CODE|STR|SRL|SUPPRESS|CVF
```
Key fields: CUI (col 0), SAB (col 11), CODE (col 13), STR (col 14).

**Algorithm**:
1. First pass: collect all CUI→ICD10CM code mappings (SAB='ICD10CM')
2. Second pass: collect all CUI→MeSH descriptor ID mappings (SAB='MSH', TTY='MH' for main headings)
3. Join on CUI to produce ICD10CM→MeSH pairs

**Alternatives considered**:
- Direct ICD10CM→MeSH mapping file from CMS: Does not exist. CMS publishes ICD10CM→SNOMED mappings (GEM files), not MeSH.
- Two-hop via SNOMED: ICD10CM→SNOMED→MeSH. Higher coverage but introduces SNOMED dependency and mapping uncertainty.
- Manual curation: Not scalable to 50K+ mappings.

## R2: fhir_bridges Table Design

**Decision**: Single table with composite primary key `(fhir_code, kg_node_id)` plus `bridge_type` discriminator for multi-vocabulary support.

**Schema**:
```sql
CREATE TABLE Graph_KG.fhir_bridges (
    fhir_code        VARCHAR(64),     -- source code (e.g., "J18.9" for ICD-10)
    fhir_code_system VARCHAR(128),    -- source system URI (e.g., "ICD10CM")
    kg_node_id       VARCHAR(256),    -- target KG node (e.g., "MeSH:D011014")
    bridge_type      VARCHAR(64),     -- mapping type (e.g., "icd10_to_mesh")
    confidence       FLOAT DEFAULT 1.0,
    source_cui       VARCHAR(16),     -- UMLS CUI for provenance
    CONSTRAINT pk_bridge PRIMARY KEY (fhir_code, kg_node_id)
)
```

**Rationale**: Clarification decided `get_kg_anchors()` takes ICD codes directly (not FHIR resource IDs), so the table maps codes, not resources. `fhir_code_system` enables future bridge types (drug NDC→ChEMBL, gene→HGNC) without schema changes. `source_cui` provides UMLS provenance for auditing.

**Alternatives considered**:
- Table keyed on `fhir_resource_id`: Rejected in clarification — couples IVG to FHIR schema.
- Separate tables per bridge type: Rejected — adds schema complexity for no functional gain.

## R3: get_kg_anchors() Implementation

**Decision**: Simple SQL join between `fhir_bridges` and `Graph_KG.nodes` filtered by input ICD codes.

```sql
SELECT DISTINCT b.kg_node_id
FROM Graph_KG.fhir_bridges b
JOIN Graph_KG.nodes n ON n.node_id = b.kg_node_id
WHERE b.fhir_code IN (?, ?, ?)
  AND b.bridge_type = 'icd10_to_mesh'
```

**Rationale**: The `JOIN` with `Graph_KG.nodes` ensures we only return anchors that actually exist in the KG (edge case: MeSH term in bridge but not in BEL graph). The query is parameterized and uses the primary key index on `fhir_bridges`.

## R4: Unified Pipeline Architecture

**Decision**: The unified pipeline is a standalone script (not a library function) that orchestrates across repos. It calls:
1. fhir-017 REST API for vector search (HTTP)
2. `get_kg_anchors()` for ICD→MeSH resolution (library)
3. `kg_PAGERANK()` for PPR walk (library)
4. `kg_KNN_VEC()` for literature retrieval (library)

**Rationale**: The pipeline crosses repo boundaries (fhir-017 + iris-vector-graph). Making it a library function would couple IVG to FHIR. A script in `scripts/demo/` keeps it as a demo orchestrator that uses the library's public API.

## R5: MeSH Node ID Format in KG

**Decision**: MeSH nodes in `Graph_KG.nodes` use the format `MeSH:DXXXXXX` (e.g., `MeSH:D011014`). The ingest script must prefix MeSH descriptor IDs with `MeSH:` to match.

**Rationale**: The existing BEL graph from MindWalk uses this prefix convention. Verified by checking the existing 433-node BEL graph.
