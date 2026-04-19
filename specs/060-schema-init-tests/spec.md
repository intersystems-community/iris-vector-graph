# Spec 060: Schema Initialization Test Coverage

**Branch**: 048-unified-edge-store | **Created**: 2026-04-18

## Root Cause Analysis

Five bugs shipped in v1.53.x that `initialize_schema()` testing did not catch:

| Bug | Root Cause | Test Gap |
|-----|-----------|----------|
| 1: Non-idempotent indexes | IRIS error text "already has index named" not in suppression list | No idempotency E2E test |
| 2: iFind fails on Community | `%iFind.Index.Basic` rejected (Enterprise-only) | No Community-edition test path |
| 3: JSON_VALUE functional index | IRIS rejects functional indexes with JSON_VALUE | No test on actual IRIS |
| 4: kg_TXT/kg_RRF fail without iFind | `%FIND.Rank` not available on Community | No procedure creation smoke test |
| 5: LoadDir host path wrong | IRIS sees container path, not host path | No test of ObjectScript deployment |

All five shared one root cause: **`initialize_schema()` was only tested with mocked cursors** that return success for every `cursor.execute()` call. Real IRIS rejects specific DDL statements that the mocks silently accept.

## What Tests Are Needed

### Test 1: `initialize_schema()` completes without raising on a real container

The most basic gate. Currently absent. If this test had existed, all 5 bugs would have been caught before release.

### Test 2: `initialize_schema()` is idempotent — calling twice raises no error

Verifies Bug 1 fix. Run `initialize_schema()`, then run it again. Second call must complete cleanly.

### Test 3: Optional indexes fail gracefully without raising

`idx_props_val_ifind` (iFind) and `idx_edges_confidence` (JSON_VALUE) must not raise, even if creation fails. Bug 2 and 3.

### Test 4: All required tables exist after initialization

After `initialize_schema()`, verify `nodes`, `rdf_edges`, `rdf_labels`, `rdf_props`, `rdf_reifications`, `kg_NodeEmbeddings` all exist and are queryable.

### Test 5: Core stored procedures are callable after initialization

`kg_KNN_VEC` and `Graph_KG.MatchEdges` must exist and be callable (even with empty args). Bug 4.

### Test 6: ObjectScript capability detection is accurate

`probe_capabilities()` must return correct values — not raise even when ObjectScript classes are missing.

## Requirements

- **FR-001**: All 6 tests MUST run against the live `iris_vector_graph` container
- **FR-002**: Tests MUST be idempotent — re-running against an already-initialized schema must pass
- **FR-003**: Tests MUST pass on both IRIS Community (no iFind) and IRIS Enterprise
- **FR-004**: Test failure messages MUST name the specific table/index/procedure that failed
- **FR-005**: `SKIP_IRIS_TESTS` guard preserved

## Success Criteria

- **SC-001**: All 6 tests pass on the `iris_vector_graph` container
- **SC-002**: The 5 bugs in this spec's root cause table are all caught by at least one test
- **SC-003**: Zero regressions on existing 513 tests
