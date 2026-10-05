# Spec 236 Story 4: graph-version token audit

Date: 2026-10-03. Scope: FR-016 to FR-022. Empirical checks ran on
`ivg-iris-enterprise` in a scratch namespace `IVGSpike` (own database), since
removed. Labels: **V** = verified in code at the cited line, **E** = verified
empirically on the container, **U** = unverified.

## Summary (10 lines)

1. Recommended token: `^KG("__version")`, hardened. One `$Get` per check.
2. Ledger `seq` (A) is unsound: it is global, not per graph, and it does not
   move on legacy writes, erasure or snapshot restore (E).
3. `^NKG("$meta","version")` (B) is unsound: temporal shadow writes and any
   write with no `^NKG` skeleton leave it alone while the answer changes (E).
4. Every algorithm in scope reads only `^KG("out"/"in"/"deg")` (default graph
   for pagerank, wcc, cdlp; a named graph for ppr). Weights are not read (V).
5. Four code fixes make `__version` cover every `^KG` mutation: `BumpVersion`
   must use `$Increment`; BuildKG's multigraph branch must bump; snapshot
   restore must bump after import; the keyed primitives must bump.
6. Cypher DML and raw SQL do not touch `^KG` at all (E). No token can move for
   them, and the answer does not change either, so FR-019 tests for those two
   paths must compare cached against recomputed, not "answer changed".
7. `kg_SUBGRAPH` must self-bypass (FR-018): it reads live SQL `rdf_labels`, and
   a label insert changes its answer with no token moving (E).
8. `set_edge_weight` changes PageRank although weights are unused, because
   `WriteAdjacency` re-increments `^KG("deg")` (E). A separate bug, noted only.
9. Hazards found: `Kill ^KG` in `Loader.Load` and `BenchFormat` resets the
   counter to a reused value (E); `BumpVersion` is a non-atomic read-add-write
   that a rolled-back transaction undoes (E on rollback, V on the race).
10. Incident: `Build2HopExactStats` moves the TCP process to namespace USER (E).
    See "Incident" at the end.

## 1. What the in-scope algorithms read

| Algorithm | Entry | Reads | Graph |
| --- | --- | --- | --- |
| pagerank | `PageRank.PageRankGlobalJson` (PageRank.cls:276) | `^KG("deg"/"out", 0, ...)` | default only (`pGraph = 0`, :278) |
| ppr | `PageRank.RunJson` / `Run` (:30, :74) | `^KG("deg"/"out"/"in", pGraph, ...)` | any graph |
| wcc | `Algorithms.WCCJson` (Algorithms.cls:6) | `^KG("deg"/"in"/"out", pGraph)` | default only (:8) |
| cdlp | `Algorithms.CDLPJson` (:86) | same | default only (:88) |
| subgraph | `Subgraph.SubgraphJson` (Subgraph.cls:6) | `^KG("deg"/"in"/"out")`, `^KG("prop")`, SQL `rdf_labels` (:120-140) | default |

V. Also V: all four `ArnoAccel` entry points for these algorithms delegate to
the Owners today (ArnoAccel.cls:129-173), so `^KG` is the only data source.
Native Readers that read `^ArnoKG` are already gated on `^KG("__version")`
(ArnoAccel.cls:69-77).

Weights: PageRank divides by edge counts, never by weight (PageRank.cls:100-200,
V). WCC and CDLP are unweighted (V). A weight-only change cannot change these
answers. Embeddings: not read by any Owner. `kg_SUBGRAPH(include_embeddings=True)`
reads them in Python after the Owner returns (algorithms.py:477-504), so an
embedding write never affects a cached Owner answer (V); keep embeddings out of
the cached payload.

## 2. Write-path matrix

Columns: **Bumps** means the token moves after the mutation. Scoped means the
token is per graph. Monotonic means it never reuses a value.
`KGV` = `^KG("__version")`, `NKGV` = `^NKG("$meta","version")`,
`SEQ` = `^IVG.Ledger("head")`.

