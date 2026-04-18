# Tasks: Unified Edge Store (spec 048)

---

## PR-A — Phase 1: Setup & Verification

- [ ] T001 Confirm `iris_src/src/Graph/KG/EdgeScan.cls` does not exist; record current `translate_relationship_pattern` line for rdf_edges JOIN (line ~1299 in `iris_vector_graph/cypher/translator.py`)
- [ ] T002 Run `pytest tests/unit/ -q` — record baseline (492 passed, 7 pre-existing failures); this is the regression gate for all PR-A work

---

## PR-A — Phase 2: Foundational (EdgeScan ObjectScript)

**Goal**: `Graph.KG.EdgeScan.MatchEdges` compiled and callable. No Python wiring yet.

**Independent test criterion**: `##class(Graph.KG.EdgeScan).MatchEdges("A", "", 0)` returns JSON array of all edges from A; `MatchEdges("", "TREATS", 0)` returns all TREATS edges across the whole graph.

- [ ] T003 Create `iris_src/src/Graph/KG/EdgeScan.cls` — `Class Graph.KG.EdgeScan Extends %RegisteredObject` with three ClassMethods: `MatchEdges(sourceId As %String, predicate As %String, shard As %Integer = 0) As %String [SqlProc]`, `WriteAdjacency(s As %String, p As %String, o As %String, w As %String = "1.0")`, and `DeleteAdjacency(s As %String, p As %String, o As %String)`; implement all three (see plan.md for algorithm); compile into `iris_vector_graph` container and confirm clean compile
- [ ] T004 [P] Verify `MatchEdges` bound-source+bound-predicate: call `##class(Graph.KG.EdgeScan).MatchEdges("test_s","TREATS",0)` after seeding one edge; assert JSON contains `{s,p,o,w}` tuple
- [ ] T005 [P] Verify `MatchEdges` bound-source+unbound-predicate: call `MatchEdges("test_s","",0)`; assert all predicates from test_s are returned
- [ ] T006 [P] Verify `MatchEdges` unbound-source: call `MatchEdges("","",0)`; assert returns all edges in graph (equivalent to full rdf_edges scan)
- [ ] T007 Verify `WriteAdjacency` + `DeleteAdjacency`: call Write; assert `$Data(^KG("out",0,s,p,o))=1`; call Delete; assert `$Data(^KG("out",0,s,p,o))=0`

---

## PR-A — Phase 3: US1 + US2 — Temporal edges visible + No BuildKG requirement

**Story goal**: `MATCH (a {id:'X'})-[r]->(b)` returns both static and temporal edges from X without requiring `BuildKG()`.

**Independent test criterion**: Insert one static edge (`create_edge`) + one temporal edge (`create_edge_temporal`) from node X. Run `MATCH (a {id:'X'})-[r]->(b) RETURN type(r), b.id`. Assert both edges appear. Do NOT call `BuildKG()`.

- [ ] T008 [US1] Add `WriteAdjacency` call to `create_edge` in `iris_vector_graph/engine.py` (after the existing SQL INSERT + commit at line ~1276): call `self._iris_obj().classMethodVoid("Graph.KG.EdgeScan", "WriteAdjacency", source_id, predicate, target_id, str(weight if weight else 1.0))`; wrap in try/except with `logger.warning` on failure (recovery via BuildKG is the documented fallback)
- [ ] T009 [US1] Add `DeleteAdjacency` call to `delete_edge` in `iris_vector_graph/engine.py`: call `self._iris_obj().classMethodVoid("Graph.KG.EdgeScan", "DeleteAdjacency", source_id, predicate, target_id)` after the SQL DELETE; same try/except pattern
- [ ] T010 [US1] Verify `create_edge_temporal` in `iris_vector_graph/engine.py` already writes `^KG("out",0,s,p,o)` via `TemporalIndex.InsertEdge` — read `TemporalIndex.cls` and confirm the shard slot is `0`; if not, update `InsertEdge` to use slot 0
- [ ] T011 [US2] Add unit test `test_create_edge_writes_kg_global` in `tests/unit/test_unified_edge_store.py`: mock `_iris_obj()`; call `create_edge("A","TREATS","B")`; assert `classMethodVoid("Graph.KG.EdgeScan","WriteAdjacency",...)` was called with correct args
- [ ] T012 [P] [US2] Add unit test `test_delete_edge_kills_kg_global` in `tests/unit/test_unified_edge_store.py`: mock; call `delete_edge`; assert `DeleteAdjacency` was called
- [ ] T013 [P] [US2] Add unit test `test_kg_write_failure_does_not_raise` in `tests/unit/test_unified_edge_store.py`: make `classMethodVoid` raise; assert `create_edge` returns True anyway (SQL succeeds, ^KG failure is non-fatal)
- [ ] T014 [US2] Run `pytest tests/unit/test_unified_edge_store.py -v` — T011, T012, T013 pass

