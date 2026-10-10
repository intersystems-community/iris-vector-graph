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

Reproduce with `scripts/line_coverage/run_gate.sh` (loads `Cov.cls` into each namespace first).

Added tests: `test_temporal_index_queries`, `test_subgraph_json`, `test_communities_leiden`,
`test_nkg_adjacency_export`, `test_vecindex_trees`. They found one defect, fixed:
`TemporalIndex.GetDistinctCount` answered 11 for an empty window (all HyperLogLog registers zero).

Open: `VecIndex.Distance` has no `mahalanobis` branch, so an index created with that metric ranks by
cosine and `Mahalanobis` is unreachable from `Search`. `test_ledger_us06_history` and
`test_ledger_us11_temporal_coexist` error in setup when run after the other ledger files but pass alone.

Largest remaining gaps: FHIRGraph 249, Eraser 184, NKGAccelAdjacency 154 (private `ExportAdjacencyKG`
and `ExportAdjacencyFromSeed`), Ledger 134, Service 103 and MCPTools 92 (REST and MCP handlers that need
a web application), NKGAccelTraversal 100, TraversalBFS 97, LedgerApply 95.

### Second round (2026-10-09)

Added `test_traversal_khop_paths`, `test_traversal_bfs_json` and `test_ledger_ops_branches`, and ran the
FHIR files under a USER monitor as well (several of them connect to USER, not IVGFHIR, so only a USER
monitor sees them). Merged raw: **8417 of 10669 lines, 78.9%**.

A second figure leaves out lines no test can reach by design: the entry line of every compiled
method and the generated embedded-SQL stub subroutines (`%0dsql*`, `%xxsql`), which run once when a
statement is first prepared. On that basis: **8184 of 9740, 84.0%**.

Defect found and fixed: `TraversalBFS.ReadBFSPage` handed back the next unread item as the cursor, and
the next call resumed after it, so one result was lost at every page boundary (a result set larger
than the page size, 500 for the engine). The cursor is now the last item returned.

### Third round

Added `test_snapshot_files`, `test_embed_queue_process` and `test_bench_format`. Merged over every run
above: **8549 of 10773 lines, 79.4% raw; 8316 of 9839, 84.5%** leaving out entry lines and generated
SQL stubs.

Defect found and fixed: `Snapshot.ExportGlobals` and `ImportGlobals` called `%GlobalEdit.Export` and
`.Import`, which do not exist, so neither could ever run. They now use `$System.OBJ.Export` /
`$System.OBJ.Load` on a `.GBL` item. Nothing in the Python package calls them; they were reachable only
through a raw class-method call.

Not worth chasing: `NKGAccelTraversal.KHopNeighbors` and the Brandes neighbourhood fallback read the
legacy no-shard `^KG("out", node, ...)` layout that production no longer writes (the class header says
so), `Service` and `MCPTools` need a web application, and `IVG.CypherEngine` has no
caller in the Python package. `iris.vector.graph.GraphOperators` is called from `operators.py` and has
63 uncovered lines still to test.

### Fourth round

Added `test_nkg_legacy_layout`, `test_graph_operators_sql`, `test_ledger_lock_and_chunks` and
`test_fhirgraph_pure_methods`. Merged over every run: **8730 of 10846 lines, 80.5% raw; 8496 of 9912,
85.7%** leaving out entry lines and generated SQL stubs.

Defect found and fixed: `NKGAccelTraversal.PPRNative` printed `alpha` as `.85`, which is not valid JSON,
so the legacy-layout PPR answer could not be parsed.

The legacy-layout readers are reachable after all: `BenchFormat.Seed` writes that layout and the
process-private NKG switch (`NKGAccelLoader.ResetCache`) makes `UseNKG()` answer false.

### Fifth round

Added `test_betweenness_neighbourhood`. Merged over every run: **8779 of 10846 lines, 80.9% raw; 8545 of
9912, 86.2%** leaving out entry lines and generated SQL stubs.

What is left is mostly code a test cannot or should not reach: the compiled cursor loops of embedded
SQL that only run on first prepare, `SqlErr` and `TROLLBACK` branches that need an injected SQL failure,
`Service` and `MCPTools` (web application handlers), `ExportAdjacencyFromSeed` (no caller anywhere in
`iris_src`) and `IVG.CypherEngine` (no caller in the Python package).

