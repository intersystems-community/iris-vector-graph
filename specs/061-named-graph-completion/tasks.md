# Tasks: Named Graph Completion (spec 061)

---

## Phase 1 — Setup

- [ ] T001 Run `pytest tests/unit/ -q` — record baseline 519 passed
- [ ] T002 Create `tests/unit/test_named_graphs.py` with SKIP_IRIS_TESTS guard, `TestNamedGraphsE2E` class, `iris_connection` fixture, `_run` UUID suffix, cleanup teardown

---

## Phase 2 — FR-005: import_rdf writes graph_id

**E2E test MUST fail before implementation (currently import_rdf ignores graph=).**

- [ ] T003 [US1] Add E2E test `test_import_rdf_graph_id_written` to `TestNamedGraphsE2E`: create minimal TTL string with 2 triples using `tempfile`; call `engine.import_rdf(path, graph="g061_{run}")` (graph= already in signature); query `USE GRAPH 'g061_{run}' MATCH (a)-[r]->(b) RETURN count(r) AS c`; assert `c > 0`; cleanup with `drop_graph` — **run test now, confirm it FAILS** before T004
- [ ] T004 [US1] Fix `import_rdf` in `iris_vector_graph/engine.py` line ~2171: in `_flush()`, change the edge INSERT to include `graph_id` when `graph` param is set — `INSERT INTO rdf_edges (s, p, o_id, graph_id) VALUES (?, ?, ?, ?)` with `[s, p, o, graph]`; also pass `graph_id` on props inserts is NOT needed (properties are not scoped by graph); handle `infer=True` path — `graph` is now a proper parameter so pass directly
- [ ] T005 [US1] Run `pytest tests/unit/test_named_graphs.py::TestNamedGraphsE2E::test_import_rdf_graph_id_written -v` — MUST PASS

---

## Phase 3 — FR-006: USE GRAPH falls back to rdf_edges

**E2E test MUST fail before implementation (MatchEdges CTE has no graph_id, so USE GRAPH leaks cross-graph edges).**

- [ ] T006 [US4] Add E2E test `test_use_graph_no_cross_graph_leak` to `TestNamedGraphsE2E`: `create_edge("A_{run}", "R", "B_{run}", graph="g1_{run}")`; `create_edge("A_{run}", "R", "C_{run}", graph="g2_{run}")`; query `USE GRAPH 'g1_{run}' MATCH (a {{id:'A_{run}'}})-[r]->(b) RETURN b.id`; assert rows == `[["B_{run}"]]` (C not present) — **run now, confirm it FAILS** (currently returns both B and C)
- [ ] T007 [US4] Fix `translator.py` in `translate_relationship_pattern`: at line ~1827, wrap the MatchEdges CTE path in `if src_id_sql is not None and not context.graph_context:`; when `context.graph_context` is set, fall through to the `rdf_edges` JOIN at line 1844 — the existing `WHERE e.graph_id = 'x'` filter then applies
- [ ] T008 [US4] Run `pytest tests/unit/test_named_graphs.py::TestNamedGraphsE2E::test_use_graph_no_cross_graph_leak -v` — MUST PASS

---

## Phase 4 — FR-002: bulk_create_edges respects graph

**E2E test MUST fail before implementation (bulk ignores graph=).**

