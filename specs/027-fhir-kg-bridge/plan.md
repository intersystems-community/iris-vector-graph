# Implementation Plan: FHIR-to-KG Bridge Layer

**Branch**: `027-fhir-kg-bridge` | **Date**: 2026-03-27 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/027-fhir-kg-bridge/spec.md`

## Summary

Add a `Graph_KG.fhir_bridges` table and supporting code to connect FHIR clinical data (ICD-10 diagnosis codes) to the BEL knowledge graph (MeSH terms). Includes: schema DDL, UMLS MRCONSO ingest script, `get_kg_anchors()` library function, and a unified pipeline script that chains FHIR vector search → ICD→MeSH anchor extraction → PPR walk → literature retrieval. Target: <500ms end-to-end for the READY talk demo.

## Technical Context

**Language/Version**: Python 3.11
**Primary Dependencies**: `iris_vector_graph` (engine, operators, schema), `intersystems-irispython`
**Storage**: InterSystems IRIS — extends `Graph_KG` schema with one new table (`fhir_bridges`)
**Testing**: pytest — unit + integration + e2e against live IRIS
**Target Platform**: IRIS 2023.1+ / IRISHealth 2026.2.0AI
**External Data**: NLM UMLS Metathesaurus MRCONSO.RRF (user-provided, pipe-delimited)
**Integration**: fhir-017 FHIR vector search endpoint (DocumentReference._v_content) — consumed by the unified pipeline script, not by the library itself

## Constitution Check

- [x] A dedicated, named IRIS container (`iris-vector-graph-main`) managed by `iris-devtester` (verified: conftest.py:153/348)
- [x] An explicit e2e test phase (non-optional) covering all user stories
- [x] `SKIP_IRIS_TESTS` defaulting to `"false"` in all new test files
- [x] No hardcoded IRIS ports; all resolved via `IRISContainer.attach().get_exposed_port(1972)`
- [x] Integration tests in `tests/integration/` for SQL-layer validation (Principle IV)
- [x] Schema change (`fhir_bridges` table) is additive — no changes to existing tables

**Gate status**: PASS

## Project Structure

### Source Code (repository root)

```text
iris_vector_graph/
├── schema.py            # MODIFY: Add fhir_bridges table DDL to schema deployment
├── engine.py            # ADD: get_kg_anchors(icd_codes) method on IRISGraphEngine

scripts/ingest/
├── load_umls_bridges.py  # NEW: MRCONSO parser → fhir_bridges ingest (ICD10CM→MeSH)

scripts/demo/
├── unified_pipeline.py   # NEW: FHIR search → anchors → PPR → literature demo script

sql/
├── fhir_bridges.sql      # NEW: CREATE TABLE Graph_KG.fhir_bridges DDL

tests/
├── unit/test_fhir_bridges.py                    # NEW: 6+ unit tests
├── integration/test_fhir_bridges_integration.py  # NEW: 2+ integration tests
└── e2e/test_fhir_bridges_e2e.py                  # NEW: 3+ e2e tests
```

**Structure Decision**: `get_kg_anchors()` lives on `IRISGraphEngine` because it's a data access function, not a graph algorithm. Ingest script in `scripts/ingest/` alongside existing PubMed loaders. Unified pipeline in `scripts/demo/` alongside `end_to_end_workflow.py`.

## Complexity Tracking

No constitution violations. Schema change is additive (one new table, no existing table modifications).
