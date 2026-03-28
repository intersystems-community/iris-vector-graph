# Data Model: FHIR-to-KG Bridge Layer

**Feature**: 027-fhir-kg-bridge | **Date**: 2026-03-27

## New Table: Graph_KG.fhir_bridges

```
fhir_bridges
├── fhir_code: VARCHAR(64)          # PK part 1 — source code (e.g., "J18.9")
├── kg_node_id: VARCHAR(256)        # PK part 2 — target KG node (e.g., "MeSH:D011014")
├── fhir_code_system: VARCHAR(128)  # Source system (e.g., "ICD10CM", "NDC", "HGNC")
├── bridge_type: VARCHAR(64)        # Mapping type (e.g., "icd10_to_mesh")
├── confidence: FLOAT               # Mapping confidence (default 1.0)
└── source_cui: VARCHAR(16)         # UMLS CUI for provenance tracking
```

**Primary Key**: `(fhir_code, kg_node_id)` — composite, ensures no duplicate mappings.

**Relationships**:
- `kg_node_id` → `Graph_KG.nodes.node_id` (FK: bridge targets must exist in KG for anchors to be returned, but no hard FK constraint — filtered at query time via JOIN)

**Indexes**:
- PK index covers lookups by `fhir_code` (the primary access pattern for `get_kg_anchors()`)
- Optional: index on `bridge_type` for multi-type queries

## Modified Entity: IRISGraphEngine

```
IRISGraphEngine (existing, extended)
└── get_kg_anchors(icd_codes: List[str], bridge_type: str = "icd10_to_mesh") → List[str]
```

Takes ICD-10 codes, returns KG node IDs that exist in `Graph_KG.nodes`. Caller is responsible for extracting ICD codes from FHIR — IVG stays decoupled from FHIR schema.

## No Changes to Existing Tables

`Graph_KG.nodes`, `Graph_KG.rdf_edges`, `Graph_KG.rdf_labels`, `Graph_KG.rdf_props`, `Graph_KG.kg_NodeEmbeddings` — all unchanged.
