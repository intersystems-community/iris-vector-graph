<!-- markdownlint-disable MD013 MD060 -->

# Risk register: the adjacency as a functional index

Second pass of the spike in [README.md](README.md), 2026-10-12, same scratch namespace and build. It takes the
"not covered" list and measures the items that could sink the design. Probes: `AdjIndexL.cls`, `EdgeL.cls`,
`Risk.cls`.

## Summary

| #   | Risk                                                          | Severity | State                                                                |
| --- | ------------------------------------------------------------- | -------- | -------------------------------------------------------------------- |
| R1  | `$Increment` counters are not rolled back                     | High     | Measured. `deg`/`degp` cannot be exact inside the index              |
| R2  | A rollback-safe counter needs a lock, and the lock deadlocks  | High     | Measured. 0 of 8 writers finished in 182 s                           |
| R3  | Writers that bypass the edge table                            | High     | Inventoried. Temporal edges and four others write `^KG` themselves   |
| R4  | `^NKG` interning inside a transaction                         | High     | Measured. A rollback leaves nameless ids; the lock is held to commit |
| R5  | Behaviour changes the unification forces                      | Medium   | Analysed, not built                                                  |
| R6  | Moving an existing install                                    | Medium   | Rate measured, procedure not tried                                   |
| R7  | Bulk-load path                                                | Medium   | One trap found                                                       |
| R8  | Operational: purge scope, journal volume, mirroring, sharding | Medium   | Partly known, rest unmeasured                                        |
| R9  | Verification can pass while the stores are wrong              | Medium   | Found in this spike's own first pass                                 |

## R1. A rolled-back write leaves its counters behind

`$Increment` is not undone by `TROLLBACK` (spec 236's own test `test_25_a_rolled_back_bump_is_not_undone` relies
on that, for the version token). A functional index that counts with it drifts on every rolled-back insert.

Measured: insert one edge in a transaction, roll back.

| Variant                        | `out` / `in` | `deg` | `degp` | `__version` |
| ------------------------------ | ------------ | ----- | ------ | ----------- |
| `$Increment` (the first spike) | gone         | **1** | **1**  | moved by 1  |
| `Set` under a lock             | gone         | none  | none   | moved by 1  |

So `out` and `in` (plain `Set`) are transactional and the counters are not. This spike's own first
`Verify()` missed it, because it only compared counters of sources that still had rows (R9).

What this means for the design:

- The version token is _helped_ by this behaviour: it must never be reused, and a rolled-back bump only
  over-invalidates a cache, which is safe.
- `deg` and `degp` cannot be exact if they are counted with `$Increment` inside the index.
- The current system is already inexact here (spec 236 audit item 6: "`deg` drifts"), so an advisory `deg` is
  not a regression. It is a regression only if something relies on exactness. Readers of `deg` need auditing
  (step 4 below).

## R2. The rollback-safe alternative deadlocks

Keeping `deg` with `Set` under `Lock +` rolls back correctly, because `Set` does. But a lock taken inside a
transaction is held until the transaction ends, so a writer holds the locks of every source in its batch.

| Workload                                                                         | `$Increment`         | `Set` + lock                         |
| -------------------------------------------------------------------------------- | -------------------- | ------------------------------------ |
| 1 writer, 20,000 edges from one hub                                              | 0.20 s               | 0.21 s                               |
| 8 writers, 160,000 edges from one hub                                            | 1.43 s               | 3.96 s (2.8x), counts exact          |
| 8 writers, 40,000 edges over 40 shared sources, 50 per transaction, random order | 0.32 s, counts exact | **182 s, 0 of 8 finished, 146 rows** |

Two writers that take locks in different orders deadlock, and each waits out its lock timeout. This is not
tunable: any batch that touches overlapping sources in different orders will do it.

Consequence: no lock-based exact counter in the write path. Options for `deg`/`degp`:

1. **Advisory counter** (`$Increment`), reconciled whenever the index is rebuilt. Cheap, drifts on rollback.
2. **No stored counter.** `deg(s)` is the number of `out` entries under `(graph, s)`, counted on demand
   (O(degree); a hub of 100,000 edges costs a 100,000-node `$Order` walk). Exact by construction.
3. A stored counter that is recomputed lazily when `__version` has moved since it was written.

Recommendation: option 2 for correctness-critical readers, option 1 only where the reader treats it as a hint.

## R3. Who else writes the stores

The "one write path" argument holds only if every writer goes through the edge table. Counting `Set` / `Kill` /
`$Increment` of the derived `^KG` stores in `iris_src/src`:

| Class                                    | Writes | What it is                                                                                              | Fate under the index                                             |
| ---------------------------------------- | ------ | ------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------- |
| `EdgeScan`                               | 50     | the primitives (`WriteAdjacency`, `DeleteAdjacency`, keyed variants)                                    | delete the adjacency ones; keep what is not adjacency            |
| `Eraser`                                 | 36     | deletes adjacency, labels, props, `deg2p` directly                                                      | adjacency part disappears; labels/props/`deg2p` stay             |
| `TraversalBuild`                         | 25     | rebuild from SQL (`BuildKG`, `Build2HopStats`)                                                          | becomes `%BuildIndices` for adjacency; `deg2p` stays             |
| `LedgerApply`                            | 6      | applies SQL, then **writes `out`/`in` itself** and bumps the version                                    | would double-write (two `$Increment`s); must drop its own writes |
| `FHIRGraph`                              | 8      | inserts `rdf_labels`/`rdf_props` **and** `^KG("label"/"prop")`                                          | labels/props are outside this index; unchanged                   |
| `Loader`, `BenchFormat`                  | 10     | write `^KG` directly (`Loader`: no SQL row at all)                                                      | rewrite to insert rows, or retire                                |
| `TemporalIndex`                          | 58     | the temporal stores (`tout`/`tin`/`bucket`/`tagg`/`edgeprop`) and a **structural shadow** in `out`/`in` | see below                                                        |
| `NKGAccel*`, `Communities`, `GraphIndex` | ~150   | `^NKG` and its snapshots                                                                                | see R4                                                           |

