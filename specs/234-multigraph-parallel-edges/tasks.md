# Tasks: Opt-in multigraph (parallel edges)

**Input**: [spec.md](spec.md), [plan.md](plan.md), [research.md](research.md)
**Container**: `ivg-iris-enterprise` (port 31972) only. Run IRIS suites through
`/tmp/iris_locked.sh …`. Never run two at once.
**TCK baseline**: `/tmp/tck_r4_base.tsv`. For per-area checks, run
`/tmp/tck_cmp.sh <repo> <areas…>`. It takes the lock itself, so never wrap it in
`iris_locked.sh`.

Format: `- [ ] T### [P?] [USn?] description (file path)`. Tests come first in
every phase and must fail before the code they cover is written. Each
user-story phase ends with an E2E gate.

## Phase 1: Setup and spikes

**Purpose**: settle the two IRIS facts the design depends on (research R8), and
the counter-subscript sort order, before any code.

- [x] T001 Record the pre-change reference state on `ivg-iris-enterprise`. Take
      a `$Query` dump of `^KG` and the ledger head fingerprint from a seeded
      single-edge fixture. Save them to `specs/234-multigraph-parallel-edges/baseline.txt`
      for SC-003. Record `create_edge` and `^KG("out")` scan medians on a
      100k-edge graph for SC-004.
- [x] T002 Spike: on a scratch table copied from `rdf_edges`, check whether
      `ALTER TABLE … ADD COLUMN ekey INTEGER DEFAULT 0` backfills existing rows
      physically. Check `SELECT COUNT(*) WHERE ekey IS NULL`, then whether
      `ALTER COLUMN ekey NOT NULL` succeeds. Record the answer in `research.md`
      R8, and fix the migration step order in `plan.md` D2.
- [x] T003 Spike: check whether `INSERT INTO … SELECT …, COALESCE(m.mx, -1) + ROW_NUMBER() OVER (PARTITION BY c1, c2, c3 ORDER BY %ID) …`
      is accepted and correct on the enterprise image. Test it inside a CTE and
      with a correlated `MAX`. Record the answer in `research.md` R8. If it is
      rejected, mark plan D3's row-at-a-time fallback as the chosen path.
- [x] T004 Spike: in an IRIS terminal, confirm that `$Order` over
      `^KG("out", g, s, p, o, …)` returns integer `ekey`s before the string
      subscript `"#"`. Confirm that `$Data` of a leaf with children is 11. Also
      check that `Kill ^KG("out", g, s, p, o)` removes the children. Record the
      results in `research.md` R3.

## Phase 2: Foundational (mode, column, EdgeScan, BuildKG)

**Purpose**: the storage model that every user story sits on. No user-visible
behaviour changes while the mode is off.

### Tests first

- [x] T005 [P] Unit test: `RDF_EDGES_DDL` declares
      `ekey INTEGER NOT NULL DEFAULT 0` and `u_spo_graph_ekey`, and no longer
      declares `u_spo_graph` (`tests/unit/test_234_multigraph_sql.py`).
- [x] T006 [P] E2E test for the mode API. `is_multigraph` is False by default.
      `set_multigraph(g, True)` sets it. Disabling with parallel edges present
      raises `parallel_edges_present` with a count. `EraseGraph` clears the
      mode (`tests/e2e/test_234_multigraph_storage_e2e.py`).
- [x] T007 [P] E2E test for the `WriteAdjacencyKeyed`/`DeleteAdjacencyKeyed`
      transitions. It covers one→two→three→two→one edges, deleting the lowest
      `ekey`, and deleting the last edge. After each step, assert: - the leaf value, the children and the `"#"` counter; - `deg`/`degp` equal to the live edge count; - `^NKG` present iff at least one edge is live; - after going back to one edge, a `$Query` dump byte-identical to a
      never-parallel edge.

      File: `tests/e2e/test_234_multigraph_storage_e2e.py`.

- [x] T008 [P] E2E test: after 200 random keyed creates and deletes over 20
      triples, `BuildKG` gives a `^KG` dump identical to the incrementally
      written one (FR-006) (`tests/e2e/test_234_multigraph_storage_e2e.py`).
- [x] T009 [P] E2E test for the upgrade. Start from a pre-234 schema: drop
      `ekey`, restore `u_spo_graph`, seed data and run the 213 ledger. Then run
      the upgrade and check: - every row has `ekey = 0`; - the constraint swap happened; - the `^KG` dump and ledger head fingerprint equal the pre-upgrade ones
      (SC-003); - a second run reports `already at 234`.

      File: `tests/e2e/test_234_multigraph_storage_e2e.py`.

