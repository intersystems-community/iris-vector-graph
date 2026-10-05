<!-- markdownlint-disable MD013 -->

# Research: Arno Callout Adoption (spec 236)

Raw evidence: `research-capabilities.md`, `research-token-audit.md`, and
`docs/research/zf-heap-and-exstr.md`, `docs/research/arno-rzf-paths-forward.md`.
Labels: VERIFIED = measured or read in code; UNVERIFIED = inference.

## Baseline (T001, 2026-10-04)

- Branch `236-arno-callout-adoption`; `ivg-iris-enterprise` Up (healthy), `arno rust_callout: True`, `arno bfs: True`.
- Loaded library: `docker/enterprise/libarno_callout.so` sha256 `35ea20aebe4a54c599073ad2c6c8bf321afda9eb7c8ff33248e29b0a17af96d4` (the old 1.0.0 build, not 1.1.0). `/tmp/libarno_callout.so` in the container has the same sha.
- 1.1.0 arm64 build on disk: `~/ws/arno/target-linux/release-1.1.0/libarno_callout-1.1.0-linux-arm64.so` sha256 `da28ca7184e2d33a68af86a5f875cca32293a021dbf5388d776f03c9c299ab03` (matches the pinned value).
- `USER` contents: `nodes`, `rdf_edges`, `rdf_labels`, `rdf_props` all 0 rows (same as the 2026-10-03 baseline; Tom confirmed USER is test-only).

## R1. How does ivg read the library's capability report?

- **Decision**: add `Graph.KG.ArnoAccel.ReaderRevisions()`. It calls the
  library's `capabilities` entry with `$ZF(-4,3,dllid,"capabilities")` then
  `$ZF(-5,dllid,funcid)` inside `Try/Catch`, and returns `"{}"` when nothing is
  loaded or the entry is missing. Python parses it with `_call_classmethod` and
  treats an empty map as "no Native Readers".
- **Rationale**: VERIFIED on `ivg-iris-enterprise`: the `-4,3` + `-5` pair
  returns `{"version":"arno-callout 1.1.0+3b2123b8e7bb","readers":{...}}`;
  `$ZF(-3,path,"capabilities")` returns the same. arno's own release smoke test
  uses the same form. On the old build the call throws `<ILLEGAL VALUE>`, which
  `Try/Catch` catches (VERIFIED), and the process is unharmed afterwards.
- **Correction to the spec**: the spec's Assumptions say `$ZF(-6)`. `$ZF(-6)`
  returned `<VALUE OUT OF RANGE>` for every call, including `version`
  (VERIFIED), so the earlier spike's error was the call form, not the old
  library. The spec is amended to say `-4,3` + `-5`.
- **Trap**: the existing loader checks `If fid = 0` after `$ZF(-4,3,...)`. A
  missing name throws instead, so that check is dead code; new code must catch.
- **Alternatives rejected**: `NKGAccelLoader.Capabilities` (hard-codes the
  algorithm list when `IsAvailable()`); parsing the version string (coarse,
  ordering of `1.x.y+sha` is brittle).
- **UNVERIFIED**: x86_64 build; TCP job process that never ran `Load`; loading
  the same path twice; Community container.

## R2. Which value is the graph-version token?

- **Decision**: `^KG("__version")`, hardened by five changes (below). One
  `$Get` per validity check, plus the entry read.
- **Rationale**: VERIFIED by the audit. All five in-scope algorithms read only
  `^KG("out"/"in"/"deg")` (subgraph also reads `^KG("prop")` and SQL
  `rdf_labels`); weights and embeddings are never read by an Owner. Of the
  candidates:
  - Ledger `seq`: unsound. One counter per namespace, moves only on ledger
    commits; legacy writes, erasure and snapshot restore leave it unchanged.
  - `^NKG("$meta","version")`: unsound. Temporal shadow writes, and any write
    while the `^NKG` skeleton is absent, leave it unchanged while the answer
    changes.
  - New dedicated counter: sound but duplicates `^KG("__version")`, which
    already exists and already guards the `^ArnoKG` chunk path.
  - `^KG("__version")` as it stands: unsound (lost update, resets, missed
    paths); hardened: sound.
