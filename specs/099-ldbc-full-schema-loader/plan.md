# Implementation Plan: Spec 099 — LDBC Full-Schema Loader

**Branch**: `099-ldbc-full-schema-loader`  
**Date**: 2026-05-04

## Summary

Load all 14 LDBC SNB entity types into IVG using the engine API. Enables measurement
of all 14 IC queries (currently only 5 measurable). Uses `engine.bulk_create_nodes()`
for entities+properties and `BulkIngestEdges` for relationships.

## Technical Context

**Language**: Python  
**Primary file**: `tests/benchmarks/ldbc_full_loader.py` (NEW)  
**Benchmark file**: `tests/benchmarks/ldbc_ic_benchmark.py` (NEW)  
**Engine**: `iris_vector_graph/engine.py` — `bulk_create_nodes`, `bulk_create_edges`, `execute_cypher`  
**LDBC data**: `/tmp/sf10_out/social_network-sf10-CsvBasic-LongDateFormatter/`  
**Test target**: `iris-enterprise-2026` port 4972 (embedded Python for BulkIngestEdges)

## Engine API Usage

```python
# Node creation with properties — engine manages SQL + rdf_props
engine.bulk_create_nodes([
    {"id": "person_933", "labels": ["Person"],
     "properties": {"firstName": "Alice", "lastName": "Smith", "birthday": "1985-03-15"}}
], disable_indexes=True)

# Edge creation — BulkIngestEdges for speed (writes ^KG directly)
o.classMethodString("Graph.KG.EdgeScan", "BulkIngestEdges",
    json.dumps([["person_933", "post_456"]]), "HAS_CREATOR")

# Property queries — engine Cypher
engine.execute_cypher("MATCH (p:Person {node_id:$id}) RETURN p.firstName", {"id": "person_933"})
```

## Node ID Scheme

| LDBC entity | IVG node_id | Example |
|-------------|------------|---------|
| Person | `person_{id}` | `person_933` |
| Post | `post_{id}` | `post_4947802324992` |
| Comment | `comment_{id}` | `comment_4947802324993` |
| Forum | `forum_{id}` | `forum_68719476777` |
| Tag | `tag_{id}` | `tag_0` |
| Organisation | `org_{id}` | `org_1226` |
| Place | `place_{id}` | `place_0` |

## Load Order (dependency-safe)

1. Static: Tag, Organisation, Place (no FK deps)
2. Dynamic: Person (no FK deps)
3. Dynamic: Post, Comment (need hasCreator edges after)
4. Dynamic: Forum (need hasMember edges after)
5. Edges: all relationship files in bulk

## Estimated Load Times (SF10)

| Step | Rows | Method | Est. time |
|------|------|--------|-----------|
| Person nodes | 62K | bulk_create_nodes | ~30s |
| Post nodes | 7.4M | bulk_create_nodes | ~60s |
| Comment nodes | 21.9M | bulk_create_nodes | ~180s |
| All edges (30+ files) | ~80M | BulkIngestEdges | ~10min |
| BuildKG + BuildNKG | — | — | ~5min |
| Total | — | — | ~20min |

Comment nodes at 21.9M will be the bottleneck. Consider post-only mode for faster iteration.

## IC Query Implementation Strategy

For each IC, implement as a Python function using `engine.execute_cypher()`. IC queries
that use variable-length paths require spec 100 to be deployed first. Queries that only
use 1-hop MATCH are implementable immediately.

| IC | Immediately implementable? | Blocker |
|----|--------------------------|---------|
| IC2 | Yes — 1-hop MATCH on friends | None |
| IC3 | After spec 100 | VL path [*1..2] |
| IC4 | Yes | None |
| IC5 | After spec 100 | VL path for forum membership |
| IC7 | Yes | None |
| IC8 | Yes | None |
| IC9 | Yes — date filter on 1-hop | None |
| IC11 | Yes | None |
| IC12 | After spec 100 | VL path for expertise search |
