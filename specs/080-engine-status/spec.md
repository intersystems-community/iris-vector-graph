# Feature Specification: Engine Status Snapshot

**Feature Branch**: `080-engine-status`
**Created**: 2026-04-26
**Status**: Draft

## Summary

Add `engine.status() -> EngineStatus` — an explicit, on-demand method that returns a structured snapshot of all IVG engine components. This eliminates the "why is BFS/vector search returning nothing" debugging loop by giving callers a single call that surfaces everything relevant: SQL row counts, adjacency index state, ObjectScript deployment, Arno, and index inventory.

Explicitly NOT automatic — called on demand only. Cost is ~50ms (10-15 SQL probes). Not run at init, not run before queries.

## User Scenarios & Testing

### US1 — Developer calls status() to diagnose silent empty BFS results (P1)

After loading a graph and finding BFS returns 0 results, a developer calls `engine.status()` and immediately sees `^KG empty but edges exist — run BuildKG()`. They fix it in one step.

**Acceptance**: `status().report()` includes actionable message when `adjacency.kg_populated = False` and `tables.edges > 0`.

### US2 — Operator calls status() after container restart to verify readiness (P1)

After restarting the Mindwalk container, an operator calls `engine.status()` before routing traffic. `status().ready_for_bfs` returns `False` if `^KG` wasn't rebuilt by `iris-init.sh`.

**Acceptance**: `ready_for_bfs`, `ready_for_vector_search`, `ready_for_full_text` all return correct booleans based on actual state.

### US3 — Notebook cell shows full component inventory (P2)

```python
print(engine.status().report())
```

Returns a formatted table showing all counts, flags, and index names. Human-readable in a notebook cell.

**Acceptance**: `report()` includes all components: tables, ^KG/^NKG, ObjectScript, Arno, HNSW/IVF/BM25/PLAID.

### US4 — Programmatic readiness check before running a query (P2)

```python
s = engine.status()
if not s.ready_for_bfs:
    engine.build_graph_globals()
```

**Acceptance**: Properties `ready_for_bfs`, `ready_for_vector_search`, `ready_for_full_text` usable as gates.

### Edge Cases

- Fresh engine with no data: all counts 0, all flags False — no error
- IRIS connection dead: `status()` returns `EngineStatus` with `errors` populated, does not raise
- ObjectScript not deployed: `objectscript.deployed = False`, graceful
- Arno not loaded: `arno.loaded = False`, graceful
- IVF/BM25/PLAID tables missing (older schema): empty lists, no error

## Requirements

- **FR-001**: `IRISGraphEngine.status() -> EngineStatus` MUST exist as a public method
- **FR-002**: `status()` MUST query row counts for: `nodes`, `rdf_edges`, `rdf_labels`, `rdf_props`, `kg_NodeEmbeddings`, `kg_EdgeEmbeddings`
- **FR-003**: `status()` MUST detect `^KG` population via `$Data(^KG)` using the native API; report `kg_edge_count` by counting `^KG("out",0,...)` entries
- **FR-004**: `status()` MUST detect `^NKG` population via `$Data(^NKG)`
- **FR-005**: `status()` MUST detect ObjectScript deployment via `%Dictionary.ClassDefinition` existence check for `Graph.KG.Traversal`
- **FR-006**: `status()` MUST detect Arno via `_detect_arno()` and expose `arno.capabilities` dict
- **FR-007**: `status()` MUST detect HNSW by checking if `kg_NodeEmbeddings_optimized` table has rows
- **FR-008**: `status()` MUST list IVF, BM25, PLAID indexes by querying their respective catalog tables
- **FR-009**: `status()` MUST complete in <500ms on a standard IRIS instance with <1M rows
- **FR-010**: Any probe failure MUST be caught, added to `EngineStatus.errors`, and NOT raise to the caller
- **FR-011**: `EngineStatus.report() -> str` MUST produce a human-readable multi-line string suitable for notebook output
- **FR-012**: `EngineStatus.ready_for_bfs`, `.ready_for_vector_search`, `.ready_for_full_text` MUST be computed properties
- **FR-013**: `status()` MUST NOT be called automatically at init or before queries — explicit call only

## Key Entities

- **`EngineStatus`**: Top-level result. Fields: `tables`, `adjacency`, `objectscript`, `arno`, `indexes`, `embedding_dimension`, `probe_ms`, `errors`
- **`TableCounts`**: Row counts for all 6 tables
- **`AdjacencyStatus`**: `kg_populated`, `kg_edge_count`, `kg_edge_count_capped: bool` (True if count hit the 10,000 cap — meaning actual count is ≥10,000), `nkg_populated`
- **`ObjectScriptStatus`**: `deployed`, `classes: List[str]`
- **`ArnoStatus`**: `loaded`, `capabilities: dict`
- **`IndexInventory`**: `hnsw_built`, `ivf_indexes`, `bm25_indexes`, `plaid_indexes`

## Success Criteria

- **SC-001**: `engine.status().adjacency.kg_populated` is `False` when `^KG` is empty, `True` when populated
- **SC-002**: `engine.status().report()` includes "⚠ ^KG empty but edges exist" when edges > 0 and ^KG empty
- **SC-003**: `engine.status()` completes in <500ms on a 10K node / 50K edge graph
- **SC-004**: All probe failures result in entries in `.errors`, never exceptions
- **SC-005**: `ready_for_bfs` is `True` only when both `kg_populated` and `edges > 0`
- **SC-006**: 556+ existing tests pass unmodified
