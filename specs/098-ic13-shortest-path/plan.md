# Implementation Plan: Spec 098 — IC13 Shortest Path

**Branch**: `098-ic13-shortest-path`
**Date**: 2026-05-04
**Spec**: `specs/098-ic13-shortest-path/spec.md`

## Summary

Add `ShortestPathNKG` to `Graph.KG.NKGAccel` using bidirectional BFS over `^NKG`
integer adjacency. Add Cypher `shortestPath()` support to the translator. Target:
3-hop IC13 from 175ms → ~5ms.

## Technical Context

**Language**: ObjectScript (IRIS)  
**File**: `iris_src/src/Graph/KG/NKGAccel.cls`  
**Also**: `iris_vector_graph/cypher/translator.py` for Cypher support  
**Test target**: `iris-enterprise-2026` (port 4972, IRIS 2026.1 Build 234)  
**Data**: LDBC SF1 knows graph (9,163 persons, 180K edges) loaded at `/tmp/ldbc_sf1/`

## Algorithm: Bidirectional BFS over ^NKG

Forward BFS from `srcId`, backward BFS from `dstId`, meeting in the middle.

```
fwdSeen(srcIdx)=0, bwdSeen(dstIdx)=0
For hop = 1:1:maxHops {
    Expand forward frontier one hop via ^NKG(-1, nodeIdx, pred, dst)
    If any fwd node appears in bwdSeen → return fwdDepth + bwdDepth
    Expand backward frontier one hop via ^NKG(-2, nodeIdx, pred, src)
    If any bwd node appears in fwdSeen → return fwdDepth + bwdDepth
}
```

Key: `^NKG(-2,...)` is the INBOUND adjacency — enables backward BFS without
full graph reversal.

## Phases

### Phase 1: Failing tests
Write `tests/e2e/test_ic13_shortest_path.py` — must be RED before implementation.

### Phase 2: ShortestPathNKG (ObjectScript)
Add to `NKGAccel.cls`. Returns `{"hops": N}` or `{"hops": -1}`.

### Phase 3: Cypher shortestPath() support  
Add to `translator.py`: detect `shortestPath(...)` in MATCH pattern, route to
`ShortestPathNKG` via stored proc call.

### Phase 4: Compile + deploy on iris-enterprise-2026

### Phase 5: Validate + benchmark IC13

## Constitution Check

- [x] Test-first: failing tests before ObjectScript changes
- [x] Container: `iris-enterprise-2026` port 4972 (stable compiler)
- [x] `SKIP_ARNO_TESTS` env var respected