### Implementation

- [x] T010 Add `ekey` and `u_spo_graph_ekey` to `RDF_EDGES_DDL`
      (`iris_vector_graph/schema.py:31-41`).
- [x] T011 Add a migration `ensure_ekey` in the order fixed by T002. Wire it
      next to `update_spo_unique_constraint` in the status block
      (`iris_vector_graph/schema.py:683`, `:753`).
- [x] T012 [P] Create `Graph.KG.GraphMode` with `IsMulti`, `Set` and
      `ParallelCount` (`iris_src/src/Graph/KG/GraphMode.cls`).
- [x] T013 [P] Add `set_multigraph`/`is_multigraph`, with the per-connection
      cache, to `iris_vector_graph/_engine/admin.py`. Add facade entries in
      `iris_vector_graph/engine.py` (`_GraphSubEngine`, `:143`).
- [x] T014 Register `^IVG.GraphMode`: - `STORE_PLAN` (`iris_vector_graph/_engine/snapshot.py:46`); - an inventory entry (`iris_src/src/Graph/KG/GraphStores.cls`); - kill per graph in `EraseGraph` and whole in `EraseAll`
      (`iris_src/src/Graph/KG/Eraser.cls:47`, `:294`).
- [x] T015 Implement `WriteAdjacencyKeyed` and `DeleteAdjacencyKeyed` per plan
      D2, gating `GraphIndex.DeleteIndex` on the last edge. Leave
      `WriteAdjacency` and `DeleteAdjacency` unchanged
      (`iris_src/src/Graph/KG/EdgeScan.cls:133`, `:198`).
- [x] T016 Make `BuildKG` read `ekey`, group by triple and write the leaf,
      children, counter and edge-counted `deg`/`degp`
      (`iris_src/src/Graph/KG/TraversalBuild.cls:26`).
- [x] T017 Compile via TCP deploy (`scripts/enterprise-container.sh`).

**Gate**: T005–T009 pass. The full unit suite keeps its baseline (1F/8470P
known). `tests/e2e/test_fhir_demo_e2e.py` and the 231/232 E2E tests pass.

## Phase 3: US3, relationship identity by `edge_id` (P1, mode-independent)

**Note**: this may already be done by the 203 edge_id merge; verify. A separate
agent is working on Match4 [8] and Match9 [6] on `203-tck-remaining-scenarios`.
Start with T018. Skip any task that merged code already satisfies, and mark it
`[X] (203)`.

### Tests first

- [ ] T018 [US3] Verify what the 203 `edge_id` merge landed. Grep for
      `__edge_{…}_id` in `iris_vector_graph/cypher/translator.py` and run its
      tests in `tests/unit/test_203_tck_regressions.py`. Write the covered and
      missing touchpoints (T60, T63–T65, T67–T70, T73–T74) into this file.
- [ ] T019 [P] [US3] Unit tests (may already be done by the 203 merge; verify): - WITH promotion carries `__edge_{v}_id`; - `r1 = r2` and isomorphism compare `edge_id`; - GROUP BY on an edge variable uses `edge_id`; - relationship lists hold `edge_id`.

      File: `tests/unit/test_234_multigraph_sql.py`.

- [ ] T020 [P] [US3] Unit test: `CY_VLP_PATHS`, `CY_VLP_HAS` and
      `CY_VLP_DISJOINT` build path key `k` from `edge_id`
      (`tests/unit/test_234_multigraph_sql.py`).
- [ ] T021 [P] [US3] Unit tests for Bolt: - a relationship with `edge_id` packs that id at all three sites; - with no `edge_id`, the fallback id is stable across `PYTHONHASHSEED`
      values; - the path-rel site is covered.

      File: `tests/unit/test_234_bolt_rel_id.py`.

- [ ] T022 [US3] E2E test for Match4 [8] and Match9 [6] with the mode off, and
      for US3 acceptance scenario 3 (two parallel equal-property edges give
      `count(r) = 2` after WITH) with the mode on
      (`tests/e2e/test_234_identity_e2e.py`).

### Implementation

- [ ] T023 [US3] Carry `__edge_{v}_id` through created relationships, stage
      re-matches and WITH promotion (may already be done by the 203 merge;
      verify). Locations: `iris_vector_graph/cypher/translator.py:5197`,
      `:5258`, `:8638`, `:9976`, `:18762-18766`.
