# Tasks: shortestPath() openCypher Syntax (spec 047)

---

## Phase 1 — Setup & Verification

- [ ] T001 Verify `tests/unit/test_shortest_path.py` does not exist; confirm `iris_vector_graph` container name in `docker-compose.yml`
- [ ] T002 Confirm existing broken stub in `iris_vector_graph/cypher/translator.py` at the `if fn in ("shortestpath", "allshortestpaths")` block (line ~1488) — note its location for removal in T011
- [ ] T003 Run `pytest tests/unit/ -q` — record baseline pass count (expected ~476 passed, ~56 errors from E2E container conflict)
- [ ] T004 Create `tests/unit/test_shortest_path.py` with `SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"`, empty `TestShortestPathUnit` and `TestShortestPathE2E` classes

---

## Phase 2 — Foundational: AST + Lexer + Parser

**Goal**: Make `MATCH p = shortestPath((a {id:$from})-[*..8]-(b {id:$to}))` parse without error. No execution yet.

**Independent test criterion**: `parse_query("MATCH p = shortestPath((a {id:'x'})-[*..5]-(b {id:'y'})) RETURN p")` raises no exception; resulting AST has `NamedPath` with `variable_length.shortest == True`.

- [ ] T005 Extend `VariableLength` dataclass in `iris_vector_graph/cypher/ast.py`: add `shortest: bool = False` and `all_shortest: bool = False` fields; relax `__post_init__` to allow `max_hops ≤ 15` when `shortest or all_shortest is True`
- [ ] T006 In `iris_vector_graph/cypher/parser.py`, extend `parse_match_clause` (line ~247): before calling `parse_graph_pattern()`, check if `self.peek().kind == TokenType.IDENTIFIER and self.peek().value in ("shortestPath", "allShortestPaths")`; if so: (1) consume the function name token, (2) consume `LPAREN`, (3) call `parse_graph_pattern()` to get the inner pattern, (4) consume `RPAREN`, (5) set `shortest=True` or `all_shortest=True` on the inner pattern's relationship `variable_length` (creating a `VariableLength` with default `min_hops=1, max_hops=5` if none was specified), (6) wrap in `NamedPath` if `path_var` was set
- [ ] T007 Add unit test `test_shortestpath_parses_without_error` in `TestShortestPathUnit` (`tests/unit/test_shortest_path.py`): call `parse_query` on the mindwalk reported query `"MATCH p = shortestPath((a {id:'hla-a*02:01'})-[*..8]-(b {id:'DOID:162'})) RETURN p"`; assert no exception; assert result has a `NamedPath` with `variable_length.shortest == True`
- [ ] T008 [P] Add unit test `test_all_shortest_paths_parses` in `TestShortestPathUnit`: assert `parse_query("MATCH p = allShortestPaths((a {id:'x'})-[*..6]-(b {id:'y'})) RETURN p")` parses cleanly with `all_shortest == True`
- [ ] T009 [P] Add unit test `test_shortestpath_without_max_hops_defaults_to_5` in `TestShortestPathUnit`: parse `"MATCH p = shortestPath((a {id:'x'})--( b {id:'y'})) RETURN p"`; assert `variable_length.max_hops == 5`
- [ ] T010 Run `pytest tests/unit/test_shortest_path.py::TestShortestPathUnit -v` — T007, T008, T009 pass

---

## Phase 3 — US1: Shortest Path Between Two Nodes (RETURN p)

**Story goal**: `MATCH p = shortestPath((a {id:$from})-[*..8]-(b {id:$to})) RETURN p` executes end-to-end and returns `{"nodes":[...], "rels":[...], "length":N}`.

**Independent test criterion**: 5-node chain graph A→B→C→D→E; `shortestPath((A)-[*..4]-(E)) RETURN p` returns `{nodes:[A,B,C,D,E], rels:[r1,r2,r3,r4], length:4}`.