- [ ] T009 [US2] Add E2E test `test_bulk_create_edges_graph_id` to `TestNamedGraphsE2E`: call `engine.bulk_create_edges([{"source_id": "X_{run}", "predicate": "P", "target_id": "Y_{run}"}, {"source_id": "X_{run}", "predicate": "P", "target_id": "Z_{run}", "graph": "override_{run}"}], graph="bulk_{run}")`; query `USE GRAPH 'bulk_{run}' MATCH (a {{id:'X_{run}'}})-[r]->(b) RETURN b.id`; assert `["Y_{run}"]` only (Z in override); query `USE GRAPH 'override_{run}'` returns `["Z_{run}"]` — **run now, confirm FAILS**
- [ ] T010 [US2] Add `graph: Optional[str] = None` to `bulk_create_edges` signature in `engine.py:1969`
- [ ] T010b [US2] **Schema migration**: add `add_graph_id_to_unique_constraint` to `GraphSchema.ensure_indexes` in `schema.py`: attempt `ALTER TABLE Graph_KG.rdf_edges DROP CONSTRAINT u_spo` then `ALTER TABLE Graph_KG.rdf_edges ADD CONSTRAINT u_spo_graph UNIQUE (s, p, o_id, graph_id)`; suppress "does not exist" and "already exists" errors idempotently; this allows same SPO in different named graphs
- [ ] T011 [US2] Add `"rdf_edges_with_graph"` template to `GraphSchema.get_bulk_insert_sql` in `schema.py`: `INSERT INTO Graph_KG.rdf_edges (s, p, o_id, graph_id) SELECT ?, ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.rdf_edges WHERE s = ? AND p = ? AND o_id = ? AND (graph_id = ? OR (graph_id IS NULL AND ? IS NULL)))` — 9 params `[s, p, o, g, s, p, o, g, g]`
- [ ] T012 [US2] In `bulk_create_edges` edge_params loop: read per-edge `e.get("graph", graph)` to get effective graph_id; if any edge has a non-None graph_id, use the `"rdf_edges_with_graph"` template; build params as `[s, p, o, g, s, p, o, g, g]`; if all graph_ids are None, use existing template (no regression)
- [ ] T013 [US2] Run `pytest tests/unit/test_named_graphs.py::TestNamedGraphsE2E::test_bulk_create_edges_graph_id -v` — MUST PASS

---

## Phase 5 — FR-003/FR-004: create_edge_temporal and bulk_create_edges_temporal respect graph

**E2E tests MUST fail before implementation.**

- [ ] T014 [P] [US3] Add E2E test `test_create_edge_temporal_graph_id` to `TestNamedGraphsE2E`: call `engine.create_edge_temporal("srv_a_{run}", "CALLS_AT", "srv_b_{run}", timestamp=1000, graph="temporal_{run}")`; query `USE GRAPH 'temporal_{run}' MATCH (a {{id:'srv_a_{run}'}})-[r]->(b) RETURN type(r)`; assert `"CALLS_AT"` in result — **run now, confirm FAILS**
- [ ] T015 [P] [US3] Add E2E test `test_bulk_create_edges_temporal_graph_id` to `TestNamedGraphsE2E`: call `engine.bulk_create_edges_temporal([{"s": "ta_{run}", "p": "CALLS", "o": "tb_{run}", "ts": 1000, "w": 1.0}], graph="tbulk_{run}")`; query `USE GRAPH 'tbulk_{run}' MATCH (a {{id:'ta_{run}'}})-[r]->(b) RETURN type(r)`; assert `"CALLS"` present — **run now, confirm FAILS**
- [ ] T016 [US3] Add `graph: Optional[str] = None` to `create_edge_temporal` in `engine.py:3936`; after the `InsertEdge` ObjectScript call, upsert source and target into `Graph_KG.nodes` (INSERT ... WHERE NOT EXISTS), then execute `INSERT INTO Graph_KG.rdf_edges (s, p, o_id, graph_id) VALUES (?, ?, ?, ?) WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.rdf_edges WHERE s = ? AND p = ? AND o_id = ? AND (graph_id = ? OR (graph_id IS NULL AND ? IS NULL)))` — wrap in try/except, non-fatal on failure; only execute when `graph is not None` to avoid inserting duplicate default-graph rows for temporal edges that are already in rdf_edges via static path
- [ ] T017 [US3] Add `graph: Optional[str] = None` to `bulk_create_edges_temporal` in `engine.py:3965`; after the `BulkInsert` call, for each edge in the input list: upsert source/target nodes, then executemany `INSERT INTO Graph_KG.rdf_edges (s, p, o_id, graph_id) SELECT ?, ?, ?, ? WHERE NOT EXISTS (...)` using `e.get("graph", graph)` as graph_id; wrap in try/except; only execute the rdf_edges inserts when at least one edge has a non-None graph_id
- [ ] T018 [US3] Run `pytest tests/unit/test_named_graphs.py::TestNamedGraphsE2E -k "temporal" -v` — T014, T015 MUST PASS

