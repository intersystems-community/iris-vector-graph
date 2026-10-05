# Arno and rzf: paths forward for spec 236

Design review spike for spec 236 (Arno adoption). Date: 2026-10-03.
Read-only on arno and rzf. One optional measurement on `ivg-iris-enterprise`
(process-local only, no residue). Companion to
`docs/research/zf-heap-and-exstr.md` (the "prior spike"), whose
measurements are taken as given.

Labels: VERIFIED means read in source, or measured by me (command named).
UNVERIFIED means inference, hearsay, or a claim I could not reproduce.

Paths used below: `arno` is `~/ws/arno`, `callout` is
`~/ws/arno/iris-integration/arno-callout/src`, `rzf` is `~/ws/rzf`.

## Short answer

1. The original idea (Arno lazily loads a subgraph, runs fast code on it)
   is not dead. It shipped in one place, `BetweennessNeighborhood`, and that
   place works because the Rust side keeps the adjacency **resident** and
   ivg only passes a version number and a node list. The code that did not
   pan out is `kg_bfs_global`, which re-ships the **whole** adjacency as a
   string on **every** call. That is the opposite of lazy.
2. State in the loaded `.so` is per OS process and dies with the job.
   VERIFIED by measurement (section 3). So "load once, query many" pays only
   where the process is long-lived (a pooled DBAPI connection), not in
   short TCP jobs.
3. The "`$ZF(-5)` cannot read globals" claim in Arno's source is stale.
   Arno's own spec 065 KL-2 disproves it (section 2). The empty `[]` from
   `kg_bfs_compute` in the prior spike is therefore unexplained, and the
   likeliest culprit is the `ns.keys()` iteration pattern that ivg already
   measured as flaky, not `-5` versus `-6`.
4. Recommendation: spec 236 should contain (b) result caching keyed by
   graph version, plus a measured gate deciding whether the BFS callout is
   worth keeping at all. The Arno asks (resident adjacency, EXSTR entry) go
   in a follow-up spec. Section 6.

## 1. What Arno is, and where state lives

### Shape of the callout