| # | Write path (file:line) | Touches `^KG` out/in/deg | KGV | NKGV | SEQ |
| --- | --- | --- | --- | --- | --- |
| 1 | `create_edge` (nodes_edges.py:838) via `WriteAdjacency` (EdgeScan.cls:133) | yes | bump (:141) E | bump only if skeleton (:153) E | no E |
| 2 | `set_edge_weight` (:909) via `WriteAdjacency` | yes, re-increments deg | bump E | only if skeleton | no |
| 3 | `delete_edge` (:939) via `DeleteAdjacency` (EdgeScan.cls:202) | yes | bump (:219) E | only if skeleton (`DeleteIndex`, GraphIndex.cls:164) | no |
| 4 | `delete_edge(all_graphs)` `DeleteAdjacencyAllGraphs` (:369) | yes | bump per graph V | as 3 | no |
| 5 | `bulk_ingest_edges` (:1426) via `BulkIngestEdgesSQL` (EdgeScan.cls:438) | yes | bump per edge E | only if skeleton E | no |
| 6 | `bulk_create_edges` / `bulk_create_nodes` / `bulk_load_session` (:1151-1420) | no; `sync()` then `BuildKG` | bump at BuildKG (TraversalBuild.cls:116) V | BuildNKG bumps | no |
| 7 | Cypher CREATE/MERGE/SET/DELETE (translator.py:6239, 7353, 8152; `execute_transaction` iris_sql_store.py:1039) | **no** E | no E | no E | no |
| 8 | Raw SQL `INSERT INTO rdf_edges` | **no** E | no E | no E | no |
| 9 | Temporal insert (TemporalIndex.cls:89 via `WriteAdjacencyShadow`, EdgeScan.cls:173) | yes, shadow | bump on first structural edge only (:183) E | **never** E | no |
| 10 | Temporal delete, last timestamp (TemporalIndex.cls:216 via `DeleteAdjacency`) | yes | bump V | only if skeleton | no |
| 11 | Temporal delete (not last) / purge (:458-610) | no (shadow stays) | no change needed E | no | no |
| 12 | `delete_nodes`, `delete_edges`, `delete_nodes_by_prefix` (Eraser.cls:301-425) | yes | bump via `InvalidateDerived` (:570, :416, :803) E | `DropNKG` +1 E | no |
| 13 | `erase_graph` (Eraser.cls:47) | yes | bump, even with no edges (:234) E | +1 only if edges E | **no** E |
| 14 | `erase_all` (Eraser.cls:653) | yes | bump (:748) E | +1 E | **no** |
| 15 | `restore_snapshot(merge=False)` (snapshot.py:807-862, globals :1251-1270) | yes, imported with Native `set` | bump at the `erase_all` step only, **not after import** E | raised by `_raise_nkg_version` (:10) | **no** E |
| 16 | `restore_snapshot(merge=True)` | yes | **no bump** E | +1 | no |
| 17 | `BuildKG` single-edge branch (TraversalBuild.cls:89-116) | yes | bump at end E | BuildNKG +2 E | no |
| 18 | `BuildKG` when `^IVG.GraphMode` is set (TraversalBuild.cls:81-87) | yes | **no bump** V | BuildNKG +2 | no |
| 19 | `BuildNKG`, `DropNKG`, `sync_nkg` | no (`^NKG` only) | no change needed | bump | no |
| 20 | Ledger commit create/delete rel (LedgerApply.cls:443-500, 638-668) | yes, via `WriteAdjacency`/`DeleteAdjacency` | bump E | as 1 | +1 E |
| 21 | Ledger commit label/prop only | no | no E | no | +1 E |
| 22 | Ledger `Enable` | no | no | no | 0 to 1 E |
| 23 | Ledger `PurgeAll` (Ledger.cls:230) | no | no | no | **reset** (`Kill ^IVG.Ledger`) V |
| 24 | FHIR graph sync edges (FHIRGraph.cls:2195, 2222, 2237, 2426, 2559) | yes, via primitives | bump V | as 1 | no |
| 25 | FHIR graph `^KG("prop"/"label")` (FHIRGraph.cls:2501-2528, 2578) | no (prop/label only) | **no** V | no | no |
| 26 | `materialize_inference` / `retract_inference` (schema.py:2395, 2541) | yes, via primitives | bump V | as 1 | no |
| 27 | `import_rdf`, `load_networkx` (snapshot.py:342, 518) | no; `BuildKG` after | bump at BuildKG V | yes | no |
| 28 | kg_node_stores migration (migrations/kg_node_stores.py:172) | kills `^KG` stores, then `BuildKG` | bump at BuildKG if it completes V | yes | no |
| 29 | Keyed multigraph primitives (EdgeScan.cls:240, 290) | yes | bump only when they delegate to `WriteAdjacency`/`DeleteAdjacency`; the 2nd+ edge path **does not** V | no | no |
| 30 | Legacy `Loader.Load` (Loader.cls:18), `BenchFormat` (:29, :90) | `Kill ^KG`, writes flat layout | **counter destroyed** (`BenchFormat` sets 1, :42) V | `Loader` calls `DropNKG` | no |
| 31 | Embedding writes | no | no (correct) | no | no |
| 32 | `map_sql_table` bridge (fhir.py:79-89) | no; rows never copied | n/a | n/a | n/a |