- **Required changes** (each gets a test):
  1. `ArnoAccel.BumpVersion` uses `$Increment` (atomic, survives
     `TROLLBACK`).
  2. Snapshot restore bumps after the import, and on `merge=True`.
  3. `BuildKG` multigraph branch bumps on commit, and `BuildKG`'s `Catch`
     bumps after `TRollback`.
  4. Keyed adjacency primitives bump on every edge (latent: no production
     caller).
  5. `Loader.Load` / `BenchFormat` must not reset the counter (`Kill ^KG`
     reuses values).
- **Granularity**: global, not per graph. Over-invalidates other graphs but is
  sound. A per-graph counter is a later optimisation.
- **Self-bypass (FR-018)**: `subgraph` reads live SQL `rdf_labels`; a label
  insert changes its answer with no token moving. Subgraph results are not
  cached in this spec.
- **Paths that cannot change an answer**: Cypher DML and raw SQL into
  `rdf_edges` never touch `^KG` (VERIFIED), so neither moves the token nor
  changes an Owner answer. Their FR-019 tests compare cached against
  recomputed, not "answer changed".
- **Separate bug, not in scope**: `set_edge_weight` changes PageRank because
  `WriteAdjacency` re-increments `^KG("deg")`. Recorded in KNOWN_ISSUES.

## R3. Namespace flip in `Build2HopExactStats` (found by the audit)

- VERIFIED: `BuildNKG` (so `BuildKG`, `sync()`) leaves the TCP Native API
  process in namespace `USER` when the connection was opened elsewhere. Steps
  after `BuildKG` then run against `USER`. The audit's first run erased the
  default-graph content of `USER` on `ivg-iris-enterprise` this way (incident
  recorded in `research-token-audit.md`).
- **Decision**: this spec's cache and gate tests re-establish namespace after
  any `BuildKG`/`sync` and assert a sentinel global before writing. Fixing the
  flip itself is a separate defect, listed in KNOWN_ISSUES and the open
  questions; it is not scoped here.

## R4. BFS size gate placement

- **Decision**: check the serialized adjacency length at the point the string
  exists, before the `$ZF` call, and return to the ObjectScript BFS when it
  exceeds 34,791 characters.
- **Rationale**: VERIFIED limits (34,791 passes, 34,792 fails with
  `<OUT OF $ZF HEAP SPACE>`, 65,536+ fails with `<MAX $ZF STRING>`) from
  `docs/research/zf-heap-and-exstr.md`. The exact insertion point in
  `NKGAccelTraversal.cls` (near :341-353) is UNVERIFIED until the task is
  written.
- **Rejected**: raising `zfheap` (gains ~30K, cap stays 65,535); adopting
  `kg_bfs_compute` (returned `[]` on the probed build; arno's diagnostic
  `test_neg_int_keys` has not been run).

## R5. Cache storage and layout

- **Decision**: `^IVG.AlgoCache(graphKey, algorithm, paramHash)` holding
  `{token, payload}`. Graph key uses the existing Graph Key derivation
  (`$Select(graphId="": 0, 1: $ZStrip(graphId,"*C"))`, `CONTEXT.md`), not the
  ledger form. Erase paths also kill the graph's subtree, as defence in depth
  on top of the token.
- **Rationale**: shared across connections (Story 4); separate from `^KG` so
  `Kill ^KG` cannot take the cache with it (the token mismatch still catches
  it). `^IVG.Ledger` already lives under `^IVG`.
- **PPG front layer (FR-021)**: benchmark-gated, deferred to a task after the
  shared cache works.

## R6. Proof file and routing

- **Decision**: `docs/arno-reader-proof.json`, schema in
  `contracts/arno-reader-proof.schema.json`. Routing loads it once per process;
  missing or unparseable means every algorithm uses its Owner.
- **Library swap**: the library in the container today has sha256 `35ea20ae…`
  (not 1.1.0). The 1.1.0 arm64 build (`da28ca71…ab03`) exists on disk in the
  arno checkout; the gate run installs it via `enterprise-container.sh`
  (`docker/enterprise/libarno_callout.so` is gitignored).

## Open items carried to tasks

