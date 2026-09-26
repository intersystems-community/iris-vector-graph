# Implementation Plan: Opt-in multigraph (parallel edges)

**Branch**: `234-multigraph-parallel-edges` (work lands on
`203-tck-remaining-scenarios`) · **Date**: 2026-09-26 · **Spec**:
[spec.md](spec.md) · **Research**: [research.md](research.md)

## Summary

Add parallel edges as a per-graph opt-in. A graph turns it on with
`^IVG.GraphMode(gKey) = "multi"`. The storage change is a new
`rdf_edges.ekey` column, and the unique key becomes
`(s, p, o_id, graph_id, ekey)`.

In `^KG`, a triple keeps its leaf. It gains `ekey` children only while two or
more of its edges are live. `deg`/`degp` count edges. `^NKG`, Arno, PPR and
edge embeddings keep one entry per triple.

`edge_id` becomes the relationship identity everywhere, in both modes: stage
columns, lists, comparisons, the var-length path key and Bolt. `create_edge` is
unchanged. A new `create_edge_returning_id()` returns the new `edge_id`.

With the mode off, the SQL, `^KG` and ledger bytes are identical to today.

## Technical Context

- **Language/Version**: Python 3.10+ (`.venv` 3.11); ObjectScript on IRIS
  2024.1+ (test image `irishealth:2026.3.0AI.113.0`).
- **Primary Dependencies**: `intersystems-irispython` (DB-API + Native API). No
  new runtime dependency. Bolt's fallback id uses `hashlib.sha256`.
- **Storage**:
  - Changed: `Graph_KG.rdf_edges` gains `ekey`, and `u_spo_graph` becomes
    `u_spo_graph_ekey`.
  - `^KG("out"/"in")` gains `ekey` children (FR-004).
  - New global `^IVG.GraphMode`.
  - `^IVG.Ledger("tuple", …)` gains a 6th subscript for parallel edges only.
- **Testing**:
  - `pytest` unit tests (`tests/unit/`), with no IRIS for translator SQL shape.
  - E2E and integration tests against `ivg-iris-enterprise` (port 31972).
  - TCK via behave, run with `/tmp/tck_cmp.sh` per area and
    `/tmp/iris_locked.sh /tmp/tck_all.sh <dir>` for the full run.
- **Target Platform**: IRIS enterprise image. The Community container is not
  used.
- **Performance Goals**: SC-004. With the mode off, and with it on but no
  parallel edges, the `^KG("out")` scan and `create_edge` stay within 3% of
  `92b078e`.
- **Constraints**:
  - Byte-identical `^KG` and ledger fingerprints for single-edge data (SC-003).
  - Zero TCK regressions against `/tmp/tck_r4_base.tsv`.
- **Scale/Scope**:
  - 78 touchpoints (research R1). 59 need a change, 12 are readers, and 7 are
    already correct.
  - Six target TCK scenarios (SC-001).

## Constitution Check

| Principle                    | Status | How                                                                                                                                                                                   |
| ---------------------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| I. Library-first             | Pass   | Everything goes in `iris_vector_graph/` and `iris_src/src/Graph/KG/`.                                                                                                                 |
| II. Compatibility-first      | Pass   | Off by default. `create_edge`, `WriteAdjacency` and `DeleteAdjacency` keep their signatures and behaviour. The id-returning path is a new method (Q6 → c).                            |
| III. Test-first              | Pass   | Every phase in [tasks.md](tasks.md) opens with failing tests.                                                                                                                         |
| IV. Live IRIS E2E            | Pass   | Each user story has an E2E gate on `ivg-iris-enterprise:31972`. Integration tests go in `tests/integration/`, because SQL translation changes.                                        |
| V. Simplicity                | Pass   | Layout 1 (research R3) adds no new subtree. Readers that don't count edges are untouched.                                                                                             |
| VI. Grounding                | Pass   | Paths below were re-read at `972ae7c`. Research line numbers from `92b078e` have drifted; see "Drift from research".                                                                  |
| VII. Cypher conformance gate | Pass   | The translator changes, so the direction-symmetry integration test (`tests/integration/test_cypher_advanced.py::test_integration_direction_symmetry_optional_match`) must stay green. |
| VIII. Spec hygiene           | Pass   | No `GraphStore` protocol method is added, so Gate 4 is unaffected. Gate 2 covers the `EdgeScan`, `TraversalBuild` and `Ledger*` compiles.                                             |

## Design

### D1. Mode (FR-001, FR-002)

- **Storage**: `^IVG.GraphMode(gKey) = "multi"`, where `gKey` comes from
  `Graph.KG.GraphKey.ForIndex` (`GraphKey.cls:51`).