Candidate (C), a new dedicated counter, would need the same 20-odd bump sites
as KGV. KGV already sits in 14 of them.

### Candidate verdicts

| Token | Bumps on every `^KG` mutation? | Per graph | Monotonic | Survives restore and erasure | Verdict |
| --- | --- | --- | --- | --- | --- |
| A ledger `seq` | No: rows 1-5, 7-16, 24-30 never move it (E) | No: one head for the namespace (Ledger.cls:5, :523); graph is per record | Yes, except `PurgeAll` | **No**: erase and restore leave it at 3 while the answer changed (E) | Unsound |
| B `NKGV` | No: rows 9, 11 and any write without skeleton (E) | No: `^NKG` has no graph dimension (GraphIndex.cls:148-152) | Yes: `DropNKG` keeps it rising (GraphIndex.cls:23-27); restore raises it past both values (snapshot.py:10-18) | Yes | Unsound |
| C new counter | Only if every site is edited | Could be | Yes if `$Increment` | Yes if restore/erase bump | Sound but duplicates KGV |
| D `KGV` as is | No: rows 15, 16, 18, 29, 30 | No, global | Reset by row 30; lost-update race | Erase yes, restore no | Unsound today |
| **D' `KGV` hardened** | Yes after the fixes below | Global (over-invalidates other graphs; sound) | Yes | Yes | **Recommended** |

## 3. Answers to the specific questions

**What does `NKGV` count?** A single namespace-wide counter, not per graph
(V). Incremented by `WriteAdjacency` (only when `^NKG("$meta","nodeCount")`
exists, EdgeScan.cls:150-154), `DeleteIndex` (GraphIndex.cls:164), `BuildNKG`
(TraversalBuild.cls:271 plus `DropNKG`), `ArnoAccel.BumpVersion` (:64) and
snapshot restore. It is **not** reused after a rebuild: `DropNKG` writes
`tVer + 1` (GraphIndex.cls:23-27) and restore sets `max(floor, restored) + 1`.
A stale entry cannot falsely match a rebuilt `^NKG` on reuse grounds. The
failure is the opposite: it does not move when it should (rows 9, 11,
skeleton-absent writes). E: `create_edge` with no skeleton left it at 30 while
the PageRank answer changed; a temporal insert left it at 31 while the answer
changed.

**Is ledger `seq` graph-scoped?** No (V). One `^IVG.Ledger("head")` per
namespace; a revision can span graphs (ADR-0004). **Monotonic across erasure?**
It never goes backwards, but it does not bump on erasure (E: `erase_graph`
left head at 3 while the answer went from `8fb61ee6` to the empty-graph hash).
This matches ADR-0004 (history kept, nothing appended). A cache keyed on `seq`
would serve an erased graph's answer, violating FR-020. **Legacy or disabled
ledger?** Writes made with the ledger absent, disabled, or enabled in
non-strict mode never move it (E: legacy `create_edge` with the ledger on left
head at 3). Strict mode only blocks the engine entry points
(`_ledger_check`); FHIR writers, raw SQL and `BuildKG` are not blocked.
`PurgeAll` kills the global, so `seq` restarts and values repeat (V).

**Does raw SQL into `rdf_edges` move any token?** No (E: kg 14 to 14, nkg 16 to
16, head unchanged). It also does not change any algorithm answer, because the
Owners read `^KG`, which the SQL insert did not touch. **BuildKG?** KGV +1,
NKGV +2, answer changes (E) except the multigraph branch (row 18, V).
**Erasure?** KGV +1 for `erase_graph`, `erase_all`, `delete_nodes` (E). **Snapshot
restore?** KGV is bumped by the `erase_all` inside restore but not after the
import (E, see 4.2). **Delete?** KGV +1 (E).

