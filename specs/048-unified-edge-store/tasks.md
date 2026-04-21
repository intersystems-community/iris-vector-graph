# Tasks: Unified Edge Store (spec 048)

---

## PR-A — Phase 1: Setup & Verification

- [x] T001 Confirm `iris_src/src/Graph/KG/EdgeScan.cls` does not exist; rdf_edges JOIN at translator.py:1299; baseline latency noted
- [x] T002 Create `tests/unit/test_unified_edge_store.py` with standard scaffold
- [x] T002b Baseline: 492 passed, 7 pre-existing failures; new file collects 0 tests

---

## PR-A — Phase 2: Foundational (EdgeScan ObjectScript)

**Goal**: `Graph.KG.EdgeScan.MatchEdges` compiled and callable. No Python wiring yet.

**Independent test criterion**: `##class(Graph.KG.EdgeScan).MatchEdges("A", "", 0)` returns JSON array of all edges from A; `MatchEdges("", "TREATS", 0)` returns all TREATS edges across the whole graph.

- [x] T003 Create `iris_src/src/Graph/KG/EdgeScan.cls` — `Class Graph.KG.EdgeScan Extends %RegisteredObject` with three ClassMethods: `MatchEdges(sourceId As %String, predicate As %String, shard As %Integer = 0) As %String [SqlProc]`, `WriteAdjacency(s As %String, p As %String, o As %String, w As %String = "1.0")`, and `DeleteAdjacency(s As %String, p As %String, o As %String)`; implement all three (see plan.md for algorithm); compile into `iris_vector_graph` container and confirm clean compile
- [x] T004 [US1] Add unit test `test_matchedges_bound_source_bound_predicate` to `TestUnifiedEdgeStoreUnit` in `tests/unit/test_unified_edge_store.py`: mock `_iris_obj().classMethodValue` to return `'[{"s":"A","p":"TREATS","o":"B","w":1.0}]'`; call `engine._iris_obj().classMethodValue("Graph.KG.EdgeScan","MatchEdges","A","TREATS",0)`; assert result parses to list with one dict containing keys `s,p,o,w` — must FAIL before T003 is compiled
- [x] T005 [P] [US1] Add E2E test `test_matchedges_returns_correct_json` to `TestUnifiedEdgeStoreE2E`: seed one edge `(A)-[TREATS]->(B)` via `_call_classmethod("Graph.KG.EdgeScan","WriteAdjacency","A","TREATS","B","1.0")`; call `MatchEdges("A","TREATS",0)` via `_call_classmethod`; parse JSON; assert one entry with `s=="A"`, `p=="TREATS"`, `o=="B"`; cleanup
- [x] T006 [P] [US1] Add E2E test `test_matchedges_unbound_predicate` to `TestUnifiedEdgeStoreE2E`: seed two edges from A with different predicates; call `MatchEdges("A","",0)`; assert both predicates returned
- [x] T007 [P] [US1] Add E2E test `test_matchedges_unbound_source` to `TestUnifiedEdgeStoreE2E`: seed two edges from different sources; call `MatchEdges("","",0)`; assert both returned (validates full scan path)
- [x] T007b [US1] Run `pytest tests/unit/test_unified_edge_store.py -v` — T004 passes (mock-based unit), T005–T007 pass (E2E via container); confirm T004 FAILS before T003 compile

---

## PR-A — Phase 3: US1 + US2 — Temporal edges visible + No BuildKG requirement

**Story goal**: `MATCH (a {id:'X'})-[r]->(b)` returns both static and temporal edges from X without requiring `BuildKG()`.

**Independent test criterion**: Insert one static edge (`create_edge`) + one temporal edge (`create_edge_temporal`) from node X. Run `MATCH (a {id:'X'})-[r]->(b) RETURN type(r), b.id`. Assert both edges appear. Do NOT call `BuildKG()`.

