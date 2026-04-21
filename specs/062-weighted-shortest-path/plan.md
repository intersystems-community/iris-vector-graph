# Plan: Weighted Shortest Path (spec 062)

**Branch**: 062-weighted-shortest-path | **Date**: 2026-04-19

## Summary

Three components:
1. `Graph.KG.Traversal.DijkstraJson` ObjectScript ClassMethod — Dijkstra using `^||Dij.*` process-private globals for priority queue (same pattern as `ShortestPathJson`)
2. `_translate_weighted_shortest_path` in translator.py — Stage CTE exactly like `_translate_ivf_search`
3. `_execute_weighted_shortest_path` in engine.py — calls `DijkstraJson`, builds result

## Dijkstra Implementation (ObjectScript)

Priority queue: `^||Dij.pq(cost, node) = ""` — IRIS globals auto-sort by subscript, giving us a min-heap for free.
Parent tracking: `^||Dij.parent(node) = $ListBuild(parentNode, relType, edgeCost)`
Visited: `^||Dij.seen(node) = ""` to avoid re-processing.

Weight lookup per edge `(s, p, o)`:
1. Try `^KG("out", 0, s, p, o)` — numeric value stored there
2. If weightProp non-empty, query rdf_edges via SQL for qualifiers JSON key
3. Default 1.0

```
ClassMethod DijkstraJson(src, dst, weightProp, maxCost, maxHops, direction) As %String
  Kill ^||Dij.pq, ^||Dij.parent, ^||Dij.seen
  If src = dst → return zero-path
  Set ^||Dij.pq(0, src) = ""
  For {
    Set minCost = $Order(^||Dij.pq(""))  Quit:minCost=""
    Set node = $Order(^||Dij.pq(minCost, ""))  Quit:node=""
    Kill ^||Dij.pq(minCost, node)
    If $Data(^||Dij.seen(node)) Continue
    Set ^||Dij.seen(node) = ""
    If node = dst → backtrack and return
    If minCost > maxCost Continue
    Expand neighbors from ^KG("out", 0, node, p, o) [and ^KG("in"...) if both]
    For each neighbor:
      w = ^KG("out",0,node,p,neighbor) value or 1.0
      If weightProp non-empty, lookup qualifiers JSON
      newCost = minCost + w
      If newCost <= maxCost and NOT seen:
        Set ^||Dij.pq(newCost, neighbor) = ""
        Set ^||Dij.parent(neighbor) = $LB(node, p, w)
  }
  Kill ^||Dij.pq, ^||Dij.parent, ^||Dij.seen
  Return "[]"
```

## Translator CTE

Identical to `_translate_ivf_search` / `_translate_bm25_search` pattern.
Arguments: `(from_id, to_id, weightProp, maxCost, maxHops)`
Emits: `WS AS (SELECT j.node, j.totalCost, j.path FROM JSON_TABLE(Graph_KG.DijkstraProc(...), ...) j)`

Actually since DijkstraJson returns a single JSON object (not array of rows), simpler to route through `_execute_shortest_path_cypher` pattern in engine.py.

## Files Changed

```
iris_src/src/Graph/KG/Traversal.cls   — ADD DijkstraJson ClassMethod
iris_vector_graph/cypher/translator.py — ADD _translate_weighted_shortest_path
iris_vector_graph/engine.py            — ADD _execute_weighted_shortest_path
tests/unit/test_weighted_shortest_path.py — NEW E2E tests
```

## Constitution Check
- [x] E2E test fails before implementation, passes after
- [x] SKIP_IRIS_TESTS guard
- [x] No hardcoded ports
