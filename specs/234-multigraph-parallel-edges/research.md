# Research: Opt-in multigraph (parallel edges)

**Spec**: [spec.md](spec.md) · **Baseline commit**: `92b078e` · **Date**: 2026-09-26

This file lists every place that treats `(s, p, o_id, graph_id)` as the identity
of an edge, found by reading the code at `92b078e`. Line numbers are for that
commit. No IRIS run was needed; each entry is a read of the code, not a
measurement.

## R1. Where edge identity is the triple

Each row is one touchpoint. **Kind** is one of:

- **K**: a key or constraint that enforces the triple as unique;
- **W**: a writer that assumes one edge per triple;
- **R**: a reader that would under-count or merge parallel edges;
- **ID**: a place that derives an identity from the triple;
- **OK**: already correct for parallel edges, listed so it is not changed by
  mistake.

### SQL schema, constraints and migrations

| #   | Location                                                          | Kind | What it does                                                                                                          |
| --- | ----------------------------------------------------------------- | ---- | --------------------------------------------------------------------------------------------------------------------- |
| T01 | `iris_vector_graph/schema.py:31-41`                               | K    | `RDF_EDGES_DDL`: `edge_id` IDENTITY PK, FKs to nodes, `CONSTRAINT u_spo_graph UNIQUE (s, p, o_id, graph_id)`.         |
| T02 | `iris_vector_graph/schema.py:755-770`                             | K    | Migration that drops `u_spo`/`uspo` and adds `u_spo_graph`. The new constraint swap goes next to it.                  |
| T03 | `iris_vector_graph/schema.py:1936`                                | K    | Comment on the legacy graph-blind `UNIQUE (s, p, o_id)`. The 4.0.0 upgrade path has to know both old keys.            |
| T04 | `iris_vector_graph/schema.py:362`                                 | K    | Edge embeddings: `uq_edge_emb_graph_spo UNIQUE (graph_id, s, p, o_id)`. An edge vector is keyed by the triple.        |
| T05 | `iris_vector_graph/_engine/schema.py:205-217`                     | K    | Routed edge-embedding DDL `uq_{short} UNIQUE (graph_id, s, p, o_id)`. Same ambiguity as T04.                          |
| T06 | `iris_vector_graph/_engine/admin.py:180`                          | R    | The status report names the edge key as UNIQUE `(s, p, o_id)`.                                                        |
| T07 | `iris_vector_graph/_engine/embeddings.py:1064`                    | R    | Edge-embedding lookup by triple.                                                                                      |
| T08 | `iris_vector_graph/migrations/upgrade.py` (4.0.0 re-key, line 20) | W    | Rebuilds `rdf_edges` into a staging table and drops the source. It must carry `ekey` once the column exists.          |
| T09 | `iris_vector_graph/migrations/graph_scoped_embeddings.py:278`     | K    | `_rekey_children` re-keys the child tables by `(graph_id, s, p, o_id)`. It has to leave the new constraint unchanged. |

### `^KG` writers (ObjectScript)

