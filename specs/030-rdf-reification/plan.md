# Implementation Plan: RDF 1.2 Reification for KBAC

**Branch**: `030-rdf-reification` | **Date**: 2026-03-31 | **Spec**: [spec.md](./spec.md)

## Summary

Add RDF 1.2 reification — statements about edges as first-class graph entities. One new SQL table (`rdf_reifications`), one new ObjectScript class, three Python API methods. Zero changes to existing tables. Enables KBAC, provenance, confidence, and audit as graph-traversable metadata.

## Technical Context

**Language/Version**: Python 3.11 + ObjectScript
**Primary Dependencies**: `iris_vector_graph` (engine, schema, security)
**Storage**: InterSystems IRIS — extends `Graph_KG` schema with one new table
**Testing**: pytest — unit + integration + e2e against live IRIS
**Target Platform**: IRIS 2024.1+
**Constraints**: Zero changes to `rdf_edges`, `Edge.cls`, or `GraphIndex`. Edge.cls is `Final`.

## Constitution Check

- [x] Container `iris-vector-graph-main` (conftest.py:153/348)
- [x] Explicit e2e test phase (non-optional)
- [x] `SKIP_IRIS_TESTS` defaults `"false"`
- [x] No hardcoded IRIS ports
- [x] Integration tests (Principle IV)
- [x] Additive schema change

**Gate status**: PASS

## Project Structure

```text
iris_src/src/Graph/KG/
├── Reification.cls          # NEW

iris_vector_graph/
├── engine.py                # MODIFY: reify_edge, get_reifications, delete_reification + cascade
├── schema.py                # MODIFY: DDL for rdf_reifications
├── security.py              # MODIFY: add to VALID_GRAPH_TABLES

sql/
├── rdf_reifications.sql     # NEW

tests/
├── unit/test_reification.py
├── integration/test_reification_integration.py
└── e2e/test_reification_e2e.py
```

## Complexity Tracking

No constitution violations. Additive schema change.
