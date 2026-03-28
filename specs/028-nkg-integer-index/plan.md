# Implementation Plan: NICHE Knowledge Graph Integer Index (^NKG)

**Branch**: `028-nkg-integer-index` | **Date**: 2026-03-28 | **Spec**: [spec.md](./spec.md)
**Input**: arno integration request + spec from `/specs/028-nkg-integer-index/spec.md`

## Summary

Update the existing `Graph.KG.GraphIndex` functional index to dual-write both `^KG` (string-subscripted, backward compatible) and `^NKG` (integer-encoded, for arno acceleration). Add `InternNode` and `InternLabel` classmethods with fine-grained locking. Update `Traversal.BuildKG()` with a batch `^NKG` encoding pass. Pure ObjectScript — no Python changes.

## Technical Context

**Language/Version**: ObjectScript (IRIS 2025.1+)
**Primary Dependencies**: None — pure ObjectScript over globals
**Storage**: `^NKG` global (new), `^KG` global (existing, maintained for backward compat)
**Testing**: pytest e2e against live IRIS via native API bridge (`classMethodValue`, `$Get` on globals)
**Target Platform**: IRIS 2025.1+ / IRISHealth 2026.2.0AI
**Constraints**: Fine-grained locking for concurrent InternNode/InternLabel. Integer encoding rule: label index N → subscript -(N+1).

## Constitution Check

- [x] A dedicated, named IRIS container (`iris-vector-graph-main`) managed by `iris-devtester`
- [x] An explicit e2e test phase (non-optional) covering all user stories
- [x] `SKIP_IRIS_TESTS` defaulting to `"false"` in all new test files
- [x] No hardcoded IRIS ports; all resolved via `IRISContainer.attach().get_exposed_port(1972)`
- [x] Integration tests for ObjectScript method verification (Principle IV)
- [x] No schema changes to existing SQL tables — only global writes

**Gate status**: PASS

## Project Structure

### Source Code (repository root)

```text
iris_src/src/Graph/KG/
├── GraphIndex.cls     # MODIFY: Add InternNode, InternLabel; update InsertIndex, DeleteIndex, UpdateIndex for ^NKG dual-write
├── Traversal.cls      # MODIFY: Add BuildNKG batch pass at end of BuildKG()

tests/
├── unit/test_nkg_index.py                    # NEW: 6+ unit tests
├── integration/test_nkg_index_integration.py  # NEW: 2+ integration tests
└── e2e/test_nkg_index_e2e.py                  # NEW: 3+ e2e tests
```

**Structure Decision**: Add InternNode/InternLabel directly to `GraphIndex.cls` — they're utility methods for the functional index. The `BuildKG()` NKG pass goes in `Traversal.cls` where `BuildKG()` already lives.

## Complexity Tracking

No constitution violations. No complexity justifications needed.
