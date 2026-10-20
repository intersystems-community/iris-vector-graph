<!-- markdownlint-disable MD013 -->

# ADR-0008 — The Adjacency Index is maintained by the engine from the edge table

**Status**: Accepted; step 1 implemented (ADR-0011), step 2 implemented (spec 252, 2026-10-20)
**Date**: 2026-10-12
**Evidence**: `docs/research/spikes/functional-index-adjacency/` (README, RISKS, DEG-AUDIT)
**Relates to**: ADR-0001, ADR-0003, ADR-0010
**Reverses**: the removal of the `GraphIdx` functional index; the guard tests
`test_no_functional_index_declaration.py` and `test_adjacency_counter_ownership.py` are rewritten.

## Decision

`^KG("out")`, `^KG("in")` and `^KG("label")` are maintained by functional indexes on `rdf_edges` and
`rdf_labels`, inside the transaction of the row they derive from. A SQL insert, Cypher DML, a loader or a
rollback keeps them current with no caller doing anything, and `sync()` stops being needed for them.

It is reached in two steps behind one module interface. First, one module owns every way these stores change (live
write, delete, bulk rebuild, restore, and the version stamp), so the six writers that bypass `EdgeScan` today
(`TraversalBuild`, `Eraser`, `LedgerApply`, `Loader`, `BenchFormat`, snapshot restore) go through it. Then the
implementation behind that interface becomes the functional index. Callers never see the change.

The stores that stay rebuilt (`^NKG`, the two-hop statistics, the Arno snapshot, cached algorithm results) are
**Rebuilt stores**: each records the Graph-Version Token it was built from, and is current while that equals the
live token. The stores stay under `^KG`; every reader goes through the module, so moving the root later is a change
to one module (the Arno readers would need the root as a parameter).

## Scope of step 2 (decided 2026-10-19)

Found again on the alternative path: after a Cypher `CREATE` a one-hop `MATCH` from a bound node answered nothing,
and after a Cypher `DELETE` it answered the deleted edge, because the k-hop fast path, `shortestPath`, Dijkstra, BFS
and the algorithms read the Adjacency Index and Cypher DML is a SQL-only write.

- **The index owns** `out`, `in`, `label`, the keyed multigraph children and their `"#"` count, and moves the degree
  generation `gen` (ADR-0012) and the Graph-Version Token on every change it makes. An edge's weight comes from its
  row's `qualifiers` on insert and update. The `prop` store goes; its reader moves to SQL. A temporal edge's
  structural entry follows from its row (ADR-0009).
- **Bulk loads** (`bulk_load_session`, the SNB loaders) keep writing `%NOINDEX`, dedupe themselves, and end in one
  `%BuildIndices`; every other writer is a plain insert. No `%NOINDEX` writer returns before its `%BuildIndices`.
- **Existing installs**: `ensure_indexes` declares the index and runs one announced `%BuildIndices`, which also
  clears any Drift already there. `sync()` stays callable and is no longer needed for these stores.
- The breadth benchmark on AWS (SNB SF1/SF10) runs after this lands, so it measures the shipped write path.
- **No adjacency without a row.** Temporal edges get their structural row (ADR-0009, implemented in the same spec); an edge's
  weight lives in its row's `qualifiers` (`create_edge(weight=)` and `set_edge_weight` write it there). The paths whose
  purpose was adjacency with no row change: `Loader` is removed, `BenchFormat` seeds rows, `bulk_delete_adjacency` becomes a
  deprecated alias that deletes the edge rows, `Snapshot.ImportGlobals` restores rows only.

## Context

The index is derived from SQL but written by about ten classes, each respelling the layout, and the version stamp is
bumped by hand at six sites. A write path that forgets one produces Drift: variable-length Cypher returns nothing
after `store_edge`, `sync()` erases the structural entry of a temporal-only edge, `LedgerApply` writes both SQL and
the globals. A functional index was tried once and removed, for reasons that were bugs in that implementation (the
default graph's key was hard-coded, the counters had no graph subscript, `PurgeIndex` killed the whole `^KG` root,
and the table class compiled before its index class under `LoadDir`).

The spike rebuilt it as a throwaway: the stores matched a recount after inserts, a rollback, a SQL delete and a SQL
update; a rolled-back insert left nothing; four concurrent writers lost no update; and the write cost was in the range
of the hand-written path (+14% to +40% over a plain insert, against +51%).

## Consequences

- The callbacks take `pID, pArg...`; helpers are called by full class name (the body is inlined into the table
  class); the empty graph id arrives as `$Char(0)` and must map to `0`; `PurgeIndex` kills only the stores it owns.
- `INSERT %NOINDEX` skips the unique index too, so a deferred bulk path must dedupe itself.
- Exact `deg`/`degp` counters cannot live in the index (ADR-0010). `^NKG` interning cannot either: a rollback leaves
  nameless ids and the locks it takes deadlock under overlapping writers.
- Temporal edges need a row, or the index cannot produce their structural entry (ADR-0009).
- The `prop` store is dropped (its one reader moves to SQL); `label` is kept and engine-maintained.
- Existing installs build the indexes once (`%BuildIndices` ran at about 200,000 rows/s in the spike).
- Property tests must walk the derived stores for keys with no row behind them: the spike's first verification
  passed while ghost counters existed.

## Considered options

- Stop at the explicit owner: closes the bypasses but leaves every SQL-only write stale.
- Hand-maintain with a lock for exact counters: deadlocked (182 s, 0 of 8 writers finished).
- Move the derived stores to their own root now: gives side-by-side rebuilds, but touches every reader and the
  Arno library at once. Kept as the named follow-up.
