<!-- markdownlint-disable MD013 -->

# ADR-0015 — A predicate can opt out of the structural rows its temporal edges imply

**Status**: Accepted (2026-10-25, Tom); released in 4.5.0. Amends ADR-0009.
**Date**: 2026-10-25
**Relates to**: ADR-0001, ADR-0002, ADR-0004, ADR-0009 (and its spec 254 amendment), ADR-0011, ADR-0012
**Evidence**: opsreview's report on IVG 4.4.1 (2026-10-25); `tests/e2e/test_temporal_bare_nodes_e2e.py`, `tests/e2e/test_temporal_structural_optout_e2e.py`; the measurements below.

## Decision

A **graph** keeps a small registry of **predicates that are temporal-only**: `^IVG.TemporalStructural(graphKey, predicate) = 0`,
set and cleared through one class (`Graph.KG.TemporalPolicy`) and `engine.set_temporal_structural(predicate, enabled, graph=None)`.
For a predicate in the registry, `InsertEdge` and `BulkInsert` write the temporal globals (`tout`, `tin`, `edgeprop`, `bucket`,
`tagg`, `tlast`, the sketch) and **nothing else**: no `Graph_KG.nodes` row, no marked `Graph_KG.rdf_edges` row, no
`^KG("out"/"in")` entry. Every other predicate behaves as ADR-0009 says. **The default is unchanged** (a predicate is
structural until it is registered), because turning it around changes what `MATCH`, `edge_count` and BFS see and needs a major
version.

The registry is the single answer to "does this triple get a row?", read by every place that today assumes yes:

| Place                                                                              | Rule for an opted-out predicate                                                                                            |
| ---------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `WriteStores` (`InsertEdge`, `BulkInsert`)                                         | skips `AssertTemporalRow` / `PutShadow`; one `$Data` per distinct predicate in a batch                                     |
| `BackfillStructuralRows` (the `initialize_schema()` upgrade step)                  | skips the triple: this is the step that wrote 6.79 million rows in 69 minutes                                              |
| `RetireExpired` (the purge)                                                        | leaves the triple out of the retract set (today a triple with no row costs a `DELETE` that finds nothing, then a `SELECT`) |
| `retire_bare_nodes` (this release)                                                 | the triple's endpoints are not candidates: no node was written for them                                                    |
| `Graph.KG.TemporalPolicy.DropRows(graph, predicate, batch)` (new, explicit, below) | deletes the marked rows an earlier release already wrote for the predicate, then retires the nodes that leaves bare        |
| `GraphStores.Inventory`, `Eraser.EraseAll`, snapshot/restore                       | the registry is listed; `EraseGraph` keeps it (it is configuration, as `fhir_graphs` is), `EraseAll` kills it              |

## Context

ADR-0009 made a temporal edge imply a structural edge, so that the Adjacency Index could be purely engine-maintained. That is
right for the predicates a graph is traversed over and wrong for a stream of observations. Measured on 2026-10-25, 4-CPU lab
container, host load 20-27, alternating runs, throughput ratios only:

- A new temporal triple writes two node rows and one marked edge row (the edge row's foreign keys and its two indexes
  included): **13,900 triples a second after the embedded-SQL change in this release (7,900 before) against 83,000 a second with
  no adjacency index and so no rows**. The rows are 6 to 8 times a write, and the floor under the change (two plain prepared
  `INSERT`s) is about 49 us of the remaining ~72 us. No statement-level change gets under it.
- The `COALESCE(graph_id, '')` predicate is not the cost: after `TUNE TABLE` the existence checks use `pk_nodes_graph` and
  `u_spo_graph_ekey` with `graph_id` as a key column, and with that index ignored the optimizer's three-index intersection
  answers a lookup in the same 0.37 ms (round trip included) on a table of 536,000 edges, 200,000 of them in the graph asked about.
- opsreview: 6,789,717 marked rows from `OBSERVED_AT` (distinct span target each), `EMITTED_AT`, `METRIC_AT`; 7.18 million
  nodes; 69 minutes and +1 GB of `USER` for the upgrade step; 425,978 orphan nodes right after it; millions of orphans a day
  at their span rate. `retire_bare_nodes` and `erase_bare_nodes` (this release) remove the orphans, not the rows or the write
  cost of the rows while their triple is live.

For the span predicate the rows are pure cost: no one runs BFS over `<tenant>|span:<id>`, and every question asked of it is a
window, bucket or aggregate read of the temporal globals.

## What stops working for an opted-out predicate, and what does not

Stops (each reads `^KG("out"/"in")` or the rows, which the predicate no longer has):

- Traversal: `execute_bfs`, k-hop, shortest path, Cypher variable-length paths, PPR and every centrality/community algorithm
  (their input is `^KG`/`^NKG`); degree and `get_edge_count`; `MatchEdges`.
