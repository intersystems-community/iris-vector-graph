<!-- markdownlint-disable MD013 MD060 -->

# ObjectScript line coverage of the 2026-10 changes

Measured 2026-10-08 with IRIS's own line monitor (`%Monitor.System.LineByLine`), the same
mechanism `iris_coverage` drives, on `ivg-iris-enterprise` (2026.3.0AI.113).

## How

1. `bbsiz=4096` in the `[config]` section of `iris.cpf` (`Config.config`), then a restart. With
   the default `-1` the monitor starts and collects nothing. (`iris_coverage` reports the same
   condition as `BBSIZ_NOT_CONFIGURED`; its hint names `gmheap`, but `bbsiz` is what mattered
   here.)
2. A driver class (kept as `scripts/ObjectScriptCoverageMonitor.cls.txt`) starts the monitor
   on a list of INT routines (`Package.Class.1`), waits for a stop file while pytest runs
   against the server, **reads the counters before `Stop()`** (`Stop()` clears them: reading
   after it returned zero rows on the first attempt), and writes each INT line with its count
   and text.
3. A line counts as code when it is indented, not a comment, not a label and not a bare brace.
   Hit means the monitor's count is above zero.

## Result

Changed or added ObjectScript methods: **554 of 621 code lines, 89.2%**. Two namespaces were
monitored (the scratch `IVG236`, and `USER` for the tests that need the Arno library or run
there); counts are merged only where the compiled line text is identical.

| Class.method                                 | Lines | Hit |     % |
| -------------------------------------------- | ----: | --: | ----: |
| BM25Index.Insert                             |    40 |  39 |  97.5 |
| BM25Index.Delete                             |    15 |  11 |  73.3 |
| BM25Index.Upgrade                            |    45 |  40 |  88.9 |
| BM25Index.RemoveDoc / RequireWritable        |    22 |  22 | 100.0 |
| BM25Index.Search                             |    60 |  59 |  98.3 |
| BM25Index.Build / IndexDoc                   |    66 |  60 |  90.9 |
| AlgoCache (all six methods)                  |    17 |  17 | 100.0 |
| ArnoAccel.ReaderRevisions / Load             |    28 |  24 |  85.7 |
| ArnoAccel.NativeResult                       |     4 |   1 |  25.0 |
| NKGAccelTraversal.BFSJson                    |    48 |  36 |  75.0 |
| NKGAccelAdjacency.ExportAdjacencyWithPreds   |    36 |  24 |  66.7 |
| EdgeScan keyed write / delete                |    66 |  60 |  90.9 |
| Loader.Load, BenchFormat.Seed                |    43 |  43 | 100.0 |
| TraversalBuild.BuildKG / Build2HopExactStats |    95 |  85 |  89.5 |
| Eraser.InvalidateDerived                     |     4 |   4 | 100.0 |

## What the first pass found

The first pass (before the tests below) read 71.7% and exposed three real gaps:

- `BM25Index.Build` and `IndexDoc` were at 0%: no live test called `Build`, though both were
  rewritten. Added `test_build_writes_the_same_schema_2_state_as_inserting_the_same_documents`
  and `test_upgrade_equals_a_fresh_build`.
- The keyed-edge token test passed for the wrong reason. `WriteAdjacencyKeyed` tells which
  ekey the bare leaf belongs to from `rdf_edges` rows; with none, a "second edge" is a weight
  refresh, so no parallel edge was ever created. The tests now insert the SQL rows, assert the
  `#` counter and `deg`, and fail on the unfixed `EdgeScan` (checked).
- Lock timeout, rollback, `Get`, `InvalidateGraph`, `LibraryVersion`, the native entry points
  and the `Delete` of a missing index had no tests. Added. (A lock taken through the Native
  API is not seen by ObjectScript's `Lock` command, so the tests hold the lock in an
  ObjectScript terminal process.)

## Still unhit, and why

- **Error and rollback `Catch` blocks** in `Delete`, `Upgrade`, `BuildKG`: no test makes them
  fail. `Insert`'s rollback is covered.
- **`NativeResult` success path** (`CallFn`, the `ERROR` check): the proof file is empty, so
  nothing routes to a Native Reader, and the gate calls them only through `ArnoAccel`'s
  `*NativeJson`, whose lines show hit. The `CallFn` lines read zero even while the gate ran;
  see the caveat below.
- **Chunked export** (`ExportAdjacencyWithPreds` over 3 MB) and `BFSJson`'s `CHUNKED:` branch:
  needs a graph with a 3 MB adjacency.
- **`Search`'s mid-flip idf fallback**: reachable only if `Upgrade` flips while a `Search` that
  began on schema 1 is running; not deterministic to test.