## 4. Gaps and required fixes

Order of use in the cache protocol (see 6): read KGV, compute, store with that
KGV. This is sound as long as every mutation is followed by a bump. Fixes:

1. **`ArnoAccel.BumpVersion`** (ArnoAccel.cls:61-65, used by
   `Eraser.InvalidateDerived`, Eraser.cls:803-808). It does
   `Set ^KG("__version") = $Get(...) + 1`. Two defects. (a) Lost update: two
   concurrent bumps can write the same value, so a reader that cached between
   the two mutations matches after the second (V). (b) A plain `Set` inside the
   Eraser transaction is undone by `TROLLBACK`; `$Increment` is not (E:
   `$Increment` stayed 6 after rollback, plain `Set` reverted to 6 from 6+1).
   A rolled-back erase would leave a dirty-read entry tagged with a value the
   next bump reuses. Fix: `Set tmp = $Increment(^KG("__version"))`.
2. **`restore_snapshot` global import** (snapshot.py:1251-1270, `finally:`
   block that calls `_raise_nkg_version`). After the import, add
   `iris_obj.classMethodVoid("Graph.KG.ArnoAccel", "BumpVersion")` (after fix 1).
   E: KGV was 33 at the end of `erase_all` and 33 after the import, so an
   answer cached in that window (the empty graph, hash `2a8a43b4`) stays valid
   after the data returns (final hash `8fb61ee6`). `merge=True` never bumps (E:
   33 to 33 while content was added).
3. **`BuildKG` multigraph branch** (TraversalBuild.cls:81-87) returns after
   `BuildKGKeyed` and `TCommit` without the increment at :116. Add
   `Set tmp = $Increment(^KG("__version"))` after the `TCommit` at :83. Also add
   it in the `Catch` at the end of `BuildKG` (after `TRollback`): a failed
   rebuild has already exposed a partial `^KG` to readers (reads are
   uncommitted in IRIS).
4. **Keyed primitives** `WriteAdjacencyKeyed` and `DeleteAdjacencyKeyed`
   (EdgeScan.cls:240-333): only the paths that delegate to the single-edge
   writers bump. The 2nd-and-later edge path changes `^KG("deg")`, which
   PageRank divides by, with no bump. Add
   `Set tmp = $Increment(^KG("__version"))` before `Quit` inside the `Try`. No
   production caller exists today (grep: tests and docs only), so this is
   latent.
5. **`Kill ^KG` in `Loader.Load` (Loader.cls:18) and `BenchFormat`
   (:29, :42, :90).** They destroy or reset the counter, so a value is reused
   (E: after `Kill ^KG("__version")` the next write produced 1, a value seen
   before). Both are legacy or benchmark tools. Change them to keep the counter:
   read it, kill, then `Set ^KG("__version") = tOld + 1`; or kill the named
   subtrees as `EraseAll` does (Eraser.cls:724-746). Also: the cache must
   `Kill ^IVG.AlgoCache` in any such tool.
6. **`WriteAdjacency` is not idempotent in `deg`** (EdgeScan.cls:137-138).
   `set_edge_weight` calls it on an existing edge and increments `deg` again, so
   PageRank changes with no structural change (E: answer `471fc6c1` to
   `c8b59b08` with weights unused). Not a token problem, but a cache will
   faithfully return the drifted value. Separate bug.
7. **Weights, future-proofing.** Rows `WriteAdjacencyShadow` existing-edge
   branch (EdgeScan.cls:176-184), the same-edge branches of the keyed writer
   (:255-257, :268-275) and `PersistQuals` (bumps, LedgerApply.cls:549) differ on
   weight-only changes. Harmless for the in-scope algorithms (V). If a weighted
   algorithm is ever cached, bump on every weight set.

### Self-bypass list (FR-018)