- Cypher `MATCH (a)-[:P]->(b)` on the SQL and native paths (they read `rdf_edges`); `sync()` and `BuildKG` produce nothing for it.
- `Eraser.EraseEdges` for such a triple removes no row (there is none) and leaves its temporal entries; `EraseNodeIds` still
  clears them, because `TemporalIndex.EraseNodes` walks the temporal globals, not the rows.
- `verify_graph` does not drift over it: neither side holds the triple.

Keeps working, because it reads only the temporal globals (ADR-0002): window reads (`get_edges_in_window`,
`iter_edges_in_window`, `QueryWindow`), the latest-event index, buckets and aggregates, distinct counts, velocity and bursts,
edge attributes, `DeleteTemporalEdge`, `purge_raw_before` / `purge_before` / `purge_bucket_range`, temporal Cypher (`CALL
ivg.temporal...`), snapshot and restore of the temporal globals.

A triple can be both: an explicit `create_edge` of `(s, P, o)` writes a normal unmarked row, with its nodes, whatever the
registry says. It is then an ordinary structural edge that happens to have timestamps; the purge never removes it.

## Options considered

1. **A flag on the write** (`temporal_structural=False` on `create_edge_temporal` / `bulk_create_edges_temporal`). Rejected:
   the answer lives in no store, so the upgrade backfill cannot know which triples were written with it and would write rows
   for all of them (the 69-minute step again), the purge cannot tell a triple that never had a row from one that lost it, and
   two writers of one predicate disagree. A per-write flag may still exist as a convenience that is checked against the registry
   and refused when they disagree.
2. **A per-graph setting** (all temporal edges of the graph, or none). Rejected: opsreview's default graph holds structural
   data (941 nodes, 4,023 edges) next to the temporal streams, and some of its temporal predicates may need traversal later.
3. **A SQL table `Graph_KG.temporal_policy(graph_id, predicate, structural)`.** Visible to SQL and carried by the table
   inventory, but one SQL read per batch in the hottest write path and a DDL migration for a flag. `^IVG.GraphMode` (spec 234) is
   the precedent for per-graph engine configuration, and a global read is about 1 us.
4. **A per-predicate registry in `^IVG` (chosen).** One `$Data` per distinct predicate per batch (cached in a local array for
   the batch); read by the five places above; no DDL; survives restarts and snapshots; the unit of decision is the unit of
   cost, the predicate.

Matching is exact on the predicate string. A prefix or pattern (`OBSERVED_*`) is deliberately left out: it makes "does this
triple get a row?" an expression to evaluate on the hot path and in the backfill, and a predicate is a name an application
chose.

## API

```python
engine.set_temporal_structural("OBSERVED_AT", False, graph=None)   # Graph.KG.TemporalPolicy.Set(graph, predicate, 0)
engine.get_temporal_structural("OBSERVED_AT", graph=None)          # True while unregistered
engine.list_temporal_structural_opt_outs(graph=None)               # the registered predicates of the graph
engine.drop_temporal_structural_rows(["OBSERVED_AT", "EMITTED_AT"], graph=None, batch_size=10000, dry_run=False, retire_bare_nodes=True)  # counts per predicate
engine.backfill_temporal_structural_rows("OBSERVED_AT", graph=None)  # the reverse: rows from the temporal globals, for a predicate re-registered as structural
```

`set_temporal_structural` takes effect for the next write. It does nothing to rows that exist: that is `drop_temporal_structural_rows`,
a separate and explicit step, in bounded transactions, with a dry run that counts and changes nothing (the same proof as
`erase_bare_nodes`), deleting only rows that still carry the temporal marker (an explicit row stays). Registering and dropping are
two calls because dropping 6.79 million rows is a maintenance job that must be schedulable and resumable, and registering
is a one-line configuration change that must be possible before the upgrade runs.

`initialize_schema()` reads the registry. On an install that has no `idx_edges_adj` yet, the backfill skips the registered
predicates and logs how many triples it skipped, so an application that registers before upgrading pays for none of the rows.
It never registers anything itself, and it never drops rows (an upgrade must not delete data).

## Migration and rollback

- **New install, or upgrade before the first backfill** (opsreview on 4.4.1 is not this case; a 4.2.0 install is): register,
  then upgrade. No row is written for the registered predicates.
- **An install that already has the rows** (opsreview): register, then `drop_temporal_structural_rows(..., dry_run=True)` for the
  count, then the real run during a quiet period, as `erase_bare_nodes` is run. Writers are never blocked: the registry is read per
  batch, so writes already in flight at the moment of registering finish with rows and are removed by the drop.
- **Rollback**: `set_temporal_structural(p, True)` makes new writes structural again. The window in between has temporal entries
  and no rows; `backfill_temporal_structural_rows(p)` walks the temporal globals (the authority) and writes the missing marked
  rows with `pTrustIndex=0`, as the upgrade step does. Nothing is lost in either direction, because the rows are a projection of
  the temporal globals, never the reverse.