- [ ] T024 [US3] Make `_edge_ident_cols`, `_rel_identity_comparison` and the
      GROUP BY for edge variables compare `edge_id` (may already be done by the
      203 merge; verify). Locations:
      `iris_vector_graph/cypher/translator.py:7671`, `:9937`, `:18622`.
- [ ] T025 [US3] Build the path key `k` in `CY_VLP_*` from `edge_id`
      (`iris_vector_graph/schema.py`, `CY_VLP_PATHS`/`CY_VLP_HAS`/`CY_VLP_DISJOINT`).
      Redeploy with
      `/tmp/iris_locked.sh env PYTHONPATH=$PWD .venv/bin/python /tmp/deploy_udf.py`.
      STR_SPLIT -300 is pre-existing.
- [ ] T026 [US3] Use `edge_id` for relationship ids at `:743`, `:789` and
      `:808`, with a sha256 fallback (`iris_vector_graph/bolt_server.py`).

**Gate**: T019–T022 pass. The direction-symmetry integration test is green.
`/tmp/tck_cmp.sh $PWD match with return returnOrderBy withOrderBy` shows 0 REG.

## Phase 4: US1, parallel edges in a multigraph (P1)

### Tests first

- [x] T027 [P] [US1] Unit tests for multigraph mode: - CREATE with VALUES emits an `ekey` subselect and no triple guard; - CREATE after MATCH drops `DISTINCT` and the NOT EXISTS guard; - with the mode off, both are byte-identical to golden SQL captured at
      `972ae7c`.

      File: `tests/unit/test_234_multigraph_sql.py`.

      Done in `tests/unit/test_234_multigraph_cypher_sql.py`. The golden file,
      `tests/unit/golden/234_mode_off_sql.json`, was captured at `5bb7437`,
      the commit before the translator change.

- [ ] T028 [P] [US1] Integration test at the SQL layer: two parallel INSERTs
      get `ekey` 0 and 1; a racing duplicate `ekey` gets `-119` and succeeds on
      retry (`tests/integration/test_234_multigraph_sql.py`).
- [ ] T029 [US1] E2E test for US1 acceptance scenarios 1–5. After `sync()`,
      check `count(r)`, distinct `edge_id`s and `ekey` 0/1. Delete one edge and
      check that one remains, with `deg`/`degp` equal to 1. Check the
      neighbour appears once while `GraphVerify` and `Subgraph` count 2. Check
      the Bolt ids equal the `edge_id`s. Also cover Match6 [14]'s same-triple
      double CREATE (`tests/e2e/test_234_multigraph_cypher_e2e.py`).
      Partly done: `count(r)`, distinct `edge_id`s, `ekey` 0/1, CREATE after
      MATCH, the Match6 [14] shape and the mode-off check pass. Open: delete
      one of two, `deg`/`degp`, `GraphVerify`/`Subgraph` (needs T036) and the
      Bolt ids (needs T026; `id(r)` is SQLCODE -29 in both modes today).
- [ ] T030 [P] [US1] E2E test: - `create_edge` returns True twice in multigraph mode, and False on the
      second call with the mode off; - `create_edge_returning_id()` returns distinct `edge_id`s, and `None` for
      a single-edge duplicate; - `delete_edge(edge_id=)` removes exactly one edge.

      File: `tests/e2e/test_234_multigraph_cypher_e2e.py`.

- [ ] T031 [P] [US1] E2E test for FR-015: on a multigraph with parallel edges,
      PPR and BFS results equal those of the same graph collapsed to one edge
      per triple (`tests/e2e/test_234_multigraph_cypher_e2e.py`).
- [ ] T032 [P] [US1] E2E test for SC-005: after 1,000 random creates and
      deletes over 50 triples, `GraphVerify` reports zero drift
      (`tests/e2e/test_234_multigraph_storage_e2e.py`).

### Implementation

- [ ] T033 [US1] Add the keyed create path. It covers `_create_edge_keyed`,
      `create_edge` routing, `create_edge_returning_id()`, the `delete_edge`
      `edge_id=` parameter, and a multigraph `set_edge_weight` that does not
      touch the counters (`iris_vector_graph/_engine/nodes_edges.py:821`,
      `:892`, `:922`). Add the facade in `iris_vector_graph/engine.py`.
