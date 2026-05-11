# Feature Specification: FHIR-to-KG Clinical Bridge

**Feature Branch**: `027-fhir-kg-bridge`  
**Created**: 2026-03-27  
**Updated**: 2026-05-10  
**Status**: Implementation Ready  
**Input**: READY talk unified demo design + CareConnect integration + VUMC-ISC MOU

## Context

Two concrete consumers drive this feature:

1. **CareConnect** (iris-ai) — healthcare AI demo on IRIS AI Hub with working ObjectScript tools (SearchClinicalNotes, GetPatientGraphNeighborhood, FindRelatedEvidence) calling IVG via POST /api/cypher. These informal implementations should be absorbed into IVG as first-class supported functionality.

2. **Academic Medical Center Partnership** — knowledge engineering research group interested in graph databases + FHIR as a directed graph + computer-interpretable clinical guidelines. The ISC MOU includes a testbed running IRIS for Health with real patient data.

The `_v_content` FHIR vector search parameter (fhir-017, shelved for IF team review) is a future upgrade path. The pipeline is designed to optionally use `_v_` semantic search as a pre-filter but does NOT require it. Plain FHIR search works for the initial implementation.

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

### User Story 4 - FHIR Search Tool for AI Agents (Priority: P2)

An AI agent (via MCP or fhiragent) needs to search a FHIR server for patient data — Conditions, Observations, DocumentReferences — and get a structured clinical summary suitable for graph reasoning. The tool handles FHIR REST API details (pagination, auth, resource parsing) so the agent doesn't need to know FHIR internals.

**Why this priority**: CareConnect already has this in ObjectScript (SearchClinicalNotes). The Python equivalent enables Mindwalk and any MCP-connected agent to access FHIR data.

**Acceptance Scenarios**:

1. **Given** a configured FHIR endpoint, **When** the tool searches for Conditions for patient maria-gonzalez-001, **Then** it returns a structured list of conditions with ICD-10 codes extracted.
2. **Given** a FHIR server requiring BasicAuth, **When** credentials are provided, **Then** authentication succeeds and resources are returned.
3. **Given** a FHIR server that is unreachable, **When** the tool is called, **Then** it returns an error message (not a crash) within 5 seconds.

---

### User Story 5 - Patient Graph Neighborhood for AI Agents (Priority: P2)

An AI agent needs to go from a patient ID to their relevant knowledge graph neighborhood in one call: patient → FHIR Conditions → ICD codes → KG anchors → PPR walk → ranked graph concepts. This is the Python equivalent of CareConnect's GetPatientGraphNeighborhood ObjectScript tool.

**Why this priority**: This is the core clinical reasoning primitive. Without it, agents must manually chain 4 separate calls.

**Acceptance Scenarios**:

1. **Given** patient maria-gonzalez-001 with T2 Diabetes (E11.9) and Hypertension (I10), **When** the neighborhood tool is called, **Then** it returns KG concepts ranked by relevance to those conditions.
2. **Given** a patient with no conditions in FHIR, **When** the tool is called, **Then** it returns an empty neighborhood (not an error).
3. **Given** the fhir_bridges table has no mappings loaded, **When** the tool is called, **Then** it returns empty results with a message indicating no bridge data available.

---

### User Story 6 - Patient Anchor Resolution in Cypher Queries (Priority: P3)

A developer writing Cypher queries wants to reference a patient's KG anchors without pre-resolving them. If a query includes a `fhir_patient_id` parameter, the system automatically resolves that patient's conditions to KG anchor nodes before executing the Cypher query.

**Why this priority**: Simplifies the developer experience for clinical graph queries — no need to manually chain FHIR lookup + bridge resolution before every Cypher query.

**Acceptance Scenarios**:

