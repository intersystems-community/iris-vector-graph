<!-- markdownlint-disable MD013 MD060 -->

# Implementation Plan: The Adjacency Index kept current by the edge table

**Branch**: `alt-parity` | **Date**: 2026-10-19 | **Spec**: [spec.md](spec.md) | **Research**: [research.md](research.md)

## Summary

Two functional indexes, declared by DDL, make the Adjacency Index a derivative of its tables:
`idx_edges_adj ON Graph_KG.rdf_edges (graph_id, s, p, o_id, qualifiers, ekey) AS Graph.KG.AdjIndex` and
`idx_labels_adj ON Graph_KG.rdf_labels (graph_id, s, label) AS Graph.KG.LabelIndex`. Their callbacks reach the stores only through
`Graph.KG.Adjacency` (ADR-0011), so the single-writer guard holds. Every writer that today writes a row and then `^KG` stops
writing `^KG`; every writer that wrote `^KG` with no row gets a row (temporal edges, ADR-0009; weights in `qualifiers`) or is
removed. Rebuilds become `%BuildIndices`.

## Technical Context

**Language/Version**: ObjectScript (IRIS 2024.1+, test image `irishealth:2026.3.0AI.113.0`); Python 3.10+ (`.venv` 3.11).
**Primary Dependencies**: `%Library.FunctionalIndex`; `intersystems-irispython` (DB-API + Native API).
**Storage**: `^KG("out"/"in"/"label")` and the keyed children (spec 234); `rdf_edges`, `rdf_labels`; a temporal-origin column on `rdf_edges` (ADR-0009).
**Testing**: unit (no IRIS) for the Python paths; one E2E per writer path (FR-005) against a recount helper in scratch namespace `TCKJ`; TCK; the 40 SNB SF0.1 queries.
**Constraints**: write cost at most +40% (SC-003); bulk load within 10% (SC-004); no reader interface changes.

## Constitution Check

- **III Test-First**: every phase starts with failing tests (tasks.md).
- **IV IRIS E2E**: one live E2E per writer path; the recount helper also walks index entries with no row behind them (spike R9).
- **VII Cypher gates**: TCK 3896/3896 after each phase that touches DML.
- **VIII Gate 3** (adjacency smoke on container startup): extended with a Cypher `CREATE` then a fast-path read.
- **ADR-0011 guard**: `Graph.KG.AdjIndex` / `LabelIndex` call `Adjacency` and write no global themselves.

## Design

### Callbacks

`Graph.KG.AdjIndex` (`%Library.FunctionalIndex`): `InsertIndex(pID, g, s, p, o, quals, ekey)` calls `Adjacency.RowPut`;
`DeleteIndex` calls `Adjacency.RowRemove`; `UpdateIndex` receives new values then old values and does remove-old / put-new only
when the triple, graph or ekey changed, otherwise `Reweight` (a `SET r.weight`); `PurgeIndex` calls `Adjacency.RemoveAll`
(out, in, gen, degc of every graph, never the `^KG` root); `SortBeginIndex` / `SortEndIndex` are no-ops.

`Adjacency.RowPut(graph_id, s, p, o, quals, ekey)`: graph key by `GraphKey.ForIndex` (maps `$Char(0)` and `""` to `0`), weight
from `qualifiers.weight` (default 1.0, as `BuildKG` reads it); a graph in multigraph mode goes to `PutKeyed`, any other to `Put`.
`RowRemove` mirrors it with `RemoveKeyed` / `Remove`. `LabelIndex` sets and kills `^KG("label", g, label, s)` through two new
`Adjacency` methods (`PutLabel`, `RemoveLabel`).

### Fixes the callbacks need inside a transaction

- `PutKeyed` / `RemoveKeyed` keep the `"#"` count with `$Increment`, which a rollback does not undo (spike R1): recompute it
  from the children with `Set` instead.
- `Remove` decrements `^NKG(-3, s)` even when the entry was already gone (research finding 1): do it only when it was there.
- `^NKG` interning stays where `Put` does it today (no regression; spike R4 recorded as a known limit: a rolled-back write can
  leave an interned id with no edge, which a `BuildNKG` clears).

### Writers

| Group | Change |
| --- | --- |
| Row, then `^KG` (research §A) | drop the `^KG` call: `create_edge`, `delete_edge`, `bulk_ingest_edges` (both paths), `materialize_inference`, `retract_inference`, FHIRGraph `ResyncKey` / `DropNode`. LedgerApply and the FHIR repair are already `$Data`-guarded and become no-ops. Eraser keeps its `Remove*` calls as clean-up of entries with no row. |
| Rebuild | `BuildKG` / `BuildKGKeyed`: the edge and label passes become `%BuildIndices` of the two indexes; `deg2p`, two-hop stats, `^NKG` unchanged. `sync()` keeps its name and calls it. |
| Temporal (ADR-0009) | every temporal write asserts the structural row with a temporal-origin marker; `PutShadow` calls go; the last timestamp's delete removes the row only while marked; an upgrade backfills marked rows from `^KG("tout")`. |
| Weight | `create_edge(weight=)` writes `qualifiers.weight`; `set_edge_weight` updates the row. |
| Row-less (research §B) | `Loader` removed; `BenchFormat` seeds rows; `bulk_delete_adjacency` deprecated, deletes edge rows; `Snapshot.ImportGlobals` and `restore_snapshot` restore rows and skip archived `out`/`in`/`label` lines. |
| Bulk | `bulk_load_session` writes `%NOINDEX` (deduped, as `BulkIngestEdgesSQL` already checks) and ends in `%BuildIndices` over the new ID range, success or failure; `BulkLoader` already does. `Ledger.RebuildEdgeIndices` is a full rebuild by design. |
| `prop` | `Subgraph` reads properties from SQL; nobody writes `^KG("prop")`. |

### Existing installs

`ensure_indexes` declares both indexes when `Graph.KG.AdjIndex` is deployed (a DDL-only namespace keeps today's behaviour) and
announces the one build `CREATE INDEX` runs over the existing rows. It runs after the ekey (spec 234) and temporal-marker
migrations, because the index reads both columns.

## Phases

1. **Index and callbacks** (US1): the two classes, `RowPut` / `RowRemove` / label methods, the three fixes, the declaration in
   `ensure_indexes`; E2E: Cypher CREATE / MERGE / DELETE / DETACH DELETE / SET weight / labels read back through every reader.
2. **Writers** (US2): drop the double writes, rebuild through `%BuildIndices`; one E2E per path with the recount helper; rollback.
3. **No adjacency without a row** (US2): ADR-0009 rows and upgrade backfill, weight in `qualifiers`, row-less APIs.
4. **Bulk** (US3): `%NOINDEX` plus a ranged `%BuildIndices`; load-time comparison.
5. **Existing installs and docs** (US4): drifted-install E2E, `CONTEXT.md`, CHANGELOG, then the SNB SF0.1 check and TCK; then SF1/SF10 on AWS.
