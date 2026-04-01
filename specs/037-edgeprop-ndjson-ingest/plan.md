# Implementation Plan: Edge Properties + NDJSON Import/Export

**Branch**: `037-edgeprop-ndjson-ingest` | **Date**: 2026-04-01 | **Spec**: [spec.md](./spec.md)

## Summary

Add rich edge attributes (`edgeprop` global) to temporal edges and NDJSON import/export for observability datasets. Extends TemporalIndex.cls with attrs support. Adds Python `import_graph_ndjson` / `export_graph_ndjson`. Enables RCAEval and Train-Ticket benchmark ingest.

## Technical Context

**Language/Version**: Python 3.11 (build) + ObjectScript (write/query)
**Primary Dependencies**: `iris_vector_graph` (engine, schema), `intersystems-irispython`
**Storage**: `^KG("edgeprop", ts, s, p, o, key) = value` — new subscript key in existing `^KG`
**Testing**: pytest — unit + e2e against live IRIS
**Constraints**: Zero changes to existing `^KG("out/in/tout/tin/bucket")` paths

## Constitution Check

- [x] Container `iris-vector-graph-main` (conftest.py:153/348)
- [x] Explicit e2e test phase
- [x] `SKIP_IRIS_TESTS` defaults `"false"`
- [x] No hardcoded ports
- [x] Additive global writes only

**Gate status**: PASS

## Project Structure

```text
iris_src/src/Graph/KG/
├── TemporalIndex.cls      # MODIFY: InsertEdge + BulkInsert accept attrs; new GetEdgeAttrs

iris_vector_graph/
├── engine.py              # MODIFY: create_edge_temporal attrs param; import_graph_ndjson;
                           #   export_graph_ndjson; export_temporal_edges_ndjson; get_edge_attrs

tests/
├── unit/test_edgeprop_ndjson.py
└── e2e/test_edgeprop_ndjson_e2e.py
```

## Complexity Tracking

No constitution violations. Additive global writes.