- VERIFIED. `libarno_callout.so` is a Rust `cdylib` built on rzf. Each export
  is a `#[rzf]` function; `rzf::init!()` builds the `$ZF` table
  (`callout/lib.rs:1046`; `arno/docs/ARCHITECTURE.md`, "Critical
  Configuration": LTO must be off to keep the table).
- VERIFIED. Every export takes and returns `rzf::ShortString`, descriptor
  `2b`/`2B` (`callout/lib.rs:290-345, 818-900`;
  `arno/specs/063-rzf-dependency-pinning/FINDING.md`, "ABI unchanged").
- VERIFIED. ivg loads it with `$ZF(-4,1,path)` and resolves names with
  `$ZF(-4,3,dllid,name)`, then calls `$ZF(-5,dllid,fnid,...)`
  (`iris_src/src/Graph/KG/NKGAccelTraversal.cls:339-363`,
  `NKGAccelLoader.cls:15-24`). ivg caches `dllid` and `fnid` in the
  process-private global `^||NKGAccel`, and `^ArnoAccel("lib_path")` for TCP
  jobs that never ran `Load` themselves.

### State between calls

Arno holds state in three process-wide Rust statics. All live inside the
`.so`, so inside the IRIS process that loaded it.

- `ADJ_BUFFER`, `RESULT_BUFFER` (`kg_ffi.rs:2701-2702`): the chunked
  adjacency string, and the result string behind `kg_get_result_*`.
- `BETWEENNESS_ADJ_CACHE` (`kg_ffi.rs:2704-2709`): parsed `nodes` and `adj`,
  keyed by `^NKG("$meta","version")`.
- `KG_CACHE` (`kg_ffi.rs:16`): named `KGIndexData` entity index; not used by
  the BFS path.

- VERIFIED (source). `ADJ_BUFFER` is drained by `take_adj_buffer`
  (`kg_ffi.rs:2853`): every `kg_*_run` consumes it (`std::mem::take`).
  The chunked family is "append, append, run", single-shot.
- VERIFIED (source). `BETWEENNESS_ADJ_CACHE` is the opposite: it survives
  the call, and a later call with the same `nkg_version` skips the
  read and parse (`kg_ffi.rs:2723-2740`). `kg_betweenness_neighborhood_v`
  then extracts a subgraph from that resident copy and errors
  "adjacency cache miss" if cold (`kg_ffi.rs:2788-2803`). **This is pattern
  (a) and pattern (g) already working in production code.** ivg calls it
  from `BetweennessNeighborhood` (`NKGAccelCentrality.cls:366-430`).
- VERIFIED (measurement, this spike). Lifetime is per process. Two separate
  `iris session` processes on `ivg-iris-enterprise`, each loading the `.so`
  and calling `kg_adj_append("0:1\n")` then `kg_adj_append("")`:

  | Process | call 1 | call 2 |
  | ------- | ------ | ------ |
  | 1       | `OK:4` | `OK:4` |
  | 2       | `OK:4` | `OK:4` |

  Process 2 starts at 0, not 4 or 8, so the static is not shared. Call 2 in
  each process sees call 1's data, so it persists across `$ZF` calls inside
  one process. The two processes also received different `dllid` values
  (`-33374336`, `-33112192`). Cleanup: nothing to clean, the state died with
  the sessions; no global was written.

- UNVERIFIED. Behaviour under a long-lived pooled DBAPI connection (the
  statics should persist for the life of that server-side process). The
  prior spike's open question about `<MAX $ZF STRING>` "corrupting" such a
  connection is the same unknown.
- UNVERIFIED. Thread safety across a `$ZF` call is not an issue in IRIS
  (one process, one call at a time), but rzf also starts a tokio runtime
  (`rzf/readme.md:314-340`), so resident state could even outlive a call on
  background threads. Arno does not use that.

### Spec 063 and the ABI

- VERIFIED. Spec 063 (resolved 2026-10-02) pinned rzf to `darpa/rzf`
  `6272f5b` and iris-rs. It says "ABI unchanged": current rzf defaults
  `String` to EXSTR (`n`/`N`), but Arno deliberately keeps `ShortString` so
  the descriptors stay `2b`/`2B`. "Moving to EXSTR is a separate decision:
  it changes the contract for every caller"
  (`arno/specs/063-rzf-dependency-pinning/FINDING.md`, Resolution).
- VERIFIED. Spec 065 defines the frozen contract: 16 symbols
  (`callout/../065 spec.md` FR-002), with semver in FR-003: removing a
  symbol or changing arguments or result format is a **major** bump; adding
  a symbol is a **minor** bump. `kg_adj_append` and the `*_run` family are
  not in the 16. `kg_bfs_global` and `kg_betweenness_neighborhood_v` are.
- Consequence for spec 236 (inference, follows from FR-003): a new BFS
  entry is a minor bump and is free to design. Switching the existing
  `kg_bfs_global` descriptor to `n/N` is a major bump and needs ivg and
  opsreview to move together.
- VERIFIED. The deployed `.so` in `ivg-iris-enterprise` predates the
  `capabilities` entry (prior spike). Any gate must treat "absent" as "old".

## 2. What rzf exposes, and the global-read question

### rzf capabilities (VERIFIED, `rzf/readme.md`, `rzf/ARCHITECTURE.md`)

- `String` is EXSTR, 3.6 MB, `n`/`N` linkage, the default type
  (`readme.md:764-783`).
- `ShortString` is ZWARRAYP, `2b`/`2B`, what Arno uses (`readme.md:764-783`).
- Global get, set, order, keys, query via `NameSpace` and `GlobalRef`
  (`readme.md:213-260`).
- Transactions with `with_transaction` (`readme.md:290-310`).
- Call ObjectScript class and instance methods (`readme.md:792`).
- `xecute` and `$xecute`: run arbitrary COS from Rust (`readme.md:992-1030`).
- XDEV: a Rust-backed IRIS device with streaming JSON
  (`readme.md:1039-1345`).
- A tokio runtime inside a callout (`readme.md:314`).
- Call-in binary mode `as_binary`: a Rust process attaches to IRIS through
  `libirisdb` (`readme.md:36-45, 1742`).

So **yes, Rust can call back into IRIS** from a callout: it reads and writes
globals, calls class methods, and can xecute code. Arno already does so with
`NameSpace::try_new("USER")` plus `ns.get` / `ns.keys`
(`callout/zf_global.rs`, `kg_ffi.rs:3080-3170`). Note that Arno carries its
own `iris_callin.rs` function-table shim (`callout/iris_callin.rs:1-60`,
"Verified offsets for IRIS 2025.1 function table") alongside rzf's. That
offset table is a version-coupling risk; rzf's typed API is not.

### Why `kg_bfs_compute` returned `[]`

- VERIFIED (source). The doc comment on `ffi_kg_bfs_global`
  (`kg_ffi.rs:3367-3376`) says reading `^NKG` from `$ZF(-5)` "silently
  returns empty" because the callin table is initialised only for `$ZF(-6)`.
  `lib.rs:90-100` repeats the claim for negative integer subscripts.
- VERIFIED (source), and in conflict with it. Immediately above
  `read_nkg_adjacency_with_preds` (`kg_ffi.rs:3071-3079`): "This was a
  misdiagnosis - the actual cause was a missing `extern` declaration in
  `iris_zf_wrapper.c` causing silent ZFENTRY registration failure. Verified
  working on IRIS Enterprise with correct build." And spec 065 KL-2:
  "`$ZF(-5)` global reads - RESOLVED, not a defect ... `kg_build_nkg` and
  `kg_build_2hop_exact_stream` read the fixture `^KG` and return the
  intended results. The comment is stale."
  (`arno/specs/065-callout-release-contract/spec.md`, Known Limitations).
  So Arno's own record says `-5` is fine and the comment at `3367` is wrong.
- VERIFIED (ivg measurement, `docs/KNOWN_ISSUES.md:581-592`). The
  `kg_*_global` readers that walk `^KG` with `ns.keys()` are unreliable:
  the first call saw 8 of 66 nodes; later calls disagreed with each other.
  `kg_bfs_compute` and `ag_bfs_lazy` use the same `ns.keys()` pattern over
  integer and negative-integer subscripts (`kg_ffi.rs:3115-3160, 4725-4760`),
  and the repo ships a diagnostic for exactly this
  (`test_neg_int_keys`, `lib.rs:98-108`, `kg_ffi.rs:3251`).
- Inference, UNVERIFIED: the prior spike's `[]` comes from `ns.keys()` on
  negative integer subscripts (or from the deployed `.so` being the
  2026-07-04 build), not from `-5` versus `-6`. Cheapest proof: call
  `test_neg_int_keys` on the loaded `.so` (it writes then reads a scratch
  global, so use an `^IVGSpike` name) and report `bug`. The prior spike
  also could not get `$ZF(-6)` to accept the library id, so `-6` is not
  evidence either way.
- VERIFIED (source). Point reads (`ns.get`) are not implicated: `ag_bfs_lazy`
  and `read_nkg_adjacency` use them for `$ND`, `$NI`, `$LI` and the
  `^ArnoKG("KG","nkg_adj",i)` chunks (`kg_ffi.rs:1142-1175, 4660-4720`), and
  `kg_build_nkg` passes its E2E. Only key iteration is suspect.

### Arno's existing global side channel

- VERIFIED (source). `NKGAccelLoader.CacheNKGAdj`
  (`NKGAccelLoader.cls:44-70`) already stores a serialized adjacency in
  `^ArnoKG("KG","nkg_adj",i)` chunks plus a count, stamped with
  `^ArnoKG("KG","nkg_adj_ver")` against `^NKG("$meta","version")`. Rust's
  `read_nkg_adjacency` reads those chunks by point `get` and parses them
  (`kg_ffi.rs:1142-1175`). No `$ZF` string-size limit applies, because the
  payload travels through a global. This is a working big-payload channel
  that BFS does not use (`BFSJson` builds a fresh string instead).
- Limits: the stored form is `ExportAdjacency` (no predicates), so predicate
  BFS cannot read it as-is. And ivg's own notes list it as a stale all-graph
  snapshot with no graph dimension (memory `arno_ppr_cache_layers`: "still
  open").

## 3. ivg's side: what is built and what is cached

### Structures

- VERIFIED. `^NKG(-1, srcIdx, -(predIdx+1), dstIdx)` out-edges;
  `^NKG("$ND", idx)` and `^NKG("$NI", id)` node maps; `^NKG("$LI", name)`
  predicates; `^NKG("$meta","version")` a counter that must only rise
  (`arno/specs/035-nkg-bfs-callout/spec.md`, "^NKG Layout";
  `KNOWN_ISSUES.md:594-598`). `ArnoAccel.BumpVersion` increments both
  `^KG("__version")` and `^NKG("$meta","version")`
  (`ArnoAccel.cls:61-65`).
- VERIFIED. The ledger (spec 213) keeps `^IVG.Ledger("head")`, a sequence
  number that advances per committed revision, and
  `Graph.KG.Ledger.Head()` returns it (`Ledger.cls:156`). State is
  `never-enabled` on installs that did not call `Ledger.Enable`
  (`Ledger.cls:38-43`), so it cannot be the only cache key.
- UNVERIFIED. Whether every write path that changes `^NKG` also bumps
  `^NKG("$meta","version")`. The KNOWN_ISSUES entry says betweenness relies
  on it, and a monotonic test exists
  (`tests/integration/test_411_nkg_version_monotonic.py`), but a per-write-
  path audit is spec work (the repo's testing rule: a case per write path).

### Private-process globals in ivg (what actually exists)

Found by `grep '^||' iris_src`: 20 classes use them. By purpose:

- Handles: `dllid`, function ids and the `useNKG` flag, in `^||NKGAccel`
  (`NKGAccelLoader.cls`).
- Transport for strings over the limit: `^||adj`, `^||adjWP`,
  `^||LargeOut`.
- Scratch for one call: `^||seedBFS`, `^||PPRG`, `^||bm25scores`.
- Ledger staging and output chunks: `^||IVG.LedgerStage`,
  `^||IVG.LedgerOut`.
- Node-set hand-off into Rust: `^||nbhIdx` in `BetweennessNeighborhood`.

- VERIFIED. None of these caches a **query result across calls**. They are
  handles, transport, or per-call scratch, and most are killed at function
  exit.
- NOT FOUND. I could not locate, in ivg or in the arno docs, the
  `JSON_TABLE` / ASQ result cache in a `^||` global that Tom describes.
  What exists is: (i) arno's design for a process-private cache of ASQ query
  results, `^||arno.Cache(sha1)` / `^||arno.CacheMeta(sha1)` enabled by
  `ARNO_USE_PPG=1` (`arno/docs/ARCHITECTURE.md:560-570`,
  `callout/docdb_cache.rs:29-37`, `arno/specs/029-docdb-sidecar-shadow/
research.md`); the spec's stated reason is that process-private globals
  avoid lock contention and global-buffer synchronisation and so reach a
  "<100us hot query" target, at the price of no sharing across sessions;
  (ii) ivg's `JSON_TABLE(fn(...))` idiom, where a stored procedure returns
  one JSON string and the SQL engine unpacks it (`translator.py:1802-1960`),
  with the quirk that the procedure arguments cannot be bound
  (`KNOWN_ISSUES.md:1079-1097`).
  I do not have the measurement that showed the cache "very helpful". The
  analogy below is built from (i) and from the mechanism, and is labelled
  UNVERIFIED until Tom points at the measurement. Question 1 in section 7.

### The precise analogy

What a `^||` result cache buys (UNVERIFIED mechanism, consistent with the
029 research): a repeat call with the same key becomes a handful of global
reads in the caller's own process. It skips the SQL expansion, the callout,
JSON encode and decode, and any lock traffic. It is only valid while the
graph is unchanged, so the key must include a graph version, and it is only
shared within one process.

The BFS problem differs from a `JSON_TABLE` row expansion in one way that
matters: the expensive part of `BFSJson` is **before** the callout.
`ExportAdjacencyWithPreds` (`NKGAccelAdjacency.cls:239-284`) walks every
edge in ObjectScript with `$Order`, which is the same cost class as the
ObjectScript BFS that Arno was meant to beat (spec 035 motivation: the
`$Order` loop and JSON construction are the bottleneck, 112-128 ms p50 at
10K nodes / 50K edges, `arno/specs/035-nkg-bfs-callout/spec.md`,
Motivation). So, inference, UNVERIFIED: a per-call export plus callout
cannot beat `BFSFastJsonSorted` except at depth, and it falls over at 34,791
characters anyway. A result cache removes the repeat cost; a resident
adjacency removes the per-call export; neither helps a **cold, unique**
query. Measurement M1 in section 5 settles it.

## 4. Candidate paths

Scoring key: Value (what it buys), Cost, Risk. "Needs" lists dependencies on
arno, rzf and ivg separately.

### (a) Resident adjacency inside the `.so` (load once per process)

- **Idea.** Generalise `BETWEENNESS_ADJ_CACHE`: keep the parsed adjacency
  (with predicate indices) in a static, keyed by `nkg_version`. A BFS call
  passes only `(version, seed, preds, hops, max)`.
- **Mechanics.** New entries, both minor-bump additions: `kg_adj_load(
version, chunk_global)` (fills the static from a global side channel,
  see (j)) and `kg_bfs_resident(version, seed, preds, hops, max, out_global)`
  which returns `STALE` on version mismatch so ObjectScript reloads or falls
  back. Per call, ObjectScript reads one global (`^NKG("$meta","version")`).
- **Needs.** Arno: two entries, one static, memory cap. rzf: nothing new.
  ivg: version read, load-on-stale, fallback, `useNKG` routing.
- **Cost.** Arno: small to medium (the pattern and the parser
  `parse_adj_str_with_preds` exist). ivg: small.
- **Risks.** Per-process memory copy (N pooled connections x adjacency);
  short-lived TCP jobs reload every time and gain nothing (section 1);
  staleness if any write path skips the version bump; ivg's open
  `nkg_adj` graph-dimension bug applies if the load reads that snapshot.
- **Prove cheaply.** In one process: load 10K/50K synthetic adjacency once,
  time 100 `kg_bfs_resident` calls against 100 `BFSFastJsonSorted` calls.
  Needs a scratch global `^IVGSpike`, killed afterwards.
- **Status.** Pattern VERIFIED (betweenness neighborhood ships it); BFS
  variant UNVERIFIED.

### (b) Result cache keyed by (algorithm, params, graph revision)

- **Idea.** Cache the answer, not the graph. Key:
  `(algo, sha(params), ^NKG version [, ledger head when enabled])`.
- **Mechanics.** In ObjectScript around `BFSJson`, PPR, WCC and friends:
  `Set k = algo_"|"_hash`, check `^||IVG.Result(k)` and its stored version;
  on hit return; on miss compute (any backend) and store. Private-process
  by default (no lock traffic, auto-cleaned); a shared `^IVG.ResultCache`
  variant as opt-in for cross-process hits, guarded by the version check.
  Bounded: evict by count or bytes, or `Kill` the whole subtree when the
  version changes (one `$Get` per call detects it).
- **Needs.** Arno: nothing, and that is the point. rzf: nothing. ivg: a
  small cache class, a canonical-params hash (the ledger already has a
  canonical serialisation, spec 213), invalidation tests per write path.
- **Cost.** Small. Independent of the callout, so it de-risks all of spec
  236: the answer source can be Arno, ObjectScript, or either.
- **Risks.** Hit rate depends on repeated identical queries and on
  connection affinity (each DBAPI connection is a process; a pool spreads
  the misses). Staleness is the whole risk; see the version audit in
  section 3. Memory growth if unbounded. Results depend on graph scope
  (`graph_id`, multigraph, spec 234): include it in the key.
- **Prove cheaply.** Wrap one read path, run a repeated-query workload on
  the enterprise container, compare p50 with and without, then write an
  edge and assert the next call misses (database-state assertion, per the
  repo's testing policy).
- **Status.** Mechanism design VERIFIED to exist in arno (ARNO_USE_PPG);
  ivg hit-rate and gain UNVERIFIED.

### (c) `kg_bfs_run` after chunked `kg_adj_append`

- **Idea.** Let BFS join the chunked family so a large adjacency fits.
- **Mechanics.** `kg_adj_append` already works to 686,670 chars at default
  heap (prior spike, VERIFIED). Add `kg_bfs_run(result_global, seed, preds,
hops, max)` that `take`s `ADJ_BUFFER` and runs the existing BFS. The wire
  format must be the predicate form `src<US>pred<US>dst\n`, not the integer
  form `src:dst,...` the other `*_run` entries parse.
- **Needs.** Arno: one entry, one minor bump. ivg: chunk the export into
  <= 30,000-char appends; still builds the whole string per call.
- **Cost.** Small in Arno; zero removal of the per-call export.
- **Risks.** Pays the full O(E) ObjectScript export and the $ZF append
  round trips on every call, so the time win is doubtful. Buffer is
  single-shot; a failed run leaves stale appended data unless the caller
  issues an empty-run `take` (prior spike).
- **Prove cheaply.** M1 (below) tells whether the export, not the callout,
  dominates. If it does, (c) is pointless.
- **Status.** Chunked append VERIFIED; BFS variant UNVERIFIED; worth doing
  only as a stepping stone to (a).

### (d) EXSTR (`n`/`N`) entries

- **Idea.** Pass up to 3.6 MB as one argument; bypass `zfheap`.
- **Mechanics.** A new symbol such as `kg_bfs_x(String ...) -> String`
  using `rzf::String` (readme:764-783). Do not change `kg_bfs_global`: that
  is a major bump (065 FR-003).
- **Needs.** Arno: one entry; the 063 note that EXSTR is "a separate
  decision". rzf: nothing. ivg: build the string in ObjectScript (long
  strings are fine up to 3.6 MB).
- **Cost.** Smallest Arno change of all. Also lifts the result-side chunking.
- **Risks.** Still O(E) export per call; the prior spike did not measure a
  3 MB `n` argument on this build (its open question), and rzf's
  `iris_bytes_io` note says a related EXSTRP binary path is "not yet merged
  in the IRIS kernel" (`rzf/ARCHITECTURE.md`, feature flags). That caveat
  is about `Status` binary IO, not strings, but it shows the EXSTRP path is
  less exercised than ZWARRAYP.
- **Prove cheaply.** Build a 3 MB adjacency string in a `$ZF(-5)` call to
  any `n`-linkage function. rzf has test callouts (`rzf_callout_tests`);
  running one needs a built `.dylib`/`.so` and a container, so this is a
  spike for the Arno side.
- **Status.** Documented by InterSystems and rzf, UNVERIFIED in practice.

### (e) A global-read path that works

- **Idea.** Have Rust read `^NKG` directly, so no payload crosses `$ZF`.
- **Mechanics.** Two sub-options. (e1) fix the key-iteration bug behind
  `kg_bfs_compute` / `ag_bfs_lazy` so `ns.keys()` works on negative integer
  subscripts; or (e2) avoid `keys()`: walk with `$ORDER`-equivalent
  `ns.order`/`ns.query`, or ObjectScript lists the node ids and Rust does
  `ns.get` only. The `-5` versus `-6` question looks moot (section 2).
- **Needs.** Arno: a diagnosis using `test_neg_int_keys`, then a fix or a
  rewrite of the iteration. rzf: possibly a bug in `ns.keys()` for negative
  subscripts (the repo flags it as "Reproducer for rzf bug",
  `lib.rs:98-100`). ivg: nothing until it works.
- **Cost.** Unknown until diagnosed.
- **Risks.** A per-node `ns.get` loop crosses the callin boundary once per
  edge or node; it may lose to resident data by a wide margin. Also: the
  hard-coded namespace `"USER"` in every reader (`kg_ffi.rs:3082, 3193`,
  spec 065 KL-3) means ivg's namespace-aware engine (spec 212) cannot use
  these readers outside `USER` at all. That alone blocks (e) for IVGTEST
  and customer namespaces until Arno takes the namespace as an argument.
- **Prove cheaply.** `test_neg_int_keys` on the loaded `.so`.
- **Status.** UNVERIFIED. Treat as a diagnosis, not a feature.

### (f) Out-of-process Arno server

- **Idea.** A Rust daemon holds the adjacency once, for all clients.
  Candidates: a call-in binary (`as_binary`, `rzf/readme.md:36-45`) that
  attaches to the local instance and reads globals in its own process; or
  the Native API from outside.
- **Mechanics.** Daemon loads `^NKG` through call-in, re-checks
  `^NKG("$meta","version")` on each request, answers BFS/PPR over a Unix
  socket or HTTP. ivg's Python engine calls it directly, bypassing SQL and
  `$ZF`; ObjectScript callers use `%Net.TCP` or a device.
- **Needs.** Arno: a server binary (arno already has subprocess and
  sidecar precedents: specs `017-docdb-subprocess`, `029-docdb-sidecar-
shadow`; I did not read them). rzf: call-in binary mode. ivg: a client, a
  lifecycle (start, health, version), a security review (new network
  surface; spec 196 hardening applies).
- **Cost.** Large. New deployment artefact per container.
- **Risks.** Call-in multithreading is unsupported on macOS and needs
  `libirisdbt` on Linux (`rzf/readme.md:1685-1700, 1787`). One copy of the
  adjacency replaces N, which is the real win over (a), but the operational
  surface is the biggest of any option.
- **Status.** UNVERIFIED end to end.

### (g) Lazy subgraph: k-hop ball in ObjectScript, Rust on the ball

- **What the repo says happened.** The idea exists in three forms and no
  file records a decision that it failed:
  - `kg_subgraph_global`, `kg_khop_sample`, `kg_neighbor_agg` are not lazy:
    they call `read_kg_adjacency_auto` and read the **whole** graph before
    extracting (`kg_ffi.rs:2607-2650`).
  - `ag_bfs_lazy` is truly lazy: per-node `ns.get` of degree, then `keys()`
    of predicates and destinations (`kg_ffi.rs:4660-4780`). It depends on
    the suspect `keys()`. Spec 065 lists only its sibling `ag_bfs_compute`
    (missing from the table because it is defined after `rzf::init!()`),
    and nothing records a result for `ag_bfs_lazy`. Not exercised by ivg.
  - `BetweennessNeighborhood` (ivg) is the working hybrid: ObjectScript
    computes the k-hop node set (`KHopNeighbors`), stashes indices in
    `^||nbhIdx`, Rust extracts the induced subgraph from the resident
    cache (`NKGAccelCentrality.cls:366-430`).
- **Why plain lazy BFS has no case.** BFS from a seed in ObjectScript already
  touches only the ball, which is exactly what a lazy Rust BFS would touch.
  Rust's advantage is the tight loop, not the data access, and per-node
  callin reads cost more than `$Order`. Inference, UNVERIFIED.
- **Where it does pay.** Algorithms that are superlinear on the ball
  (betweenness, PPR with many iterations, Leiden, triangle count on an
  extracted neighbourhood): ObjectScript picks the ball (cheap, O(ball)),
  Rust computes (expensive, O(ball x iterations)). The payload is sized
  to the ball, not the graph, so the 34,791-character wall applies only to
  large balls and chunked append (c) covers the rest.
- **Needs.** Arno: `*_run` entries that take a ball; several already do.
  ivg: the ball builder (`KHopNeighbors` exists) and routing by ball size.
- **Prove cheaply.** Already measured for betweenness (ivg tests on the
  karate graph only, "direction not checked", `KNOWN_ISSUES.md:603-606`), so
  not at scale.
- **Status.** Hybrid form VERIFIED to ship; benefit at scale UNVERIFIED.

### (h) Bulk-load via a side-channel file

- **Idea.** ObjectScript (or Rust) writes the adjacency to a file; the
  callout receives only a path and reads or `mmap`s it.
- **Mechanics.** Compact binary CSR snapshot named by `nkg_version`
  (`/usr/irissys/mgr/ivg/nkg-<ver>.csr`), written once by whichever process
  finds it missing (atomic rename). Every job calls
  `kg_bfs_file(path, seed, ...)`; the OS page cache shares the pages across
  processes, so N jobs do not hold N heap copies, and loading is a map, not
  a parse. This is the only option that makes "load once" work for
  short-lived jobs.
- **Needs.** Arno: a reader, a writer (or ObjectScript writes the text
  form), a snapshot format version, `memmap2`. rzf: nothing. ivg: path
  convention, write-then-rename, cleanup on version change, permissions.
- **Cost.** Medium.
- **Risks.** Shared filesystem assumption (same container or node; not
  mirrors, shards, or a read-only image); stale files; the file is
  unversioned data outside IRIS journaling and backup; security of the
  path (never take a path from a client); IRIS writing binary files from
  ObjectScript is slow unless Rust writes it. Arno has a close precedent
  in the `iris-fast-scan` crate and `callout/docdb_cache.rs`, not read in
  depth.
- **Prove cheaply.** Have a test callout write a 10K/50K CSR to `/tmp`, map
  it in two processes, time the first query in each.
- **Status.** UNVERIFIED.

### (i) Added by me: XDEV streaming device as an unbounded pipe

- **Idea.** rzf can register a Rust-backed IRIS device. ObjectScript
  `USE dev WRITE chunk` streams bytes to Rust with "no limit" for
  `XDevSerdeStreamed` (`rzf/readme.md:1039-1345`), flush with `WRITE *-3`.
  That would be chunked append without the `$ZF` argument limits and
  without a buffer static that `take` drains.
- **Risks.** Niche, device names must be numeric, needs a flush protocol,
  and Arno does not use it. UNVERIFIED in the deployed `.so`. Only worth it
  if (c) is rejected. I list it to close the question "is there a native
  streaming primitive". There is.

### (j) Added by me: adjacency via the existing `^ArnoKG` chunk global

- **Idea.** Combine (a) with the side channel that already works: ObjectScript
  writes `ExportAdjacencyWithPreds` chunks to `^ArnoKG("KG","nkg_adjwp",i)`
  stamped with `^NKG` version (this is `CacheNKGAdj` with the predicate
  form). Rust `kg_adj_load` reads them by point `get`, builds the resident
  index once, and `kg_bfs_resident` answers.
- **Why it is the best mechanical fit.** It uses only VERIFIED-working
  pieces: chunk global write (ivg), chunk `get` read (Arno
  `read_nkg_adjacency`), version-keyed resident static (betweenness), and
  it avoids `ns.keys()` and the `$ZF` limits in one move. The per-call
  export disappears after the first call in a process **and** after the
  first call anywhere, because the chunk global is shared across jobs.
- **Risks.** Chunk global is shared across graphs (the open `nkg_adj`
  issue): key it by graph. Rewriting the chunk global is O(E) ObjectScript
  on each version bump, so write-heavy graphs rebuild often; that cost is
  the same as any cache invalidation.
- **Status.** Components VERIFIED; composition UNVERIFIED.

### (k) Added by me: do not call the callout for small graphs

- **Idea.** Below a measured threshold the ObjectScript BFS wins outright;
  the callout earns its keep only when the graph is both large enough to
  amortise and small enough for the chosen transport. The prior spike's
  recommendation 4 (skip the callout above the limit) is one half; the
  other half is skip it below a break-even.
- **Prove cheaply.** M1 below.
- **Status.** UNVERIFIED, cheap.

## 5. Measurements that would decide this (none run yet)

Each is residue-free if run in a single `iris session` process against an
`^IVGSpike`-prefixed scratch global and killed after. I did not run them:
`ExportAdjacencyWithPreds` is hard-wired to `^NKG` (confirmed: the USER
namespace of `ivg-iris-enterprise` holds only `^NKG("$meta")`, so the export
returned length 0 in 0.08 ms), and writing a synthetic `^NKG` there would
mix with the test suite's data. Use a scratch namespace (`IVGTEST`,
`IVGFIX`) created and dropped for the run, or take the measurement in the
test harness.

- M1: what fraction of `BFSJson` is export, callout and result decode?
  Time the three phases at 300 and 3,000 nodes in one process.
- M2: does `ns.keys()` on negative integer subscripts work in the loaded
  `.so`? Call `test_neg_int_keys` with an `^IVGSpike` name.
- M3: repeat-query latency with a `^||` result cache. Wrap one path, run
  1,000 repeats, then invalidate with a write.
- M4: a resident BFS against `BFSFastJsonSorted` at 10K/50K. Needs a test
  entry in Arno, built from the pinned source.
- M5: is a 3 MB `n` argument accepted? Any `n`-linkage test entry in
  `rzf_callout_tests` will do.

## 6. Ranking for spec 236

1. (b) Result cache by graph version. In spec 236; independent of Arno;
   build first.
2. (k) Size gate, plus the prior spike's pre-check. In spec 236; measured by
   M1; keeps the fallback honest.
3. (j)+(a) Resident adjacency via the chunk global. Follow-up spec, after
   M1 and M4. The real BFS fix.
4. (g) Ball in ObjectScript, Rust on the ball. In scope only for algorithms
   that are superlinear.
5. (d) EXSTR entry. Follow-up; the cheapest Arno ask; worth it only if (j)
   is rejected.
6. (c) `kg_bfs_run`. A stepping stone; drop if M1 shows export dominates.
7. (h) File snapshot (mmap). Follow-up spec if short-lived TCP jobs matter.
8. (f) Out-of-process server. A separate initiative; do not fold into 236.
9. (e) Fix global reads. Arno-side diagnosis (M2); not an ivg deliverable.
10. (i) XDEV pipe. Drop unless (c) is rejected.

### Recommendation

1. **In spec 236:** require correctness parity between the Arno path and the
   ObjectScript owner at every size (as the prior spike said), add the
   version-keyed result cache (b) with database-state tests per write path,
   and add the size/break-even gate (k). Make Arno a fast path, never the
   only path.
2. **Decide BFS by measurement, not design.** Run M1 before promising a BFS
   speed-up. If the export dominates, no callout that re-ships the graph
   can win, and (c) and (d) are moot. If the callout dominates, (d) is the
   cheapest unlock.
3. **Follow-up spec:** resident adjacency (j)+(a), asked of Arno as two new
   symbols (minor bump under spec 065). The betweenness neighbourhood path
   is the working template, and it needs ivg's graph-dimension bug in
   `nkg_adj` fixed first.
4. **Drop:** `zfheap` tuning (prior spike), the `-5` versus `-6` theory, and
   any framing of `kg_bfs_compute` as "close to working".

### Where the evidence is thin

- The size of any win from a result cache in ivg: no measurement found.
- Any BFS speed-up: no benchmark exists for the string-adjacency path on a
  loaded container. The 112-128 ms figure is the ObjectScript baseline.
- Behaviour on long-lived pooled connections and on x86_64 or 8-bit builds.
- Whether `ns.keys()` or the deployed `.so` build is the cause of `[]`.

## 7. Open questions for the Arno maintainers

1. Where is the measurement that showed `^||` result caching for the
   `JSON_TABLE` / ASQ work? (Ask Tom, not Arno.) Which keys, what hit rate,
   and was the cache per process or shared?
2. Please run `test_neg_int_keys` and `test_str_keys` on the current build and
   report the `bug` field. Does `ns.keys()` iterate negative integer
   subscripts under `$ZF(-5)` on arm64 Linux, in a process that has
   already made one `$ZF` call?
3. `kg_ffi.rs:3367-3376` says global reads return empty under `$ZF(-5)`;
   spec 065 KL-2 says that is stale. Please delete or correct the comment,
   and say what `ffi_kg_bfs_compute` returns `[]` for (it swallows every
   error into `"[]"` at `kg_ffi.rs:3554-3560`; can it return the error?).
4. Can `kg_bfs_resident` (version-keyed resident adjacency with predicate
   indices) be added as a minor-version symbol, following
   `BETWEENNESS_ADJ_CACHE`? Please state the memory cap and the eviction rule.
5. Can a load entry read the adjacency from a global by point `get`, with the
   **global name and namespace as arguments**, not the hard-coded `"USER"`
   (`kg_ffi.rs:3082, 3193`, spec 065 KL-3)? ivg runs in `IVGTEST`,
   `IVGSEC` and customer namespaces.
6. Would you add an `n`/`N` (EXSTR) sibling of `kg_bfs_global` under a new
   name, leaving the frozen `2b/2B` symbol untouched, and confirm a 3 MB
   argument works on IRIS 2026.3 build 113?
7. Does `ADJ_BUFFER` ever get cleared on a failed `*_run`? Please add an
   explicit `kg_adj_reset` so a caller can recover without a dummy run.
8. Please ship `capabilities` in the next build of the `.so` deployed to
   `ivg-iris-enterprise`; the deployed one predates it, so ivg cannot route
   by Reader Revision there.
9. Is `ag_bfs_lazy` intended to be supported? It is in the table, has no
   record of a result, and shares the iteration pattern in question.
   `ag_bfs_compute` is defined after `rzf::init!()` and is missing from the
   table; which is intended?
10. The `iris_callin.rs` function-table offsets are "verified for IRIS
    2025.1". Is that table still used, or has the rzf typed API replaced
    it on all paths? If used, what is the plan for 2026.x?
11. Is `ARNO_USE_PPG=1` exercised anywhere in CI, and is it a real environment
    variable read at call time or at load? (It is read per call in
    `docdb_cache.rs:32`; confirm it is settable from an IRIS process.)

## Sources

- `docs/research/zf-heap-and-exstr.md` (prior spike, ivg).
- `~/ws/arno/iris-integration/arno-callout/src/kg_ffi.rs`: 16, 1142-1175,
  2607-2650, 2701-2735, 2763-2863, 3071-3176, 3367-3376, 3547-3600,
  4660-4780.
- `~/ws/arno/iris-integration/arno-callout/src/lib.rs`: 59-108, 290-345,
  739-900, 1046-1112.
- `~/ws/arno/specs/035-nkg-bfs-callout/spec.md`,
  `specs/063-rzf-dependency-pinning/FINDING.md`,
  `specs/065-callout-release-contract/spec.md` (FR-002, FR-003, KL-1..3),
  `specs/029-docdb-sidecar-shadow/research.md`,
  `docs/ARCHITECTURE.md:540-575`,
  `BUG_REPORT_kg_bfs_global_maxstring.md`.
- `~/ws/rzf/readme.md`: 36-45, 213-340, 764-800, 992-1030, 1039-1345,
  1685-1787; `~/ws/rzf/ARCHITECTURE.md`.
- ivg: `iris_src/src/Graph/KG/NKGAccelTraversal.cls:339-400`,
  `NKGAccelLoader.cls:15-70`, `NKGAccelCentrality.cls:366-430`,
  `NKGAccelAdjacency.cls:239-284`, `ArnoAccel.cls:61-80`, `Ledger.cls:1-45,
156`, `docs/KNOWN_ISSUES.md:581-606, 1079-1097`.
- Measurement: two one-shot `iris session IRIS -U USER` processes on
  `ivg-iris-enterprise`, `kg_adj_append` state test (section 1), and a
  read-only `ExportAdjacencyWithPreds` call on the empty USER `^NKG`
  (section 5). No global written, no CPF change.
