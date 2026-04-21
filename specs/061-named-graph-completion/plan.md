# Implementation Plan: Named Graph Completion (spec 061)

**Branch**: `061-named-graph-completion` | **Date**: 2026-04-19

## Summary

Pure Python changes — no ObjectScript. Six surgical edits across engine.py, schema.py, translator.py.

## Constitution Check
- [x] Every FR has an E2E test against `iris_vector_graph` container that fails before implementation
- [x] SKIP_IRIS_TESTS defaults "false"
- [x] No hardcoded ports
- [x] No mock-only coverage for new behavior

## Files Changed

```
iris_vector_graph/
├── engine.py         5 changes (import_rdf INSERT, bulk_create_edges, create_edge_temporal, bulk_create_edges_temporal, relTypeProperties)
└── cypher/translator.py  1 change (rdf_edges fallback when graph_context set)
iris_vector_graph/schema.py  1 change (bulk INSERT template with graph_id)
tests/unit/test_named_graphs.py  NEW — E2E tests for all 5 FRs, fail-before-pass
```

## Change 1: import_rdf — thread graph_id to INSERT (engine.py:2171)

Current:  `INSERT INTO rdf_edges (s, p, o_id) VALUES (?, ?, ?)`
Fix: when graph set, `INSERT INTO rdf_edges (s, p, o_id, graph_id) VALUES (?, ?, ?, ?)`
graph= param already in signature — just not threaded to INSERT.

## Change 2: bulk_create_edges — method-level graph= + per-edge override (engine.py:1969)

Add `graph: Optional[str] = None` to signature.
Per-edge dict key "graph" overrides method-level.
Need "rdf_edges_with_graph" bulk template in schema.py accepting 7 params [s, p, o_id, graph_id, s, p, o_id].

## Change 3: create_edge_temporal — write rdf_edges row (engine.py:3936)

Add `graph: Optional[str] = None`.
After ObjectScript InsertEdge call, also INSERT into rdf_edges with graph_id.
Temporal edges then visible to both ^KG BFS (graph-agnostic) and SQL USE GRAPH queries.

## Change 4: bulk_create_edges_temporal — write rdf_edges rows (engine.py:3965)

Add `graph: Optional[str] = None`.
After BulkInsert ObjectScript call, executemany into rdf_edges with graph_id.

## Change 5: relTypeProperties — return actual data (engine.py:1205)

Replace hardcoded empty return:
- SELECT DISTINCT p FROM rdf_edges → rel types
- For each type, sample qualifiers JSON keys
- Always include "weight"
- Return (relType, propertyName, ["STRING"], false) per pair

## Change 6: Translator — rdf_edges fallback when graph_context set (translator.py:1827)

When graph_context is not None, skip MatchEdges CTE and use rdf_edges JOIN (line 1844).
graph_id WHERE filter on rdf_edges then works correctly.