### Sixth round: failure injection

A `BEFORE DELETE` or `BEFORE INSERT` trigger that sets `%ok=0` makes one table refuse its rows, which
reaches the `SqlErr` and `TROLLBACK` branches without touching the code under test. Added
`test_eraser_failures` (every store, through `EraseGraph`, `EraseNodes` and `EraseNodeIds`, plus
`EraseAll` and `EraseEdges`; each asserts the error and that nothing was erased) and
`test_ledger_store_failures` (a commit whose node, label, property or edge insert is refused leaves the
head alone and succeeds on retry).

Merged over every run: **8856 of 10882 lines, 81.4% raw**. The adjusted figure stays at 86.2% because the
denominator grew: embedded-SQL cursor names such as `%c10418413` change at every recompile, so a class
compiled in two runs counts those generated lines twice.

### Seventh round: the classes "that need a web application"

`Graph.KG.Service` and `Graph.KG.MCPTools` can be run without a web gateway: the REST handlers only
read `%request.Content` and write to the current device, and the MCP tools are plain instance methods.
A terminal session with stub `%request` / `%response` objects, or one that instantiates the tool,
runs the same code (`test_rest_service_handlers`, `test_mcp_tools`). A throwaway subclass compiled into
the scratch namespace reaches the Private `ExportAdjacencyFromSeed` (`test_adjacency_from_seed`).

Merged over every run: **9034 of 10999 lines, 82.1% raw; 8754 of 10064, 87.0%** leaving out entry lines
and generated SQL stubs.

Defects found and fixed this way, none of which a call from the Python package would have shown:

- `Service.WriteJSON` and `WriteError` called `%response.Write`, which `%CSP.Response` does not have, so
  every REST handler failed, including its own error reply.
- `MCPTools.RunSQL` called `%GetColumnName` on the result, so every SELECT returned an error.
- `MCPTools.PPRWalk` read PageRank rows as arrays (every node came back as `""` with score 0) and passed
  `topK` as the `bidir` argument.
- `MCPTools.EvidenceSearch` used `next()` / `getColumn()`, which `iris.sql` results do not have.

Not fixed, because it cannot be run here: `MCPTools.CypherQuery` uses the same `rs.next()` /
`getColumn()` calls and imports `iris_vector_graph`, which is not installed in the container's embedded
Python, so it ends in its error branch before reaching them.

### Eighth round: embedded Python

With the package and its dependencies copied into the container's embedded Python path
(`tests/integration/_embedded_pkg.py`), the Python-language methods of `IVG.CypherEngine` and
`Graph.KG.MCPTools` run. That exposed the embedded execution mode of the engine itself:
`EmbeddedCursor.description` was always empty, `iris.createIRIS` does not exist inside IRIS (now
`EmbeddedIRIS`), and an empty-string parameter is bound as NULL (now inlined). An inlined statement that
touches no row raised a message-less `SQLError` (SQLCODE 100), which broke edge creation through an
embedded engine; it is now treated as a zero-row result.

Merged over every run (17 monitored passes): **9145 of 11234 lines, 81.4% raw; 8614 of 9711, 88.7%**
leaving out entry lines, generated SQL stubs and generated cursor variables. The raw figure fell a little
against the previous round although more lines ran: classes edited after an earlier monitored run
(`Service`, `MCPTools`, `CypherEngine`) now count their old and new compiled text, so each carries up to
100 stale lines in the denominator. A full cold pass over all three namespaces would remove that; the
last one was taken before the fixes of the last four rounds.

The merge script is `scripts/line_coverage/merge.py` (pass the `.out` files of each monitored run).

### Ninth round

Added `test_fhirgraph_register_e2e` (validation refusals and idempotent registration against the live
repository). `FHIRGraph` went from 1766 to 1773 of 1936 lines; the merge over all monitored passes is
**9164 of 11306, 81.1% raw; 8621 of 9715, 88.7%** on the adjusted basis.

Tried and dropped: refusing-table triggers on `ledger_revisions` and `ledger_stats` to reach the
`Enable`, `Disable` and `SetStrict` rollback branches. They reached 5 of 26 such lines, so the test
was removed rather than kept as a weak assertion. Returns are now small (a few lines per test): the
remainder is `Service` dispatch (HTTP), ledger and FHIR rollback branches that need a write to fail
at a precise point, and the stale-text lines described above.

