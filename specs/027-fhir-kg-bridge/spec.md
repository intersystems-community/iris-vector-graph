# Feature Specification: FHIR-to-KG Bridge Layer

**Feature Branch**: `027-fhir-kg-bridge`  
**Created**: 2026-03-27  
**Status**: Draft  
**Input**: READY talk unified demo design + MIMIC-IV integration plan

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Load ICD-10 to MeSH Crosswalk (Priority: P1)

A developer wants to connect clinical patient data (stored as FHIR Conditions with ICD-10 codes) to the biomedical knowledge graph (which uses MeSH/MONDO identifiers). They load a public ICD-10→MeSH crosswalk into a bridge table, enabling automatic mapping between the two identifier systems.

**Why this priority**: Without this mapping, FHIR patient data and the BEL knowledge graph are completely disconnected. This is the foundational link that makes the unified demo possible.

**Independent Test**: Can be tested by loading the crosswalk data, then querying for a known ICD-10 code (e.g., J18.9 Pneumonia) and verifying it maps to the correct MeSH term (D011014).

**Acceptance Scenarios**:

1. **Given** the UMLS MRCONSO-derived ICD-10→MeSH crosswalk file, **When** a developer runs the ingest script, **Then** the `Graph_KG.fhir_bridges` table contains at least 50,000 mappings with `bridge_type='icd10_to_mesh'`.
2. **Given** ICD-10 code J18.9, **When** queried via the bridge table, **Then** the result includes MeSH term D011014 (Pneumonia, Unspecified).
3. **Given** an ICD-10 code with no known MeSH mapping, **When** queried, **Then** the result is empty (no false mappings).

---

### User Story 2 - Query Patient KG Anchors from FHIR Resources (Priority: P1)

A developer wants to take a FHIR patient's conditions (ICD-10 codes) and automatically resolve them to knowledge graph node IDs, producing a set of "anchor nodes" for graph traversal. This enables the pipeline: FHIR query → extract conditions → bridge to KG → PPR walk.

**Why this priority**: This is the query-time bridge that connects a specific patient's clinical record to the graph. Without it, the unified pipeline has no starting point for graph analytics.

**Independent Test**: Can be tested by inserting a test FHIR-style patient with known ICD codes, querying for anchors, and verifying the returned KG node IDs exist in `Graph_KG.nodes`.

**Acceptance Scenarios**:

1. **Given** a patient with Condition ICD-10 codes [J18.9, E11.9], **When** a developer calls `get_kg_anchors(icd_codes=["J18.9", "E11.9"])`, **Then** the result contains the corresponding MeSH node IDs that exist in the knowledge graph.
2. **Given** ICD codes with no conditions that map to KG nodes, **When** querying anchors, **Then** the result is an empty list (no error).
3. **Given** multiple patients, **When** querying anchors for each, **Then** each patient gets their own distinct set of anchors based on their conditions.

---

### User Story 3 - Run Unified Clinical-to-Literature Pipeline (Priority: P2)

A developer wants to execute the full query pipeline: start from a clinical query (e.g., "ARDS COVID dexamethasone"), find relevant patients via FHIR vector search, extract their KG anchors, run PPR through the knowledge graph, and retrieve ranked literature — all in a single orchestrated call.

**Why this priority**: This is the demo-day story. It depends on US1 (bridge data loaded) and US2 (anchor extraction working), but is the payoff that shows the unified platform value.

**Independent Test**: Can be tested by running the pipeline with a known clinical query against pre-loaded MIMIC + BEL data, verifying the output contains ranked literature results with provenance chains back to the patient conditions.

**Acceptance Scenarios**:

1. **Given** loaded MIMIC FHIR data, ICD→MeSH bridges, and PubMed embeddings, **When** a developer runs the unified pipeline with query "pneumonia elderly immunocompromised", **Then** the result contains ranked literature with provenance showing: patient condition → MeSH term → BEL mechanism → PubMed paper.
2. **Given** the same pipeline, **When** executed end-to-end, **Then** total latency is under 500ms.
3. **Given** a query with no FHIR matches, **When** the pipeline runs, **Then** it falls back gracefully to KG-only search (no crash).

---

### Edge Cases

