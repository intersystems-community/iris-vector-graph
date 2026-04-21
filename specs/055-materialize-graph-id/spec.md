# Spec 055: materialize_inference Respects Named Graphs

**Created**: 2026-04-18 | **Branch**: 048-unified-edge-store

## Overview

`materialize_inference()` (v1.53.1) ignores the `graph_id` column added in v1.53.0. All inferred triples are inserted with `graph_id = NULL` (default graph) regardless of the source graph context. This means if you load an ontology into named graph `'umls'` and run inference, the inferred subClassOf triples land in the default graph — invisible to `USE GRAPH 'umls' MATCH` queries.

## Requirements

- **FR-001**: `materialize_inference(rules, graph=None)` MUST accept an optional `graph` parameter
- **FR-002**: When `graph` is specified, `_fetch_edges()` MUST filter `WHERE graph_id = ?` (or `IS NULL` for default)
- **FR-003**: Inferred triples MUST be inserted with the same `graph_id` as their source
- **FR-004**: `retract_inference(graph=None)` MUST accept optional `graph` to delete only inferred triples from a specific graph
- **FR-005**: `import_rdf(path, infer="rdfs", graph=None)` MUST pass `graph` through to `materialize_inference`
- **FR-006**: Default behavior (no `graph` arg) unchanged — runs on and inserts into NULL/default graph

## User Scenarios

```python
engine.import_rdf("umls.ttl", graph="umls", infer="rdfs")
engine.execute_cypher("USE GRAPH 'umls' MATCH (a)-[:rdfs__subClassOf]->(b) RETURN a.id, b.id")
# Now returns both asserted AND inferred triples in the umls graph
```

## Success Criteria
- Inferred triple from `graph='umls'` source has `graph_id='umls'`
- `USE GRAPH 'umls'` Cypher returns inferred triples
- Default-graph inference unchanged