- Confirm the BFS gate insertion point and the exact serialized length counter.
- Fix or guard `Build2HopExactStats` namespace flip for tests.
- Decide the CDLP similarity threshold from the first real comparison data; the
  threshold is then written into the proof file, not chosen in advance.

## Implementation notes (2026-10-04)

- **Dispatch sites (T014)**: all five are in `stores/iris_sql_store.py`
  (`execute_pagerank/ppr/wcc/cdlp/subgraph`). `_engine/algorithms.py` `ppr()`
  calls `NKGAccel.PPRNative`, a local-neighbourhood ObjectScript PPR over
  `^NKG`, not a Rust reader; it is left unchanged. `kg_WCC`, `kg_CDLP`,
  `kg_SUBGRAPH` already go through the store.
- **Native entry points**: `ArnoAccel.{PageRank,PPR,WCC,CDLP,Subgraph}NativeJson`
  are new; the Owner-delegating `*Json` methods are untouched (pinned by
  `test_411_arno_kg_readers`). The store no longer consults the hard-coded
  `_arno_capabilities["algorithms"]` list for these five.
- **Subgraph** native has no edge-type parameter, so a filtered request goes to
  the Owner.
- **Starter proof (T015)**: `library.version` is `arno-callout 0.0.0+000000000000`,
  not `+none`, because the schema requires a hex suffix.
- **Wheel installs** carry no `docs/` proof file, so they route to the Owners
  (safe default). `IVG_ARNO_PROOF` repoints the file.
- Legacy dispatch tests that stubbed `_arno_available` now call
  `tests/unit/_236_helpers.arm_native`, because a Native Reader needs a reported
  revision and a proof entry.

## Evidence gate results (T021, T021a, T024; 2026-10-04)

- **Library installed**: `scripts/enterprise-container.sh install-arno` with
  the 1.1.0 arm64 build (sha256 `da28ca71…ab03`, verified before copying), then
  loaded into `ivg-iris-enterprise` as `/tmp/libarno_tcp_da28ca7184e2.so`
  (one file per build; an in-place overwrite can crash a process that has the
  old one mapped). `ReaderRevisions()` reports
  `{betweenness:1, bfs:1, cdlp:2, kcore:1, leiden:1, pagerank:1, ppr:2, scc:1, subgraph:1, triangle_count:1, wcc:2}`.
- **Existing Arno tests on 1.1.0** (`test_bfs_arno`, `test_411_*`, the Arno
  integration and e2e files, 131 tests): all pass. No failure that did not occur
  on the old build.
