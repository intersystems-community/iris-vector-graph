# Spec 061: Named Graph Completion

**Branch**: `061-named-graph-completion`
**Created**: 2026-04-18
**Status**: Draft

## Overview

Named graphs were added in v1.53.0 but only `create_edge()` received the `graph=` parameter. Three write paths were left incomplete — and no E2E tests existed to catch it:

- `import_rdf()` — accepts `graph=` in signature but does NOT pass it to the SQL INSERT
- `bulk_create_edges()` — no `graph=` parameter; per-edge `graph` key silently ignored
- `create_edge_temporal()` — no `graph=` parameter

Additionally:
- `db.schema.relTypeProperties()` returns hardcoded empty rows — LangChain relationship schema always blank
- `USE GRAPH 'x' MATCH (a)-[r]->(b)` uses MatchEdges CTE which doesn't carry `graph_id`, so the WHERE filter can never match

**Every feature in this spec MUST have an E2E test that fails before implementation and passes after.**

## Clarifications

### Session 2026-04-18
- Q: bulk_create_edges — per-edge graph key OR method-level graph= OR both? → A: Both — method-level `graph=` is the default; per-edge `"graph"` key overrides it
- Q: create_edge_temporal — write graph_id to rdf_edges only or also ^KG temporal globals? → A: rdf_edges only; ^KG("tout") has no graph subscript in current layout (PR-B scope)
- Q: db.schema.relTypeProperties — what to return when qualifiers are missing? → A: Return `weight` always plus top-20 distinct JSON key names sampled from rdf_edges.qualifiers for each predicate
- Q: MatchEdges CTE graph_id — add to proc or fall back to rdf_edges SQL? → A: **Option B** — translator falls back to rdf_edges SQL JOIN when graph_context is set; no ObjectScript changes
- Q: bulk_create_edges_temporal with graph= — write rdf_edges or out of scope? → A: **Option A** — does write rdf_edges rows with graph_id; consistency over performance

## User Scenarios & Testing

### User Story 1 — import_rdf writes to named graph (P1)

```python
engine.import_rdf("test.ttl", graph="umls")
result = engine.execute_cypher("USE GRAPH 'umls' MATCH (a)-[r]->(b) RETURN count(r) AS c")
assert result["rows"][0][0] > 0
```

**E2E test MUST fail before implementation** (currently returns 0 because graph_id not written).

**Acceptance Scenarios**:
1. `import_rdf(path, graph="g1")` → all rdf_edges rows have graph_id = "g1"
2. `import_rdf(path)` → rows have graph_id = NULL (backward compat)
3. `import_rdf(path, graph="g1", infer="rdfs")` → inferred triples also have graph_id = "g1"

### User Story 2 — bulk_create_edges respects graph (P1)

```python
engine.bulk_create_edges([
    {"source_id": "A", "predicate": "R", "target_id": "B"},
    {"source_id": "C", "predicate": "R", "target_id": "D", "graph": "override"},
], graph="default_g")
# A→B in "default_g", C→D in "override"
```

**E2E test MUST fail before implementation** (graph= ignored, both edges in NULL graph).

**Acceptance Scenarios**:
1. Method-level graph= applies when no per-edge key present
2. Per-edge "graph" key overrides method-level graph=
3. graph=None (default) → graph_id = NULL

### User Story 3 — create_edge_temporal respects graph (P2)

```python
engine.create_edge_temporal("svc:auth", "CALLS_AT", "svc:db", timestamp=now, graph="prod")
result = engine.execute_cypher("USE GRAPH 'prod' MATCH (a)-[r]->(b) RETURN type(r)")
# CALLS_AT must appear
```

**E2E test MUST fail before implementation**.

**Acceptance Scenarios**:
1. create_edge_temporal(..., graph="g1") → rdf_edges row has graph_id = "g1"
2. bulk_create_edges_temporal([...], graph="g1") → all rows have graph_id = "g1"
3. No graph= → NULL (default, no regression)

### User Story 4 — USE GRAPH filters MatchEdges correctly (P1)

```python
engine.create_edge("A", "R", "B", graph="g1")
engine.create_edge("A", "R", "C", graph="g2")
result = engine.execute_cypher("USE GRAPH 'g1' MATCH (a {id:'A'})-[r]->(b) RETURN b.id")
assert result["rows"] == [["B"]]  # C must not appear
```

**E2E test MUST fail before implementation** (currently returns both B and C — cross-graph leak).

### User Story 5 — db.schema.relTypeProperties returns actual data (P2)

```python
engine.create_edge("X", "TREATS", "Y")
cursor.execute("CALL db.schema.relTypeProperties()")
rows = cursor.fetchall()
# at least one row with relType="TREATS"
```

**E2E test MUST fail before implementation** (currently always empty).

### Edge Cases
- import_rdf blank nodes get same graph_id as other triples in that import
- list_graphs() returns graph populated by all three write paths
- drop_graph() removes edges from all write paths
- All new graph= params default to None (backward compatible)

## Requirements

- **FR-001**: import_rdf(path, graph=None) MUST write graph_id to every rdf_edges INSERT
- **FR-002**: bulk_create_edges(edges, graph=None) MUST apply method-level graph= with per-edge override
- **FR-003**: create_edge_temporal(..., graph=None) MUST accept graph= and write to rdf_edges
- **FR-004**: bulk_create_edges_temporal(edges, graph=None) MUST accept same pattern as FR-002
- **FR-005**: MatchEdges CTE MUST include graph_id column from rdf_edges
- **FR-006**: USE GRAPH 'x' WHERE clause MUST filter on graph_id of MatchEdges CTE result
- **FR-007**: db.schema.relTypeProperties() MUST return weight + sampled JSON qualifier keys per predicate
- **FR-008**: All new graph= parameters default to None (backward compatible)

## Success Criteria
- **SC-001**: import_rdf(path, graph="g") → USE GRAPH 'g' returns imported triples
- **SC-002**: bulk_create_edges(edges, graph="g") → USE GRAPH 'g' returns bulk edges
- **SC-003**: create_edge_temporal(..., graph="g") → USE GRAPH 'g' returns temporal edge
- **SC-004**: USE GRAPH 'g1' does NOT return edges from 'g2' (no cross-graph leak)
- **SC-005**: CALL db.schema.relTypeProperties() returns non-empty rows
- **SC-006**: 519+ tests pass with zero regressions