- [x] T008 [US2] Add unit test `test_create_edge_calls_write_adjacency` to `TestUnifiedEdgeStoreUnit` in `tests/unit/test_unified_edge_store.py`: mock `_iris_obj()`; call `create_edge("A","TREATS","B")`; assert `classMethodVoid("Graph.KG.EdgeScan","WriteAdjacency","A","TREATS","B",...)` was called — must FAIL before T011
- [x] T009 [P] [US2] Add unit test `test_delete_edge_calls_delete_adjacency` to `TestUnifiedEdgeStoreUnit`: mock; call `delete_edge`; assert `classMethodVoid("Graph.KG.EdgeScan","DeleteAdjacency",...)` was called — must FAIL before T012
- [x] T010 [P] [US2] Add unit test `test_kg_write_failure_is_non_fatal` to `TestUnifiedEdgeStoreUnit`: make `classMethodVoid` raise; assert `create_edge` still returns `True` (SQL succeeds; ^KG failure is logged but non-fatal) — must FAIL before T011
- [x] T011 [US2] Implement `WriteAdjacency` call in `create_edge` in `iris_vector_graph/engine.py` (after existing SQL INSERT + commit at line ~1276): `self._iris_obj().classMethodVoid("Graph.KG.EdgeScan","WriteAdjacency",source_id,predicate,target_id,str(weight if weight else 1.0))`; wrap in try/except with `logger.warning` on failure — T008 and T010 now pass
- [x] T012 [US2] Implement `DeleteAdjacency` call in `delete_edge` in `iris_vector_graph/engine.py`: `self._iris_obj().classMethodVoid("Graph.KG.EdgeScan","DeleteAdjacency",source_id,predicate,target_id)` after SQL DELETE; same try/except — T009 now passes
- [x] T013 [P] [US2] Verify `create_edge_temporal` in `iris_vector_graph/engine.py` already writes `^KG("out",0,s,p,o)` via `TemporalIndex.InsertEdge` — read `TemporalIndex.cls` and confirm slot is `0`; if not, update `InsertEdge` to use slot 0
- [x] T014 [US2] Run `pytest tests/unit/test_unified_edge_store.py -v` — T008, T009, T010 pass; add E2E test `test_write_adjacency_sets_kg_global` to `TestUnifiedEdgeStoreE2E`: call `create_edge(A,TREATS,B)`; use native API to assert `$Data(^KG("out",0,"A","TREATS","B"))=1`; also verify `create_edge` + delete cycle removes the `^KG` entry; cleanup

---

## PR-A — Phase 4: US1 + US2 — Translator CTE swap

**Story goal**: `MATCH (a)-[r]->(b)` generates a `JSON_TABLE(MatchEdges(...))` CTE instead of `JOIN rdf_edges`. Both static and temporal edges appear in results.

**Independent test criterion**: Generate SQL for `MATCH (a {id:'X'})-[r]->(b) RETURN type(r), b.id`; assert SQL contains `Graph_KG.MatchEdges` and does NOT contain `rdf_edges` in the FROM/JOIN path.

