# Tasks: Spec 097 — Lazy Node Name Resolution

## Phase 1: Failing Tests First (Principle III)

- [X] T001 Write `tests/e2e/test_lazy_node_resolution.py` — SC-001 through SC-006
- [X] T002 Run failing tests on enterprise — confirmed RED (5 failed, 2 passed)

## Phase 2: NodeResolver Struct

- [ ] T003 Add `pub struct NodeResolver` to `native_algos` mod in `arno/iris-integration/arno-callout/src/kg_ffi.rs` — fields: `cache: HashMap<usize,String>`, `ns: NameSpace`, `n: usize`
- [ ] T004 Add `impl NodeResolver` with `new(ns, n)` (zero reads), `name(&mut self, idx)` (cache-on-miss via `^NKG("$ND",idx)`), `lookup_seed(&self, seed)` (one read via `^NKG("$NI",seed)`), `len(&self)`

## Phase 3: read_nkg_adjacency Return Type

- [ ] T005 Change `read_nkg_adjacency()` return type from `Result<(Vec<String>, Vec<Vec<usize>>), String>` to `Result<(NodeResolver, Vec<Vec<usize>>), String>` — replace `read_node_dictionary(&ns, n)` call with `NodeResolver::new(ns, n)`

## Phase 4: Algorithm Signature Updates

- [ ] T006 [P] Update `bfs_on_adj` signature: `nodes: &[String]` → `resolver: &mut NodeResolver` — internals: `nodes.iter().position(seed)` → `resolver.lookup_seed(seed)?`, `nodes[src]`/`nodes[dst]` → `resolver.name(src)`/`resolver.name(dst)`
- [ ] T007 [P] Update `ppr_on_adj` signature and internals — same pattern as T006
- [ ] T008 [P] Update WCC function signature and internals
- [ ] T009 [P] Update CDLP function signature and internals
- [ ] T010 [P] Update random walk function signature and internals
- [ ] T011 [P] Update subgraph extraction function signature and internals

## Phase 5: Caller Updates

- [ ] T012 Update `ffi_kg_bfs_compute`: `let (nodes, out_adj)` → `let (mut resolver, out_adj)`, pass `&mut resolver` to `bfs_on_adj`
- [ ] T013 Update `ffi_kg_bfs_global`: same as T012
- [ ] T014 Update `ffi_kg_ppr_global`: `let (nodes, ...)` → `let (mut resolver, ...)`, pass `&mut resolver` to `ppr_on_adj`
- [ ] T015 [P] Update WCC/CDLP/random walk `ffi_*` callers — same pattern

## Phase 6: Build + Deploy

- [ ] T016 `cd /Users/tdyar/ws/arno && touch iris-integration/arno-callout/src/kg_ffi.rs && bash build_all_arno.sh arm64` — must complete with 0 errors
- [ ] T017 Verify DEBUG/CHUNKED strings still in binary: `strings libarno_callout_arm64_linux.so | grep -E "CHUNKED:BFS|DEBUG:n="`
- [ ] T018 Deploy: `docker cp /Users/tdyar/ws/arno/libarno_callout_arm64_linux.so iris-vector-graph-enterprise:/usr/irissys/mgr/libarno_callout.so`

## Phase 7: Validation on Enterprise

- [ ] T019 Run `IRIS_PORT=2972 pytest tests/e2e/test_lazy_node_resolution.py tests/e2e/test_arno_bfs_global.py tests/e2e/test_large_output_chunked.py -v` — ALL GREEN, 0 failures
- [ ] T020 XL BFS cold benchmark: load 1M/10M via BulkIngestEdges, cold BFS d=1 < 500ms, hot d=1..10 < 100ms each
- [ ] T021 Mark all tasks complete, update spec 097 with measured cold/hot numbers

## Dependencies

```
T001-T002 (failing tests — must be RED)
    ↓
T003-T004 (NodeResolver struct — sequential)
    ↓
T005 (read_nkg_adjacency return type)
    ↓
T006-T011 (algorithm signatures — parallel within phase)
    ↓
T012-T015 (caller updates — T012/T013 sequential, T014/T015 parallel)
    ↓
T016-T018 (build + deploy — sequential)
    ↓
T019-T021 (validation — sequential)
```