- **Finding: the readers are USER-only.** Each `kg_*_global` call, and the
  library's `init`, moves the process to USER and reads USER's `^KG`, whatever
  namespace the caller was in (measured: a loaded library in `IVG236` answered
  with USER's graph). Consequences, all implemented: `Load` restores the
  caller's namespace after `init`; `NativeResult` refuses to run outside USER;
  `_arno_route` returns the Owner for any store namespace other than USER; the
  gate runs in USER.
- **Finding: no Native Reader passes on 1.1.0.** `python scripts/arno_reader_gate.py --write-proof`:
  - `pagerank` (rev 1): scores differ from the Owner on path, star and
    multi-component graphs (e.g. path `p7` native 0.1877 vs Owner 0.1223); equal
    on dense and tie-heavy. The two normalise dangling nodes differently.
  - `ppr` (rev 2): scores differ by about 3e-5 relative on star and dense, by
    15% on the path; the native answer lists zero-score nodes the Owner omits.
  - `wcc` (rev 2) and `cdlp` (rev 2): answer a summary (`{"components":N,"largest":[...]}`,
    `{"communities":N,...}`), not a node-to-group map, so no partition can be compared.
  - `subgraph` (rev 1): edges carry `"type":"REL"` instead of the predicate, so
    the edge sets differ on every fixture.
  Nothing was recorded; `docs/arno-reader-proof.json` holds the 1.1.0 library
  identity and an empty `algorithms` map, so routing stays on the Owners. Each
  algorithm enters the file only when a later build passes the gate.

## BFS size gate (T028, T029; 2026-10-04)

- **Spot (R4 closed)**: `Graph.KG.NKGAccelTraversal.BFSJson`, between
  `ExportAdjacencyWithPreds()` and the `$ZF(-5, dllid, fnid, adjStr, ...)` call.
  `NKGAccelLoader` builds no adjacency string.
- **The budget is on all four string arguments, not the adjacency alone.** The
  heap model in `docs/research/zf-heap-and-exstr.md` (2 bytes a character for
  every input string, plus a 65,534-byte output reservation, in 135,168) gives
  34,808 characters across adjacency, result-global name, seed and predicate
  list. The 34,791 figure in FR-013 is that budget minus the 17 characters the
  measurement's other arguments used. The gate uses `ZFMAXCHARS = 34808`, so with
  a short seed and `[]` the adjacency limit is 34,795 (the test computes it),
  and a long seed or predicate list lowers it. A gate on the adjacency alone
  passes a call that then raised and was caught by the existing `Try/Catch`:
  still correct, but the failing call is what the gate exists to avoid.
- A `CHUNKED:n` export (over 3 MB) is not an adjacency and now skips the
  callout too.
- `BFSJson` records `last_bfs_path` (`native`, `size-gate`, `fallback`) and
  `last_bfs_error` in process-private globals; `LastBfsPath()` / `LastBfsError()`
  read them. That is how the tests tell the gate from the catch.
- **Observed, not investigated**: `Traversal.BFSFastJsonSorted` with a
  non-empty predicate list (`["P"]`) on a one-predicate chain returns only hop 1
  at `maxHops` 2 and 3; with `[]` it returns every hop. The native path returns
  every hop either way. Recorded in `docs/KNOWN_ISSUES.md`.

## Token hardening and cache (T031–T044; 2026-10-04)

- **Write-path tests** (`test_236_token_write_paths.py`, 38 cases): the oracle is
  "an Owner answer changed ⇒ `^KG("__version")` moved", plus never falls, never
  reuses, survives a rolled-back transaction. Seven failed before the fixes
  (restore replace and merge, `BuildKG` multigraph, keyed primitives,
  `BenchFormat.Seed`, `Loader.Load`, concurrent bumps, rollback); all 38 pass
  after the five fixes.
- **Not covered**: audit row 24/25 (FHIR graph sync edges and `^KG(prop/label)`)
  needs a FHIR repository fixture; the audit read those paths (they call
  `WriteAdjacency`/`DeleteAdjacency`, exercised by cases 01 and 03) but no test
  here drives them. Row 20's `Enable` and row 23's `PurgeAll` are covered by case
  19 only for "a later commit still moves the token".
- **`BenchFormat.Seed` and `Loader.Load`** now keep the counter rising across
  their `Kill ^KG`. `restore_snapshot` sets the counter to
  `max(before, archived) + 1` after the import so the archive's older value can
  never overwrite the live one.
- **Cache key**: `^IVG.AlgoCache(graphKey, algorithm, paramHash)` with the graph
  _name_ passed in and `Graph.KG.GraphKey.ForIndex` deriving the subscript, so
  Python never spells a key form (ADR-0003). Registered in
  `GraphStores.Inventory` as graph-scoped; `Eraser` kills it per graph, and
  `InvalidateDerived` kills all of it, because the token is global and not every
  erase caller knows its graph.
- **`kg_PAGERANK()` without seeds** called the Owner directly and never reached
  the store; it now goes through `execute_pagerank` (falling back to the direct
  call on error), as `kg_WCC` and `kg_CDLP` already did.
- **Benchmark (FR-021)**, `ivg-iris-enterprise`, median of 200 calls, one Native
  API round trip each:

  | Result size   | Shared hit (Lookup + parse) | Token check alone | Ratio |
  | ------------- | --------------------------: | ----------------: | ----: |
  | 242 chars     |                     0.10 ms |           0.08 ms | 1.3x  |
  | 110,022 chars |                     1.28 ms |           0.10 ms | 12.5x |

  Decision: the process-private layer is added **only for results of 20,000
  characters or more** (`FRONT_MIN_CHARS`), where it clears the 2x bar; below
  that it would cost a round trip per miss for no gain. It re-reads the token on
  every use (kept shared check), holds at most 8 entries, and returns copies.
