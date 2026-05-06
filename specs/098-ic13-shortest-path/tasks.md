# Tasks: Spec 098 — IC13 Shortest Path

## Phase 1: Failing Tests First

- [ ] T001 Write `tests/e2e/test_ic13_shortest_path.py` — SC-001 through SC-005: correctness vs ShortestPathJson, 3-hop ≤20ms p50, far-pair ≤200ms, Cypher shortestPath() works, no regression
- [ ] T002 Run tests — confirm RED on enterprise port 4972

## Phase 2: ShortestPathNKG (ObjectScript)

- [ ] T003 Add `ClassMethod ShortestPathNKG(srcId, dstId, maxHops=10) As %String` to `iris_src/src/Graph/KG/NKGAccel.cls` — bidirectional BFS over `^NKG(-1,...)` outbound and `^NKG(-2,...)` inbound adjacency, returns `{"hops":N}` or `{"hops":-1}`, length-only (no path reconstruction)
- [ ] T004 Compile `Graph.KG.NKGAccel.cls` on `iris-enterprise-2026` via docker cp + iris session load

## Phase 3: Cypher shortestPath() support

- [ ] T005 Update `iris_vector_graph/cypher/translator.py` — detect `shortestPath(pattern)` function call in MATCH clause, extract src/dst node variables and predicate, emit call to `Graph.KG.NKGAccel.ShortestPathNKG` via SQL stored proc or classMethodString

## Phase 4: Validation

- [ ] T006 Run `IRIS_PORT=4972 pytest tests/e2e/test_ic13_shortest_path.py -v` — all GREEN
- [ ] T007 Run IC13 benchmark: 200 random SF1 pairs, record p50/p90/min/max
- [ ] T008 Update `specs/098-ic13-shortest-path/spec.md` with measured results

## Dependencies

```
T001-T002 (failing tests — RED required)
    ↓
T003-T004 (ObjectScript — compile before testing)
T005 (Cypher — parallel with T003-T004)
    ↓
T006-T008 (validation — needs T003 + T004)
```
