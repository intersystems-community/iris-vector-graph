# Spec 052: Turtle / RDF Import

**Branch**: `052-turtle-rdf-import`
**Created**: 2026-04-18
**Status**: Draft

## Overview

iris-vector-graph currently only accepts graph data via Python API calls (`create_node`, `create_edge`, `bulk_create_edges`) or NDJSON files. Standard RDF datasets — ontologies, knowledge bases, biomedical terminologies — are distributed in Turtle (.ttl), N-Triples (.nt), and N-Quads (.nq) formats. Adding import support means any OBO ontology, Wikidata dump, UMLS subset, or custom knowledge base can be loaded directly without format conversion.

This spec covers import only (not export, not SPARQL). The primary use case is loading standard ontologies and biomedical knowledge graphs.

## Clarifications

### Session 2026-04-18
- Q: Which formats to support? → A: Turtle (.ttl) and N-Triples (.nt) as highest priority. N-Quads (.nq) for named graph support (pairs with spec 053). JSON-LD is out of scope.
- Q: How should RDF literals be mapped? → A: String literals → rdf_props.val; typed literals strip the type annotation and store as string (e.g., `"42"^^xsd:integer` → `"42"`); language tags → stored as `val` with a separate `val_lang` property row.
- Q: What about blank nodes? → A: Generate stable synthetic IDs: `_:b{hash}` where hash is deterministic from blank node identifier within the file. Blank nodes from different files get different namespaces.

## User Scenarios & Testing

### User Story 1 — Load Turtle ontology file (P1)

```python
engine.import_rdf("go.ttl", format="turtle")
# or auto-detected:
engine.import_rdf("go.ttl")
```

OBO Gene Ontology loads: nodes for each GO term, edges for `rdfs:subClassOf`, `owl:equivalentClass`, properties for `rdfs:label`, `obo:definition`.

**Independent Test**: Load a 10-triple Turtle file; verify correct node count, edge count, and property values.

**Acceptance Scenarios**:
1. **Given** Turtle file with 3 triples `<A> <B> <C>`, **When** `import_rdf(file)`, **Then** 3 nodes created (A, B, C), 1 edge (A→B→C), correct predicates
2. **Given** Turtle with literal `<A> rdfs:label "Drug A"@en`, **When** imported, **Then** rdf_props has row `(A, "rdfs:label", "Drug A")`
3. **Given** N-Triples file `.nt`, **When** `import_rdf(file, format="nt")`, **Then** same behavior as Turtle
4. **Given** file with blank node `_:b1 <p> <o>`, **When** imported, **Then** blank node gets stable synthetic ID `_:b{hash}`

### User Story 2 — Streaming large files (P2)

```python
engine.import_rdf("wikidata-subset.nt", format="nt", batch_size=50000)
# Streams 5M triples in batches without OOM
```

**Independent Test**: Import 100K-triple N-Triples file under 60 seconds.

### Edge Cases
- File not found → clear FileNotFoundError
- Malformed Turtle → raise ParseError with line number
- Duplicate triples → idempotent (INSERT OR IGNORE semantics)
- Cyclic imports (`owl:imports`) → not followed (out of scope)
- Very long literals (>64000 chars) → truncate with warning

## Requirements

### Functional Requirements
- **FR-001**: `engine.import_rdf(path, format=None, batch_size=10000)` MUST load Turtle and N-Triples files into the graph
- **FR-002**: Format auto-detected from file extension when `format=None` (`.ttl` → turtle, `.nt` → nt, `.nq` → nquads)
- **FR-003**: RDF subject URIs → `nodes.node_id`; predicates → `rdf_edges.p`; object URIs → `rdf_edges.o_id`
- **FR-004**: RDF literal objects → `rdf_props(s, predicate_local_name, literal_value)`
- **FR-005**: Language-tagged literals MUST store the string value; language tag stored as separate property `{predicate}_lang`
- **FR-006**: Typed literals MUST store the lexical value as string (type annotation discarded with debug log)
- **FR-007**: Blank nodes MUST get deterministic synthetic IDs scoped to the import file
- **FR-008**: Import MUST be idempotent — re-importing same file produces same graph (no duplicates)
- **FR-009**: Large files MUST be processed in streaming batches — constant memory regardless of file size
- **FR-010**: Progress callback `import_rdf(..., progress=callback)` called every `batch_size` triples with `(triples_processed, elapsed_s)`

## Success Criteria
- **SC-001**: OBO Gene Ontology (50K terms, Turtle format) imports in < 120 seconds
- **SC-002**: Resulting graph is queryable via Cypher with correct node/edge/property counts
- **SC-003**: Re-importing same file produces identical graph (idempotent)
- **SC-004**: Memory usage stays below 500MB for a 1M-triple N-Triples file