- **Writers**: a new ObjectScript helper `Graph.KG.GraphMode` with
  `IsMulti(gKey)`, `Set(gKey, on)` and `ParallelCount(gKey)`.
  - `ParallelCount` is a SQL `GROUP BY s, p, o_id HAVING COUNT(*) > 1` scoped to
    the graph.
  - `Set(gKey, 0)` refuses with `parallel_edges_present` when that count is
    above 0.
- **Python API**: `set_multigraph(graph, enabled)` and `is_multigraph(graph)` on
  the engine (`_engine/admin.py`), plus facade entries in `engine.py`
  (`_GraphSubEngine`).
- **Inventory**: an entry in `STORE_PLAN` (`_engine/snapshot.py:46`) and one in
  `GraphStores.cls`. `Eraser.EraseGraph` (`Eraser.cls:47`) kills the entry, and
  `EraseAll` (`:294`) kills the whole global.
- **Python-side cache**: a per-connection dictionary cleared by
  `set_multigraph`. The translator gets the mode through `context`, like
  `graph_context`.

### D2. Storage (FR-003 to FR-006)

- **DDL**: `RDF_EDGES_DDL` (`schema.py:31-41`) gains
  `ekey INTEGER NOT NULL DEFAULT 0` and `u_spo_graph_ekey`.
- **Migration**: a migration `ensure_ekey` goes next to
  `update_spo_unique_constraint` (`schema.py:753`) and is called from the same
  status block (`schema.py:683`). The steps are:
  1. `ADD COLUMN ekey INTEGER DEFAULT 0`;
  2. `UPDATE … SET ekey = 0 WHERE ekey IS NULL`;
  3. `ALTER COLUMN ekey NOT NULL`;
  4. add `u_spo_graph_ekey`;
  5. drop `u_spo_graph`.

  The order means there is always a unique key in place. The step is idempotent,
  and it reports `already at 234`.

- **EdgeScan**: `WriteAdjacencyKeyed` and `DeleteAdjacencyKeyed` go in
  `EdgeScan.cls`, next to `WriteAdjacency` (`:133`) and `DeleteAdjacency`
  (`:198`).
  - They keep the live-edge count in a counter subscript under the leaf:
    `^KG("out", g, s, p, o, "#")`. It is killed when the count reaches 1, so a
    single edge's bytes stay unchanged.
  - One edge → two: write both children, then the counter.
  - Two → one: kill the children and the counter, and set the leaf to the
    survivor's weight.
  - Deleting the lowest `ekey`: the leaf takes the new lowest live `ekey`'s
    weight.
  - `deg`/`degp` change by ±1 per edge.
  - `^NKG` is written on the first edge. `GraphIndex.DeleteIndex` runs only on
    the last one.
  - The counter's subscript name is settled in spike T004. `$Order` must never
    return it as an `ekey`, so a string subscript sorts after the integer
    `ekey`s.
- **BuildKG** (`TraversalBuild.cls:26`): it reads `ekey` and groups rows by
  triple, then writes the leaf, children and counters with the same rules.
  - This is the only `^KG` writer for Cypher CREATE/DELETE. The translator
    writes SQL, and `^KG` follows through `sync()` → `_sync_kg` → `BuildKG`
    (`_engine/schema.py:2190`).
  - So FR-006 is what makes US1 acceptance scenarios 2 and 3 hold for edges that
    Cypher writes.
- **Readers that must count edges** (T38, T42–T44): `GraphVerify.VerifyGraph`
  (`GraphVerify.cls:35`) compares `rdf_edges` row counts with leaves plus
  children.
  - `Subgraph` and the path-returning BFS/paths descend one level when
    `$Data(...) = 11`.
  - Node-set readers are unchanged.

### D3. Writers (FR-007 to FR-009)

- **`create_edge`** (`_engine/nodes_edges.py:821`): unchanged in single-edge
  mode. In multigraph mode it goes to `_create_edge_keyed`:
  1. `INSERT … SELECT COALESCE(MAX(ekey) + 1, 0)` over the triple;
  2. on `-119`, retry once;
  3. call `WriteAdjacencyKeyed`;
  4. return True.
- **`create_edge_returning_id()`**: shares `_create_edge_keyed`, which works in
  either mode, and returns `edge_id`. In single-edge mode it returns `None` for
  a duplicate.
- **`delete_edge`** (`:922`): unchanged in single-edge mode. In multigraph mode
  it gains an optional `edge_id=` parameter. With no `edge_id`, it deletes every
  parallel edge of the triple, which is today's meaning.
