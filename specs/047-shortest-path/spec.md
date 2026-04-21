# Spec 047: shortestPath() openCypher Syntax

**Feature Branch**: `047-shortest-path`
**Created**: 2026-04-18
**Status**: Draft

## Clarifications

### Session 2026-04-18

- Q: What path decomposition functions must be supported alongside `RETURN p`? → A: Full decomposition — `RETURN p`, `nodes(p)`, `relationships(p)`, `length(p)` all supported (same as Neo4j standard)
- Q: Must the target node be a bound ID, or can it be a label filter in v1? → A: Both source and target must be bound node IDs in v1; label-only target is out of scope
- Q: Does `nodes(p)` return node ID strings or full node objects? → A: Node ID strings only; consistent with existing named path return format

## Overview

Add native `shortestPath()` and `allShortestPaths()` support to the iris-vector-graph openCypher
parser and translator. Fixes a bug reported by mindwalk where `MATCH p = shortestPath(...)` throws
`Expected (, got IDENTIFIER` at parse time. Backed by a new `Graph.KG.Traversal.ShortestPathJson`
ObjectScript method that performs BFS with parent-pointer path reconstruction.

## User Scenarios & Testing

### User Story 1 — Shortest path between two nodes (P1)

A mindwalk user finds the shortest connection between an HLA allele and a disease:
```cypher
MATCH p = shortestPath((a {id: $from})-[*..8]-(b {id: $to}))
RETURN p
```
Returns `{"nodes": ["hla-a*02:01", "GENE:123", "DOID:162"], "rels": ["ASSOCIATED_WITH", "CAUSES"], "length": 2}` or empty result if no path within 8 hops.

**Why this priority**: This is the reported bug — shortestPath is broken entirely.

**Independent Test**: 5-node graph A→B→C→D→E; `shortestPath((A)-[*..4]-(E))` returns length-4 path `[A,B,C,D,E]`.

**Acceptance Scenarios**:
1. **Given** nodes A, B, C with edges A→B→C, **When** `shortestPath((A)-[*..5]-(C))`, **Then** returns path `{nodes:[A,B,C], rels:[r1,r2], length:2}`
2. **Given** no path exists within maxHops, **When** `shortestPath(...)`, **Then** returns empty result, no error
3. **Given** source == target, **When** `shortestPath((a {id:$x})-[*..5]-(a {id:$x}))`, **Then** returns `{nodes:[$x], rels:[], length:0}`

### User Story 2 — `length(p)` in RETURN / WHERE (P2)

```cypher
MATCH p = shortestPath((a {id: $from})-[*..10]-(b {id: $to}))
RETURN length(p) AS hops
WHERE length(p) <= 3
```
Returns integer hop count. Enables filtering on path depth.

**Independent Test**: Build graph with known path of length 3; assert `length(p) == 3`.

### User Story 3 — `allShortestPaths` (P3)

```cypher
MATCH p = allShortestPaths((a {id: $from})-[*..6]-(b {id: $to}))
RETURN p
```
Returns all paths of minimum length (not just one).

**Independent Test**: Diamond graph A→B→C and A→D→C; both paths returned with length 2.

### Edge Cases

- `maxHops` not specified → default 5
- `maxHops > 15` → clamped to 15
- Disconnected graph → empty result
- Cyclic graph → BFS still terminates (visited set)
- Directed `(a)-[*..N]->(b)` → only follow out-edges
- Undirected `(a)-[*..N]-(b)` → follow both in and out edges
- `shortestPath` with no endpoint node ID bound → raise clear error: "shortestPath requires both source and target node IDs to be bound"
- Label-only target (e.g. `(b:Disease)`) without `{id: ...}` → out of scope in v1, raise clear error

## Requirements

### Functional Requirements

- **FR-001**: Parser MUST recognize `shortestPath(pattern)` and `allShortestPaths(pattern)` as path function wrappers
- **FR-002**: Both directed (`-->`) and undirected (`--`) relationship directions MUST be supported
- **FR-003**: Optional relationship type filter (`[:TYPE*..N]`) MUST be forwarded to BFS predicate filter
- **FR-004**: `length(p)` MUST return integer hop count when `p` is a shortestPath result
- **FR-005**: `nodes(p)` MUST return ordered list of node ID strings along path (not full node objects)
- **FR-006**: `relationships(p)` / `rels(p)` MUST return ordered list of relationship types along path
- **FR-007**: `allShortestPaths` MUST return all paths of minimum length as multiple rows
- **FR-008**: No path found MUST return empty result (not an error)
- **FR-009**: `Graph.KG.Traversal.ShortestPathJson(srcId, dstId, maxHops, predsJson, findAll)` MUST reconstruct actual path via parent pointers, not just reachability

### Key Entities

- **Path**: ordered sequence of alternating nodes and relationships connecting source to target
- **Hop**: a single traversal of one edge; path length = number of hops

## Success Criteria

- **SC-001**: `shortestPath` query on 10K-node/50K-edge graph with path length ≤ 8 completes in < 100ms
- **SC-002**: `allShortestPaths` on diamond graph returns all minimum-length paths correctly
- **SC-003**: Existing `[*..N]` var-length path queries are unaffected (no regression)
- **SC-004**: Cypher parse error for `shortestPath(...)` is eliminated for the mindwalk reported query

## Out of Scope

- Weighted shortest path / Dijkstra (future spec)
- `shortestPath` to multiple targets in one call
- Target specified as label filter only (e.g. `(b:Disease)`) — both endpoints must be bound node IDs
- Named path variable used in WHERE predicates (`WHERE ALL(n IN nodes(p) WHERE ...)`)
- `shortestPath` inside `WITH` subquery pipelines

## Reuse from Existing Code

| Component | Reused from | Notes |
|-----------|-------------|-------|
| BFS traversal | `Graph.KG.Traversal.BFSFast` | Extend with parent pointer array |
| `^KG("out")` traversal | Existing BFS | Already used |
| `^KG("in")` traversal | `TemporalIndex` inbound pattern | Needed for undirected |
| `_execute_var_length_cypher` | `engine.py:638` | Extend to detect `shortest` mode |
| `VariableLength` AST node | `ast.py:60` | Add `shortest: bool`, `all_shortest: bool` |

## Parser / AST Changes

```
shortestPath((a)-[*..8]-(b))
  → RelationshipPattern.variable_length.shortest = True

allShortestPaths((a)-[*..8]-(b))
  → RelationshipPattern.variable_length.all_shortest = True
```

Lexer: `shortestPath` and `allShortestPaths` recognized as keywords before identifier fallback.
Parser: `path_function_expr` rule wraps existing `node_rel_node_pattern`.