| #   | Location                                                                               | Kind | What it does                                                                                                                                                                                                         |
| --- | -------------------------------------------------------------------------------------- | ---- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| T10 | `Graph/KG/EdgeScan.cls:132-151`                                                        | W    | `WriteAdjacency`: sets `^KG("out", g, s, p, o)` and `("in", …)`. It increments `deg`/`degp` even when the triple is already there. It writes `^NKG` only for a new triple.                                           |
| T11 | `Graph/KG/EdgeScan.cls:170-181`                                                        | W    | `WriteAdjacencyShadow`: the temporal shadow, idempotent in the counters. Many timestamps count as one structural edge.                                                                                               |
| T12 | `Graph/KG/EdgeScan.cls:196-216`                                                        | W    | `DeleteAdjacency`: kills the leaf and decrements the counters once. With parallel edges, deleting one would drop them all.                                                                                           |
| T13 | `Graph/KG/EdgeScan.cls` (`DeleteAdjacencyAllGraphs`, called from `nodes_edges.py:977`) | W    | Delete across all graphs, delegating per edge to T12.                                                                                                                                                                |
| T14 | `Graph/KG/LedgerApply.cls:140-151`                                                     | ID   | `FindEdge`: `SELECT %ID … WHERE s = ? AND p = ? AND o_id = ? AND graph_id = ?`, taking the first row.                                                                                                                |
| T15 | `Graph/KG/LedgerApply.cls:154-163`                                                     | ID   | `AllocStmt`: `^IVG.Ledger("tuple", s, p, o, gk) = stmtId`, one statement per live triple.                                                                                                                            |
| T16 | `Graph/KG/LedgerApply.cls:166-200`                                                     | ID   | `ResolveRef` with a `tuple` ref resolves through T14 and T15.                                                                                                                                                        |
| T17 | `Graph/KG/LedgerApply.cls:376-392`                                                     | ID   | Detach-delete of a node looks up each incident edge's statement by triple.                                                                                                                                           |
| T18 | `Graph/KG/LedgerApply.cls:446-500`                                                     | W    | `OpCreateRel`: returns `relationship_exists` when a triple is found; upsert merges qualifiers into it; otherwise it INSERTs, then sets `^KG("out"/"in")` or calls `WriteAdjacency`. Records `TupleJson(s, p, o, g)`. |
| T19 | `Graph/KG/LedgerApply.cls:537, 546-547, 660`                                           | W    | Weight update by `%ID`, the `^KG` weight rewrite by triple, and DELETE by `%ID`.                                                                                                                                     |
| T20 | `Graph/KG/LedgerGenesis.cls:38`                                                        | ID   | Genesis: `SELECT s, p, o_id, graph_id, qualifiers FROM rdf_edges ORDER BY s, p, o_id, graph_id`. The order assumes the triple is unique.                                                                             |
| T21 | `Graph/KG/LedgerGenesis.cls:81-102`                                                    | ID   | Genesis reconcile: `tStmtByTuple(s, p, o, gk)` and the `tuple` index.                                                                                                                                                |
| T22 | `Graph/KG/Ledger.cls:8-10, 176`                                                        | ID   | The layout of record: `("stmt", id) = $LB(s, p, o, graph, …)`, `("tuple", s, p, o, gk)`, and the idem fingerprint.                                                                                                   |
| T23 | `Graph/KG/Ledger.cls:539-560`                                                          | ID   | `CanonicalRecord`: the JSON that is fingerprinted. For a relationship, `new` is `TupleJson(s, p, o, g)`.                                                                                                             |
| T24 | `Graph/KG/Ledger.cls:211-219`                                                          | OK   | `RebuildEdgeIndices`: rebuilds the SQL indices and so picks up a new constraint.                                                                                                                                     |
| T25 | `Graph/KG/TraversalBuild.cls:6, 48, 79, 96`                                            | W    | `BuildKG`: reads `rdf_edges` and writes one `^KG("out", g, s, p, o)` per row. For a second parallel row it overwrites the weight and counts the degree twice.                                                        |
| T26 | `Graph/KG/TraversalBuild.cls:138-190`                                                  | W    | `BuildNKG`: walks `^KG("out")` into `^NKG`.                                                                                                                                                                          |
| T27 | `Graph/KG/TraversalBuild.cls:192, 233, 277, 333`                                       | R    | `BackfillDegp`, two-hop stats and `KGEdgeCount` count leaves, not edges.                                                                                                                                             |
| T28 | `Graph/KG/Loader.cls:36-37`, `BenchFormat.cls:35-39`                                   | W    | The legacy no-graph layout, used by benchmarks only.                                                                                                                                                                 |
| T29 | `Graph/KG/Eraser.cls:214-222, 368-376`                                                 | W    | Kills whole subtrees, including `edgeprop`. That is safe for extra subscripts.                                                                                                                                       |
| T30 | `Graph/KG/Eraser.cls:494-525`                                                          | ID   | Ledger scrub: `Kill ^IVG.Ledger("tuple", s, p, o, gk)`, built from `("stmt")`.                                                                                                                                       |
| T31 | `Graph/KG/GraphIndex.cls:144`                                                          | W    | `DeleteIndex`: removes the `^NKG` entry by triple.                                                                                                                                                                   |
| T32 | `Graph/KG/GraphStores.cls:91-92`                                                       | R    | The store inventory lists `^IVG.Ledger("tuple")` as "subscript 5". A new subscript changes that entry.                                                                                                               |

