# Spec 090: Auto-CTE Split for Deep JOIN Chains (SQLCODE -400)

## Status: Partial — see edition note

## Problem

GQS generates synthetic Cypher queries with 20-83 comma-separated MATCH patterns that translate to 20-83 SQL JOINs, triggering IRIS community edition optimizer limit (~20-24 JOINs), causing SQLCODE -400 "Fatal error occurred".

## Implementation Shipped (v1.69.0)

`_maybe_split_deep_joins()` in translator.py: when assembled SQL has >20 JOINs, no aggregates, no GROUP BY, wraps in `WITH _MR AS (...) SELECT aliases FROM _MR`. Resolves queries at 21-29 JOINs. Queries at 24+ crash inside the CTE body — IRIS limit regardless of wrapping.

## IRIS Edition Note — Action Required

The remaining -400 crashes (24-83 JOINs) are **IRIS Community Edition optimizer hard limits**, not fixable by SQL restructuring.

### 1. Enterprise IRIS test container
- Spin up `iris-enterprise-test` alongside `gqs-ivg-test`
- Enterprise IRIS handles 50+ JOIN chains without -400
- Request enterprise license key from ISC IT (Tom to file)
- Expected: -400 crashes disappear entirely

### 2. NKGAccel / Rust BFS validation on enterprise

`Graph.KG.NKGAccel.cls` requires enterprise IRIS + arno `.so`. Community silently falls back to ObjectScript BFS. Spec 079 (rust-accelerated-bfs) needs an enterprise container for full validation.

### Acceptance Criteria for Enterprise Container

1. GQS 10-min pass rate >= 99.5% (vs 98.2% community)
2. `engine.status().bfs_path` = `/usr/irissys/mgr/libarno_callout.so`
3. `engine.status().ready_for_multihop_bfs` = `True`
4. NKGAccel BFS tests pass: `pytest tests/e2e/ -k "arno or nkg_accel or bfs_rust"`
5. SQLCODE -400 crashes = 0

### Action Items
- [ ] Request enterprise license key from ISC IT
- [ ] Create `docker-compose.enterprise.yml` with enterprise IRIS + arno `.so`
- [ ] Add `IRIS_EDITION=enterprise` conftest guard for NKGAccel tests
- [ ] Re-run GQS against enterprise container
- [ ] Update spec 079 test suite for enterprise