- [ ] T011 [US1] Remove broken stub in `iris_vector_graph/cypher/translator.py` (the `if fn in ("shortestpath", "allshortestpaths"):` block at line ~1488 that imports `generate_shortest_path_sql`); replace with a no-op pass comment so the function falls through to normal handling
- [ ] T012 [US1] In `iris_vector_graph/cypher/translator.py`, extend the `var_length_paths` dict appended in `translate_match_clause` (line ~1102): add `"shortest": rel.variable_length.shortest`, `"all_shortest": rel.variable_length.all_shortest`, and `"direction": "both" if rel.direction == ast.Direction.BOTH else "out"`; also extract the source and target node property filter values at translation time — from the source node's `properties.get("id")` and target node's `properties.get("id")` — and store as `"src_id_param": src_val` and `"dst_id_param": dst_val` where each value is either a literal string or a parameter name prefixed with `$` (e.g. `"$from"`); if a node has no `id` property filter, store `None` and T014 will raise a clear error at execution time
- [ ] T013 [US1] Add `Graph.KG.Traversal.ShortestPathJson(srcId As %String, dstId As %String, maxHops As %Integer, predsJson As %String, direction As %String, findAll As %Integer)` ClassMethod to `iris_src/src/Graph/KG/Traversal.cls`: `direction` is `"out"` (follow `^KG("out",...)` only) or `"both"` (follow both `^KG("out",...)` and `^KG("in",...)`); `findAll` is `0` (stop at first path) or `1` (collect all paths of minimum length); (1) if `srcId = dstId` return `[{"nodes":["srcId"], "rels":[], "length":0}]`; (2) BFS using process-private globals: `^||SP.parent(nodeId) = $ListBuild(parentId, relType)` for parent pointers, `^||SP.frontier(nodeId) = ""` for current frontier, `^||SP.seen(nodeId) = ""` for visited set; (3) expand frontier: for each node in frontier, iterate `^KG("out", s, p, o)` always; if `direction = "both"` also iterate `^KG("in", s, p, o)` (treating `s` as the other endpoint); skip if `predsJson` is non-empty and `p` is not in parsed predicate list; skip if already in `^||SP.seen`; record `^||SP.parent(o) = $ListBuild(s, p)` on first visit; (4) when `dstId` reached: backtrack `^||SP.parent` chain from `dstId` to `srcId`, reverse to get ordered `nodes` and `rels` arrays; (5) if `findAll = 0` → return immediately as `[{"nodes":[...],"rels":[...],"length":N}]`; if `findAll = 1` → complete the current BFS depth level collecting all paths to `dstId` at that depth, then stop; (6) Kill `^||SP.parent`, `^||SP.frontier`, `^||SP.seen` before returning; return `[]` if no path found; compile into `iris_vector_graph` container
- [ ] T014 [US1] Add `_execute_shortest_path_cypher(self, sql_query, parameters)` method to `IRISGraphEngine` in `iris_vector_graph/engine.py`: (1) extract `vl = sql_query.var_length_paths[0]`; (2) resolve `source_id`: if `vl["src_id_param"]` starts with `$`, look up the parameter name (without `$`) in the `parameters` dict; otherwise use the literal value directly; same for `target_id` from `vl["dst_id_param"]`; raise `ValueError("shortestPath requires both source and target node IDs to be bound")` if either resolves to `None`; (3) call `_call_classmethod(self.conn, "Graph.KG.Traversal", "ShortestPathJson", source_id, target_id, vl["max_hops"], preds_json, vl["direction"], 1 if vl["all_shortest"] else 0)`; (4) parse JSON response; (5) return `{"columns": ["p"], "rows": [[json.dumps({"nodes": row["nodes"], "rels": row["rels"], "length": row["length"]})]] for row in results}` or `{"columns": ["p"], "rows": []}` if empty
- [ ] T015 [US1] In `iris_vector_graph/engine.py`, extend `execute_cypher` (line ~585): in the `if sql_query.var_length_paths:` branch, check `if sql_query.var_length_paths[0].get("shortest") or sql_query.var_length_paths[0].get("all_shortest")` → call `self._execute_shortest_path_cypher(sql_query, parameters)` instead of `self._execute_var_length_cypher`
- [ ] T016 [US1] In `iris_vector_graph/cypher/translator.py`, extend the `translate_return_clause` handler for path variables: when RETURN item variable is in `context.named_paths` AND the named path has `variable_length.shortest == True`, serialize result as `JSON_OBJECT('nodes', ..., 'rels', ..., 'length', ...)` — but since execution is engine-side, just ensure the RETURN clause emits the path variable name as a pass-through column so `_execute_shortest_path_cypher` can map it
- [ ] T017 [US1] Add unit test `test_shortestpath_translate_sets_shortest_flag` in `TestShortestPathUnit`: call `translate_to_sql` on a shortestPath query; assert `sql_obj.var_length_paths[0]["shortest"] == True` and `sql_obj.var_length_paths[0]["direction"] in ("both", "outgoing")`
- [ ] T018 [P] [US1] Add E2E test `test_shortestpath_chain_graph` in `TestShortestPathE2E`: create nodes A,B,C,D,E with edges A→B→C→D→E; run `engine.execute_cypher("MATCH p = shortestPath((a {id:'A'})-[*..5]-(b {id:'E'})) RETURN p")`; assert rows non-empty; assert result path length == 4 and nodes == [A,B,C,D,E]; cleanup
- [ ] T019 [P] [US1] Add E2E test `test_shortestpath_no_path_returns_empty` in `TestShortestPathE2E`: two disconnected nodes; assert `execute_cypher(shortestPath(...))` returns `{"rows": []}`, no exception
- [ ] T020 [P] [US1] Add E2E test `test_shortestpath_same_node_returns_zero_length` in `TestShortestPathE2E`: assert `shortestPath((a {id:'X'})-[*..5]-(b {id:'X'}))` returns `length:0, nodes:['X'], rels:[]`
- [ ] T021 [US1] Compile `Traversal.cls` and run `pytest tests/unit/test_shortest_path.py::TestShortestPathE2E -v -k "chain or no_path or same_node"` — T018, T019, T020 pass