### `^KG` readers (ObjectScript)

These walk `$Order(^KG("out"|"in", g, s, p, o))` and treat a leaf as one edge.
With the layout in spec FR-004 they keep working unchanged: the leaf keeps its
value, and `$Data` stays true (it returns 11, not 1). They only need to change if
they must count parallel edges. `grep` finds no `$Data(^KG("out" …)) = 1` test
and no `$Query` over `^KG` (checked at `92b078e`).

| #   | Class                                            | `^KG` refs    | Needs multiplicity?                                                                                  |
| --- | ------------------------------------------------ | ------------- | ---------------------------------------------------------------------------------------------------- |
| T33 | `ArnoAccel.cls` (`:87`, `:104-112`)              | 4             | No. It feeds Arno one edge per triple; see R5.                                                       |
| T34 | `Algorithms.cls`                                 | 10            | No (reachability, components).                                                                       |
| T35 | `Centrality.cls`                                 | 12            | Degree centrality reads `^KG("deg")`, so it gets the right count.                                    |
| T36 | `FHIRGraph.cls`                                  | 10            | No. FHIR sync writes one edge per reference param.                                                   |
| T37 | `GraphKey.cls`, `GraphIndex.cls`                 | 2             | No.                                                                                                  |
| T38 | `GraphVerify.cls`                                | 10            | **Yes**: it compares `rdf_edges` row counts with `^KG` leaf counts.                                  |
| T39 | `NKGAccelTraversal.cls`, `NKGAccelAdjacency.cls` | 16            | No. This is the legacy layout.                                                                       |
| T40 | `PageRank.cls`, `PageRankEmbedded.cls`           | 9             | Open: a multi-edge could weight PPR. Out of scope (FR-015).                                          |
| T41 | `TemporalIndex.cls:71-110, 204-213`              | 1 (+tout/tin) | No. Temporal edges are keyed `(g, ts, s, p, o)`, so the timestamp is already their multiplicity key. |
| T42 | `Subgraph.cls`                                   | 8             | **Yes**, when a subgraph is returned as a list of edges.                                             |
| T43 | `TraversalKHop.cls`                              | 18            | No for node sets; yes for edge counts.                                                               |
| T44 | `TraversalBFS.cls`, `TraversalPaths.cls`         | 19            | **Yes** for paths returned as edges; no for node reachability.                                       |

### Python writers

| #   | Location                                                                            | Kind | What it does                                                                                                                       |
| --- | ----------------------------------------------------------------------------------- | ---- | ---------------------------------------------------------------------------------------------------------------------------------- |
| T45 | `_engine/nodes_edges.py:852-889`                                                    | W    | `create_edge`: INSERT; treats `unique` or `-119` as a benign duplicate and returns False; then calls `WriteAdjacency`.             |
| T46 | `_engine/nodes_edges.py:892-915`                                                    | W    | `set_edge_weight`: by triple, then `WriteAdjacency`. That double-counts `deg` today (T10).                                         |
| T47 | `_engine/nodes_edges.py:960-995`                                                    | W    | `delete_edge`: DELETE by triple, then `DeleteAdjacency`.                                                                           |
| T48 | `_engine/nodes_edges.py:1424, 1432, 1535, 1705`                                     | W    | Bulk edge insert paths, with a NOT EXISTS or `-119` skip.                                                                          |
| T49 | `bulk_loader.py:351`                                                                | W    | `skip_existing` avoids UNIQUE violations.                                                                                          |
| T50 | `_engine/snapshot.py:386, 391`                                                      | W    | `import_rdf`: `INSERT … SELECT … WHERE NOT EXISTS (… s = ? AND p = ? AND o_id = ? AND graph_id = ?)`.                              |
| T51 | `_engine/snapshot.py:465`                                                           | W    | `BuildKG` after an RDF import (T25).                                                                                               |
| T52 | `_engine/snapshot.py:880-905`                                                       | W    | Restore from NDJSON: strips `id`, keeps the other columns. An archive without `ekey` has to land at 0.                             |
| T53 | `_engine/snapshot.py:46-80` (`STORE_PLAN`), `:1217-1250`                            | OK   | The plan and the generic recursive walk. Extra `^KG` subscripts are carried with no change; a new subtree would need a plan entry. |
| T54 | `_engine/admin.py:462`, `_engine/schema.py:2331, 2336`                              | W    | Maintenance paths that rebuild or verify adjacency.                                                                                |
| T55 | `_engine/temporal.py:113, 220`                                                      | W    | Temporal insert calls the shadow (T11).                                                                                            |
| T56 | `_validate.py:59`, `schema.py:1275-1276, 2172`, `stores/iris_sql_store.py:608, 616` | W/R  | Validation and store-level edge queries by triple.                                                                                 |
| T57 | `ledger/changeset.py:70-91, 213, 230`                                               | ID   | `_tuple_wire` and `_rel_ref`: a relationship ref is a tuple, a `stmt_id` or an `op_ref`.                                           |