- What happens when an ICD-10 code maps to multiple MeSH terms? All mappings are stored; the anchor extraction returns all matching KG nodes.
- What happens when a MeSH term from the bridge doesn't exist as a node in the KG? The anchor is silently filtered out — only nodes present in `Graph_KG.nodes` are returned.
- What happens when the crosswalk file has malformed rows? The ingest script logs a warning and skips the row (no abort).
- What happens when `fhir_bridges` already has data and the ingest is re-run? The ingest is idempotent — duplicate entries are skipped via the primary key constraint.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST provide a `Graph_KG.fhir_bridges` table that maps FHIR resource identifiers to knowledge graph node IDs.
- **FR-002**: System MUST include an ingest script that loads ICD-10-CM→MeSH mappings extracted from the NLM UMLS Metathesaurus (MRCONSO table) into the bridge table.
- **FR-003**: The ingest script MUST be idempotent — re-running it does not create duplicates.
- **FR-004**: System MUST provide a `get_kg_anchors(icd_codes)` function that takes a list of ICD-10 codes and returns the set of KG node IDs linked through the bridge table, filtered to only nodes that exist in `Graph_KG.nodes`.
- **FR-005**: System MUST provide a unified pipeline function that chains FHIR vector search → anchor extraction → PPR walk → literature retrieval.
- **FR-006**: The bridge table MUST support multiple bridge types (e.g., `icd10_to_mesh`, `drug_to_chembl`, `gene_to_hgnc`) for future extensibility.
- **FR-007**: The ingest script MUST handle malformed input rows gracefully (log and skip, no abort).

### Key Entities

- **fhir_bridges**: A mapping table connecting clinical codes to KG nodes. Key attributes: fhir_code (source code), fhir_code_system (source vocabulary), kg_node_id (target KG node), bridge_type (mapping type), confidence (score), source_cui (UMLS provenance).
- **KG Anchor**: A set of knowledge graph node IDs derived from ICD-10 codes via the bridge table. The caller extracts ICD codes from FHIR; `get_kg_anchors()` handles only the ICD→MeSH→KG resolution. Used as seed nodes for PPR and subgraph extraction.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The ICD-10→MeSH crosswalk loads at least 50,000 mappings into the bridge table.
- **SC-002**: Anchor extraction for a patient with 3 ICD codes completes in under 10ms.
- **SC-003**: The unified pipeline (FHIR search → anchors → PPR → literature) completes in under 500ms end-to-end.
- **SC-004**: 100% of existing tests continue to pass after the bridge table and functions are added (zero regressions).
- **SC-005**: Bridge table ingest, anchor extraction, and unified pipeline are covered by at least 6 unit tests and 3 e2e tests.

## Assumptions

- The ICD-10-CM to MeSH crosswalk is sourced from the NLM UMLS Metathesaurus (MRCONSO table). The user downloads the UMLS release and provides the extracted file; the ingest script does not handle UMLS authentication.
- FHIR patient data is accessible via SQL queries against the IRIS FHIR tables in the same namespace (HSCUSTOM), or passed as structured input to the pipeline.
- The BEL knowledge graph (MeSH/HGNC/MONDO terms) is already loaded in `Graph_KG.nodes` and `Graph_KG.rdf_edges`.
- PubMed embeddings (143K) are already loaded in `kg_NodeEmbeddings`.
- The FHIR vector search endpoint (fhir-017) is operational and returns DocumentReference resources.
- This feature adds one new table (`fhir_bridges`) to the existing `Graph_KG` schema. No changes to existing tables.

## Scope Boundaries

**In scope (Phase 1 — READY talk)**:
- `Graph_KG.fhir_bridges` table creation
- ICD-10→MeSH crosswalk ingest script
- `get_kg_anchors()` Python function in `iris_vector_graph/engine.py`
- Unified pipeline script (FHIR search → anchors → PPR → literature)
- Unit and e2e tests

**Out of scope (future)**:
- Drug→ChEMBL bridge type
- Gene→HGNC bridge type
- Automatic bridge population from FHIR subscription events
- Full MIMIC-IV FHIR load (handled separately by the MIMIC ingest pipeline)
- ICU length-of-stay prediction model (demo slide, not library code)
- Graph-augmented feature engineering for ML (Arno k-hop sampling)

## Clarifications

### Session 2026-03-27

- Q: How should get_kg_anchors() access ICD codes — cross-namespace SQL, ICD codes as input, or bridge-only lookup? → A: ICD codes as input (Option B). Caller extracts from FHIR; IVG stays decoupled from FHIR schema.
- Q: What is the crosswalk source for ICD-10→MeSH mappings? → A: NLM UMLS Metathesaurus MRCONSO extract (user has UMLS access).