## Caveats

- The counts are a lower bound. A Native API connection can be served by a pooled server
  process that loaded the routine before `Start()`; the monitor instruments routines as they
  load, so such a process is not counted. This probably explains `NativeResult` reading 25%
  while the gate passes.
- The monitor works at INT-line granularity. Lines that are only `Try {`, or that end a
  method with `Return` and a closing brace, often read zero although they ran (about 25 such
  lines in the unhit list). They are counted as unhit above; treating them as hit would put
  the total near 93%.
- A coverage run on the same container as another suite collides with it (both write to
  `USER`). The first USER pass showed eight setup errors in `test_store_bfs_deep.py` that did
  not recur when re-run alone.

## Repo-wide baseline (interim, 2026-10-09)

One all-class line monitor in USER, one pytest process per file over `tests/integration` and
`tests/e2e` (about 305 of 318 files; the monitor stopped early, so the last files are missing).
`bbsiz` must be large: 4096 caps every IRIS process at 4 MB and fails FHIR setup with `<STORE>`
and long sessions with `-400`; 262144 works.

Executable lines hit: 5804 of 11169 (52.0%) across 54 classes. Largest gaps by uncovered lines:
FHIRGraph 1832 (9.2%), LedgerStats 537 (22%), TemporalIndex 304 (55%), Subgraph 183 (36%),
NKGAccelAdjacency 164 (31%), Ledger 134, VecIndex 134, CypherFn 123 (0%), LedgerApply 120,
BM25Index 117, Service 103 (0%).

FHIRGraph's low figure is partly an artifact: its e2e files ran with the monitor active but the
FHIR server work runs in pooled server jobs that loaded the routine before `Start`.

### Correction: FHIR code runs in IVGFHIR

Monitored routines are per namespace. The FHIR and genomics tests run in `IVGFHIR`, so the USER
monitor saw almost none of `FHIRGraph`. A second monitor in `IVGFHIR` over the 29 FHIR test files
gives `FHIRGraph` 1753/2018 (86.9%, was 9.2%). Merging the two passes (a line counts when either
pass hit it; the compiled line text is identical) and leaving out the `%Persistent`-generated
`LedgerStats` / `LedgerRevision` code and the runtime-generated `User.CypherFn`:
7260 of 10414 executable lines, 69.7%.

Largest remaining gaps: TemporalIndex 304 uncovered (55%), FHIRGraph 249, Subgraph 187 (36%),
NKGAccelAdjacency 164 (31%), Eraser 147, VecIndex 134 (52%), Ledger 134, LedgerApply 120,
BM25Index 117, Communities 107.

## Result after the added tests (2026-10-09)

Executable ObjectScript lines hit, hand-written classes only (the `%Persistent`-generated
`LedgerStats` / `LedgerRevision` code and the runtime-generated `User.CypherFn` are left out),
merging three monitors that ran the whole integration and e2e suite: USER, IVGFHIR and IVG236,
each started before any test session opened:

**8013 of 10499 lines, 76.3%** (was 52.0% as first measured, 54.7% for hand-written classes only).

Two measurement traps, both of which made the earlier numbers too low:

- Monitored routines are per namespace. FHIR code runs in `IVGFHIR` and the scratch tests in `IVG236`.
- A DBAPI or Native API call is served by a pooled server job. A job that loaded the routine before
  `Start` is not instrumented, so the monitor must start on a freshly restarted container, before the
  first test session. A warm pool counted 73.8% for the same suite.

Reproduce with `scripts/line_coverage/run_line_coverage.sh` (loads `Cov.cls` into each namespace first).

Added tests: `test_temporal_index_queries`, `test_subgraph_json`, `test_communities_leiden`,
`test_nkg_adjacency_export`, `test_vecindex_trees`. They found one defect, fixed:
`TemporalIndex.GetDistinctCount` answered 11 for an empty window (all HyperLogLog registers zero).

Open: `VecIndex.Distance` has no `mahalanobis` branch, so an index created with that metric ranks by
cosine and `Mahalanobis` is unreachable from `Search`. `test_ledger_us06_history` and
`test_ledger_us11_temporal_coexist` error in setup when run after the other ledger files but pass alone.

Largest remaining gaps: FHIRGraph 249, Eraser 184, NKGAccelAdjacency 154 (private `ExportAdjacencyKG`
and `ExportAdjacencyFromSeed`), Ledger 134, Service 103 and MCPTools 92 (REST and MCP handlers that need
a web application), NKGAccelTraversal 100, TraversalBFS 97, LedgerApply 95.