- [x] T034 [US1] Translator CREATE with VALUES in multigraph mode, with the
      `ekey` subselect (`iris_vector_graph/cypher/translator.py:5062-5067`).
- [x] T035 [US1] Translator CREATE after MATCH in multigraph mode, using the
      `ROW_NUMBER` path or the row-at-a-time fallback chosen in T003
      (`iris_vector_graph/cypher/translator.py:5113-5119`).
- [ ] T036 [US1] Make the edge-counting readers descend into children: - `GraphVerify.VerifyGraph` (`iris_src/src/Graph/KG/GraphVerify.cls:35`); - `iris_src/src/Graph/KG/Subgraph.cls`; - the path-returning parts of `iris_src/src/Graph/KG/TraversalBFS.cls`,
      `TraversalPaths.cls` and `TraversalKHop.cls`.
- [x] T037 [US1] Add `IVG_TCK_MULTIGRAPH=1` support: `set_multigraph` in
      `before_all`, and again after each `_flush_all_tck_data`
      (`tests/tck/environment.py:85`).
      Done as on by default (SC-001): the harness turns the mode on for the
      default graph in `before_all` and off again in `after_all`;
      `IVG_TCK_MULTIGRAPH=0` runs the suite with the mode off.

**Gate**: T027–T032 pass. With `IVG_TCK_MULTIGRAPH=1`, Match6 [14] passes.
`/tmp/tck_cmp.sh $PWD match create delete set` shows 0 REG with the variable
set and with it unset.

## Phase 5: US2, MERGE over parallel edges (P1)

### Tests first

- [x] T038 [P] [US2] Unit test: with the mode off, MERGE relationship SQL is
      byte-identical to golden SQL captured at `972ae7c` for the Merge5 and
      Merge1 patterns. With the mode on, the create guard uses the full fit
      predicate, including inline properties
      (`tests/unit/test_234_multigraph_sql.py`).
- [x] T039 [US2] E2E test for US2 acceptance scenarios 1–4: Merge5 [3], [5] and
      [21] shapes in multigraph mode, plus the mode-off check
      (`tests/e2e/test_234_multigraph_cypher_e2e.py`).

### Implementation

- [x] T040 [US2] Multigraph MERGE: - match every fitting edge; - create one edge with a fresh `ekey` only when none fits; - re-evaluate per input row.

      Locations: `translate_merge_clause`,
      `iris_vector_graph/cypher/translator.py:5499`; the NOT EXISTS rewrite at
      `:5773-5790`.

      `DELETE t MERGE … RETURN` (Merge5 [21]) moves the DELETE behind the
      result as an `__after_result__` statement, bounded by the edge_id
      high-water mark; `execute_transaction` runs it after reading the result.

**Gate**: T038–T039 pass. With `IVG_TCK_MULTIGRAPH=1`, Merge5 [3], [5] and
[21] pass. `/tmp/tck_cmp.sh $PWD merge` shows 0 REG with the variable set and
with it unset.

## Phase 6: US4, upgrade, restore, ledger (P2)

### Tests first

- [ ] T041 [P] [US4] E2E test for the ledger: - a single-edge `create_rel` gives byte-identical records and fingerprints
      compared with `baseline.txt`; - a multigraph `create_rel` always creates, and records `ekey` only when
      it is not 0; - `upsert` over two fitting edges fails with `ambiguous_tuple`; - a tuple ref with `ekey` resolves; - genesis orders by `ekey`.

      File: `tests/e2e/test_234_ledger_e2e.py`.

- [ ] T042 [P] [US4] E2E test: restoring a pre-234 NDJSON archive, with no
      `ekey`, lands every edge at 0. Turning the mode off with parallel edges
      present is refused (`tests/e2e/test_234_multigraph_storage_e2e.py`).
- [ ] T043 [P] [US4] E2E test: the 4.0.0 rescue and re-key path carries `ekey`
      (`tests/e2e/test_234_multigraph_storage_e2e.py`).

### Implementation

- [ ] T044 [US4] Ledger changes: - `TupleJson` and `CanonicalRecord` take an optional `ekey`; - `FindEdge` gains `ekey` and returns `ambiguous_tuple`; - `AllocStmt` writes the 6-subscript tuple; - `OpCreateRel` always creates in multigraph mode.

      Files: `iris_src/src/Graph/KG/LedgerApply.cls:107`, `:139`, `:155`,
      `:443`; `iris_src/src/Graph/KG/Ledger.cls:539`.