---

## PR-A — Phase 4: US1 + US2 — Translator CTE swap

**Story goal**: `MATCH (a)-[r]->(b)` generates a `JSON_TABLE(MatchEdges(...))` CTE instead of `JOIN rdf_edges`. Both static and temporal edges appear in results.

**Independent test criterion**: Generate SQL for `MATCH (a {id:'X'})-[r]->(b) RETURN type(r), b.id`; assert SQL contains `Graph_KG.MatchEdges` and does NOT contain `rdf_edges` in the FROM/JOIN path.

- [ ] T015 [US1] In `iris_vector_graph/cypher/translator.py`, extend `translate_relationship_pattern` (line ~1285): after computing `edge_cond` and before `context.join_clauses.append(f"{jt} {_table('rdf_edges')} ...")`, check if `rel.variable_length is None` (simple pattern, not BFS) AND `rel.variable_length is None` (not temporal); if so, build the `EdgeScan_{alias}` CTE and insert into `context.stages` using `JSON_TABLE(Graph_KG.MatchEdges(src_id_sql, pred_sql, 0), '$[*]' COLUMNS(s VARCHAR(256) PATH '$.s', p VARCHAR(256) PATH '$.p', o VARCHAR(256) PATH '$.o', w DOUBLE PATH '$.w')) j`; use `source_alias.node_id` as `src_id_sql` when source is bound, `''` when unbound; use the single predicate string when `len(rel.types) == 1`, else `''`; replace the `rdf_edges` JOIN clause with `JOIN EdgeScan_{alias} {edge_alias} ON {edge_cond}` where `edge_cond` references `{edge_alias}.s`, `{edge_alias}.p`, `{edge_alias}.o` instead of `rdf_edges.s`, `rdf_edges.p`, `rdf_edges.o_id`
- [ ] T016 [US1] Add unit test `test_simple_match_uses_edgescan_cte` in `tests/unit/test_unified_edge_store.py`: `translate_to_sql` on `MATCH (a {id:'X'})-[r]->(b) RETURN type(r), b.id`; assert `"MatchEdges"` in SQL and `"rdf_edges"` NOT in the JOIN path (rdf_edges may still appear in subqueries for DELETE/CREATE — only check the main FROM/JOIN of a MATCH query)
- [ ] T017 [P] [US1] Add unit test `test_predicate_filtered_match_uses_bound_predicate` in `tests/unit/test_unified_edge_store.py`: `MATCH (a {id:'X'})-[r:TREATS]->(b) RETURN b.id`; assert SQL contains `MatchEdges(` and contains `'TREATS'` as the predicate parameter
- [ ] T018 [P] [US1] Add unit test `test_unbound_source_match_passes_empty_sourceid` in `tests/unit/test_unified_edge_store.py`: `MATCH (a)-[r:TREATS]->(b) RETURN a.id, b.id`; assert SQL contains `MatchEdges(''` (empty source)
- [ ] T019 [US1] Run `pytest tests/unit/test_unified_edge_store.py -v` — T015-T018 pass
- [ ] T020 [P] [US1] Run `pytest tests/unit/ -q` — baseline 492 pass maintained, no regressions in existing MATCH/BFS/temporal tests