### Tenth round: the REST dispatcher without a gateway

`Graph.KG.Service.DispatchRequest(url, method, 0)` runs the whole URL map in a terminal session when
`%request`, `%response` and `%session` are stubbed, so the routes, the 404 / 405 answers and the handlers
can be tested without HTTP (`test_rest_service_handlers`, 10 tests). The first end-to-end call found that
no request had ever got past parsing:

- every handler read its defaults with `$Get(in.k, 50)` on a dynamic object, which throws
  `<INVALID CLASS>`;
- `PyOps.VectorSearch` and `HybridSearch` called `fetchall()` on an `iris.sql` result.

`Service` from its latest compile alone: 70 of 102 lines (69%), up from 20 before the dispatcher test
(the merged table above still counts three compiled versions of it). Merged over all monitored passes:
**9227 of 11411, 80.9% raw; 8684 of 9780, 88.8%** adjusted.

### Eleventh round: one clean cold pass

One uninterrupted pass over all three namespaces (19 scratch-namespace files in IVG236, 30 in IVGFHIR,
333 in USER), each started on a freshly restarted container, gives **8176 of 10505 lines, 77.8% raw;
7777 of 9447, 82.3%** adjusted. That is lower than the merge of the shorter passes (81.1% / 88.8%), and
the reason is a measurement property, not missing tests: a test session that recompiles a class in
the namespace being monitored (several fixtures do) stops the monitor counting that routine in jobs
already running, so a long pass loses lines that a short pass right after a restart keeps. Four ledger
files also errored in setup in that pass (the 30 s head-lock timeout), which the shorter passes did
not hit.

The figure to quote is the union of all monitored passes: **9318 of 11483, 81.1% raw; 8692 of 9784,
88.8%** adjusted. Every line in it was counted as executed in at least one monitored run.

### Twelfth round

`test_fhirgraph_failures_e2e` (rebuild, sync and drop with a refusing table) and
`test_ledger_records_query` (`ledger_records`, `CanonicalRecord`) added. Union of all monitored passes:
**9511 of 11759 lines, 80.9% raw; 8873 of 9952, 89.2%** adjusted. Leaving out bare `Try {`, `Catch {`,
`Quit` and `Return` lines as well, which the monitor is known to report as unexecuted when they run:
8850 of 9834, 90.0%. Of the remaining `FHIRGraph` gap (119 lines on the latest union), a large share
is of that kind.

### Thirteenth round

Adjacency drift repair in `FHIRGraph` (`ResyncKey` removing and rewriting adjacency, `DropNode`
clearing adjacency that has no SQL row) tested by writing the drift directly with
`EdgeScan.WriteAdjacency` / `Kill ^KG`. `FHIRGraph` is at 1798 of 1936 lines. Union of all monitored
passes: **9534 of 11795, 80.8% raw; 8890 of 9954, 89.3%** adjusted.

### Fourteenth round: the gate, run on the union

`scripts/line_coverage/check_gate.py` over all 29 monitored dumps: **PASS, adjusted 89.1%** against the
88.0% floor (9217 of 10350 lines; raw 9876 of 12291, 80.4%). The ratio fell a little against the
previous round although more lines ran, because three classes edited since their first monitored run
(`LedgerApply`, `Service`, `MCPTools`) now count both compiled versions in the denominator.

Added since the last figure: `EdgeScan` match scans and file ingest, the ledger half of `Eraser`, the
`LedgerApply` reification cascade (which found that deleting a relationship through the ledger never
found its reifying nodes), and the FHIR busy answers. Largest remaining gaps by uncovered lines:
`FHIRGraph` 138, `Service` 134 (mostly the older compiled version), `LedgerApply` 90 (likewise), `Ledger` 68.

### Fifteenth round: the gate's own run

`scripts/line_coverage/run_gate.sh` end to end on the committed tree: 13 USER chunks of 25 files, one
IVG236 chunk, one IVGFHIR chunk and one USERMIX chunk (the scratch-namespace tests again, monitored in
USER), the container restarted before every chunk, 15 dumps in all. No dump comes from an older compile,
so nothing is counted twice:

