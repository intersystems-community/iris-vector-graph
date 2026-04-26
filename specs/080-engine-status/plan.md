# Implementation Plan: Engine Status Snapshot (Spec 080)

**Branch**: `080-engine-status` | **Date**: 2026-04-26

## Summary

Add `iris_vector_graph/status.py` (6 dataclasses + `EngineStatus`) and `IRISGraphEngine.status()` method. All probes are wrapped in try/except, appending to `errors` on failure. `kg_edge_count` uses capped $Order (max 10,000).

## Technical Context

- Python 3.11, IRIS DBAPI + native API (`iris.createIRIS`)
- New file: `iris_vector_graph/status.py`
- Engine changes: import + `status()` method only
- No schema changes, no new dependencies

## Constitution Check

- [x] Test-first (failing E2E before implementation)
- [x] No schema changes
- [x] Graceful degradation — errors list never raises

**Principle IV exception**: All probes are SQL + native API. Tests live in `tests/unit/` per established project convention.

## Phase 0: Research

### R-001: kg_edge_count via capped $Order

ObjectScript to count `^KG("out",0,...)` entries up to 10,000:
```objectscript
ClassMethod KGEdgeCount(maxCount As %Integer = 10000) As %Integer {
    Set count = 0
    Set s = ""
    For {
        Set s = $Order(^KG("out", 0, s))
        Quit:s=""
        Set count = count + 1
        If count >= maxCount Quit
    }
    Return count
}
```
Added as a new class method on `Graph.KG.Traversal` — avoids shipping a separate utility class.

### R-002: IVF/BM25/PLAID index catalog

- IVF: `SELECT DISTINCT name FROM Graph_KG.kg_IVFMeta` (already exists)  
- BM25: `SELECT DISTINCT name FROM Graph_KG.kg_BM25Meta`
- PLAID: `SELECT DISTINCT idx_name FROM Graph_KG.kg_PlaidMeta`
- HNSW: row count on `Graph_KG.kg_NodeEmbeddings_optimized`

All wrapped in try/except (tables may not exist in older schemas).

### R-003: ObjectScript class list

Check `%Dictionary.ClassDefinition` for existence of key classes:
`Graph.KG.Traversal`, `Graph.KG.PageRank`, `Graph.KG.IVFIndex`, `Graph.KG.BM25Index`, `Graph.KG.ArnoAccel`, `Graph.KG.Snapshot`, `Graph.KG.Dijkstra`

### R-004: $Data(^KG) / $Data(^NKG)

Use `iris.createIRIS(conn).classMethodValue("%SYSTEM.Process","$Data^KG","")` — but simpler: use embedded ObjectScript via `_iris_obj().classMethodValue("Graph.KG.Traversal","KGEdgeCount",10000)` which returns 0 if `^KG` is empty. If ObjectScript not deployed, fall back to native `iris_obj.get(["KG","out"])` check.

## Phase 1: Design

### `status.py` structure

```
iris_vector_graph/status.py
  TableCounts          — 6 int fields
  AdjacencyStatus      — kg_populated, kg_edge_count, nkg_populated
  ObjectScriptStatus   — deployed, classes: List[str]
  ArnoStatus           — loaded, capabilities: dict
  IndexInventory       — hnsw_built, ivf_indexes, bm25_indexes, plaid_indexes
  EngineStatus         — all above + embedding_dimension, probe_ms, errors
    .report() -> str
    .ready_for_bfs -> bool
    .ready_for_vector_search -> bool
    .ready_for_full_text -> bool
```

### ObjectScript: `KGEdgeCount` on `Graph.KG.Traversal`

New class method — capped $Order count. Returns 0 if `^KG` empty.

### `IRISGraphEngine.status()` probe sequence

1. Start timer
2. Table row counts (6 SQL COUNT queries, batch if possible)
3. ^KG: call `Graph.KG.Traversal.KGEdgeCount(10000)` via `_call_classmethod` — catch if not deployed
4. ^NKG: `$Data(^NKG)` via native API
5. ObjectScript classes: `%Dictionary.ClassDefinition` check for 7 classes
6. Arno: `_detect_arno()` + `_arno_capabilities`
7. HNSW: `SELECT COUNT(*) FROM kg_NodeEmbeddings_optimized`
8. IVF/BM25/PLAID: catalog queries
9. Stop timer, return `EngineStatus`

### Test Design

**Unit tests (no IRIS)**:
- `test_engine_status_report_format` — mock all probes, verify report string contains expected sections
- `test_ready_for_bfs_requires_both` — kg_populated=True AND edges>0 required
- `test_errors_captured_not_raised` — probe that raises → in errors list
- `test_report_warns_kg_empty_with_edges` — warning line present when kg empty + edges > 0

**E2E tests (live IRIS)**:
- `test_status_fresh_graph` — empty graph, all counts 0
- `test_status_after_create_edge` — edges > 0, kg_populated True
- `test_status_completes_under_500ms` — timing assertion
- `test_status_graceful_on_missing_tables` — IVF/BM25 tables missing → empty lists