---

## PR-A — Phase 5: US1 + US2 E2E Gate

**Independent test criterion (E2E)**: Insert static edge + temporal edge from same source without `BuildKG`; `MATCH (a)-[r]->(b)` returns both.

- [ ] T021 [US1] Add E2E test `test_temporal_edge_visible_in_match` in `tests/unit/test_unified_edge_store.py`: in `TestUnifiedEdgeStoreE2E`, create node X; `create_edge(X, STATIC_REL, Y)`; `create_edge_temporal(X, TEMPORAL_REL, Z, ts=1000)`; run `execute_cypher("MATCH (a {id:'X'})-[r]->(b) RETURN type(r), b.id")`; assert both STATIC_REL and TEMPORAL_REL appear; do NOT call `BuildKG()`; cleanup
- [ ] T022 [P] [US1] Add E2E test `test_no_builkg_required_for_bfs` in `tests/unit/test_unified_edge_store.py`: `create_edge(A,REL,B)` + `create_edge(B,REL,C)`; run `execute_cypher("MATCH p = shortestPath((x {id:'A'})-[*..3]-(y {id:'C'})) RETURN p")`; assert path found without `BuildKG`; cleanup
- [ ] T023 [US1] Run `pytest tests/unit/test_unified_edge_store.py::TestUnifiedEdgeStoreE2E -v` — T021, T022 pass

---

## PR-A — Phase 6: PR-A Polish

- [ ] T024 [P] Run full unit suite `pytest tests/unit/ -q` — confirm 492+ pass, no regressions
- [ ] T025 Bump version to `1.50.0` in `pyproject.toml`; add `AGENTS.md` entry for 048-unified-edge-store
- [ ] T026 [P] Update README: add entry to architecture table noting `MATCH (a)-[r]->(b)` now returns temporal + static edges via `^KG` globals
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
- [ ] T041 Add benchmark task: create a 1K-edge graph with BenchSeeder; time `MATCH (a {id:$src})-[r]->(b)` (EdgeScan CTE) vs original SQL JOIN baseline; assert p50 latency ≤ SQL baseline; document result in spec clarifications (NFR-001 tiers 1K)
- [ ] T042 [P] Bump version to `1.50.1` in `pyproject.toml` (PR-B patch on top of PR-A)
- [ ] T043 [P] Update `AGENTS.md` recent changes with PR-B subscript migration note
- [ ] T044 Commit PR-B: `feat: v1.50.1 — unified edge store PR-B: shard subscript ^KG("out",0,...), BuildKG migration, NKG update (spec 048)`
- [ ] T045 Build and publish: `python3 -m build && twine upload dist/iris_vector_graph-1.50.*`

---

**Total tasks**: 45
**PR-A tasks**: T001–T027 (27 tasks)
**PR-B tasks**: T028–T045 (18 tasks)
**Primary E2E gate**: T021 — temporal edge visible in MATCH without BuildKG
**PR-B gate**: T039 — BFS + shortestPath correct after layout migration

## Dependencies

```
T001-T002 (setup) → T003-T007 (EdgeScan proc) → T008-T014 (engine writes) → T015-T020 (translator CTE) → T021-T023 (E2E) → T024-T027 (PR-A polish)
     → MERGE PR-A →
T028-T033 (shard migration) → T034-T035 (migration tests) → T036-T039 (E2E) → T040-T045 (PR-B polish)
```

T003 (EdgeScan.cls) and T008 (engine write) can proceed in parallel once T002 is done.
T015 (translator) depends on T003 (EdgeScan.cls must exist to generate correct CTE SQL).
PR-B depends on PR-A merged (shard slot 0 already established by EdgeScan writes).

## MVP Scope

**T001–T027 (PR-A only)** — delivers the urgent fix: temporal edges visible in `MATCH`, no more stale-after-write. PR-B (shard layout) can follow at its own pace.
