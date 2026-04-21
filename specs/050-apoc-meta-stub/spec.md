# Spec 050: APOC Meta Stub (LangChain Compatibility)

**Branch**: `050-apoc-meta-stub`
**Created**: 2026-04-18
**Status**: Draft

## Overview

LangChain's `Neo4jGraph` connector calls `apoc.meta.data()` immediately on connection to discover the graph schema. Currently this throws `Unknown procedure: apoc.meta.data` which causes `Neo4jGraph()` construction to fail entirely, preventing LangChain from using iris-vector-graph as a Neo4j-compatible backend.

The fix is a minimal stub: implement `CALL apoc.meta.data() YIELD ...` as a Cypher procedure that returns schema metadata derived from `rdf_labels`, `rdf_props`, and `rdf_edges`. LangChain uses the result to populate its schema string for LLM prompts — approximate schema is far better than a hard error.

Additionally, LangChain calls:
- `apoc.meta.schema()` — higher-level schema summary
- `db.schema.nodeTypeProperties()` — node property types
- `db.schema.relTypeProperties()` — relationship property types

All four need stubs.

## Clarifications

### Session 2026-04-18
- Q: How accurate does the schema need to be? → A: Best-effort from existing SQL tables. Don't scan all nodes — use a LIMIT sample. Approximate is correct for LangChain's LLM prompt use.
- Q: Should `apoc.*` be a real Cypher procedure or a SQL proc? → A: Cypher CALL procedure, same as `ivg.vector.search`. Registered in the translator dispatch.
- Q: What YIELD columns does LangChain expect? → A: `apoc.meta.data()` yields `(label, property, type, elementType, unique, index, existence)`. LangChain actually just checks it doesn't throw — the exact columns are used to build a schema string.

## User Scenarios & Testing

### User Story 1 — LangChain `Neo4jGraph()` connects successfully (P1)

```python
from langchain_community.graphs import Neo4jGraph
graph = Neo4jGraph(url="bolt://localhost:7687", username="", password="")
# Used to throw — now succeeds
print(graph.schema)  # prints approximate schema
```

**Independent Test**: `CALL apoc.meta.data() YIELD label, property, type RETURN label, property, type LIMIT 5` runs without error and returns rows.

**Acceptance Scenarios**:
1. **Given** a graph with nodes labeled `Drug` having property `name`, **When** `CALL apoc.meta.data()`, **Then** result includes a row for `Drug.name`
2. **Given** empty graph, **When** `CALL apoc.meta.data()`, **Then** returns empty result set (not error)
3. **Given** `CALL db.schema.nodeTypeProperties()`, **Then** returns rows with `nodeType`, `propertyName`, `propertyTypes`

### User Story 2 — `db.labels()` and `db.relationshipTypes()` (P2)

```cypher
CALL db.labels() YIELD label RETURN label
CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType
```

Both used by Neo4j Browser and LangChain schema discovery.

**Independent Test**: `CALL db.labels()` returns list of distinct node labels from `rdf_labels`.

### Edge Cases
- Empty graph → all procs return empty result, no error
- Very large graph (88K nodes) → sample with LIMIT 1000, don't full-scan
- Unknown APOC proc → raise clear `Unknown procedure` error (don't silently swallow)

## Requirements

### Functional Requirements
- **FR-001**: `CALL apoc.meta.data() YIELD label, property, type, elementType, unique, index, existence` MUST return one row per (label, property) combination sampled from the graph
- **FR-002**: `CALL apoc.meta.schema()` MUST return a JSON schema summary string
- **FR-003**: `CALL db.schema.nodeTypeProperties() YIELD nodeType, propertyName, propertyTypes` MUST return node property metadata
- **FR-004**: `CALL db.schema.relTypeProperties() YIELD relType, propertyName, propertyTypes` MUST return relationship property metadata
- **FR-005**: `CALL db.labels() YIELD label` MUST return all distinct node labels
- **FR-006**: `CALL db.relationshipTypes() YIELD relationshipType` MUST return all distinct relationship types
- **FR-007**: All procs MUST handle empty graphs without error
- **FR-008**: Schema sampling MUST use LIMIT to avoid full-scanning large graphs

## Success Criteria
- **SC-001**: `Neo4jGraph(url=..., username='', password='')` constructor completes without raising an exception
- **SC-002**: `graph.schema` is a non-empty string describing the graph structure
- **SC-003**: `CALL db.labels()` returns correct label list for a known test graph
