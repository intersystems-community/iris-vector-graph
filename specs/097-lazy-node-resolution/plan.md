# Implementation Plan: Spec 097 — Lazy Node Name Resolution

**Branch**: `097-lazy-node-resolution`
**Date**: 2026-05-04
**Spec**: `specs/097-lazy-node-resolution/spec.md`

## Summary

Replace `Vec<String>` (eager full-graph node name pre-load) with `NodeResolver` (lazy
demand-paged cache) across all arno `*_on_adj` algorithm functions. Zero new
infrastructure — pure refactor of existing patterns. Unblocks LDBC SF1+ scale BFS.

## Technical Context

**Language**: Rust 1.75+ (arno callout crate)  
**File**: `arno/iris-integration/arno-callout/src/kg_ffi.rs` (~2000 lines)  
**Crate features**: `zf_global` (required for GlobalRef callin API)  
**Dependencies**: `rzf`, `serde_json`, `std::collections::HashMap`  
**IRIS callin speed**: 8.8M reads/sec inside `$ZF(-5)` — hot globals are memory-speed  
**Test target**: `iris-vector-graph-enterprise` (port 2972, ARM64 enterprise IRIS)  
**IRIS_CONTAINER**: `iris-vector-graph-enterprise` (docker exec for tests)  
**arno build**: `bash build_all_arno.sh arm64` (Docker cross-compile)

## Scope

### Functions to update (all in `native_algos` mod or callers)

| Function | File location | Change |
|----------|--------------|--------|
| `read_nkg_adjacency` | ~line 980 | Return `NodeResolver` not `Vec<String>` |
| `bfs_on_adj` | ~line 1556 | `nodes: &[String]` → `resolver: &mut NodeResolver` |
| `ppr_on_adj` | ~line 1194 | same |
| WCC/CDLP/random walk/subgraph | various | same |
| `ffi_kg_bfs_compute` | ~line 1900 | pass `&mut resolver` |
| `ffi_kg_bfs_global` | ~line 1892 | pass `&mut resolver` |
| PPR/WCC/CDLP `ffi_*` callers | various | pass `&mut resolver` |

### New type: `NodeResolver` (inside `native_algos` mod)

~50 lines. `new(ns, n)` zero-cost. `name(idx)` lazy cache-on-miss. `lookup_seed(seed)` one `^NKG("$NI")` read.

## Constitution Check

- [x] Test-first: failing tests written before Rust changes
- [x] Enterprise IRIS container: `iris-vector-graph-enterprise` port 2972
- [x] `SKIP_ARNO_TESTS` env var respected in new tests
- [x] No SQL — pure global reads via callin

## Build + Deploy Sequence

1. Write failing e2e test (`test_lazy_node_resolution.py`)
2. Confirm RED on enterprise
3. Implement `NodeResolver` in `native_algos`
4. Update `read_nkg_adjacency` return type
5. Update all `*_on_adj` signatures
6. Update all `ffi_*` callers
7. Fix compilation errors (type mismatches from signature changes)
8. Build: `bash build_all_arno.sh arm64`
9. Deploy: `docker cp libarno_callout_arm64_linux.so iris-vector-graph-enterprise:/usr/irissys/mgr/libarno_callout.so`
10. Run tests on enterprise — GREEN

## Phases

### Phase 1: Failing tests
Write `tests/e2e/test_lazy_node_resolution.py` — SC-001 through SC-006.
All must fail before implementation.

### Phase 2: NodeResolver struct
Add `NodeResolver` to `native_algos` mod in `kg_ffi.rs`.

### Phase 3: read_nkg_adjacency return type
Change return from `(Vec<String>, Vec<Vec<usize>>)` to `(NodeResolver, Vec<Vec<usize>>)`.

### Phase 4: Algorithm signature updates
Update every `*_on_adj` function signature. Fix all callers.

### Phase 5: Build + deploy
Docker build, deploy to enterprise.

### Phase 6: Validation
All tests GREEN on enterprise. XL dataset BFS cold start < 500ms.
