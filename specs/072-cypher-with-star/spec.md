# Feature Specification: Cypher WITH * Pass-Through

**Feature Branch**: `072-cypher-with-star`
**Status**: Implemented

## Summary
`WITH *` passes all currently-bound variables to the next query stage unchanged.
Previously failed with `ValueError: Undefined: <var>` because the stage transition
wiped all aliases when no explicit items were present.

## Requirements
- **FR-001**: `WITH *` emits `SELECT alias.col AS var` for every bound variable
- **FR-002**: Stage transition remaps all variables to the new Stage CTE
- **FR-003**: `WITH * WHERE condition` applies the condition to the star stage

## Success Criteria
- `MATCH (n) WITH * RETURN n.id` works (556+ tests pass)