### Cypher translator

| #   | Location                                                 | Kind  | What it does                                                                                                                                                                                                                            |
| --- | -------------------------------------------------------- | ----- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| T58 | `cypher/translator.py:4924-4929`                         | W     | CREATE with VALUES: `INSERT (s, p, o_id, [qualifiers], graph_id)`. Match6 [14] fails here on `u_spo_graph`.                                                                                                                             |
| T59 | `cypher/translator.py:4963-4987`                         | W     | CREATE after MATCH: `INSERT … SELECT DISTINCT _ge.c1, _ge.c2, _ge.c3, ? … WHERE NOT EXISTS (triple)`. The comment says "rdf_edges holds one edge per (s, p, o_id, graph_id)". Both the DISTINCT and the guard collapse per-row creates. |
| T60 | `cypher/translator.py:5048, 5105-5111`                   | ID    | `_register_created_relationship` binds `__edge_{v}_s/_p/_o`.                                                                                                                                                                            |
| T61 | `cypher/translator.py:5350, 5512, 5625-5660`             | W     | MERGE: `translate_merge_clause`, the relationship patterns, and the rewrite of VALUES and `_ge` inserts into NOT EXISTS guards.                                                                                                         |
| T62 | `cypher/translator.py:6355-6384, 6543-6548`              | OK    | SET and DELETE use `edge_id IN (subquery)`. Correct when the subquery projects `edge_id`.                                                                                                                                               |
| T63 | `cypher/translator.py:6912, 6998-7012`                   | ID    | Bound vs fresh edge identity, and path element identity columns `(s, p, o_id)` / `(_os, _p, _oo)`.                                                                                                                                      |
| T64 | `cypher/translator.py:7522-7526`                         | ID    | `_edge_ident_cols`: relationship isomorphism compares triples.                                                                                                                                                                          |
| T65 | `cypher/translator.py:8489-8523`                         | ID    | A stage-bound relationship re-matched by `__edge_{v}_s/_p/_o`.                                                                                                                                                                          |
| T66 | `cypher/translator.py:9133`                              | OK    | `edge_id <> edge_id`.                                                                                                                                                                                                                   |
| T67 | `cypher/translator.py:9788-9830`                         | ID    | `_rel_identity_comparison`: `r1 = r2` compares triples.                                                                                                                                                                                 |
| T68 | `cypher/translator.py:10026`                             | ID    | "Stage edge variables store identity as `__edge_{var}_s/p/o` columns."                                                                                                                                                                  |
| T69 | `cypher/translator.py:18578-18619`                       | ID    | WITH promotion: carries `__edge_{v}_s/_p/_o` across stages and never carries `edge_id`. This is the root of Match4 [8] and Match9 [6].                                                                                                  |
| T70 | `cypher/translator.py:18622-18640`                       | R     | GROUP BY for an edge variable adds s/p/o, so parallel edges with equal qualifiers collapse into one group.                                                                                                                              |
| T71 | `cypher/translator.py:~8074-8090`                        | OK    | The undirected CTE `_ue` carries `edge_id` (fix `92b078e`).                                                                                                                                                                             |
| T72 | `cypher/translator.py:~12707-12719`                      | OK    | `count(DISTINCT r)` counts `eN.edge_id`.                                                                                                                                                                                                |
| T73 | `schema.py:1590` (`CY_VLP_PATHS`)                        | OK/ID | Walks `rdf_edges` by SQL and de-dupes on `edge_id` (the `pe` list). Parallel edges are already enumerated. But the path key `k` it emits is `$C(2) s $C(1) p $C(1) o $C(2)`, which is triple-keyed.                                     |
| T74 | `schema.py:1593, 1596` (`CY_VLP_HAS`, `CY_VLP_DISJOINT`) | ID    | Relationship membership and disjointness over key `k`. Two parallel edges read as the same relationship.                                                                                                                                |
| T75 | `_engine/query.py:906-999`                               | R     | Engine var-length path: `_get_edge_rels(src, tgt, hop)` fetches rels by endpoint pair, and `_format_rel` renders them with no identity.                                                                                                 |

