# Implementation Plan: PLAID Multi-Vector Retrieval

**Branch**: `029-plaid-search` | **Date**: 2026-03-29 | **Spec**: [spec.md](./spec.md)

## Summary

Implement PLAID multi-vector retrieval (Santhanam et al., NAACL 2022) as a pure ObjectScript stored procedure with `$vectorop` SIMD. Build is hybrid (Python K-means + ObjectScript inverted index). Search is a single `classMethodValue` call — zero SQL, zero Python at query time. Target: <15ms search on 500 docs.

## Technical Context

**Language/Version**: Python 3.11 (build) + ObjectScript (query)
**Primary Dependencies**: `iris_vector_graph` (engine), `sklearn` (K-means at build time), `numpy`
**Storage**: InterSystems IRIS — new `^PLAID` global (independent of `^KG` and `^VecIdx`)
**Testing**: pytest — unit + integration + e2e against live IRIS
**Target Platform**: IRIS 2024.1+ (`$vectorop`, all license tiers)
**Performance**: <15ms search, <10s build (500 docs / 25K tokens)

## Constitution Check

- [x] Container `iris-vector-graph-main` managed by `iris-devtester` (conftest.py:153/348)
- [x] Explicit e2e test phase (non-optional)
- [x] `SKIP_IRIS_TESTS` defaults `"false"`
- [x] No hardcoded IRIS ports
- [x] Integration tests for global structure validation (Principle IV)
- [x] `^PLAID` independent of `^KG` / `^VecIdx` (FR-010)
- [x] No `iris-vector-rag` dependency

**Gate status**: PASS

## Project Structure

```text
iris_src/src/Graph/KG/
├── PLAIDSearch.cls          # NEW: Build, Search, Insert, Info, Drop

iris_vector_graph/
├── engine.py                # MODIFY: plaid_build, plaid_search, plaid_insert, plaid_info, plaid_drop

tests/
├── unit/test_plaid_search.py
├── integration/test_plaid_search_integration.py
└── e2e/test_plaid_search_e2e.py
```

## Complexity Tracking

No constitution violations. `^PLAID` is a new global — no existing modifications.
