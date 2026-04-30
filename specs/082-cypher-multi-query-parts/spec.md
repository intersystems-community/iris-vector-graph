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
- **FR-002**: All parts MUST be executed sequentially; only the final part's RETURN result is returned to the caller. Intermediate RETURNs execute and are discarded — this ensures mutations (MERGE, SET) in any part take effect.
- **FR-003**: Variable scope resets between parts (unless carried via WITH)
- **FR-004**: `WHERE NOT (expr)` MUST parse correctly in all positions (currently fails in deeply nested contexts)
- **FR-005**: GQS crash rate for "Expected EOF/WHERE" errors MUST drop to 0 after implementation
- **FR-006**: Any parse error in any part (including non-final parts) MUST fail the entire query immediately

## Clarifications

### Session 2026-04-30

- Q: What to do with intermediate RETURN results? → A: Execute all parts, return only the last (full openCypher semantics — mutations in all parts take effect)
- Q: Error in non-final part? → A: Fail entire query immediately on any parse error
- Q: Should multi-part queries return the LAST part's result, or accumulate? → A: Last part (standard openCypher behavior)
- Q: Variable scoping between parts — strict reset or passthrough? → A: Strict reset per openCypher spec; WITH is the explicit passthrough mechanism
