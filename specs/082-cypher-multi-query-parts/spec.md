# Feature Specification: Multi-Query Parts (MATCH after RETURN)

**Feature Branch**: `082-cypher-multi-query-parts`
**Created**: 2026-04-30
**Source**: GQS differential testing — ~989 crashes with "Expected EOF, got MATCH/WHERE"

## Summary

openCypher allows multiple query parts separated by RETURN/WITH clauses. A query like:

```cypher
MATCH (a)-[r]-(b) RETURN a.id
MATCH (c)-[r2]-(d) WHERE c.id = 1 RETURN d.id
```

is valid openCypher — each RETURN creates a boundary between independent query parts. GQS's oracle generates queries of this form to test differential correctness. IVG currently fails with "Expected EOF, got MATCH" after the first RETURN.

This is also the mechanism behind `CALL {} IN TRANSACTIONS` and other multi-part patterns.

## User Scenarios & Testing

### US1 — Two-part query with independent MATCH clauses (P1)

```cypher
MATCH (a)-[:R]-(b) WHERE a.id = $x RETURN a.id, b.id
MATCH (c) WHERE c.id = $y RETURN c.id
```

Both parts execute independently. Result is the final part's RETURN output.

**Acceptance**: Query parses, translates, and returns rows from the second MATCH.

### US2 — Result of first part feeds second (P1)

```cypher
MATCH (a) WHERE a.id IN $ids RETURN a.id AS aid
MATCH (b) WHERE b.id = aid RETURN b.id
```

In standard openCypher, the second part can reference variables from the first only via WITH. The RETURN-separated form resets the variable scope. IVG should handle both.

### US3 — GQS differential oracle pattern (P1)

GQS generates: `original_query MATCH (extra) WHERE condition RETURN modified_result` — the second part is the oracle's transformed version. IVG must execute it without crashing.

### Edge Cases

- Single-part query (current behavior): unchanged
- Three+ parts: `MATCH ... RETURN ... MATCH ... RETURN ... MATCH ... RETURN`
- WHERE NOT (expr) in complex positions: parser must handle negation in all contexts

## Requirements

- **FR-001**: Parser MUST accept a second MATCH clause following a RETURN clause in the same query string
- **FR-002**: Each part MUST be executed as an independent query; the final RETURN produces the result
- **FR-003**: Variable scope resets between parts (unless carried via WITH)
- **FR-004**: `WHERE NOT (expr)` MUST parse correctly in all positions (currently fails in deeply nested contexts)
- **FR-005**: GQS crash rate for "Expected EOF/WHERE" errors MUST drop to 0 after implementation

## Success Criteria

- **SC-001**: `MATCH (a) RETURN a.id MATCH (b) RETURN b.id` parses and executes without error
- **SC-002**: GQS 5-minute run produces 0 "Expected EOF, got MATCH" crashes (was ~989/1071)
- **SC-003**: All existing 567 unit tests continue to pass
- **SC-004**: `WHERE NOT (expr)` in nested positions parses correctly

## Root Cause Analysis

**Parser**: `parse_query` calls `parse_return_clause` which consumes the `RETURN` and then expects EOF. The outer `parse()` function needs to loop: after a RETURN, check if another MATCH/WITH follows and if so, parse another query part.

**WHERE NOT**: Parser's `parse_not_expression` handles `NOT expr` but nested `NOT ((...))` in complex boolean trees fails at a specific depth. Exact repro needed.

## Clarifications

- Q: Should multi-part queries return the LAST part's result, or accumulate? → A: Last part (standard openCypher behavior)
- Q: Variable scoping between parts — strict reset or passthrough? → A: Strict reset per openCypher spec; WITH is the explicit passthrough mechanism