| Path | Why | Action |
| --- | --- | --- |
| `kg_SUBGRAPH` (all forms) | Reads SQL `rdf_labels` live; label and prop writes bump nothing (rows 21, 25, SQL label insert). E: raw label insert changed `node_labels` with KGV 23 to 23. | Do not cache it in this feature. Alternative: cache only `nodes` and `edges` and read labels/props live. Making every label and prop writer bump is more code than the feature (create_node, store_node, BulkIngestNodesSQL, Cypher SET/REMOVE, ledger AddLabel, Eraser, FHIRGraph). |
| Cypher DML and raw SQL (rows 7, 8) | Do not update `^KG`; no token can see them | Nothing to bypass: the cache and the Owner agree (both ignore the write until `sync()`). FR-019 must use a differential test (section 7). |
| Any write made while `^KG` was killed outside the primitives (row 30) | Counter destroyed | Loader and BenchFormat: fix per 4.5, or `Kill ^IVG.AlgoCache`. |
| Multigraph mode, until fixes 3 and 4 ship | Rows 18, 29 | Bypass when `$Data(^IVG.GraphMode)` is true, as a guard. |

## 5. Cost of the check on every hit

Token read: **1** global reference, `$Get(^KG("__version"))`. Entry read: **1**
reference to the node holding the stored token
(`^IVG.AlgoCache(graphKey, algorithm, paramHash)` with the token as the node
value and the payload in a child or a `$ListBuild` piece). Minimum 2 global
reads when the payload sits in the same node; 3 if the payload is a separate
child. Do the compare inside one ObjectScript classmethod so there is one Native
API round trip, not three. If the token were a composite (epoch plus counter)
it would add 1 read; not needed once fix 5 is done. A per-graph counter (C) has
the same cost.

## 6. Recommended token and protocol

**Token = `^KG("__version")`**, after fixes 1-5. It is already bumped by the
shared primitives (`WriteAdjacency`, `WriteAdjacencyShadow` on new edge,
`DeleteAdjacency`, `BuildKG`, `InvalidateDerived`, `LedgerApply`), already
the staleness gate for `^ArnoKG`, and it is the only candidate that moves on
erasure. It is global, so a write to any graph invalidates every graph's
entries. That is sound (over-invalidation) and satisfies FR-016, which scopes
the cache **key** to one graph, not the token. A per-graph refinement
(`$Increment(^KG("gver", graphKey))` next to each bump) is possible later;
`BuildKG`, `EraseAll` and restore would bump all graphs.

Protocol (soundness argument): writers mutate, then bump (verified ordering in
`WriteAdjacency`, :135-141). Readers: (1) read T; (2) compute; (3) store
`(T, result)`. A mutation that overlaps step 2 bumps after step 2 sees it, so
the entry is tagged T and the next read sees T+1: a miss. A reader that read T
after the bump saw the completed mutation. The only unsound case is a mutation
with no following bump, which is exactly the gap list in section 4. Add one
rule: step 3 must not store if the token at store time differs from T (saves a
wasted write, not needed for soundness).

## 7. Per-write-path test list (FR-019)

Oracle for every test: `cached = run(alg)`; mutate through the path;
`after = run(alg)` (cache enabled); `truth = run(alg, cache=bypass)`;
assert `after == truth`; assert `after != cached` where the path changes `^KG`;
assert a repeat call with no change is a hit. Use `kg_PAGERANK`, `kg_WCC`,
`kg_CDLP`, `kg_PPR` (default and named graph). One test per row, parametrized
by algorithm.

