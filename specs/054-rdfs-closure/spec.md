# Spec 054: RDFS/OWL Closure (Materialized Inference)

**Branch**: `048-unified-edge-store` (shipping alongside current work)
**Created**: 2026-04-18
**Status**: Draft

## Overview

When a user loads an OBO ontology or OWL file via `import_rdf()`, the raw triples are stored but transitive/hierarchical relationships are not expanded. A query for `MATCH (n)-[:rdfs__subClassOf*..10]->(m)` would work but is slow (var-length BFS). More importantly, `MATCH (n:GO_0006915)-[:rdfs__subClassOf]->(m)` misses all indirect ancestors.

This spec adds **materialized inference** — a Python function that reads the loaded graph, applies RDFS/OWL entailment rules, and writes the inferred triples back as explicit `rdf_edges` rows. No runtime inference engine; inference runs once at load time (or on demand) and the results are queryable like any other edges.

## Rules to Materialize

### Tier 1 — RDFS (always run, fast)

| Rule | Description |
|------|-------------|
| `rdfs:subClassOf` transitivity | If A ⊑ B and B ⊑ C then A ⊑ C |
| `rdfs:subPropertyOf` transitivity | If p ⊑ q and q ⊑ r then p ⊑ r |
| `rdf:type` inheritance | If x rdf:type A and A rdfs:subClassOf B then x rdf:type B |
| `rdfs:domain` | If p rdfs:domain D and x p y then x rdf:type D |
| `rdfs:range` | If p rdfs:range R and x p y then y rdf:type R |

### Tier 2 — OWL (opt-in, more expensive)

| Rule | Description |
|------|-------------|
| `owl:equivalentClass` | If A owl:equivalentClass B → add A rdfs:subClassOf B and B rdfs:subClassOf A |
| `owl:equivalentProperty` | Same for properties |
| `owl:inverseOf` | If p owl:inverseOf q and x p y then y q x |
| `owl:sameAs` | If x owl:sameAs y → copy all triples from x to y and vice versa |
| `owl:TransitiveProperty` | If p is TransitiveProperty and x p y and y p z then x p z |
| `owl:SymmetricProperty` | If p is SymmetricProperty and x p y then y p x |

## Clarifications

### Session 2026-04-18

- Q: Should inferred triples be tagged to distinguish them from asserted triples? → A: Yes — inferred triples get `qualifiers = {"inferred": true}` so they can be excluded from queries if needed. Asserted triples are unaffected.
- Q: How to handle cycles in subClassOf? → A: Standard fixed-point iteration with visited set — cycles terminate naturally.
- Q: Should this run automatically after `import_rdf()`? → A: Optional `infer=False` default on `import_rdf()`. Call `engine.materialize_inference(rules="rdfs")` explicitly, or pass `infer=True` / `infer="rdfs"` / `infer="owl"` to `import_rdf()`.

## User Scenarios & Testing

### User Story 1 — Transitive subClassOf closure (P1)

```python
engine.import_rdf("go.ttl", infer="rdfs")
# or:
engine.import_rdf("go.ttl")
engine.materialize_inference(rules="rdfs")
```

Now `MATCH (n {id:'GO:0006915'})-[:rdfs__subClassOf]->(m) RETURN m.id` returns ALL ancestors, not just direct parents. Before: 1 row. After: N rows (all transitive ancestors).

**Independent Test**: Load 3-level hierarchy A⊑B, B⊑C; after closure `MATCH (a {id:'A'})-[:rdfs__subClassOf*..5]->(m) RETURN m.id` returns both B and C. With materialisation, a direct `MATCH (a)-[:rdfs__subClassOf]->(m)` also returns C.

**Acceptance Scenarios**:
1. **Given** A⊑B, B⊑C loaded, **When** `materialize_inference(rules="rdfs")`, **Then** `rdf_edges` contains `A rdfs:subClassOf C` with `qualifiers.inferred = true`
2. **Given** cycle A⊑B, B⊑A, **When** materialize, **Then** terminates without infinite loop
3. **Given** large GO ontology (50K terms), **When** materialize RDFS, **Then** completes in < 60 seconds

### User Story 2 — OWL inverseOf (P2)

```python
engine.materialize_inference(rules="owl")
```

**Given** `ex:knows owl:inverseOf ex:knownBy`, **When** materialize, **Then** `(Bob knownBy Alice)` inferred from `(Alice knows Bob)`.

### Edge Cases
- Empty graph → no-op
- No subClassOf triples → no-op (fast exit)
- `owl:sameAs` cycles (A sameAs B, B sameAs A) → merge into single canonical node (use min ID as canonical)
- Inferred triples that already exist as asserted → skip (don't duplicate)

## Requirements

### Functional Requirements

- **FR-001**: `engine.materialize_inference(rules="rdfs")` MUST compute transitive closure of `rdfs:subClassOf` and `rdfs:subPropertyOf`, writing inferred triples to `rdf_edges` with `qualifiers={"inferred":true}`
- **FR-002**: `engine.materialize_inference(rules="rdfs")` MUST apply `rdf:type` inheritance via `rdfs:subClassOf`
- **FR-003**: `engine.materialize_inference(rules="owl")` MUST include all RDFS rules plus OWL equivalence, inverseOf, TransitiveProperty, SymmetricProperty
- **FR-004**: `engine.import_rdf(path, infer="rdfs"|"owl"|False)` MUST call `materialize_inference` after loading when `infer` is non-False
- **FR-005**: Inferred triples MUST be distinguishable — `qualifiers` field contains `{"inferred": true}`
- **FR-006**: `engine.materialize_inference()` MUST be idempotent — re-running does not create duplicate inferred triples
- **FR-007**: `engine.retract_inference()` MUST delete all rows from `rdf_edges` where `qualifiers.inferred = true`
- **FR-008**: Cycles in subClassOf/subPropertyOf MUST terminate (fixed-point with visited set)
- **FR-009**: `rules` parameter accepts `"rdfs"` (Tier 1 only) or `"owl"` (Tier 1 + Tier 2)

### Non-Functional Requirements

- **NFR-001**: RDFS closure on GO ontology (50K classes, ~200K subClassOf triples) in < 60 seconds
- **NFR-002**: No runtime inference — all inferred triples stored as explicit rows
- **NFR-003**: Zero regressions on existing tests

## Success Criteria

- **SC-001**: After RDFS materialization, `MATCH (a {id:'A'})-[:rdfs__subClassOf]->(m)` returns all transitive ancestors of A
- **SC-002**: Inferred triples can be excluded: `MATCH (a)-[r]->(b) WHERE NOT r.inferred RETURN r` returns only asserted triples
- **SC-003**: `retract_inference()` removes all inferred triples, leaving original asserted graph intact