1. **Given** patient james-okafor-002 with CHF and Depression, **When** a Cypher query uses `patient_anchors($patient_id)`, **Then** the query resolves to the anchor nodes for that patient's conditions.
2. **Given** a Cypher query without `fhir_patient_id`, **When** executed, **Then** behavior is unchanged (backward compatible).

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
- **FR-008**: System MUST provide a FHIR search tool that queries a FHIR R4 endpoint for patient resources (Condition, Observation, DocumentReference), handles BasicAuth and unauthenticated endpoints, and returns structured clinical summaries with extracted ICD-10 codes.
- **FR-009**: System MUST provide a patient graph neighborhood tool that takes a patient ID, resolves their FHIR Conditions to KG anchors, runs PPR walk, and returns ranked graph concepts with full provenance chain (patient → condition → ICD code → KG node → PPR results).
- **FR-010**: Both tools (FR-008, FR-009) MUST be usable as MCP-compatible tool definitions for AI agent frameworks.
- **FR-011**: The Cypher query endpoint MUST accept an optional `fhir_patient_id` parameter that automatically resolves to anchor nodes before query execution.
- **FR-012**: When `fhir_bridges` table is empty or mappings yield no results, all functions MUST return empty results with a clear status message (not an error).
- **FR-013**: The unified pipeline MUST accept an optional `vector_search_param` kwarg (default None); if set to `"_v_content"`, it uses semantic pre-filtering before ICD extraction. Pipeline works without this parameter.

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

- The ICD-10-CM to MeSH crosswalk is sourced from the NLM UMLS Metathesaurus (MRCONSO table). The ingest script (`scripts/ingest/load_umls_bridges.py`) is already complete.
- FHIR patient data is accessible via standard FHIR REST API (GET /fhir/r4/Condition?patient=X). The FHIR client handles BasicAuth and unauthenticated endpoints.
- The knowledge graph (MeSH/HGNC/MONDO terms) is loaded in `Graph_KG.nodes` and `Graph_KG.rdf_edges`.
- `fhir_bridge.py` is a new file in `iris_vector_graph/` — pure Python, no new IRIS classes required.
- The feature works with iris_vector_graph v1.91.x (current version).
- The POST /api/cypher interface contract is already in production use by CareConnect and must not change.
- The `_v_content` FHIR vector search parameter is a future upgrade — the pipeline is designed to accommodate it but does not require it.

## Scope Boundaries

**In scope (Phase 1 — P1)**:
- `Graph_KG.fhir_bridges` table creation (already exists in schema.py)
- ICD-10→MeSH crosswalk ingest script (already exists: scripts/ingest/load_umls_bridges.py)
- `get_kg_anchors()` function in `iris_vector_graph/fhir_bridge.py`
- `unified_clinical_pipeline()` function in `iris_vector_graph/fhir_bridge.py`
- Unit and e2e tests with 3 synthetic demo patients as fixtures

**In scope (Phase 2 — P2)**:
- `FHIRSearchTool` — MCP-compatible FHIR REST wrapper
- `GetPatientKGNeighborhoodTool` — MCP-compatible patient→KG pipeline
- Integration with Mindwalk MCP server

**In scope (Phase 3 — P3)**:
- `patient_anchors($patient_id)` Cypher function / API parameter
- `_v_content` semantic pre-filter upgrade path

**Out of scope (future)**:
- Drug→ChEMBL bridge type
- Gene→HGNC bridge type
- Automatic bridge population from FHIR subscription events
- Full MIMIC-IV FHIR load (handled separately)
- ICU length-of-stay prediction model
- Graph-augmented feature engineering for ML

## Test Fixtures

Three synthetic patients for integration testing (no UMLS data required):

| Patient ID | Conditions | Expected KG Anchors |
|---|---|---|
| maria-gonzalez-001 | T2 Diabetes (E11.9), Hypertension (I10) | mesh:D003924, mesh:D006973 |
| james-okafor-002 | CHF (I50.9), Depression (F32.9) | mesh:D006333, mesh:D003866 |
| sarah-kim-003 | Pregnancy (O80), Anemia (D64.9) | mesh:D011247, mesh:D000740 |

Synthetic bridge entries map these ICD codes to mesh:* node IDs in the test `fhir_bridges` table. Real UMLS data is not required to run tests.

## Clarifications

### Session 2026-03-27

- Q: How should get_kg_anchors() access ICD codes — cross-namespace SQL, ICD codes as input, or bridge-only lookup? → A: ICD codes as input (Option B). Caller extracts from FHIR; IVG stays decoupled from FHIR schema.
- Q: What is the crosswalk source for ICD-10→MeSH mappings? → A: NLM UMLS Metathesaurus MRCONSO extract (user has UMLS access).
