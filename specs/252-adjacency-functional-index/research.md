<!-- markdownlint-disable MD013 MD060 -->

# Research: the Adjacency Index as a functional index

2026-10-19. Read-only inventory of every `^KG("out"/"in"/"label")` writer (code reading, file:line), plus one live probe.

## Probe: a functional index declared by DDL on a DDL table works

Scratch namespace TCKJ, throwaway table and class (deleted afterwards). `CREATE INDEX … ON t (s, p, o) AS <FunctionalIndex class>`
is accepted; INSERT, UPDATE (old and new values arrive as separate arguments), DELETE, a rolled-back INSERT and `%BuildIndices`
all maintain the global; a callback can run SQL on its own table and sees the row being filed.

## Findings that change the design

1. **`Adjacency.Remove` decrements `^NKG(-3, s)` unconditionally** (Adjacency.cls:144 → GraphIndex.cls:165). Any path that deletes
   the row (index removes the entry) and then calls `Remove` counts down twice.
2. **Two writers already cope**: LedgerApply checks `$Data` before `Put`/`Remove` (LedgerApply.cls:494, :673); the FHIR repair `Put`
   is guarded (FHIRGraph.cls:2194).
3. **Weight has no row behind it**: `create_edge(weight=)` writes the weight to `^KG` only, not to `qualifiers`; `set_edge_weight`
   (nodes_edges.py:917) writes `^KG` only. `sync()` already loses both today.
4. **Multigraph is half built**: nothing in production calls `PutKeyed`/`RemoveKeyed`; a multigraph Cypher CREATE writes an `ekey`
   row and no `^KG`; only BuildKGKeyed produces the keyed layout.
5. **Rebuilds purge**: BulkLoader writes `%NOINDEX` then `%BuildIndices` (bulk_loader.py:217, :440); `Ledger.RebuildEdgeIndices`
   runs `BUILD INDEX FOR TABLE` (Ledger.cls:216). Under the index both purge and rebuild the adjacency, wiping `^KG`-only entries.

## Writers that write a row and `^KG` (would double-apply)

`create_edge` (nodes_edges.py:846), `delete_edge` (:947), `bulk_ingest_edges` server (EdgeScan.cls:256) and fallback (:1486),
`bulk_load_session` (:116), `materialize_inference` / `retract_inference` (_engine/schema.py:2328, :2510), LedgerApply
(OpCreateRel :443, PersistQuals :536, DeleteRelInternal :636; already guarded), FHIRGraph (ResyncKey :1998, DropNode :2361),
Eraser (EraseGraph :47, EraseEdges :367, EraseIdSet :428, EraseAll :645), TraversalBuild.BuildKG (:30) and every caller of it,
temporal (`create_edge_temporal` temporal.py:134, `bulk_create_edges_temporal` :268, `delete_edge_temporal` :212; `^KG` first,
row second), `restore_snapshot` (snapshot.py:850; rows, then `^KG` overwritten from the archive).

## Writers with no row behind them (a rebuild wipes them)

`set_edge_weight`; temporal edges with no `graph=` and no structural-rows flag (temporal.py:184-209, TemporalIndex.cls:87);
ledger `create_temporal_edge` (LedgerApply.cls:601, :619; never a row); Loader.Load and BenchFormat (bare `Kill ^KG`);
`bulk_delete_adjacency` (rows left in place); Snapshot.ImportGlobals.

## SQL-only writers (stale today; the index fixes them)

Cypher DML (iris_sql_store.py:1180; multigraph CREATE with `ekey`, `SET r.weight` → `qualifiers`, `SET`/`REMOVE` label),
`bulk_create_edges` and `store_edge`, `import_rdf` rows, the `_delete_nodes_sql` fallback, BulkLoader `%NOINDEX`, and every label
row writer (LedgerApply AddLabel/OpRemoveLabel/DeleteNodeRows, `create_node`, `bulk_create_nodes`, `BulkIngestNodesSQL`, `store_node`).

## `^KG("label")` and `^KG("prop")`

Label readers: TraversalBFS.cls:528, :561; FHIRGraph.cls:2434. Label writers with rows behind them: BuildKG, FHIRGraph, Eraser,
the `kg_node_stores` migration, snapshot restore. `^KG("prop")` has one reader, Subgraph.cls:120 (no Python reader).

## Multigraph

`ekey INTEGER NOT NULL DEFAULT 0` in fresh DDL (schema.py:33-44); upgrade by `ensure_ekey` (schema.py:859). `^IVG.GraphMode` set by
GraphMode.cls:32 (`set_multigraph`, admin.py:621), killed by GraphMode.cls:40 and Eraser; read by Adjacency.cls:474 and
TraversalBuild.cls:99.