- **adjusted 90.3%** (8605 of 9532) — the gate's figure; **PASS** against the floor, now raised to **89.0%**
- raw 84.3% (9306 of 11038)

Two defects in the runner turned up on the way and are fixed: a failing test aborted the whole run
(`set -e` with a pipeline), and files that touch both namespaces were monitored in only one of them
(`test_mcp_tools` runs its tool calls in USER and its data in IVG236), hence USERMIX. One file
(`test_ledger_us11_temporal_coexist`) errored in setup in the first pass: the 30 s ledger head-lock
timeout, intermittent, and it passes on its own.

Largest remaining gaps: `FHIRGraph` 185 (the union of the earlier ad hoc passes reached 1798 of 1936 lines; the cause of the difference is not yet isolated),
`Ledger` 68, `BM25Index` 56, `NKGAccelTraversal` 46, `IVG.CypherEngine` 46 (needs the package in the
embedded Python path, so only the dedicated test reaches it), `ArnoAccel` 43.

### Sixteenth round: one file per monitor does not help

Re-running the IVG236, IVGFHIR and USERMIX groups with one test file per monitor (a restart for each, 65
chunks) measured **89.8%** adjusted, against 90.3% with 25 files per monitor. The theory that the fixtures'
recompiles hide lines was wrong, and the default is back to 25. What the per-file dumps do show: the same
test, run in a monitor of its own, can record almost nothing for a routine (`test_044_bm25_max_df` alone:
3 lines of `BM25Index`) while the same file run after `test_044_bm25_incremental` in one monitor records the
`SearchCore` cutoff lines (300 of 328). The monitor's count for a routine depends on which jobs touched
it first, and I have not found the rule. The gate therefore stays a floor on its own output (90.3% today,
floor 89%), not a statement that the uncovered lines it lists are untested: `BM25Index` `SearchCore` and
`SearchReport` appear uncovered in the full run and are exercised by ten passing tests.

### Seventeenth round: a classification bug, and the first fully clean run

The "inconsistent" `BM25Index` counts were not the monitor: `test_044_bm25_max_df` takes its scratch
fixture by importing it from another test module, so the runner's file match put it in the USER group
and its code ran in IVG236 unmonitored. Run alone under a monitor in IVG236 it records 159 lines of
`BM25Index`. The runner now matches such imports too.

`run_gate.sh` end to end, every dump from a restarted container and the current tree (IVG236, IVGFHIR,
13 USER chunks, USERMIX): **adjusted 91.5%** (8722 of 9532), raw 85.4% (9424 of 11038). PASS. The floor
is raised to **90.0%**. The runner now keeps each file's full pytest output under `OUT/logs/`.

Seven files errored in setup in that run (three `test_227_*` e2e files in USER chunk 1, four ledger files with
the 30 s head-lock timeout) and all pass on their own; the first run to lose their text is the reason for
the log directory. Remaining gaps: `FHIRGraph` 166 (36 generated), `Ledger` 61, `CypherEngine` 46,
`NKGAccelTraversal` 40, `NKGAccelAdjacency` 34, `Service` 32.

### Eighteenth round: what "adjusted" leaves out, made explicit

Most of `IVG.CypherEngine`'s 46 "uncovered" lines were `%NormalizeObject` and `%ValidateObject`: methods the
class compiler writes for every property, which run only on `%Save` or validation and never on a
`%RegisteredObject`. Likewise `FHIRGraph`'s 36 and `Ledger`'s `RecordsFetchRows`, `RecordsSendODBC` and
`RecordsGetInfo`, the ODBC entry points generated for every `%Query`. `linecov.py` now leaves both out of the
adjusted figure (labels starting with `%`, and `<Query>FetchRows|SendODBC|GetInfo`); the raw figure still
counts them. The same dumps as the previous round, no new run: **adjusted 92.8%**. The floor is
raised to **91%**.

### Nineteenth round

`VecIndex` similarity, distance and Mahalanobis (via a terminal session that builds `$VECTOR`s), the
dimension fallback in `Build`, the REST `OPTIONS` route, and the chunking of a 3,200-neighbour node in the
NKG export. The clean full-run dumps plus these three fresh-monitor runs: **adjusted 93.2%** (8738 of 9376),
raw 85.5% (9467 of 11074); the gate passes against the 91% floor.