- **`set_edge_weight`** (`:892`): today it calls `WriteAdjacency`, which
  increments `deg` again (T46). In multigraph mode it must rewrite the weight
  only. The single-edge path is left as it is (out of scope, noted).
- **Translator CREATE with VALUES** (`translator.py:5062-5067`): in multigraph
  mode the INSERT carries
  `ekey = (SELECT COALESCE(MAX(ekey) + 1, 0) FROM rdf_edges WHERE s = ? AND p = ? AND o_id = ? AND graph_id = ?)`.
  When one CREATE writes a triple twice (Match6 [14]), each INSERT statement
  runs in order, so each sees the MAX from the one before.
- **Translator CREATE after MATCH** (`translator.py:5113-5119`): in multigraph
  mode, drop the `DISTINCT` and the NOT EXISTS guard, and add
  `MAX(ekey) + ROW_NUMBER() OVER (PARTITION BY c1, c2, c3)`. This depends on
  spike T003. If the spike fails, fall back to row-at-a-time inserts, as the
  `_create_node_per_row` path from 38c3e8d does.
- **Translator MERGE** (`translate_merge_clause`, `translator.py:5499`; the
  NOT EXISTS rewrite at `:5773-5790`):
  - In multigraph mode, the match side returns every fitting edge.
  - The create side is guarded by the same fit predicate, including inline
    properties, instead of the bare triple, and allocates `ekey` as CREATE does.
  - With the mode off, the SQL is byte-identical; a golden-SQL unit test guards
    this.

### D4. Identity (FR-010 to FR-012), in both modes

> This may already be done by the 203 edge_id merge; verify. A separate agent is
> working on relationship identity via `edge_id` for Match4 [8] and Match9 [6]
> on `203-tck-remaining-scenarios`. The US3 translator tasks start by checking
> what that merge landed, and only then write code.

- **Stage columns**:
  - add `__edge_{v}_id` next to `__edge_{v}_s/_p/_o`: `translator.py:5258`
    (created relationships), `:8638` (stage re-match), `:9976`, `:10175`
    (comment), `:18762-18766` (WITH promotion);
  - `_register_created_relationship` (`:5197`) binds it.
- **Comparisons**: `_edge_ident_cols` (`:7671`) and `_rel_identity_comparison`
  (`:9937`) compare `edge_id`. WITH GROUP BY on an edge variable groups on
  `edge_id`.