---

## Phase 4 — US2: Path Decomposition (`nodes(p)`, `relationships(p)`, `length(p)`)

**Story goal**: `RETURN nodes(p)`, `RETURN relationships(p)`, `RETURN length(p) AS hops` all work on a shortestPath result.

**Independent test criterion**: chain graph; `RETURN length(p) AS hops` returns integer 4; `RETURN nodes(p)` returns list `['A','B','C','D','E']`.

- [ ] T022 [US2] In `iris_vector_graph/cypher/translator.py`, extend T012's `var_length_paths` dict to also store `"return_path_funcs": list[str]` — scan the RETURN clause items for the path variable `p` and record which path functions are requested (e.g. `["length", "nodes", "relationships"]` or `["path"]` for bare `RETURN p`); then in `iris_vector_graph/engine.py`, extend `_execute_shortest_path_cypher` to use `vl["return_path_funcs"]` to build result columns: `"path"` → JSON string `{"nodes":[...],"rels":[...],"length":N}`; `"length"` → integer; `"nodes"` → list of node ID strings; `"relationships"` → list of rel type strings; columns named by alias from RETURN clause
- [ ] T023 [P] [US2] Add unit test `test_length_p_returns_integer` in `TestShortestPathUnit`: mock engine returning `[{"nodes":["A","B","C"],"rels":["r1","r2"],"length":2}]`; call `execute_cypher("MATCH p = shortestPath(...) RETURN length(p) AS hops")`; assert `rows[0][0] == 2`
- [ ] T024 [P] [US2] Add unit test `test_nodes_p_returns_list` in `TestShortestPathUnit`: same mock; `RETURN nodes(p)`; assert `rows[0][0] == ["A","B","C"]`
- [ ] T025 [P] [US2] Add E2E test `test_length_p_end_to_end` in `TestShortestPathE2E`: chain A→B→C; `MATCH p = shortestPath((a {id:'A'})-[*..3]-(b {id:'C'})) RETURN length(p) AS hops`; assert `rows[0][0] == 2`
- [ ] T026 [US2] Run `pytest tests/unit/test_shortest_path.py -v -k "length or nodes_p"` — T023, T024, T025 pass

---

## Phase 5 — US3: `allShortestPaths`

**Story goal**: `MATCH p = allShortestPaths((a {id:$from})-[*..6]-(b {id:$to})) RETURN p` returns all minimum-length paths.

**Independent test criterion**: diamond graph A→B→C, A→D→C; both paths returned with length 2.

- [ ] T027 [US3] Add unit test `test_all_shortest_paths_translate_sets_all_shortest_flag` in `TestShortestPathUnit`: assert `translate_to_sql` on `allShortestPaths(...)` sets `var_length_paths[0]["all_shortest"] == True`
- [ ] T028 [P] [US3] Add E2E test `test_all_shortest_paths_diamond` in `TestShortestPathE2E`: create nodes A,B,C,D with edges A→B→C and A→D→C (diamond); run `allShortestPaths((a {id:'A'})-[*..3]-(b {id:'C'})) RETURN p`; assert 2 rows returned, both with length 2; cleanup
- [ ] T029 [P] [US3] Add E2E test `test_all_shortest_paths_single_path` in `TestShortestPathE2E`: chain A→B→C (no diamond); `allShortestPaths` returns exactly 1 row
- [ ] T030 [US3] Run `pytest tests/unit/test_shortest_path.py::TestShortestPathE2E -v -k "all_shortest"` — T028, T029 pass

---

## Phase 6 — Polish & Cross-Cutting

- [ ] T031 [P] Run full unit suite `pytest tests/unit/ -q` — baseline pass count maintained, 0 new failures
- [ ] T032 [P] Verify existing `[*..N]` var-length path queries still work: run `pytest tests/unit/ -q -k "var_length or traversal or neighbors"` — no regressions
- [ ] T033 Add `test_shortestpath_directed_vs_undirected` in `TestShortestPathE2E`: graph A→B→C; directed `(A)-[*..3]->(C)` finds path; undirected `(C)-[*..3]-(A)` also finds path (following in-edges); directed `(C)-[*..3]->(A)` finds nothing (no out-edges from C to A)
- [ ] T034 [P] Verify `Traversal.cls` kills all `^||SP.*` process-private globals after each call — no state leak between queries: run two sequential `ShortestPathJson` calls in the same session and assert independent results
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
