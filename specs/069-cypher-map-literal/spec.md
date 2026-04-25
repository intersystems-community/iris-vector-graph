# Feature Specification: Cypher Map Literal Expressions

**Feature Branch**: `069-cypher-map-literal`
**Created**: 2026-04-24
**Status**: Draft

## Summary

Map literal expressions `{key: value, ...}` are a core Cypher data type used in RETURN clauses, function arguments, and node property filters. The IVG parser currently fails with a syntax error when a map literal is encountered as a standalone expression.

## User Scenarios & Testing

### US1 — Map literal in RETURN (P1)

```cypher
MATCH (n:Gene) RETURN {id: n.id, label: 'Gene'} AS obj
```
**Acceptance**: Returns a JSON object `{"id": "...", "label": "Gene"}` for each matching node.

### US2 — Map literal as constant (P1)

```cypher
RETURN {a: 1, b: 2} AS m
```
**Acceptance**: Returns `{"a": 1, "b": 2}`.

### US3 — Nested map literal (P2)

```cypher
RETURN {node: {id: n.id}, score: 0.9} AS result
```
**Acceptance**: Returns nested JSON object.

### US4 — Map with param values (P2)

```cypher
RETURN {query: $term, limit: 10} AS config
```
**Acceptance**: Params substituted in the map values.

### Edge Cases

- Empty map `{}` → empty JSON object
- Map key with spaces or special chars is invalid in openCypher (keys are identifiers)

## Requirements

- **FR-001**: Parser MUST accept `{key: expr, ...}` as a primary expression
- **FR-002**: Map literal MUST translate to `JSON_OBJECT('key', expr, ...)` in SQL (IRIS SQL function)
- **FR-003**: Values inside the map are full Cypher expressions (property refs, literals, params, nested maps)
- **FR-004**: No schema changes, no new API surface

## Success Criteria

- **SC-001**: `RETURN {a: 1, b: 2} AS m` produces correct SQL with JSON_OBJECT
- **SC-002**: `RETURN {id: n.id, label: 'Gene'} AS obj` with a matched node produces correct output
- **SC-003**: 556+ tests still pass
