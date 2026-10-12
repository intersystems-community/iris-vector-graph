<!-- markdownlint-disable MD013 MD060 -->

# Spike: the adjacency stores as a functional index

Date: 2026-10-12. Throwaway code, run in the scratch namespace `IVG236` on `ivg-iris-enterprise`
(`2026.3.0AI`, build 113). Nothing under `iris_src/` changed; the classes were deleted afterwards.

## Question

`^KG("out"/"in"/"deg"/"degp")` duplicates the SQL index on `(s, p, o_id)` that IRIS already maintains, and
it is refreshed by hand on every write path (about 20 of them, per the spec 236 token audit), so it goes stale
whenever a path forgets it (`store_edge`, loaders, Cypher DML until `sync()`). Can the engine maintain it
instead, as a functional index on the edge table, in the same transaction as the row?

## What was built

- `AdjIndex.cls`: a `%Library.FunctionalIndex` whose `InsertIndex` / `UpdateIndex` / `DeleteIndex` keep
  `out`, `in`, `deg`, `degp` and a `__version` counter in the layout `^KG` uses (graph key first, ADR-0001),
  under `^IVGSpike`.
- `Edge.cls`: a persistent edge table with a unique index and `Index Adj ... As IVGSpike.AdjIndex`.
  `EdgePlain.cls`: the same table without it, as the baseline.
- `Bench.cls`: the timing loops (500 rows per transaction, in-process `&sql`), a recount `Verify()`, and
  rollback / delete / update probes.

## Results

**Correct.** After 15,000 and 60,000 rows the adjacency matched a recount from the table (every `out` and `in`
entry present, `deg` equal to the row count, 0 mismatches). The entry written inside a transaction was visible
inside it and gone after `TROLLBACK`. A SQL `DELETE` (3 rows) and a SQL `UPDATE` (a changed `O`) left the stores
exactly equal to a recount, with no `Eraser` and no `sync()`.

**Cost.** One writer, 100,000 attempts (60,000 landed, 40,000 refused as duplicate triples, the same for every
variant):

| Variant                                                                                     | Seconds |
| ------------------------------------------------------------------------------------------- | ------- |
| SQL insert only                                                                             | 0.75    |
| SQL insert + today's `EdgeScan.WriteAdjacency`                                              | 1.13    |
| SQL insert with the functional index                                                        | 1.05    |
| `BulkIngestEdgesSQL` (60,000 edges, also registers endpoints and checks for the edge first) | 2.73    |

Run to run the index added 14% to 40% over the plain insert (smaller runs were at the low end); the hand-written
`WriteAdjacency` path was in the same range, so the index is not more expensive than what it replaces. These are
single in-process runs on a loaded machine, not a benchmark.

**Concurrent.** Four writers, 50,000 edges each on disjoint node ranges: 1.22 s plain, 1.51 s with the index
(+24%). The recount found 0 mismatches and the `__version` counter was exactly 200,000: no lost `$Increment`.

**Deferred build.** `INSERT %NOINDEX` then `%BuildIndices` (100,000 attempts): 0.35 s + 0.29 s. Total is close to a
plain insert. Caveat below.

## Things the spike found that matter for a real design

1. The callbacks must take `pID, pArg...`. A fixed argument list compiled and then failed at the first insert
   with `<PARAMETER>`.
2. The callback bodies are inlined into the table's own routine, so a helper must be called as
   `##class(IVGSpike.AdjIndex).Helper()`. `..Helper()` fails to compile in the table class
   (`No such method 'Key' defined in this class`).
3. An empty string reaches the callback as `$Char(0)`, not `""`. The default graph (`graph_id = ''`) must be
   mapped, or the stores are written under the wrong key and every reader misses them. This is the kind of
   mistake that made the first `GraphIdx` write the default graph's edges at the wrong depth.
4. `%NOINDEX` skips every index on the statement, including the unique one. A bulk path that defers the
   functional index must guarantee uniqueness itself, or the table takes duplicates (`%NOCHECK` the same). With
   duplicates the degree counts parallel rows, which is the multigraph meaning of `deg`, not the simple-graph one.
5. `PurgeIndex` must kill only the derived stores. The removed `GraphIdx` ran a bare `Kill ^KG` and destroyed the
   temporal data that shares the root. Derived data needs a root of its own.
6. `&sql` does not run from a terminal session; the probes had to be compiled methods.

## Not covered

`^NKG` interning (global counters, and Arno's readers need a coherent snapshot), edge weights from the
`qualifiers` JSON, the temporal shadow, multigraph `ekey` and parallel edges, the interplay with the `Eraser`
and the Ledger, the existing-install path (`%BuildIndices` over a 4.2 database and the downtime it implies),
more than four writers on the `__version` and `deg` hot spots, mirroring and sharding.

## Verdict

Feasible, and not slower than the hand-maintained path. What it buys is correctness the current design cannot
give cheaply: every SQL write path, including loaders and Cypher DML, maintains the adjacency in its own
transaction, and a rollback undoes it. The open risk is everything in the list above, not the mechanism. A
follow-up spike should add `^NKG` and the weight, run against a copy of a real 4.2 install, and decide the
derived-data root (ADR) first.