- [x] T015 [US1] Add unit test `test_simple_match_uses_edgescan_cte` to `TestUnifiedEdgeStoreUnit`: `translate_to_sql` on `MATCH (a {id:'X'})-[r]->(b) RETURN type(r), b.id`; assert `"MatchEdges"` in SQL and `"rdf_edges"` NOT in the JOIN path — must FAIL before T015b
- [x] T015b [US1] In `iris_vector_graph/cypher/translator.py` at `translate_relationship_pattern` (line ~1285), implement the bound-source + single-predicate (or no predicate) CTE injection: when `rel.variable_length is None` (not BFS) and source node has a bound `id` property, replace `JOIN rdf_edges ...` with `JSON_TABLE(Graph_KG.MatchEdges(source_alias.node_id, pred_or_empty, 0), '$[*]' COLUMNS(s VARCHAR(256) PATH '$.s', p VARCHAR(256) PATH '$.p', o VARCHAR(256) PATH '$.o', w DOUBLE PATH '$.w')) j` CTE; update `edge_cond` to reference `{edge_alias}.s`, `{edge_alias}.p`, `{edge_alias}.o`; run T015 — must now PASS
- [x] T015c [P] [US1] Extend T015b to multi-predicate case: when `len(rel.types) > 1`, pass `''` to `MatchEdges` (full scan) and add a WHERE filter on `{edge_alias}.p IN (...)` after the CTE; add unit test `test_multi_predicate_match_passes_empty_predicate`; assert SQL contains `MatchEdges` with empty predicate and a WHERE IN clause
- [x] T015d [P] [US1] Extend T015b to unbound-source case: when source node has no bound `id` (no property filter, no variable in scope with known id), pass `''` as sourceId to `MatchEdges`; run T018 — must now pass
- [x] T016 [US1] Add unit test `test_simple_match_uses_edgescan_cte` in `tests/unit/test_unified_edge_store.py`: `translate_to_sql` on `MATCH (a {id:'X'})-[r]->(b) RETURN type(r), b.id`; assert `"MatchEdges"` in SQL and `"rdf_edges"` NOT in the JOIN path (rdf_edges may still appear in subqueries for DELETE/CREATE — only check the main FROM/JOIN of a MATCH query)
- [x] T017 [P] [US1] Add unit test `test_predicate_filtered_match_uses_bound_predicate` in `tests/unit/test_unified_edge_store.py`: `MATCH (a {id:'X'})-[r:TREATS]->(b) RETURN b.id`; assert SQL contains `MatchEdges(` and contains `'TREATS'` as the predicate parameter
- [x] T018 [P] [US1] Add unit test `test_unbound_source_match_passes_empty_sourceid` in `tests/unit/test_unified_edge_store.py`: `MATCH (a)-[r:TREATS]->(b) RETURN a.id, b.id`; assert SQL contains `MatchEdges(''` (empty source)
- [x] T019 [US1] Run `pytest tests/unit/test_unified_edge_store.py -v` — T015-T018 pass
- [x] T020 [P] [US1] Run `pytest tests/unit/ -q` — baseline 492 pass maintained, no regressions in existing MATCH/BFS/temporal tests

---

## PR-A — Phase 5: US1 + US2 E2E Gate

**Independent test criterion (E2E)**: Insert static edge + temporal edge from same source without `BuildKG`; `MATCH (a)-[r]->(b)` returns both. `TestUnifiedEdgeStoreE2E` uses `iris_connection` fixture with `IRISContainer.attach("iris_vector_graph")` (same pattern as `test_bm25_index.py`); `SKIP_IRIS_TESTS` defaults `"false"`.

- [x] T021 [US1] Add E2E test `test_temporal_edge_visible_in_match` to `TestUnifiedEdgeStoreE2E` in `tests/unit/test_unified_edge_store.py`: create nodes X, Y, Z via `create_node`; `create_edge(X, "STATIC_REL", Y)`; `create_edge_temporal(X, "TEMPORAL_REL", Z, timestamp=1000)`; run `execute_cypher("MATCH (a {id:'X'})-[r]->(b) RETURN type(r), b.id")`; assert both `"STATIC_REL"` and `"TEMPORAL_REL"` appear in results; do NOT call `BuildKG()`; cleanup all test nodes + edges
- [x] T021b [P] [US1] Add E2E test `test_delete_edge_not_visible_in_match` to `TestUnifiedEdgeStoreE2E`: `create_edge(A,"REL",B)`; verify in MATCH; `delete_edge(A,"REL",B)`; run MATCH again; assert B NOT returned — validates E2E delete propagation to `^KG`; cleanup
- [x] T022 [P] [US1] Add E2E test `test_no_builkg_required_for_bfs` to `TestUnifiedEdgeStoreE2E`: `create_edge(A,REL,B)` + `create_edge(B,REL,C)`; run `execute_cypher("MATCH p = shortestPath((x {id:'A'})-[*..3]-(y {id:'C'})) RETURN p")`; assert path found without `BuildKG`; cleanup
- [x] T023 [US1] Run `pytest tests/unit/test_unified_edge_store.py::TestUnifiedEdgeStoreE2E -v` — T021, T021b, T022 pass