### Bolt

| #   | Location                 | Kind | What it does                                                                                                                                                      |
| --- | ------------------------ | ---- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| T76 | `bolt_server.py:48, 353` | ID   | `TAG_RELATIONSHIP` and `pack_relationship`.                                                                                                                       |
| T77 | `bolt_server.py:789`     | ID   | For a bare string value, `rel_id = hash(val)`.                                                                                                                    |
| T78 | `bolt_server.py:792-811` | ID   | For a dict value, `rel_id = hash(f"{src}:{pred}:{tgt}") & 0x7FFFFFFF`. Parallel edges get the same id, and ids change from process to process (`PYTHONHASHSEED`). |

**Count**: 78 touchpoints, T01–T78.

- 7 are already correct (OK: T24, T53, T62, T66, T71, T72, T73). T73's path
  key is also triple-keyed, and T74 covers that.
- 12 are reader classes (T33–T44) that walk the layout and need no change
  unless multiplicity matters. Four of them need it: T38, T42, T43, T44.
- 59 are writers, keys or identity derivations that the spec has to cover.

## R2. Where parallel edges would come from today

Parallel edges exist nowhere today. Every write path either fails on
`u_spo_graph` (T58, T18 when not upserting) or skips the duplicate silently:

- T45 returns False;
- T48 and T49 skip;
- T50, T59 and T61 use NOT EXISTS.

So no existing install holds a parallel edge, and a new column with default 0
meets the constraint on every row that exists now.

## R3. `^KG` layout choice

Three layouts were weighed:

1. **Children under the leaf** (chosen): `^KG("out", g, s, p, o) = w_rep`, plus
   `^KG("out", g, s, p, o, ekey) = w` only while the triple has two or more live
   edges.
   - A single edge is byte-identical to today.
   - Every existing `$Order` and `$Data` reader keeps seeing one neighbour with
     a weight.
   - `Kill ^KG("out", g, s, p, o)` still removes all of them.
   - Readers that need multiplicity check `$Data(...) = 11` and descend one
     level.
2. **Side tree** `^KG("outx", g, s, p, o, ekey)`: also byte-identical when off.
   But it adds a new subtree to the STORE_PLAN, the inventory, the Eraser and
   GraphStores. Every multiplicity-aware reader then walks two trees, and
   deleting the last edge has to touch both.
3. **Always an extra level** `^KG("out", g, s, p, o, ekey)`: this breaks the
   byte-identity requirement. It forces every reader (about 150 `^KG("out"`
   references) to descend, and a single-edge scan pays a longer subscript.
   Rejected.

With layout 1, the representative weight `w_rep` is the weight of the live edge
with the lowest `ekey`. Spec FR-005 gives the rules for moving from one to many
edges and back.

**Spike T004 (2026-09-26, enterprise image, USER@31972)** confirmed layout 1:

- `$Order` over a leaf's children returned `0, 1, 2, 10, "#"`. Integer `ekey`s
  come back in numeric order, and the string counter `"#"` sorts after them, so
  a walk that stops at the first non-numeric subscript never reads it as an
  `ekey`.
- `$Data` of a leaf with children is 11, and `$Order` at the `o` level is not
  affected by the children.
- `Kill ^KG("out", g, s, p, o)` removed the children and `"#"` with the leaf.

## R4. Ledger