- **Var-length path key**: `CY_VLP_PATHS`, `CY_VLP_HAS` and `CY_VLP_DISJOINT`
  (in `schema.py` near the research's `:1590-1596`) build `k` from `edge_id`.
  The UDFs must be redeployed with `/tmp/deploy_udf.py`.
- **Bolt** (`bolt_server.py`): all three relationship `hash()` sites use
  `edge_id` when the value carries it.
  - The sites are `:743` (path relationships, missing from research T76–T78),
    `:789` and `:808`.
  - The fallback is a sha256-derived 31-bit int.
  - The node-id `hash()` at `:345` is outside the scope of FR-012. It is noted,
    not changed.

### D5. Ledger (FR-013)

- **`TupleJson`** (`LedgerApply.cls:107`) and `CanonicalRecord`
  (`Ledger.cls:539`) gain an optional `ekey` argument, which is emitted only
  when it is not 0.
- **`FindEdge`** (`LedgerApply.cls:139`) gains `ekey` and returns
  `ambiguous_tuple` when there is no `ekey` and more than one row matches.
- **`AllocStmt`** (`:155`) indexes `("tuple", s, p, o, gk)` for `ekey = 0` and
  `("tuple", s, p, o, gk, ekey)` otherwise.
- **`OpCreateRel`** (`:443`): in multigraph mode it always creates.
- **Genesis** (`LedgerGenesis.cls:38`) sorts by `…, graph_id, ekey`.
- **Other**:
  - `Eraser.EraseLedgerFor` (`Eraser.cls:502`) kills 6-subscript tuples as
    well.
  - The `GraphStores.cls:91` entry text becomes "subscript 5, or 6 for parallel
    edges".
  - `ledger/changeset.py` `_tuple_wire` and `_rel_ref` accept `ekey`.

### D6. Migration, restore and scope (FR-014 to FR-016)

- **4.0.0 rescue and re-key**: `migrations/kg_node_stores.py` (the
  `rdf_edges__ivg400rescue` path) carries `ekey` when the column exists.
- **Restore**: NDJSON restore (`_engine/snapshot.py` near `:880-905`) defaults a
  missing `ekey` to 0.
- **`import_rdf`, FHIR sync and the bulk loader**: unchanged. They always write
  `ekey = 0`, so they are single-edge in every mode (non-goal).
- **Arno and `^NKG`**: unchanged. A test asserts that PPR and BFS on a
  multigraph equal PPR and BFS on its collapsed form (FR-015).

### D7. TCK harness

- **Namespace**: the TCK runs in namespace `USER`, default graph
  (`tests/tck/environment.py:85` `before_all`).
- **Opt-in**: a new env var `IVG_TCK_MULTIGRAPH=1` makes `before_all` call
  `set_multigraph(None, True)`.
- **After a flush**: `_flush_all_tck_data` can erase the mode (FR-001 makes
  `EraseAll` kill it), so the mode is set again after every flush.
- **Per-scenario teardown**: it deletes by label and leaves the mode alone.
- **SC-002**: the full TCK is run twice, once with the env var set and once
  without it.

## Drift from research (checked at `972ae7c`)

- **`_engine/nodes_edges.py`**: `create_edge` is at `:821`, not `:852`.
  `set_edge_weight` is at `:892`, and `delete_edge` at `:922`, not `:960`.
- **`translator.py`**: 38c3e8d shifted lines by about 140.
  - T58 is now at `:5062`.
  - T59 is at `:5113-5119`.
  - T60 is at `:5197`/`:5258`.
  - T61 is at `:5499`/`:5773`.
  - T64 is at `:7671`.
  - T67 is at `:9937`.
  - T68 is at `:10175`.
  - T69 is at `:18762`.
- **`bolt_server.py`**: there is a fourth site, `:743`, where the relationship
  id in a path is a `hash()` of the triple. Research lists only `:789` and
  `:808`.
- **`BuildKG`**: callers invoke it as `Graph.KG.Traversal:BuildKG`, while the
  method is defined in `TraversalBuild.cls`. Tasks edit `TraversalBuild.cls`.
- **Baseline**: FR-016 names "best-ever 3724/3897". The current comparison
  baseline is `/tmp/tck_r4_base.tsv` at 3835/3897, and the best full run is
  3855 (r5). Gates here use `tck_r4_base.tsv`.

## Project Structure

```text
specs/234-multigraph-parallel-edges/
├── spec.md
├── research.md
├── plan.md          # this file
└── tasks.md

iris_src/src/Graph/KG/
├── GraphMode.cls        # new: IsMulti / Set / ParallelCount
├── EdgeScan.cls         # WriteAdjacencyKeyed / DeleteAdjacencyKeyed
├── TraversalBuild.cls   # BuildKG multigraph layout
├── GraphVerify.cls      # edge-count parity
├── Subgraph.cls, TraversalBFS.cls, TraversalPaths.cls, TraversalKHop.cls
├── LedgerApply.cls, Ledger.cls, LedgerGenesis.cls
├── Eraser.cls, GraphStores.cls

iris_vector_graph/
├── schema.py                    # DDL, ensure_ekey, CY_VLP_* key
├── _engine/nodes_edges.py       # create_edge keyed path, create_edge_returning_id, delete_edge(edge_id=)
├── _engine/admin.py             # set_multigraph / is_multigraph
├── _engine/snapshot.py          # STORE_PLAN, restore default
├── engine.py                    # facade
├── cypher/translator.py         # CREATE / MERGE / identity
├── bolt_server.py               # relationship ids
├── ledger/changeset.py          # ekey in tuple refs
└── migrations/kg_node_stores.py # rescue carries ekey

tests/
├── unit/test_234_multigraph_sql.py         # translator SQL shape, mode on/off golden
├── unit/test_234_bolt_rel_id.py
├── e2e/test_234_multigraph_storage_e2e.py  # EdgeScan transitions, BuildKG parity, upgrade
├── e2e/test_234_multigraph_cypher_e2e.py   # US1 / US2
├── e2e/test_234_identity_e2e.py            # US3
├── e2e/test_234_ledger_e2e.py              # US4 ledger
└── integration/test_234_multigraph_sql.py  # SQL-layer checks (Principle IV)
```

## Complexity Tracking

| Choice                            | Why                                                                                                      | Simpler option rejected                                                                                   |
| --------------------------------- | -------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| Counter subscript under the leaf  | O(1) one↔many transitions and `ParallelCount` without scanning children.                                 | Counting children with `$Order` on every write: O(k) per write, and it races with the concurrent delete.  |
| Mode lookup cached per connection | The translator needs the mode at SQL-generation time, and one global read per query costs a trip.        | Always emitting the multigraph SQL: it breaks mode-off byte-identity (US2 acceptance scenario 4, FR-009). |
| `edge_id` identity in both modes  | One code path. Single-edge answers are provably unchanged, because triple equality ⇔ `edge_id` equality. | Mode-conditional identity: two code paths, and a second set of identity bugs.                             |
