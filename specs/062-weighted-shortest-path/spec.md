# Spec 062: Weighted Shortest Path (Dijkstra)

**Branch**: `062-weighted-shortest-path`
**Created**: 2026-04-19

## Overview

`shortestPath()` uses BFS — minimum hops. Many real graphs need minimum-cost path: lowest latency route through service call graphs, highest-confidence path through biological networks, cheapest path through cost-weighted ontologies.

This adds `CALL ivg.shortestPath.weighted(from, to, weightProp, maxCost, maxHops) YIELD node, cost, path, totalCost` — Dijkstra in pure ObjectScript over `^KG("out", 0, s, p, o)` globals. Consistent with the existing `ivg.*` procedure pattern.

## Clarifications

### Session 2026-04-19
- Q: Weight source? → A: `weightProp` names a JSON qualifier key from `rdf_edges.qualifiers`; if `weightProp = 'weight'` uses the numeric value stored in `^KG("out", 0, s, p, o)`; if property not found defaults to 1.0 (unit weight = BFS equivalent)
- Q: Direction support? → A: Both directed (default) and undirected via `direction` parameter matching `ShortestPathJson`
- Q: Return format? → A: YIELD `node` (target node id), `totalCost` (float), `path` (JSON `{nodes:[...], rels:[...], costs:[...], length:N, totalCost:F}`)
- Q: Multiple paths? → A: Single minimum-cost path only (Dijkstra terminates at first arrival at target); `allShortestPaths` weighted variant is out of scope

## User Scenarios & Testing

### User Story 1 — Minimum cost path (P1)

```cypher
CALL ivg.shortestPath.weighted(
  'svc:auth', 'svc:db',
  'latency_ms', 9999, 10
) YIELD path, totalCost
RETURN path, totalCost
```

Returns the path with lowest total latency, not fewest hops.

**E2E test MUST fail before implementation**: create graph where minimum-hop path has higher total cost than a longer path; assert weighted result returns the lower-cost path.

**Acceptance Scenarios**:
1. Graph: A→B (weight=1), A→C→B (weight=0.5+0.1=0.6); weighted returns A→C→B with cost=0.6
2. No path within maxCost → empty result, no error
3. weightProp not found → falls back to unit weight (same as unweighted shortestPath)
4. `direction='both'` traverses undirected

### User Story 2 — Integration with RETURN and filtering (P2)

```cypher
CALL ivg.shortestPath.weighted($from, $to, 'confidence', 1.0, 8)
YIELD path, totalCost
WHERE totalCost < 0.8
RETURN path.nodes AS route, totalCost
```

### Edge Cases
- Negative weights → raise ValueError with clear message (Dijkstra doesn't support negative weights)
- source == target → return `{nodes:[src], rels:[], costs:[], length:0, totalCost:0}`
- Cycle in graph → terminates (Dijkstra visited set prevents re-visiting)
- `maxCost = 0` → only returns zero-cost paths (source==target)

## Requirements

- **FR-001**: `CALL ivg.shortestPath.weighted(from, to, weightProp, maxCost, maxHops)` MUST be registered as a Cypher procedure
- **FR-002**: `Graph.KG.Traversal.DijkstraJson(srcId, dstId, weightProp, maxCost, maxHops, direction)` ObjectScript method MUST implement Dijkstra with process-private globals for priority queue
- **FR-003**: Weight lookup: first check `^KG("out", 0, s, p, o)` numeric value; if `weightProp` is non-empty also check `rdf_edges.qualifiers` JSON for that key
- **FR-004**: YIELD columns: `node` (target id), `totalCost` (float), `path` (JSON string)
- **FR-005**: `path` JSON MUST include `nodes`, `rels`, `costs` (per-hop), `length`, `totalCost`
- **FR-006**: Negative weights MUST raise ValueError
- **FR-007**: `direction` parameter: `"out"` (directed, default) or `"both"` (undirected)
- **FR-008**: Falls back to unit weight 1.0 when weightProp not found on an edge

## Success Criteria
- **SC-001**: Lower-cost longer path preferred over higher-cost shorter path
- **SC-002**: `totalCost` equals sum of per-hop weights
- **SC-003**: 527+ existing tests pass with zero regressions