1. `create_edge` default graph and named graph.
2. `set_edge_weight` (assert `after == truth`; do not assert "changed by
   weight").
3. `delete_edge`, including `all_graphs=True`.
4. `bulk_ingest_edges` (ObjectScript path and the Python fallback, :1484).
5. `bulk_create_edges` with `auto_sync=True`, and `bulk_load_session`.
6. Cypher CREATE then `sync()`: before `sync()` assert `after == truth` and
   `after == cached` (write invisible to both); after `sync()` assert changed.
7. Raw SQL insert: same as 6.
8. Temporal insert: first timestamp (new structural edge), second timestamp
   (no structural change, `after == cached` and `== truth`).
9. Temporal delete: last timestamp removes the shadow; non-last leaves it.
10. Temporal purge: `after == truth`.
11. `delete_nodes`, `delete_edges`, `delete_nodes_by_prefix`.
12. `erase_graph` with edges, with no edges, and the graph named in a cached
    entry (FR-020: assert no erased node id appears in any cached payload).
13. `erase_all`.
14. `restore_snapshot(merge=False)`: compute between the internal erase and the
    import by wrapping `erase_all` as the spike did; assert the final answer
    equals `truth` (fails until fix 2).
15. `restore_snapshot(merge=True)`.
16. `BuildKG` single-edge and with `^IVG.GraphMode` set (fails until fix 3).
17. `BuildNKG` / `DropNKG` only: `after == cached` is allowed; `after == truth`
    required.
18. Ledger commit create and delete relationship; ledger commit label-only
    (assert `after == truth`).
19. Ledger `Enable`, `PurgeAll` then new commits (seq reuse must not matter).
20. FHIR graph sync edge add/delete.
21. `materialize_inference` and `retract_inference`.
22. Keyed primitives: second edge on a triple (fails until fix 4).
23. `Kill ^KG` tools (`Loader.Load`, `BenchFormat`): assert a later write
    produces a value not previously seen (fails until fix 5).
24. Concurrency: bump racing a store; two concurrent `BumpVersion` calls
    produce two distinct values (fails until fix 1).
25. Rolled-back erase: `after == truth` (fails until fix 1).
26. Two graphs: a write in graph B never changes graph A's answer; both stay
    consistent with `truth`.
27. Different parameters produce separate entries; two connections share a hit.
28. Embedding-only write: `after == cached`, hit retained for the Owners
    (expected correct; KGV must not move).
29. `kg_SUBGRAPH`: assert it is never served from the cache.

## 8. Unverified items and open questions

- U: the `ledger delete_node` cascade and `delete_relationship` paths were
  read, not run (`Changeset.delete_node` accepts `strict` or `detach`, not
  `cascade`). Code reads as `DeleteRelInternal` to `DeleteAdjacency` (bump).
- U: `EraseEdges` and `EraseNodes` with real matches were not run; the bump
  sites (:416, :570) were read. `delete_nodes` through `EraseNodeIds` was run (+2).
- U: `fhir_links.py` and the genomics writers (specs 233, 235) were only
  grepped; they contain no direct `^KG` writes, and FHIRGraph edges use the
  primitives. Node ids through `fhir_graph.py` sync were not run.
- U: `BuildKG` failing midway: partial-state behaviour is from the code
  comments (TraversalBuild.cls:32-47), not run.
- U: the lost-update race in `BumpVersion` is V by reading, not reproduced.
- U: `import_rdf` / `load_networkx` were not run; they call `BuildKG`.
- Open: should the cache also expose `Kill ^IVG.AlgoCache` in `EraseAll`? Cheap
  and defensive; the token already covers it.
- Open: whether to make `WriteAdjacency` idempotent in `deg` (item 6) before
  the cache lands, so the first cached answers are not wrong answers.
- Open: default-graph-only algorithms (pagerank, wcc, cdlp) could use a
  per-graph counter and stop invalidating when only named graphs change.
- Open: `restore_snapshot` with a SQL-only archive leaves `^KG` stale by
  design; the cache follows `^KG`, so it is consistent, but FR-019 test 14
  should include a sync.

## Incident (disclose)

`Graph.KG.Traversal.Build2HopExactStats`, called by `BuildNKG` and so by
`BuildKG`, `sync()` and `engine.sync()`, leaves the TCP Native API process in
namespace USER when the connection was opened in another namespace (E: after
`BuildKG` in `IVGSpike`, `^KG("__version")` read 54958, the USER value, while the
`IVGSpike` value was 8). The first run of my spike therefore executed the steps
after `BuildKG` (`delete_edge`, `bulk_ingest_edges`, `delete_nodes_by_prefix`,
`erase_graph(None)`) against the **USER** namespace, not the scratch one. USER
had 0 rows in `nodes`, `rdf_edges`, `rdf_labels`, `rdf_props` afterwards, and
its `^KG` default-graph adjacency was empty; its default-graph content, if any
existed, was erased (it was a shared test namespace rebuilt by fixtures; I
could not tell what it held beforehand). The second and third runs recreated
the native connection before every step and asserted a sentinel global, and ran
only in `IVGSpike`. Cleanup: namespace `IVGSpike`, database `IVGSPIKE` and its
directory, and `/tmp/tcpsrc_IVGSpike` removed; container `/tmp/*.scr` removed.
`/tmp/spike/*.txt` on the host belongs to another session and was left alone.
The namespace flip is itself a finding for spec 236 (any non-USER deployment).
