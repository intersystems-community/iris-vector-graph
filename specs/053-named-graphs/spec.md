# Spec 053: Named Graphs

**Branch**: `053-named-graphs`
**Created**: 2026-04-18
**Status**: Draft

## Overview

iris-vector-graph currently has a single flat graph namespace. All nodes, edges, and properties coexist without any way to distinguish which dataset, tenant, or version they belong to. Named graphs — a core RDF 1.2 and Neo4j 5+ concept — solve this by adding a graph identifier as a fourth dimension to triples (making quads).

Use cases:
- **Multi-tenant**: Each customer's data in a named graph; queries scoped by tenant
- **Multi-dataset**: Separate named graphs for different ontologies (GO, UMLS, custom)
- **Versioning**: `graph:v1`, `graph:v2` for schema migration
- **Provenance**: Track which import batch or source each edge came from

This spec adds named graph support to the schema, engine API, and Cypher translator.

## Clarifications

### Session 2026-04-18
- Q: Should named graphs be enforced or optional? → A: Optional — existing single-graph usage continues to work unchanged. Named graph is `NULL` = default graph. Queries without graph context see all graphs (merged view).
- Q: Cypher syntax for graph context? → A: `MATCH (n) IN GRAPH 'myGraph'` or `USE GRAPH 'myGraph' MATCH (n)`. Both forms supported; `USE GRAPH` is the openCypher 2.0 proposed syntax.
- Q: Can nodes exist in multiple graphs? → A: Yes — a node is global; edges are graph-scoped. `MATCH (a)-[r]->(b) IN GRAPH 'g'` means the edge belongs to graph `g`, not the nodes.

## User Scenarios & Testing

### User Story 1 — Write edges to a named graph (P1)

```python
engine.create_edge("Drug:Metformin", "TREATS", "Disease:T2D", graph="umls")
engine.bulk_create_edges(edges, graph="go-ontology")
```

Edges tagged with a graph identifier. Default graph (`graph=None`) is unchanged.

**Independent Test**: `create_edge(A, R, B, graph='test')` → `rdf_edges` row has `graph_id = 'test'`.

**Acceptance Scenarios**:
1. **Given** edge created with `graph='umls'`, **When** `MATCH (a)-[r]->(b) IN GRAPH 'umls'`, **Then** edge returned
2. **Given** edge with `graph='umls'` and edge with `graph='go'` both from node A, **When** `MATCH (a)-[r]->(b)` (no graph filter), **Then** both edges returned (merged view)
3. **Given** `USE GRAPH 'umls' MATCH (a)-[r]->(b)`, **Then** only `umls` edges returned

### User Story 2 — List and drop named graphs (P1)

```python
graphs = engine.list_graphs()
# ['umls', 'go-ontology', 'custom']

engine.drop_graph('go-ontology')
# removes all edges in that graph; nodes are NOT removed (they may be in other graphs)
```

**Independent Test**: `list_graphs()` returns correct list after creating edges in 3 graphs.

### User Story 3 — Named graph in Cypher (P2)

```cypher
USE GRAPH 'umls'
MATCH (a {id: 'Drug:Metformin'})-[r]->(b)
RETURN type(r), b.id
```

**Independent Test**: Query returns only edges from the specified graph.

### User Story 4 — N-Quads import (P3, pairs with spec 052)

```python
engine.import_rdf("wikidata.nq", format="nq")
# Each quad's graph URI becomes the graph_id
```

### Edge Cases
- `drop_graph('nonexistent')` → no-op, no error
- `MATCH` without `IN GRAPH` → merged view of all graphs (existing behavior preserved)
- Node with edges in multiple graphs → node appears once; each edge filtered by graph
- `graph_id` of `None` or `''` → treated as default graph

## Requirements

### Functional Requirements
- **FR-001**: `rdf_edges` schema gains nullable `graph_id VARCHAR(256)` column (NULL = default graph)
- **FR-002**: `create_edge(s, p, o, graph=None)` MUST accept optional `graph` parameter
- **FR-003**: `bulk_create_edges(edges, graph=None)` MUST accept optional `graph` parameter; individual edges may also carry `graph` key
- **FR-004**: `create_edge_temporal(s, p, o, ts, ..., graph=None)` MUST accept optional `graph` parameter
- **FR-005**: `engine.list_graphs()` MUST return list of distinct non-null graph IDs
- **FR-006**: `engine.drop_graph(graph_id)` MUST delete all edges with that graph_id; nodes and rdf_props are NOT deleted
- **FR-007**: `IN GRAPH 'name'` Cypher clause MUST filter edges by graph_id in the generated SQL
- **FR-008**: `USE GRAPH 'name'` Cypher prefix MUST set default graph context for the entire query
- **FR-009**: Queries without graph context MUST return merged view (all graphs, NULL=default included)
- **FR-010**: `BuildKG()` and `MatchEdges` MUST include graph_id in `^KG` subscript when non-null; default graph uses shard-0 with no graph subscript (backward compatible)
- **FR-011**: Schema migration: `initialize_schema()` adds `graph_id` column to existing `rdf_edges` table idempotently

## Success Criteria
- **SC-001**: Existing queries without graph context return same results after migration (zero regression)
- **SC-002**: Edges in named graph `'g1'` are invisible to `MATCH` query scoped to `'g2'`
- **SC-003**: `list_graphs()` → `drop_graph()` → `list_graphs()` cycle works correctly
- **SC-004**: N-Quads file with 3 distinct graph URIs loads into 3 named graphs queryable independently
