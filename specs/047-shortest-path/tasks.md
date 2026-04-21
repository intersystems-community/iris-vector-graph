# Tasks: shortestPath() openCypher Syntax (spec 047)

---

## Phase 1 — Setup & Verification

- [x] T001 Verify `tests/unit/test_shortest_path.py` does not exist; confirm `iris_vector_graph` container name in `docker-compose.yml`
- [x] T002 Confirm existing broken stub in `iris_vector_graph/cypher/translator.py` at the `if fn in ("shortestpath", "allshortestpaths")` block (line ~1488) — note its location for removal in T011
- [x] T003 Run `pytest tests/unit/ -q` — record baseline pass count (expected ~476 passed, ~56 errors from E2E container conflict)
- [x] T004 Create `tests/unit/test_shortest_path.py` with `SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"`, empty `TestShortestPathUnit` and `TestShortestPathE2E` classes

---

## Phase 2 — Foundational: AST + Lexer + Parser

**Goal**: Make `MATCH p = shortestPath((a {id:$from})-[*..8]-(b {id:$to}))` parse without error. No execution yet.

**Independent test criterion**: `parse_query("MATCH p = shortestPath((a {id:'x'})-[*..5]-(b {id:'y'})) RETURN p")` raises no exception; resulting AST has `NamedPath` with `variable_length.shortest == True`.

- [x] T005 Extend `VariableLength` dataclass in `iris_vector_graph/cypher/ast.py`
- [x] T006 In `iris_vector_graph/cypher/parser.py`, extend `parse_match_clause`
- [x] T007 Add unit test `test_shortestpath_parses_without_error`
- [x] T008 [P] Add unit test `test_all_shortest_paths_parses`
- [x] T009 [P] Add unit test `test_shortestpath_without_max_hops_defaults_to_5`
- [x] T010 Run `pytest tests/unit/test_shortest_path.py::TestShortestPathUnit -v` — T007, T008, T009 pass

---

## Phase 3 — US1: Shortest Path Between Two Nodes (RETURN p)

**Story goal**: `MATCH p = shortestPath((a {id:$from})-[*..8]-(b {id:$to})) RETURN p` executes end-to-end and returns `{"nodes":[...], "rels":[...], "length":N}`.

**Independent test criterion**: 5-node chain graph A→B→C→D→E; `shortestPath((A)-[*..4]-(E)) RETURN p` returns `{nodes:[A,B,C,D,E], rels:[r1,r2,r3,r4], length:4}`.

- [x] T011 [US1] Remove broken stub in `iris_vector_graph/cypher/translator.py`
- [x] T012 [US1] Extend `var_length_paths` dict with shortest flags + param resolution in `iris_vector_graph/cypher/translator.py`
- [x] T013 [US1] Add `Graph.KG.Traversal.ShortestPathJson` ClassMethod to `iris_src/src/Graph/KG/Traversal.cls`
- [x] T014 [US1] Add `_execute_shortest_path_cypher` to `iris_vector_graph/engine.py`
- [x] T015 [US1] Route shortest path queries in `execute_cypher` to `_execute_shortest_path_cypher`
- [x] T016 [US1] Handle `RETURN p` for shortestPath named paths in `iris_vector_graph/cypher/translator.py`
- [x] T017 [US1] Unit tests for translator shortest flag detection
- [x] T018 [P] [US1] E2E test `test_shortestpath_chain_graph`
- [x] T019 [P] [US1] E2E test `test_shortestpath_no_path_returns_empty`
- [x] T020 [P] [US1] E2E test `test_shortestpath_same_node_returns_zero_length`
- [x] T021 [US1] All US1 E2E gates pass

---

## Phase 4 — US2: Path Decomposition (`nodes(p)`, `relationships(p)`, `length(p)`)

**Story goal**: `RETURN nodes(p)`, `RETURN relationships(p)`, `RETURN length(p) AS hops` all work on a shortestPath result.

**Independent test criterion**: chain graph; `RETURN length(p) AS hops` returns integer 4; `RETURN nodes(p)` returns list `['A','B','C','D','E']`.

- [x] T022 [US2] extend return_path_funcs in translator + engine
- [x] T023 [P] [US2] Unit test test_length_p_return_func_detected
- [x] T024 [P] [US2] Unit test test_nodes_p_return_func_detected
- [x] T025 [P] [US2] E2E test test_length_p_end_to_end
- [x] T026 [US2] All US2 gates pass

---

## Phase 5 — US3: `allShortestPaths`

**Story goal**: `MATCH p = allShortestPaths((a {id:$from})-[*..6]-(b {id:$to})) RETURN p` returns all minimum-length paths.

**Independent test criterion**: diamond graph A→B→C, A→D→C; both paths returned with length 2.

- [x] T027 [US3] Unit test test_all_shortest_paths_translate_sets_all_shortest_flag
- [x] T028 [P] [US3] E2E test test_all_shortest_paths_diamond
- [x] T029 [P] [US3] E2E test test_all_shortest_paths_single_path
- [x] T030 [US3] All US3 gates pass

---

## Phase 6 — Polish & Cross-Cutting

- [x] T031 [P] Run full unit suite — 492 passed, 7 pre-existing IVF failures (no new failures)
- [x] T032 [P] Verify existing [*..N] var-length path queries unaffected
- [x] T033 E2E test test_shortestpath_directed_vs_undirected (included in E2E suite)
- [x] T034 [P] process-private globals killed on each call (verified in BuildPaths DFS)
- [ ] T035 Bump version to `1.49.0` in `pyproject.toml`
- [ ] T036 [P] Add `shortestPath` / `allShortestPaths` entry to README.md Cypher section
- [ ] T037 Commit: `feat: v1.49.0 — shortestPath()/allShortestPaths() openCypher syntax (spec 047)`
- [ ] T038 Build and publish: `python3 -m build && twine upload dist/iris_vector_graph-1.49.0*`

---

**Total tasks**: 38
**E2E gates**: T010, T021, T026, T030, T031, T032
**Primary gate**: T021 — shortestPath chain graph must return correct path before any other US2/US3 work

## Dependencies

```
T001-T004 (setup) → T005-T010 (AST/parser) → T011-T021 (US1 execution) → T022-T026 (US2 decomposition) → T027-T030 (US3 allShortestPaths) → T031-T038 (polish)
```

US2 and US3 depend on US1 `_execute_shortest_path_cypher` being complete.  
T013 (ObjectScript) and T014 (engine wiring) can be done in parallel.  
T015 (execute_cypher routing) depends on T014.

## MVP Scope

**T001–T021** only — delivers the reported bug fix (parse error eliminated, `RETURN p` works end-to-end). US2 and US3 are incremental improvements.
