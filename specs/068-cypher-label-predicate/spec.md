# Feature Specification: Cypher WHERE n:Label Predicate

**Feature Branch**: `068-cypher-label-predicate`
**Created**: 2026-04-24
**Status**: Draft

## Summary

The Cypher label predicate `WHERE n:Label` (and its negation `WHERE NOT n:Label`, conjunction `WHERE n:Gene AND n:Protein`) is a first-class openCypher construct that is currently rejected by the IVG parser with a syntax error. It is the standard way to filter nodes by type when the node was matched without a label restriction in the MATCH clause.

## User Scenarios & Testing

### US1 — Filter by single label in WHERE (P1)

A developer writes:
```cypher
MATCH (n) WHERE n:Gene RETURN n.id
```
**Acceptance**: Translates to `JOIN rdf_labels l ON l.s = n.node_id AND l.label = 'Gene'`, returns correct results.

### US2 — Negate label in WHERE (P1)

```cypher
MATCH (n) WHERE NOT n:Gene RETURN n.id
```
**Acceptance**: Translates to `NOT EXISTS (SELECT 1 FROM rdf_labels WHERE s = n.node_id AND label = 'Gene')`.

### US3 — Combine with other predicates (P1)

```cypher
MATCH (n) WHERE n:Gene AND n.id STARTS WITH 'TP' RETURN n.id
```
**Acceptance**: Both conditions applied; returns only Gene nodes with id starting with 'TP'.

### US4 — Multiple labels (AND, OR) (P2)

```cypher
MATCH (n) WHERE n:Gene AND n:Protein RETURN n.id
MATCH (n) WHERE n:Gene OR n:Drug RETURN n.id
```
**Acceptance**: AND generates two label joins; OR generates OR'd EXISTS subqueries.

### Edge Cases

- `WHERE n:Gene AND n:Gene` — deduplicated to one join (idempotent)
- `WHERE n:` (no label name) — parser error, not crash
- Unknown label returns empty results, not error

## Requirements

- **FR-001**: Parser MUST accept `n:Label` as a boolean expression in WHERE/WITH
- **FR-002**: `n:Label` MUST translate to `EXISTS (SELECT 1 FROM rdf_labels WHERE s = n.node_id AND label = 'Label')`
- **FR-003**: `NOT n:Label` MUST translate to `NOT EXISTS (...)`
- **FR-004**: Multiple labels joined with AND/OR MUST compose correctly with other WHERE predicates
- **FR-005**: No schema changes, no new API surface — purely parser + translator fix

## Success Criteria

- **SC-001**: `MATCH (n) WHERE n:Gene RETURN n.id` produces correct SQL and E2E results
- **SC-002**: `MATCH (n) WHERE NOT n:Gene RETURN n.id` works correctly
- **SC-003**: `MATCH (n) WHERE n:Gene AND n.id = 'x' RETURN n.id` composes correctly
- **SC-004**: 553+ tests still pass after change
