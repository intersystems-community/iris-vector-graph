# Research: Cypher Temporal Edge Filtering (039)

## Decision 1: CTE Injection Mechanism

**Decision**: `SELECT ... UNION ALL SELECT ...` CTE (not `VALUES`)
**Rationale**: IRIS SQL rejects `VALUES` inside a CTE. `UNION ALL SELECT` literals are supported.
**Verified**: Live test on `iris-vector-graph-main` — `WITH x(s,p,o,ts,w) AS (SELECT ... UNION ALL SELECT ...)` returns correct results.
**Alternative rejected**: `VALUES` → `SQLCODE -1: SELECT expected, VALUES found`.

## Decision 2: Temporal Detection Point

**Decision**: Detect at `translate_match_pattern()` in `translator.py` by scanning WHERE expression for `PropertyReference(rel.variable, 'ts')`.
**Rationale**: `rel.variable` is already available at this point; WHERE AST is fully parsed; single traversal.
**AST structure verified**:
```
BooleanExpression(AND)
  BooleanExpression(GREATER_THAN_OR_EQUAL)
    PropertyReference(variable='r', property_name='ts')
    Literal(value=1000)
  BooleanExpression(LESS_THAN_OR_EQUAL)
    PropertyReference(variable='r', property_name='ts')
    Literal(value=2000)
```

## Decision 3: `engine` Threading

**Decision**: `translate_to_sql(query, params, engine=None)` — optional engine param. `execute_cypher` passes `self`. Raise `TemporalQueryRequiresEngine` when None and temporal query detected.
**Rationale**: Keeps translator usable standalone (existing tests, direct calls); enables temporal routing through `execute_cypher` path.

## Decision 4: Any Variable Name (Clarification Q5)

**Decision**: Temporal routing triggers on any relationship variable name, not just `r`.
**Detection**: `_extract_temporal_bounds(where_expr, rel_var)` takes the relationship variable name as a parameter; checks `PropertyReference.variable == rel_var`.

## Decision 5: r.ts in RETURN Without WHERE Filter (Clarification Q1)

**Decision**: Route to rdf_edges; r.ts returns NULL; emit warning.
**Implementation**: `translate_expression` for `PropertyReference(rel_var, 'ts')` checks `context.temporal_rel_ctes`. If rel_var not in temporal_rel_ctes, return SQL NULL literal and add warning.

## Decision 6: 10K Truncation (Clarification Q3)

**Decision**: Truncate to 10,000; emit warning. No temp-table fallback.
**Implementation**: In `_build_temporal_cte`, slice `edges[:10000]` and append warning to metadata.

## Decision 7: Mixed Temporal+Static Patterns (Clarification Q2)

**Decision**: Each relationship variable routed independently. r1 (temporal) → CTE; r2 (static) → rdf_edges JOIN.
**Implementation**: `context.temporal_rel_ctes` dict tracks which rel variables have been temporally resolved. Each call to `translate_match_pattern` independently detects/routes.

## IRIS SQL Findings

- `rdf_edges` columns: `edge_id, s, p, o_id, qualifiers` (no `node_id` — current translator has a latent bug joining `rdf_props` to `e3.node_id` for `r.ts` queries; this will be fixed as a side effect)
- `UNION ALL SELECT` in CTE: ✅ supported
- `VALUES` in CTE: ❌ rejected
- Empty CTE workaround: `SELECT NULL AS s, NULL AS p, NULL AS o, NULL AS ts, NULL AS w WHERE 1=0`
