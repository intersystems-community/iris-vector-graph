# Feature Specification: Multi-Part Query Variable Scope

**Feature Branch**: `084-cypher-multi-part-scope`
**Created**: 2026-04-30
**Source**: GQS — `Undefined: a0/a1`, `SQLCODE -29 Field not found` (remaining ~200 crashes)

## Root Cause (Confirmed)

Two distinct bugs:

**Bug A — RETURN after RETURN not parsed as multi-part**:
`UNWIND [...] AS a0 RETURN a0, a1 RETURN DISTINCT a0` — the second `RETURN` immediately following the first `RETURN` is not recognised as starting a new query part. The parser hits EOF after the first RETURN+ORDER BY and raises `Expected EOF, got RETURN`.

**Bug B — Variables from part N not visible in part N+1**:
When part 1 defines `a0` via UNWIND and part 2 does `RETURN DISTINCT a0`, the translator for part 2 starts with a fresh `TranslationContext` and raises `ValueError: Undefined: a0`.

## Failing Patterns

```cypher
-- Bug A: consecutive RETURN clauses
MATCH (n)-[r]->(m) UNWIND [1,2,3] AS a0 RETURN a0, sum(a0) AS a1 RETURN DISTINCT a0

-- Bug B: variable defined in part 1, used in part 2
MATCH (n) UNWIND [(n.k1)] AS a0 RETURN a0      -- part 1 defines a0
RETURN DISTINCT a0                               -- part 2 references a0 → Undefined
```

## Requirements

- **FR-001**: Parser MUST treat `RETURN` as a valid multi-part query separator — `RETURN ... RETURN ...` parses as two parts
- **FR-002**: Variables defined via UNWIND/WITH in part N MUST be accessible in part N+1's RETURN clause
- **FR-003**: The fix MUST handle the GQS oracle pattern: `MATCH...RETURN a0,a1 RETURN DISTINCT a0` where the second part is a projection of the first part's output
- **FR-004**: All existing unit tests continue to pass

## Success Criteria

- **SC-001**: `MATCH (n) UNWIND [1,2] AS a0 RETURN a0 RETURN DISTINCT a0` parses and executes
- **SC-002**: GQS `Undefined: a0/a1` errors drop to 0
- **SC-003**: GQS `SQLCODE -29` related to missing variable columns drops significantly  
- **SC-004**: 571+ existing unit tests pass

## Implementation Notes

**Bug A fix**: Add `TokenType.RETURN` to `_QUERY_STARTERS` in `parse()`. When next token is RETURN, parse it as `parse_return_clause()` + `parse_order_by_clause()` + `parse_skip()` + `parse_limit()` and attach as a `subsequent_query` with the same `query_parts` (scope passthrough).

**Bug B fix**: In `_execute_parsed`, after executing part N, capture the column names + first row values and inject them as `input_params` into the translator context for part N+1. This is a simplified variable passthrough — only scalar columns (not node/edge columns) are passed through.

**Scope semantics**: In openCypher, `RETURN` creates a new scope boundary. Variables in part N+1's RETURN refer to the OUTPUT columns of part N. So `RETURN a0, a1 RETURN DISTINCT a0` means part 2 returns the `a0` column from part 1's result set — not a fresh query.