- [ ] T045 [US4] Genesis orders by `ekey`
      (`iris_src/src/Graph/KG/LedgerGenesis.cls:38`). The ledger scrub also
      kills 6-subscript tuples (`iris_src/src/Graph/KG/Eraser.cls:502`). Update
      the inventory text (`iris_src/src/Graph/KG/GraphStores.cls:91`).
- [ ] T046 [US4] `_tuple_wire` and `_rel_ref` accept `ekey`
      (`iris_vector_graph/ledger/changeset.py:70-91`).
- [ ] T047 [US4] NDJSON restore defaults a missing `ekey` to 0
      (`iris_vector_graph/_engine/snapshot.py`, restore near `:880-905`). The
      rescue carries `ekey` (`iris_vector_graph/migrations/kg_node_stores.py`).

**Gate**: T041–T043 pass, and the 213 ledger E2E suite passes unchanged.

## Phase 7: Polish and final gate

- [ ] T048 [P] Document the mode, `ekey`, `create_edge_returning_id()` and the
      non-goals. Put this in `docs/` next to the named-graph docs, and add
      `CHANGELOG.md` entries. Run `markdownlint-cli2 --fix` and
      `prettier --write` on each edited `.md`.
- [ ] T049 Measure SC-004 against the T001 numbers: mode off, mode on with no
      parallel edges, and 10% of triples doubled. Record the results in
      `specs/234-multigraph-parallel-edges/baseline.txt`.
- [x] T050 Remove the six SC-001 scenarios from `tests/tck/wip.txt` if they are
      listed there.
- [ ] T051 Final gate, part 1: full TCK with the mode off,
      `/tmp/iris_locked.sh /tmp/tck_all.sh /tmp/tck_234_off`, then
      `python3 /tmp/tck_sum.py /tmp/tck_234_off /tmp/tck_234_off.tsv`. Zero REG
      vs `/tmp/tck_r4_base.tsv`.
- [x] T052 Final gate, part 2: full TCK with `IVG_TCK_MULTIGRAPH=1`, into
      `/tmp/tck_234_on`. Zero REG vs `/tmp/tck_r4_base.tsv`. All six SC-001
      scenarios pass.
- [ ] T053 Full unit and E2E suites keep their pass rates at `972ae7c`
      (FR-016).

Status 2026-09-26: T050: `tests/tck/wip.txt` is empty. T052: 3896/3896
strict and typed at `937d4b7` via `scripts/tck/run_sharded.sh`. T051: mode
off measured 3889/3896 at `937d4b7`; the 7 failures all need parallel edges.
Earlier-phase boxes were not ticked while the work landed; the tests exist
(`tests/unit/test_234_*`, `tests/e2e/test_234_*`), but no one has audited
them against each task. US4 (ledger, restore, rescue) has no tests and is
not done. `create_edge_returning_id()` does not exist.

## Dependencies

- Phase 1 comes before everything. T002 blocks T011, T003 blocks T035, and
  T004 blocks T015.
- Phase 2 blocks Phases 4, 5 and 6.
- Phase 3 (US3) depends only on Phase 1. It can run in parallel with Phase 2,
  and T018 decides its size.
- Phase 5 (US2) depends on Phase 4 (keyed CREATE and `ekey` allocation).
- Phase 6 (US4) depends on Phase 2. It can run in parallel with Phases 4 and 5.
- Phase 7 comes after all others.

## Parallel examples

- Phase 2 tests: T005, T006, T007, T008 and T009 are in different test classes
  and have no shared fixture state beyond a per-test graph name.
- Phase 2 code: T012 (`GraphMode.cls`) and T013 (`admin.py`) can be written
  together. T015 and T016 are both ObjectScript and should compile together.
- Phases 3 and 6 can proceed side by side once Phase 2 lands. They touch
  disjoint files: the translator and Bolt, against the ledger classes.

## Task counts

| Phase                 | Tasks  | Tests          |
| --------------------- | ------ | -------------- |
| 1 Setup and spikes    | 4      | –              |
| 2 Foundational        | 13     | 5              |
| 3 US3 identity        | 9      | 5 (incl. T018) |
| 4 US1 parallel edges  | 11     | 6              |
| 5 US2 MERGE           | 3      | 2              |
| 6 US4 upgrade, ledger | 7      | 3              |
| 7 Polish, final gate  | 6      | –              |
| **Total**             | **53** | **21**         |
