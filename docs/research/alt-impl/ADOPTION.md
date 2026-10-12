<!-- markdownlint-disable MD013 MD060 -->

# Alternative implementation: what was built, what was proven, what to adopt and when

Status: exploratory. Nothing here is on `main`, nothing is pushed or published. The aim of the exercise was to build the
design of ADR-0008 to 0010 far enough, in isolated branches, to decide how and when to take parts of it.

## What exists

Eleven work packages, each built by a separate agent in its own worktree from `main` (6f17be0f), each with its own spec in
`specs/237` to `specs/247` (gitignored, force-added). Ten of them are merged onto the integration branch
**`alt-integration`**, which sits on `native-handle-module` (the host module, A1). The eleventh, `host-sites-2`, is
deliberately left out (see below). Method of checking: the lead reviewed each report, merged, ran the unit suite the way CI
does (`scripts/ci-parity.sh`), then ran every live test serially on `ivg-iris-enterprise`.

| Gate on `alt-integration`                                      | Result                                                                                              |
| -------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| `scripts/ci-parity.sh 3.12` (container hidden)                 | PASS, wheel carries 50 `Graph.KG` classes                                                           |
| Live tests of the new packages                                 | all pass, with two known exceptions (the `Loader` case, and the oracle's lenient case) listed below |
| Python 3.13 parity run, full integration suite, coverage gates | not run                                                                                             |

## The packages

| Package (branch `alt/…`)  | What it does                                                                                                                                          | Unit | Live (enterprise)                                                               | Risk    | Depends on                        |
| ------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- | ---- | ------------------------------------------------------------------------------- | ------- | --------------------------------- |
| `derived-oracle-harness`  | Test oracle that walks `^KG` out/in/deg/degp for entries with no backing row (the check that would have caught the ghost counters)                    | 28   | strict cases xfail as designed; lenient case open (F4)                          | none    | nothing (test-only)               |
| `freshness-module`        | `Freshness(host)`: a rebuilt store is current iff its built-from stamp equals the version token; replaces `_nkg_dirty` once wired                     | 16   | 4 pass                                                                          | low     | `host.py`; engine wiring is later |
| `migration-as-data`       | The docs and edge-vector step's SQL as named `Statement` templates with golden-text tests and one `run_statement`                                     | 23   | covered by the unit step tests; no live run                                     | low     | nothing                           |
| `single-graph-backfill`   | One constant UPDATE for the `rdf_labels`/`rdf_props` graph_id backfill when `nodes` holds one graph                                                   | 16   | 2 pass: result identical to the old path (named, default)                       | low     | nothing                           |
| `host-sites-1`            | `kg_node_stores`, `lazy_kg`, `arno_bridge`, `schema._call_classmethod[_large]` reach IRIS through `host_for`                                          | 9    | embedded and lazy_kg e2e pass (16); `test_bfs_arno` errors pre-date this change | low-med | `host.py` (A1)                    |
| `temporal-erase`          | `TemporalIndex` is the only writer of the temporal globals; `Eraser` delegates (`EraseGraph`, `EraseAll`, `EraseNodes`)                               | 13   | 4 pass                                                                          | medium  | ADR-0002                          |
| `adjacency-owner`         | `Graph.KG.Adjacency` is the only writer of `^KG("out"/"in")`; `EdgeScan`'s primitives delegate; `LedgerApply`/`Loader`/`BenchFormat` route through it | 43   | 5 pass; `Loader` case needs a scratch namespace (skipped)                       | medium  | ADR-0001 amendment                |
| `adj-degree-helpers`      | `Graph.KG.Adj` (`Degree`, `NextSource`, `HasEdges`); `Algorithms`, `Centrality`, `PageRank`, `Subgraph` stop reading `deg`/`degp`                     | 174  | 11 pass                                                                         | medium  | ADR-0010                          |
| `adj-traversal-readers`   | Same for `TraversalBFS`, `TraversalKHop`, `TraversalBuild`, `ArnoAccel`; dead `^KG` fallback in `NKGAccelAdjacency` deleted                           | 13   | 5 pass, with the stored counters corrupted on purpose                           | medium  | `adj-degree-helpers` (`Adj`)      |
| `temporal-structural-row` | Flag `temporal_structural_rows` (default off): a temporal edge also owns a marked `rdf_edges` row; idempotent backfill with a read-only dry run       | 17   | 3 pass                                                                          | medium  | ADR-0009, major-version decision  |
| `host-sites-2`            | About 120 engine/store call sites moved onto `Host`; about 70 test files re-seamed                                                                    | 72   | not merged                                                                      | high    | `host.py` needs a void operation  |

## Findings from building and validating (read these before adopting)

- **F1. `host-sites-2` is not adoptable as built.** Merged, it left 139 unit failures in a container-less CI run
  (`test_query_fastpath_unit`, `test_231_*`, `test_235_*`, `test_nodes_edges_unit` and others). Its agent verified with a
  container visible, where the old seam still happened to work. Backed out of `alt-integration` (revert commit). Also
  needs a `Host` void operation (14 `classMethodVoid` sites stay raw) and an `orderAll` mapping check. Redo as smaller steps
  (store first, then engine), CI parity after each.
- **F2. The packages were built on `main`, not on `native-handle-module`**, so each re-created or lacked `host.py`. Resolved
  at integration (host-sites-1's extended version wins). For the next round, branch agents from the integration branch.
- **F3. Four live-test defects in the agents' own tests, all fixed on `alt-integration`**: a rollback "ghost" that the
  autocommitting connection committed; `create_edge` upserts so it cannot make parallel edges (use a row with its own `ekey`
  plus `WriteAdjacencyKeyed`); the Native API returns `None` for `""` (an unbounded `NextSource` loop hung the run, and a
  snapshot compare failed); `classMethodValue` on a void method raises. None was a defect in the ObjectScript.
- **F4. Resolved: the oracle's lenient case, and a correction to ADR-0004's reasoning.** The rollback steps used the SQL
  connection's `rollback()`, but the engine's Native API handle is a dedicated connection (`_iris_obj`; the test fixtures
  share one native connection too), so SQL-side rollback cannot undo adjacency written through the Native API. ADR-0004's
  "the Native API rides the same connection as the DBAPI cursor" is therefore not true in the engine; the rollbacks the engine
  guarantees are the ones ObjectScript owns (`Ledger.Commit`, `Eraser`). A caller that disables autocommit, writes a row by SQL
  and an edge through the engine, then rolls back by SQL leaves the adjacency behind: a narrow but real hazard in the baseline
  too. The oracle now rolls back inside a Native transaction: lenient passes (adjacency never drifts across 3 seeds x 40
  random steps), strict xfails on the `$Increment` ghost counters (RISK R1, confirmed directly: `deg=1 degp=1` after a
  rolled-back write). After the degree-cache work the strict cases should pass and the xfail reason becomes obsolete.
- **F5. Pre-existing PageRank multigraph leak (not introduced here).** The divisor counts parallel edges but each distinct
  (predicate, target) gets one share, so rank mass leaks. Kept unchanged by the degree work; worth its own fix.
- **F6. Traversal semantics need a decision.** `KHopCount`'s counting branch counts distinct triples, the stored `deg` it
  replaces counted parallel edges (DEG-AUDIT V2). Same on single-edge graphs, different on multigraph.
- **F7. An agent broke the no-container rule** (temporal-structural-row, host-sites-1 and host-sites-2 each ran unit files
  whose fixture connects to IVG). No intentional writes; a few files write and clean `ng_`-prefixed rows. Worth checking the
  enterprise container for strays before the next release run.
- **F8. Guard tests that this design reverses** were repointed on purpose, not worked around: the multigraph single-edge writer
  pins now hash `Adjacency.Put`/`Remove` normalised back to the old `EdgeScan` text (byte-identical, one dropped comment
  restored); the coverage floor lists `Adj` and `Adjacency`; `test_adjacency_counter_ownership` reads `Adjacency`. Still to
  rewrite when the counters go: `test_no_functional_index_declaration`, and ADR-0001 needs an amendment (a third class touches
  `^KG`).

## Performance

Measured by a separate agent on `IVGPERFA` (baseline 6f17be0f) and `IVGPERFB` (`alt-integration`): same generated graph
(499,984 edges, 48,889 sources, one 100,000-edge hub), five interleaved reps, every checksum equal. Full report:
[PERF.md](PERF.md). Host load was 11 to 54, so only the large ratios are well above noise. The generator keeps the stored
counters consistent, so the drift the alternative guards against was not exercised.

| Item                                        | A median        | B median               | B/A                      | Reading                                                                  |
| ------------------------------------------- | --------------- | ---------------------- | ------------------------ | ------------------------------------------------------------------------ |
| `WriteAdjacency` (µs per edge)              | 1.7             | 1.9                    | 1.12                     | extra delegating call; irrelevant next to client RTT                     |
| `DeleteAdjacency` (µs per edge)             | 2.1             | 2.3                    | 1.12                     | same                                                                     |
| `WriteAdjacencyKeyed` (µs per edge)         | 646             | 638                    | 0.99                     | same (dominated by a SQL lookup)                                         |
| `PageRankGlobalJson`, 20 iterations         | 722 ms          | 835 ms                 | 1.16                     | slower: the degree pre-pass                                              |
| PageRank `RunJson`, 20 seeds + hub          | 1280 ms         | 1385 ms                | 1.08                     | slightly slower                                                          |
| Degree centrality out, all predicates       | 109 ms          | 292 ms                 | **2.67**                 | slower: a second walk per source                                         |
| Degree centrality out, one predicate        | 100 ms          | 174 ms                 | 1.74                     | slower                                                                   |
| `KHopCount`, 20 seeds x 5                   | 0.19 ms         | 49.8 ms                | **~270x**                | slower: the O(1) stored-counter answer was removed                       |
| `KHopCount`, hub x 200                      | 0.085 ms        | 1556 ms                | **~18,000x**             | 7.8 ms per call on the hub                                               |
| BFS with predicate sequence                 | 237 ms          | 225 ms                 | 0.95                     | same                                                                     |
| WCC                                         | 5313 ms         | 5405 ms                | 1.02                     | same                                                                     |
| `BuildNKG`                                  | 7655 ms         | 7367 ms                | 0.96                     | same                                                                     |
| `Build2HopStats`                            | 398 ms          | 537 ms                 | 1.35                     | slower (routing sums through `in`)                                       |
| `Adj.Degree`, 200 / 1,000 / 100,000 entries | 0.057 µs stored | 30.7 / 154 / 15,058 µs |                          | about 0.15 µs per out-edge; the stored read is flat                      |
| `import iris_vector_graph`                  | 251 ms          | 249 ms                 | 1.0                      | no change                                                                |
| `ClientHost` call over the fake             |                 | +0.1 to 0.35 µs        |                          | negligible next to a real Native API round trip (inferred, not measured) |
| `rdf_labels` backfill, 100,000 rows         | 6.36 s          | 0.41 s                 | **0.065 (15.5x faster)** | the one clear win; one table, synthetic                                  |

**Verdict: as built, the reader packages make the runtime slower, not faster.** The only clear win is the migration
backfill. The reader cost comes from one decision: ADR-0010 removes the stored degree and counts it on demand, which trades
speed for an answer that is exact when a transaction rolls back. Three of the readers pay more than they have to:

- **`KHopCount` (the real problem).** Removing the stored-counter shortcut turns an O(1) answer into a walk. Options: keep the
  stored counter but trust it only while it is fresh (use `Freshness`: counter stamp equals the version token, otherwise
  count), which is exact and fast on read-mostly graphs; or accept the walk and say so. Needs a decision before
  `adj-traversal-readers` is adopted.
- **Degree centrality.** Counting at the point of use walks each source twice. Count while the loop already iterates the
  source (one walk): expected back to about 1.0x. A fix, not a design question.
- **`PageRankGlobalJson` and `Build2HopStats`.** Small (8 to 35 %); the pre-pass and the routed sums are fixable or
  acceptable, to be settled together with the above.

**Follow-up spike ([degree-cache](../spikes/degree-cache/README.md)): the cost is fixable and the idea is sound.** Writers bump a
per-source generation (`$Increment` is safe here, because a rolled-back write only over-invalidates) and take a shared lock;
`Degree` returns a cache entry when its generation matches, and fills it only when no writer was in flight (an exclusive
no-wait probe) and the generation did not move. Measured on the same graph: `KHopCount`-style reads 49.9 ms to 0.06 ms warm
(hub call 7.6 ms to 0.48 µs), the all-sources degree loop 93.6 ms to 28 ms against 12.3 ms stored, writes level with today
(1.15 to 1.24 µs against 1.07 to 1.31) or 15 to 25 % cheaper without the lock. Correctness: without the probe a cross-process
rollback leaves a wrong valid entry (shown); with it the entry is right; 6,000 random operations with rollbacks gave 0
mismatches; four writers in opposite orders finished in 0.1 s with no deadlock; a 200,000-write transaction committed. What
it needs from the rest of the work: every writer of `out`/`in` bumps the generation and takes the shared lock, which only
works once `adjacency-owner` is in. So `adj-degree-helpers` should be reworked to use the cache, not adopted as built.

Writes get no faster yet, because nothing has removed the `deg`/`degp` increments from the write path. That removal (two
`$Increment`s per edge, and the ghost-counter class of bug) is where a write-side gain would come from, and it is not
measured.

## Recommended adoption order

1. **Now, low risk, test-only or unwired:** `derived-oracle-harness` (keep the xfail reason), `freshness-module` (module only),
   `migration-as-data`, `single-graph-backfill` (equivalence proven live; measure the gain on a real legacy database),
   `host-sites-1`.
2. **Next, behaviour-preserving ownership moves, live-proven:** `temporal-erase` (decide: node erase now removes temporal
   edges, as ADR-0004 intends) then `adjacency-owner` (write the ADR-0001 amendment in the same change).
3. **Rework before adopting:** `adj-degree-helpers` + `adj-traversal-readers`, after `adjacency-owner`. Replace the counting
   `Degree` with the generation-validated cache from the degree-cache spike (writers bump the generation and hold the shared
   lock inside `Adjacency`), which also settles `KHopCount` and centrality; then re-run the benchmark and resolve F5 and F6. Only then remove the `deg`/`degp` writes from
   `Adjacency`, `TraversalBuild` and the other writers, and drop the guard allow-lists the packages left behind.
4. **Needs a product decision:** `temporal-structural-row` (ship behind the flag; default flip is a major-version change).
5. **Redo:** `host-sites-2`, in smaller pieces after a `Host` void operation exists.

## Not built in this round

Engine-maintained adjacency from the edge table (the functional-index step, C2), wiring `Freshness` into the engine
(`freshness-module` lists the 11 `_nkg_dirty` sites and their replacements in its report), the `^NKG` stamp, a migration
for existing installs, and the ADR amendments. An inbound request from the opsreview session (a ledger `redact` that keeps
ordinals and verify working, an extension of ADR-0004) is separate from this work and is noted in the ledger discussion only.
