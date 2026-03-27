# Implementation Plan: Subquery Clauses (CALL { ... })

**Branch**: `026-subquery-call` | **Date**: 2026-03-27 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/026-subquery-call/spec.md`

## Summary

Add `CALL { ... }` subquery support to IVG's Cypher parser and translator. Independent subqueries translate to CTEs; correlated subqueries (Phase 1) translate to scalar subqueries in the SELECT list. `IN TRANSACTIONS` is parsed but treated as a no-op. Implementation touches 4 files in the Cypher stack (ast.py, lexer.py, parser.py, translator.py) with zero new dependencies.

## Technical Context

**Language/Version**: Python 3.11
**Primary Dependencies**: `iris_vector_graph.cypher` (ast, lexer, parser, translator) — no new deps
**Storage**: InterSystems IRIS — existing `Graph_KG` schema
**Testing**: pytest — unit + integration + e2e against live IRIS
**Target Platform**: IRIS 2023.1+
**Project Type**: Single library
**Constraints**: No LATERAL joins in Phase 1 — correlated subqueries limited to single-column scalar subqueries in SELECT

## Constitution Check

- [x] A dedicated, named IRIS container (`iris-vector-graph-main`) managed by `iris-devtester` (verified: conftest.py:153/348)
- [x] An explicit e2e test phase (non-optional) covering all user stories
- [x] `SKIP_IRIS_TESTS` defaulting to `"false"` in all new test files
- [x] No hardcoded IRIS ports; all resolved via `IRISContainer.attach().get_exposed_port(1972)`
- [x] Integration tests in `tests/integration/` for SQL translation validation (Principle IV)

**Gate status**: PASS

## Project Structure

### Source Code (repository root)

```text
iris_vector_graph/cypher/
├── ast.py               # ADD: SubqueryCall dataclass; MODIFY: QueryPart.clauses union type
├── lexer.py             # ADD: TRANSACTIONS, ROWS keyword tokens (OF already exists)
├── parser.py            # ADD: parse_subquery_call; MODIFY: parse_query_part CALL disambiguation
├── translator.py        # ADD: translate_subquery_call (CTE for independent, scalar for correlated)

tests/
├── unit/test_subquery_call.py
├── integration/test_subquery_call_integration.py
└── e2e/test_subquery_call_e2e.py
```

**Structure Decision**: Extends existing Cypher stack. The parser already handles `CALL` for procedures — disambiguate `CALL identifier(...)` vs `CALL {` by peeking for `LBRACE`.

## Complexity Tracking

No constitution violations. No complexity justifications needed.
