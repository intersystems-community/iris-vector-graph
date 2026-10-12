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
- **F4. Open: the derived-store oracle's lenient case.** With real transactions the strict cases fail as designed (the
  `$Increment` counters survive a rollback; confirmed directly: `deg=1 degp=1` after a rolled-back write). The lenient cases
  also report ghost `out`/`in` entries after rollback or failed-batch steps inside pytest, but a hand replay of the same
  seed outside pytest shows rows and adjacency agree, and a probe shows Native sets inside `START TRANSACTION` are undone by
  `rollback()`. Either the fixture connection or a real engine/connection interaction. Marked xfail with this reason. Until
  isolated, the oracle proves the counter drift (R1) but not the adjacency claim.
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

The alternative moves cost around; measured so far (by the lead, one machine, noisy):

| Item                                  | Baseline             | Alternative                                | Note                                        |
| ------------------------------------- | -------------------- | ------------------------------------------ | ------------------------------------------- |
| In-IRIS edge write (`WriteAdjacency`) | about 2.8 µs (`Put`) | about 3.2 µs via the delegating `EdgeScan` | one extra method call, about 0.4 µs (≈14 %) |
| Degree of a 200-entry node            | 0.07 µs stored `deg` | 39 µs counted                              | about 500x per call; readers pay O(E) once  |
| Degree of a 100,000-entry hub         | 0.07 µs              | about 13 ms (DEG-AUDIT)                    | matters only for repeated hub `degree()`    |

A fuller benchmark (readers on a 500K-edge graph, build steps, host indirection, backfill) is being run by a separate agent;
its report goes in `PERF.md` in this directory when it lands. Do not adopt the reader packages before reading it.

## Recommended adoption order

1. **Now, low risk, test-only or unwired:** `derived-oracle-harness` (keep the xfail reason), `freshness-module` (module only),
   `migration-as-data`, `single-graph-backfill` (equivalence proven live; measure the gain on a real legacy database),
   `host-sites-1`.
2. **Next, behaviour-preserving ownership moves, live-proven:** `temporal-erase` (decide: node erase now removes temporal
   edges, as ADR-0004 intends) then `adjacency-owner` (write the ADR-0001 amendment in the same change).
3. **Then, together and only after the performance report:** `adj-degree-helpers` + `adj-traversal-readers` (F5, F6 first),
   followed by removing the `deg`/`degp` writes from `Adjacency`, `TraversalBuild` and the other writers, and dropping the
   guard allow-lists the packages left behind.
4. **Needs a product decision:** `temporal-structural-row` (ship behind the flag; default flip is a major-version change).
5. **Redo:** `host-sites-2`, in smaller pieces after a `Host` void operation exists.

## Not built in this round

Engine-maintained adjacency from the edge table (the functional-index step, C2), wiring `Freshness` into the engine
(`freshness-module` lists the 11 `_nkg_dirty` sites and their replacements in its report), the `^NKG` stamp, a migration
for existing installs, and the ADR amendments. An inbound request from the opsreview session (a ledger `redact` that keeps
ordinals and verify working, an extension of ADR-0004) is separate from this work and is noted in the ledger discussion only.