- **Mixed fleets**: a writer on a release without the registry keeps writing rows. The registry is read by the classes, so it
  is the deployed `Graph.KG.*` that decides, not the Python package; `initialize_schema()` deploys them together.

## Consequences

- Throughput for registered predicates goes from the structural-row rate to the temporal-globals rate (two measurements, both
  on a 4-CPU lab container under host load: the structural rows cost 6 to 8 times a write without the adjacency index
  (536K-edge table); a registered predicate wrote 13 to 16 times faster than a structural one, 6,700 to 115,000 a second
  for single-edge writes), and the node and edge counts of the graph stop growing with the stream.
- A new place where "is this predicate structural?" can be answered wrongly: every reader above must consult
  `Graph.KG.TemporalPolicy`, and `tests/unit/test_temporal_structural_optout_architecture.py` checks that no other class reads or writes the global.
- The registry joins `GraphStores.Inventory` (ADR-0004), so the Eraser, the digest and the inventory-coverage test see it.
- Cypher `MATCH` over a registered predicate returns nothing, which can read as data loss. **Decided: warn at the Python
  engine, not in the translator.** `execute_cypher` and `execute_bfs` (so k-hop and the variable-length routes that run BFS)
  are wrapped: only when the answer is already empty, and only when the query or the traversal names a registered predicate,
  they log one warning per (graph, predicate) per object (`iris_vector_graph.temporal_only`) naming the temporal reads that do
  answer and the way back. A read that returned rows pays nothing, and a registry that cannot be read leaves the read's own
  answer. A translator-level check would put a registry read on every translation and would not know the graph of a query that
  names none.
- **Decided, with a test: temporal `CALL`s keep working.** `execute_temporal_cypher` walks the temporal globals hop by hop
  (`QueryWindow`), not the rows: a two-hop walk over an opted-out predicate returns both hops
  (`test_the_temporal_window_walk_still_reads_an_opted_out_predicate_hop_by_hop`). It does not consult the registry.
- **Decided: the purge leaves the old rows of a predicate registered later.** The purge's retract set skips a registered
  predicate, so rows an earlier release wrote stay until `drop_temporal_structural_rows`, which finds them by predicate and
  marker and so does not depend on any timestamp being left. The alternative, a retract that deletes if present, would cost a
  statement per purged triple on the predicate this ADR exists to make cheap (opt-out purge of 200,000 triples: 0.8 s, against
  17.9 s with rows and node retirement).
- **Decided: drop and backfill need the adjacency index** (spec 252). Without it a temporal edge has a `^KG` shadow and no row,
  so there is nothing to drop and a "row" backfill would build ones the install does not maintain. Both raise.
- **Decided: the upgrade backfill remembers a bounded number of triples** (`pSeenLimit`, 100,000). The walk is
  timestamp-major, so a triple with several timestamps far apart is asserted again after the memory has dropped it: one
  existence check (about 16 us), never a missed row. The unbounded memory it replaces held every distinct triple (6.79 million
  for opsreview).

## Measured (4-CPU lab container, host load 30-35, alternating runs; ratios, with the absolute numbers for scale)

- Write path, 5,000 new triples a run, 6 alternating pairs: opt-out against structural, `InsertEdge` **15.9x** (8.8-20.3;
  6,700 to 115,000 a second), `BulkInsert` **13.4x** (1.6-14.1, one run caught a load spike; 7,200 to 89,000). Against a
  namespace with no adjacency index (80,000 and 68,000 a second) the opt-out path is **1.44x and 1.30x**: it also skips the
  `PutShadow` that path still does. The acceptance bar was "within 15 % of the no-index rate"; it is above it.
- `drop_temporal_structural_rows`: 200,000 marked rows and their 200,008 nodes in 19.9 s (10,000 rows a second, 20
  transactions); the dry run counts them in 0.7 s and changes nothing.
- `drop_temporal_structural_rows` over `OBSERVED_AT` (1.2 million rows), `EMITTED_AT` (200,000) and `METRIC_AT` (100,000) in one
  graph of a 1.8-million-row table, counts per predicate: dry run of all three 0.6-0.9 s (two covered counts each; the plan is
  the `idx_edges_graph_id` x `idx_edges_p` intersection, and the marker count reads the master map only for matching ids,
  or scans it for a predicate that is most of the table); real run 124 s, 18 s and 9.5 s (about 10,000 rows a second, nodes
  included). A second graph with the same predicate was untouched.
- Purge with `retire_bare_nodes`, 200,000 triples: 17.9 s structural, 0.8 s opted out.
- Upgrade-style backfill of 400,000 timestamps over 200,000 triples: `seen_peak` equals the limit (100,000, then 1,000), rows
  written 200,000; with every row present it checks 63,000 timestamps a second.

## Recommendation

Option 4 is built. `retire_bare_nodes` stays independent: it serves the predicates that stay structural. Not done: a
cross-call node memory for single `InsertEdge` calls; a prefix or pattern match on the predicate (deliberately left out, above).