---

## Phase 6 — FR-007: db.schema.relTypeProperties returns data

**E2E test MUST fail before implementation (currently hardcoded empty).**

- [ ] T019 [US5] Add E2E test `test_rel_type_properties_non_empty` to `TestNamedGraphsE2E`: call `engine.create_edge("rp_a_{run}", "TREATS", "rp_b_{run}")`; call `engine._try_system_procedure` with name `"db.schema.reltypeproperties"`; assert `len(result["rows"]) > 0` and any row has `relType == "TREATS"` — **run now, confirm FAILS** (returns empty)
- [ ] T020 [US5] Fix `_try_system_procedure` in `engine.py:1205`: replace hardcoded empty return with: `cursor.execute("SELECT DISTINCT p FROM Graph_KG.rdf_edges WHERE p IS NOT NULL ORDER BY p")` → for each rel_type, `cursor.execute("SELECT TOP 1 qualifiers FROM Graph_KG.rdf_edges WHERE p = ? AND qualifiers IS NOT NULL", [rel_type])` → parse JSON keys, always include "weight"; return rows of `[rel_type, prop_name, ["STRING"], False]`
- [ ] T021 [US5] Run `pytest tests/unit/test_named_graphs.py::TestNamedGraphsE2E::test_rel_type_properties_non_empty -v` — MUST PASS

---

## Phase 7 — Full Gate

- [ ] T022 Add E2E test `test_list_graphs_includes_all_write_paths` to `TestNamedGraphsE2E`: after inserting via import_rdf, create_edge, bulk_create_edges, create_edge_temporal each with graph="all_{run}"; call `engine.list_graphs()`; assert "all_{run}" in result
- [ ] T023 Add E2E test `test_drop_graph_removes_all_paths` to `TestNamedGraphsE2E`: insert via all 4 paths with graph="drop_{run}"; call `engine.drop_graph("drop_{run}")`; verify each write path returns 0 edges in USE GRAPH query
- [ ] T024 Run `pytest tests/unit/test_named_graphs.py -v` — ALL tests pass
- [ ] T025 [P] Run `pytest tests/unit/ -q` — 519+ passed, zero regressions

---

## Phase 8 — Polish

- [ ] T026 Bump version to 1.55.0 in pyproject.toml
- [ ] T027 Add v1.55.0 changelog entry in README.md
- [ ] T028 Commit: `feat: v1.55.0 — named graph completion: import_rdf/bulk/temporal graph=, USE GRAPH isolation, relTypeProperties (spec 061)`
- [ ] T029 Build and publish: `python3 -m build && twine upload dist/iris_vector_graph-1.55.0*`

---

**Total tasks**: 29
**Primary E2E gate**: T024 — all named graph tests pass
**Mandatory pattern**: Every implementation task (T004, T007, T010-T012, T016-T017, T020) is preceded by a test task that MUST FAIL before it runs.

## Dependencies

```
T001-T002 (setup) →
  T003 (fail) → T004 (impl) → T005 (pass) →
  T006 (fail) → T007 (impl) → T008 (pass) →
  T009 (fail) → T010-T010b-T011-T012 (impl) → T013 (pass) →
  T014-T015 (fail) → T016-T017 (impl) → T018 (pass) →
  T019 (fail) → T020 (impl) → T021 (pass) →
  T022-T025 (full gate) → T026-T029 (polish)
```
