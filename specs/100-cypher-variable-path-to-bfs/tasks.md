# Tasks: Spec 100 — Cypher Variable-Length Path to BFS

## Phase 1: Failing Tests

- [ ] T001 Write `tests/e2e/test_cypher_vl_path_bfs.py` — SC-001 through SC-005: speed <10ms, no crash at d=3, results match BFS, DISTINCT works, LIMIT passes through
- [ ] T002 Run tests on enterprise port 4972 with SF10 knows loaded — confirm RED

## Phase 2: Understand Current Code

- [ ] T003 Read `iris_vector_graph/cypher/translator.py` lines 680-730 (first var_length handler) and lines 2005-2030 (second pass) — understand exactly what SQL is generated and where to intercept
- [ ] T004 Read `iris_vector_graph/cypher/ast.py` lines 69-100 — understand VariableLength and RelationshipPattern data model

## Phase 3: Implement BFS Routing

- [ ] T005 Add `_translate_vl_path_as_bfs(match_clause, context)` function in `iris_vector_graph/cypher/translator.py` — extracts src_id, predicates, max_hops, min_hops, direction from AST; returns a `BFSTranslation` object (or inline execution plan)
- [ ] T006 Modify BOTH var_length_path handlers in `iris_vector_graph/cypher/translator.py`: line ~689 (first pass — CTE generation) AND line ~2012 (second pass — `context.var_length_paths.append`) to call `_translate_vl_path_as_bfs` when `rel.variable_length is not None`
- [ ] T007 Implement BFS execution in `execute_cypher` — when result has `bfs_plan`, call `_call_classmethod_large(iris_obj, "Graph.KG.NKGAccel", "BFSJson", ...)` and map `{s,p,o,w,step}` results to query variable bindings
- [ ] T008 Handle RETURN clause mapping: `b.node_id` → `r['o']`, `step` → `r['step']`, `count(b)` → `len({r['o'] for r in results})`, `DISTINCT b.node_id` → deduplicate on `r['o']`
- [ ] T009 Handle min_hops filter: post-filter results where `r['step'] < min_hops`
- [ ] T010 Handle LIMIT: pass as `max_results` param to BFSJson (not post-filter, for efficiency)

## Phase 4: Edge Cases

- [ ] T011 [P] Handle `[*]` (no bounds) → max_hops=10 default
- [ ] T012 [P] Handle `[*2]` (exact hop) → min_hops=max_hops=2
- [ ] T013 [P] Handle multiple predicates `[:A|B*1..2]` → pass `["A","B"]` to BFSJson
- [ ] T014 [P] Handle direction `-->` (out), `<--` (in), `--` (both) → pass to BFSJson direction param

## Phase 5: Validation

- [ ] T015 Run `IRIS_PORT=4972 pytest tests/e2e/test_cypher_vl_path_bfs.py -v` — ALL GREEN
- [ ] T016 Run full existing Cypher test suite — no regressions on single-hop queries
- [ ] T017 Benchmark: measure `[*1..2]` LIMIT 50 on SF10 — must be <10ms p50
- [ ] T018 Benchmark: measure `[*1..3]` count on SF10 — must not crash, <20ms p50
- [ ] T019 Update `specs/100-cypher-variable-path-to-bfs/spec.md` with measured results

## Dependencies

```
T001-T002 (failing tests — RED required first)
    ↓
T003-T004 (read existing code — understand before changing)
    ↓
T005-T010 (core implementation — sequential)
T011-T014 (edge cases — parallel)
    ↓
T015-T019 (validation)
```