The hard row is temporal. `create_edge_temporal` without `graph=` writes the temporal globals and a
structural shadow in `out`/`in` and **no `rdf_edges` row** (the engine docstring says the row is written only
"when `graph=` is supplied"). Those edges are in `out`/`in` and nowhere in SQL, so a table-driven index cannot
produce them. Making temporal edges own a structural row fixes that and changes behaviour (R5).

Python also writes: snapshot restore and the Native API helpers. Those were not inventoried.

## R4. `^NKG` cannot live in the index as written

`GraphIndex.InternNode` allocates ids with `$Increment(^NKG("$meta","nodeCount"))` and writes the name maps
with `Set`. Measured: intern two nodes inside a transaction, roll back.

```text
after rollback: nodeCount=2, names kept=0
next real node gets id 2 (nodeCount 3); ids 0..1 have no name
```

The ids are gone from the name maps but not from the counter: holes. Anything that treats every id below
`nodeCount` as a node (a PageRank denominator, a Rust array sized by it) is wrong afterwards. `InternNode` also
holds `Lock +^NKG("$NI", id)` until commit inside a transaction, which is R2 again. (`BulkIngestEdgesSQL`
already interns inside `TSTART`, so today's bulk path carries both hazards.)

Recommendation: do not put `^NKG` in the write path. It is a snapshot for the Arno readers; keep it rebuilt, and
let the index supply the staleness stamp the readers compare against (the `__version` the index maintains).

## R5. Behaviour the unification changes

- **Temporal edges become SQL-visible.** Once they own an `rdf_edges` row they appear in `edge_count`,
  Cypher `MATCH`, undirected patterns and exports. That is a visible change and wants a flag or a separate
  edge class.
- **What `deg` counts.** The table can hold parallel rows (multigraph mode, `ekey`); the index must decide
  whether `out` is a set (one leaf per triple, today's default) or keyed by `ekey`. The spike indexed a unique
  triple only.
- **Weights.** Today the weight comes from the `qualifiers` JSON (`WeightOf`). Parsing JSON in the index
  callback runs on every insert; not measured.
- **The empty graph id arrives as `$Char(0)`.** Mapping it is mandatory (README, item 3).

## R6. Moving an existing install

`%BuildIndices` built the index at 60,000 rows in 0.29 s (about 200,000 rows/s), so 10 million edges is in
the order of a minute, not hours. Untried: building over a table that already has the old `^KG` stores,
running the old and new roots side by side for one release, switching readers, and the rollback path. The
upgrade has to build into the new root first and only then flip the readers.

## R7. Bulk load

`INSERT %NOINDEX` skips every index on the statement, including the unique one, and `%NOCHECK` skips
uniqueness too. A deferred-index bulk path must dedupe by itself, or the build later fails or the counts
include duplicates.

## R8. Operational, not yet measured

Journal volume (each edge now journals the row plus five global sets inside the transaction; today the
primitives do the same sets after the SQL, so the volume is similar but unmeasured), mirroring behaviour of a
functional-index class, sharded namespaces (`SUPPORTSSHARDING` was set, not tested), compile order on deploy
(the table class needs the index class first; `class_deploy` already orders functional-index types), and
`PurgeIndex` scope (must never touch a shared root).

## R9. The check that passed while the stores were wrong

The first `Verify()` recounted from the table and compared per source. A source with no remaining rows is
never visited, so the ghost counters of R1 were invisible, and a dozen "0 mismatches" lines were true and
meaningless. Any real test must walk the derived stores and look for keys with no row behind them, and must
include rollback, concurrent writers and a failed statement in the middle of a batch.

## What this changes in the plan

| Step                                                                                | Decision                                                                                 |
| ----------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| 0. ADR: authoritative / derived / cache tiers, derived data under its own root      | Do first. It is the precondition for every purge and rebuild below                       |
| 1. Index `out`/`in` and the version stamp only; `deg`/`degp` advisory or computed   | Feasible as measured. Needs the R3 writers redirected and a rule for the temporal shadow |
| 2. Give temporal edges a structural row (flagged)                                   | Behaviour change. Decide with the owners of the temporal API                             |
| 3. `^NKG` stays a rebuilt snapshot, stamped by the index                            | Keeps R4 out of the write path                                                           |
| 4. Audit readers of `^KG("deg")` and `("degp")` for exactness                       | Needed before step 1 ships                                                               |
| 5. Test harness: property test over random insert/update/delete/rollback/concurrent | Must walk the stores for keys with no row (R9)                                           |

Not recommended: exact counters through locks (R2), interning in the index (R4).