- Statement ids already give a relationship an identity that does not depend on
  the triple (T15, T22). Only the `tuple` index and `FindEdge` are triple-keyed.
- `CanonicalRecord` (T23) fingerprints `TupleJson(s, p, o, g)`. Adding an `ekey`
  field **only when it is not 0** keeps every existing record, fingerprint and
  idem key byte-identical.
- Genesis ordering (T20) gains `ekey` as the last sort key. That changes nothing
  when every `ekey` is 0.

## R5. Arno and `^NKG`

- `^NKG(-1, sIdx, -(pIdx+1), oIdx)` is a set with no graph dimension
  (EdgeScan.cls:140-148). `ArnoAccel` (T33) feeds the Rust engine from the same
  set, so Arno BFS and PPR see one edge per triple.
- That is right for reachability. It is a choice for weighted PPR: parallel
  edges could sum their weights, or the representative weight could stand.
- This spec keeps the set semantics (FR-015) and records the choice as an open
  question.

## R6. Why Match4 [8] and Match9 [6] fail

These are not parallel-edge scenarios. `WITH [r1, r2] AS rs` makes a list of
relationships, and `MATCH (first)-[rs*]->(second)` then needs each element to be
a real edge identity.

The stage columns carry only `__edge_{v}_s/_p/_o` (T69), and a list of
relationships has no stable id at all. Carrying `edge_id` as the identity of a
relationship value (spec FR-010) is the same change that multigraph mode needs.
That is why the two scenarios are in this spec.

## R7. Why Match6 [14] and Merge5 [3], [5], [21] fail

- **Match6 [14]**: CREATE writes `(mid)-[:CONNECTED_TO]->(db2)` twice → `-119`
  on `u_spo_graph` (T58).
- **Merge5 [3] and [5]**: the setup CREATEs two `(a)-[:TYPE]->(b)`. The second
  is skipped or fails, so the graph holds one edge, and `count(r)` returns 1
  where 2 is expected ([3]). In [5] the `name: 'r2'` edge never exists, so MERGE
  creates it.
- **Merge5 [21]**: the setup needs two parallel `:T` edges with different
  `name`s.

## R8. Tooling facts relied on

Both were verified in spikes T002 and T003 (2026-09-26, enterprise image
`irishealth:2026.3.0AI.113.0`, USER@31972, scratch tables only).

- **`ALTER TABLE … ADD COLUMN … DEFAULT 0` does not backfill.** On a 500-row
  table every existing row read `ekey IS NULL` after the ADD, and
  `IS_NULLABLE` stayed `YES`. Rows inserted after the ADD that omit `ekey` get 0.
  - `ALTER COLUMN ekey NOT NULL` fails with SQLCODE -305 while a NULL row
    exists. After `UPDATE … SET ekey = 0 WHERE ekey IS NULL` it succeeds; a
    NULL insert is then refused (-108), and an insert that omits `ekey` still
    gets 0.
  - The widened `UNIQUE (s, p, o_id, graph_id, ekey)` adds cleanly next to the
    old key, and the old key then drops. A parallel `ekey = 1` row is accepted;
    a second `ekey = 0` row is refused (-119).
  - `DROP COLUMN ekey` fails (-322) while a constraint names it; the
    constraint has to go first. The upgrade test's downgrade fixture relies on
    this order.
  - So the migration order in plan D2 (add → fill → not null → add key → drop
    old key) is required, not just preferred.
- **`ROW_NUMBER() OVER (PARTITION BY …)` works inside `INSERT … SELECT`.** Both
  of these forms gave correct keys (existing max 4 → new rows 5, 6; a new
  triple → 0, 1, 2):
  - `COALESCE((SELECT MAX(ekey) … correlated), -1) + ROW_NUMBER() OVER
(PARTITION BY s, p, o_id ORDER BY src.%ID)`;
  - the same with a derived-table `LEFT JOIN (SELECT …, MAX(ekey) … GROUP BY
…)`.

  The CTE form `INSERT INTO … WITH m AS (…) SELECT …` is rejected (SQLCODE -1,
  "VALUES expected, WITH found"). FR-008 therefore uses the correlated or
  derived-table form, and the row-at-a-time fallback is not needed.
