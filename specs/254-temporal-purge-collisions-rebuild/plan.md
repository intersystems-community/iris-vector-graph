<!-- markdownlint-disable MD013 -->

# Plan: temporal edges that purge cleanly, collide visibly and rebuild identically

## Decision 1: the marker, not a reference count

FR-002 needs a purge to tell a structural row that only temporal edges hold up from one a non-temporal write asserted.
The plan uses the **temporal marker already on the row**: `qualifiers` `{"temporal": true}`, written by
`Adjacency.AssertTemporalRow` since spec 252 (ADR-0009), cleared by `create_edge`'s adoption of a marked row, and read
by `Adjacency.RetractTemporalRow`, which already deletes a row only while it is marked.

Why not a reference count (`^KG("tref", g, s, p, o)` = number of timestamps):

- **FR-005, no migration.** Every row an indexed install holds is already marked or not (252's backfill marked the old
  ones). A count would be a new store with nothing in it for existing data, so a purge of old data would need a backfill
  first, or a fallback that is wrong as soon as a new timestamp lands on an old triple.
- **Rollback.** `$Increment` is not undone by a rollback (ADR-0010 measured it); an exact counter needs a lock held to
  commit, which deadlocked under overlapping writers in the 252 spike. The marker is a column of the row, so it rolls
  back with the row.
- **One more store** on every write, erase, snapshot and rebuild path, and in the determinism hash. The marker adds none.
- What a count would buy, "does any timestamp still hold this triple", the purge answers itself in one walk (below).

The marker answers "who asserted the row"; the purge's survivor walk answers "does a timestamp still hold it". A row is
retired when both say temporal-only.

## Purge (FR-001, FR-002, SC-004)

`PurgeRawBefore(graph, tsEnd, tsStart)` and the legacy `PurgeBefore(tsEnd)` share one private step,
`RetireExpired(gKey, tsStart, tsEnd)`, run **before** the raw kills:

1. Walk `^KG("tout", g)` over `[tsStart, tsEnd)` and collect each distinct `(s, p, o)` into a process-private global
   `^||IVG.TPurge(s, p, o)`, with a count.
2. Walk the timestamps outside the range (the ones that survive) and kill each candidate a surviving timestamp still
   holds. A source with no candidate is skipped whole; the walk stops when no candidate is left.
3. With the adjacency index on (`Adjacency.Indexed()`), `Adjacency.RetractTemporalRows(gKey, "^||IVG.TPurge")` deletes
   each remaining triple's row while it is still marked, with two prepared statements for the whole set. The row's
   functional index removes `^KG("out"/"in")` and moves the degree generation (ADR-0012), so the degree cache answers 0.
   Without the index (a pre-252 install) the shadow is removed through `Adjacency.Remove` only when no `rdf_edges` row
   holds the triple, so the purge never makes adjacency disagree with the rows.
4. The existing raw kill loop runs unchanged.

Rows first, raw second: a purge that fails part way leaves timestamps whose rows are gone, and running the purge again
finishes it (the retract finds nothing, the kill loop runs). The other order would leave marked rows that no purge can
find again. Nodes are not touched (US1 scenario 3). The aggregates stay, as today. Returns `"deleted:skipped"` as
before, so existing parsers keep working.

Cost: two extra walks of `$Order` (the range and the survivors) and one SQL delete per retired triple. The delete is
the price of FR-001 under one node per event and is measured, not assumed (SC-004).

## Collisions (FR-003)

`WriteStores` returns `1` written, `0` skipped (mode `"skip"`), `-1` collision (mode `"insert"` or `""`, an edge already at
`(ts, s, p, o)`). A collision writes nothing: no `tout`/`tin`, no bucket, no aggregate, no HLL, no `edgeprop`, no row.
`"update"` is unchanged.

- `BulkInsertCounts(graph, batch, upsert, mode)` returns `"written:collisions:skipped"`. `BulkInsert` keeps its signature
  and returns the written count from it.
- `InsertEdge` keeps returning `1` (its callers, `LedgerApply` among them, read it as a status).
- Python: `bulk_create_edges_temporal` returns `TemporalWriteCount`, an `int` equal to the written count with
  `.written`, `.collisions` and `.skipped`. The store calls `BulkInsertCounts`.
  `create_edge_temporal` keeps returning `True` when the call did not fail: FR-003 names the bulk path for the counts;
  a single collision is reported by a one-edge batch.
- The Python mirror that re-inserted every temporal row after `BulkInsert`/`InsertEdge` is skipped when the adjacency
  index is on: `WriteStores` asserted the marked row already, and the mirror was three SQL statements per edge.

## Rebuild determinism (FR-004)

With collisions not counted, every store is a function of the set of first writes per coordinate. The marked row takes
the weight of the triple's first write (`AssertTemporalRow` skips once `^KG("out")` has the triple), so batch size never
matters, and order does not matter when a triple's timestamps share a weight and colliding writes are identical. The
US3 test pins that: shuffled replays with identical duplicates, one weight per triple.

The snapshot hashes every graph-scoped store in `Graph.KG.GraphStores.Inventory()`: SQL rows without their surrogate
columns (`edge_id`, `created_at`), and every global entry under the graph key. Excluded and why: `degc` and `degw` are
reader-filled caches (ADR-0012, "Kill it and every answer is still right"); the ledger stores are empty with the ledger
off and asserted empty.

## Write and delete paths (TESTING_POLICY: a case per path)

| Path                                                     | Expected after the purge of every timestamp                              |
| -------------------------------------------------------- | ------------------------------------------------------------------------ |
| `create_edge_temporal` / `bulk_create_edges_temporal`    | marked row and adjacency gone; nodes stay; degree 0                      |
| `TemporalIndexMS.BulkInsert` (ms precision)              | same                                                                     |
| `suppress_reverse_index`                                 | same (no `tin` entry needed)                                             |
| `create_edge` before the temporal edge                   | row stays (never marked)                                                 |
| `create_edge` after (adopts the marked row)              | row stays                                                                |
| `bulk_create_edges` after                                | row stays (adopts, new in this spec)                                     |
| Cypher `CREATE`/`MERGE` after                            | **open**: the insert is skipped and the row stays marked; strict xfail   |
| timestamps on both sides of the cutoff                   | row stays                                                                |
| two graphs, one purged                                   | the other graph's row stays                                              |
| `DeleteTemporalEdge`, `EraseNodes`, `EraseGraph`         | unchanged; a purge afterwards removes nothing more                       |

## Files

- `iris_src/src/Graph/KG/TemporalIndex.cls`: `WriteStores`, `BulkInsertCounts`, `BulkInsert`, `RetireExpired`,
  `PurgeRawBefore`, `PurgeBefore`.
- `iris_src/src/Graph/KG/Adjacency.cls`: `RetractTemporalRows`.
- `iris_vector_graph/_engine/temporal.py`, `iris_vector_graph/stores/iris_sql_store.py`: `TemporalWriteCount`, the
  counts, the mirror skip. `iris_vector_graph/_engine/nodes_edges.py`: `bulk_create_edges` adopts marked rows.
- `docs/adr/0009-...md`: amendment.
- Tests: `tests/unit/test_254_*.py` (source and Python shape), `tests/e2e/test_254_*.py` (one per story, the path
  matrix), `tests/e2e/graph_snapshot.py` (canonical hash).
