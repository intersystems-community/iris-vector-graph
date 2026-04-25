# Feature Specification: Subscript/Slice Access + DELETE Edge Fix

**Feature Branch**: `071-cypher-subscript-delete-edge`
**Created**: 2026-04-24
**Status**: Implemented

## Summary

Two fixes: (1) Subscript `list[n]`, slice `list[start..end]`, and property access `expr.key` as postfix operators on any expression; (2) `DELETE r` by relationship variable now generates correct SQL.

## Requirements
- **FR-001**: `expr[n]` → `JSON_ARRAYGET(expr, n)`
- **FR-002**: `expr[start..end]` → `JSON_ARRAY_SLICE(expr, start, end)`
- **FR-003**: `expr.key` → `JSON_VALUE(expr, '$.key')` (for map/object access)
- **FR-004**: `DELETE r` (relationship variable) emits `DELETE FROM rdf_edges WHERE (s,p,o_id) IN (SELECT ...)`

## Success Criteria
- All four patterns work; 556+ tests pass