---

## PR-A — Phase 6: PR-A Polish

- [x] T024 [P] Run full unit suite `pytest tests/unit/ -q` — confirm 492+ pass, no regressions
- [x] T025 Bump version to `1.50.0` in `pyproject.toml`; add `AGENTS.md` entry for 048-unified-edge-store
- [x] T026 [P] Update README: add entry to architecture table noting `MATCH (a)-[r]->(b)` now returns temporal + static edges via `^KG` globals
- [ ] T027 Commit PR-A: `feat: v1.50.0 — unified edge store PR-A: synchronous ^KG writes + EdgeScan CTE translator (spec 048)`

---

## PR-B — Phase 7: Shard Subscript Migration

**Goal**: All `^KG("out", s, p, o)` references become `^KG("out", 0, s, p, o)`. `BuildKG` migrates old data. `BuildNKG` updated.

**Independent test criterion**: After `BuildKG()`, `$Order(^KG("out", 0, ...))` returns expected edges; old `^KG("out", srcId, ...)` (no shard slot) no longer exists.

- [ ] T028 [US3] Update `iris_src/src/Graph/KG/Traversal.cls` — `BuildKG()` method: (1) add migration step at top to move `^KG("out", s, p, o)` → `^KG("out", 0, s, p, o)` and `^KG("in", o, p, s)` → `^KG("in", 0, o, p, s)` for any keys that are not already in the shard-slot layout (detect by checking if `$Order(^KG("out", s))` returns a string that is NOT an integer — indicating old layout); (2) update all writes in `BuildKG` from `Set ^KG("out", s, p, o)` to `Set ^KG("out", 0, s, p, o)` and `Set ^KG("in", o, p, s)` to `Set ^KG("in", 0, o, p, s)`; compile
- [ ] T029 [US3] Update all BFS/traversal reads in `iris_src/src/Graph/KG/Traversal.cls`: `BFSFast`, `BFSFastJson`, `ShortestPathJson`, `BuildNKG` — every `$Order(^KG("out", s, ...))` → `$Order(^KG("out", 0, s, ...))`; every `$Order(^KG("in", ...))` → `$Order(^KG("in", 0, ...))`; compile
- [ ] T030 [P] [US3] Update `iris_src/src/Graph/KG/TemporalIndex.cls` — `InsertEdge` method: change `Set ^KG("out", source, predicate, target) = weight` to `Set ^KG("out", 0, source, predicate, target) = weight` and same for `^KG("in", ...)`; compile
- [ ] T031 [P] [US3] Update `iris_src/src/Graph/KG/NKGAccel.cls` — `BuildNKG`: change all `$Order(^KG("out", src, ...))` to `$Order(^KG("out", 0, src, ...))`; compile (FR-010)
- [ ] T032 [P] [US3] Update `iris_src/src/Graph/KG/BenchSeeder.cls` if it writes `^KG("out", ...)` directly: add shard=0 slot; compile
- [ ] T033 [US3] Compile all updated classes into container and confirm clean compile for all four files
- [ ] T034 [US3] Add unit test `test_builkg_writes_shard_slot` in `tests/unit/test_unified_edge_store.py`: after `BuildKG()` call (or `classMethodValue("Graph.KG.Traversal","BuildKG")`), verify via native API that `$Data(^KG("out",0,...))=1` for a known edge; verify old `^KG("out",srcId,pred,dst)` (no shard) does not exist
- [ ] T035 [US3] Add unit test `test_builkg_migration_idempotent` in `tests/unit/test_unified_edge_store.py`: run `BuildKG()` twice; assert second run completes without error and data is consistent

---

