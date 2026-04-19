# Tasks: Weighted Shortest Path (spec 062)

## Phase 1 — Setup

- [ ] T001 Run `pytest tests/unit/ -q` — baseline 527 passed
- [ ] T002 Create `tests/unit/test_weighted_shortest_path.py` with SKIP_IRIS_TESTS guard and empty TestWeightedShortestPathE2E class

## Phase 2 — E2E test (must fail before implementation)

- [ ] T003 Add E2E test `test_weighted_prefers_lower_cost_longer_path`: create nodes A, B, C, D; edges A→B (weight=10.0), A→C (weight=1.0), C→B (weight=1.0); run `CALL ivg.shortestPath.weighted('A', 'B', 'weight', 99, 5) YIELD path, totalCost RETURN path, totalCost`; assert totalCost == 2.0 and path contains C — run now, confirm FAILS (procedure unknown)
- [ ] T004 [P] Add E2E test `test_weighted_no_path_returns_empty`: disconnected nodes; assert empty result
- [ ] T005 [P] Add E2E test `test_weighted_source_equals_target`: assert length=0, totalCost=0.0
- [ ] T006 [P] Add E2E test `test_weighted_fallback_to_unit_weight`: edges with no weight stored; `weightProp='nonexistent'`; assert returns a path (falls back to unit weight, same as BFS)

## Phase 3 — ObjectScript DijkstraJson

- [ ] T007 Add `DijkstraJson(srcId, dstId, weightProp, maxCost, maxHops, direction)` ClassMethod to `iris_src/src/Graph/KG/Traversal.cls` — Dijkstra using `^||Dij.pq(cost, node)` as auto-sorted priority queue, `^||Dij.parent(node) = $LB(parent, relType, edgeCost)` for backtracking, `^||Dij.seen(node)` for visited set; weight = `^KG("out", 0, s, p, o)` value cast to float; if weightProp non-empty, also try `rdf_edges.qualifiers` JSON key via embedded SQL (optional, skip on error); return JSON `{"nodes":[...],"rels":[...],"costs":[...],"length":N,"totalCost":F}` or `{}` if no path; Kill all `^||Dij.*` before return; compile into container
- [ ] T008 Add `DijkstraProc(srcId, dstId, weightProp, maxCost, maxHops, direction) As %String [SqlProc, SqlName = DijkstraPath]` ClassMethod that calls `DijkstraJson` — needed for the SQL proc path; compile

## Phase 4 — Translator + Engine

- [ ] T009 Add `_translate_weighted_shortest_path(proc, context)` to `iris_vector_graph/cypher/translator.py` following `_translate_bm25_search` pattern: validate 5 args (from, to, weightProp, maxCost, maxHops); emit engine-execution path same as shortestPath (set `var_length_paths` with `weighted=True` flag); register YIELD items `node`, `totalCost`, `path`
- [ ] T010 Wire `ivg.shortestPath.weighted` into procedure dispatch in `translator.py`
- [ ] T011 Add `_execute_weighted_shortest_path(sql_query, parameters)` to `iris_vector_graph/engine.py`: extract args from `var_length_paths[0]`; call `classMethodValue("Graph.KG.Traversal", "DijkstraJson", ...)`, parse result; build columns from `return_path_funcs`
- [ ] T012 Route to `_execute_weighted_shortest_path` in `execute_cypher` when `var_length_paths[0].get("weighted")`

## Phase 5 — Gate

- [ ] T013 Run `pytest tests/unit/test_weighted_shortest_path.py -v` — T003-T006 all PASS
- [ ] T014 [P] Run `pytest tests/unit/ -q` — 527+ passed, zero regressions

## Phase 6 — Polish

- [ ] T015 Bump version to 1.56.0
- [ ] T016 Commit and publish: `feat: v1.56.0 — ivg.shortestPath.weighted Dijkstra procedure (spec 062)`

**Dependencies**: T003-T006 (failing tests) → T007-T008 (ObjectScript) → T009-T012 (Python) → T013-T014 (gate) → T015-T016