## PR-B — Phase 8: US3 + US4 E2E Gate

- [ ] T036 [US3] Add E2E test `test_bfs_uses_new_layout` in `tests/unit/test_unified_edge_store.py`: call `BuildKG()`; BFS from known node; assert results match expected neighbors (regression: same as before migration)
- [ ] T037 [P] [US3] Add E2E test `test_shortestpath_uses_new_layout` in `tests/unit/test_unified_edge_store.py`: after `BuildKG()`, `shortestPath` across known path; assert same result as before migration
- [ ] T038 [US4] Add E2E test `test_match_unbound_source_returns_all_edges` in `tests/unit/test_unified_edge_store.py`: seed 3 edges; run `MATCH (a)-[r]->(b) RETURN type(r)` (no source filter); assert all 3 predicates returned (validates unbound-source full scan path)
- [ ] T039 [US3] Run `pytest tests/unit/test_unified_edge_store.py::TestUnifiedEdgeStoreE2E -v` — T034-T038 pass

---

## PR-B — Phase 9: Benchmark + Polish

- [ ] T040 [P] Run full unit suite `pytest tests/unit/ -q` — 492+ pass, no regressions from layout migration
- [ ] T041 Benchmark NFR-001 + NFR-002: (a) MATCH latency — time `MATCH (a {id:$src})-[r]->(b)` via EdgeScan CTE at 1K, 100K, 1M edges; assert p50 ≤ SQL baseline recorded in T001; (b) write overhead — time `create_edge` with and without `WriteAdjacency` call (add `SKIP_KG_WRITE=true` env flag temporarily); assert with-write overhead < 2x without-write; document both results in spec clarifications (NFR-001 + NFR-002)
- [ ] T042 [P] Bump version to `1.50.1` in `pyproject.toml` (PR-B patch on top of PR-A)
- [ ] T043 [P] Update `AGENTS.md` recent changes with PR-B subscript migration note
- [ ] T044 Commit PR-B: `feat: v1.50.1 — unified edge store PR-B: shard subscript ^KG("out",0,...), BuildKG migration, NKG update (spec 048)`
- [ ] T045 Build and publish: `python3 -m build && twine upload dist/iris_vector_graph-1.50.*`

---

**Total tasks**: 52 (T001–T002b setup, T003–T007b EdgeScan, T008–T014 engine writes, T015–T020 translator, T021–T023 E2E, T024–T027 PR-A polish, T028–T039 PR-B, T040–T045 PR-B polish)
**PR-A tasks**: T001–T027 + T002b + T007b + T021b + T015b–T015d = 34 tasks
**PR-B tasks**: T028–T045 = 18 tasks
**Primary E2E gate (PR-A)**: T021 — temporal edge visible in MATCH without BuildKG
**PR-B gate**: T039 — BFS + shortestPath correct after layout migration

## Dependencies

```
T001–T002b (setup) → T003 (EdgeScan.cls compile) → T004–T007b (EdgeScan tests, must fail before T003) → T003 compile → T007b pass
                   → T008–T010 (unit tests, must fail) → T011–T014 (engine writes impl + E2E)
                   → T015 (unit test, must fail) → T015b–T015d (translator impl) → T016–T020 (unit + regression)
                   → T021–T023 (E2E gate)
                   → T024–T027 (PR-A polish)
     → MERGE PR-A →
T028–T033 (shard migration) → T034–T035 (migration tests) → T036–T039 (E2E) → T040–T045 (PR-B polish)
```

T003 (EdgeScan.cls) and T008-T010 (unit tests) can proceed in parallel once T002b baseline is done.
T015 (translator unit test) can be written in parallel with T011 (engine impl) — both depend only on T007b.
PR-B depends on PR-A merged.

## MVP Scope

**T001–T027 (PR-A only)** — delivers the urgent fix: temporal edges visible in `MATCH`, no more stale-after-write. Constitution-compliant: tests written before impl, E2E via `iris_vector_graph` container.
