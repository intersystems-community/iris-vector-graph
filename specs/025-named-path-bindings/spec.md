# Feature Specification: Named Path Bindings

**Feature Branch**: `025-named-path-bindings`  
**Created**: 2026-03-27  
**Status**: Draft  
**Input**: User description: "docs/enhancements/002-named-path-bindings.md"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Bind and Return a Fixed-Length Path (Priority: P1)

A developer writing a Cypher query against the IVG knowledge graph wants to assign a matched pattern to a variable so they can return the complete path and inspect its structure. They write `MATCH p = (a)-[r]->(b) RETURN p` and receive a JSON object containing the ordered list of nodes and relationships in the path.

**Why this priority**: This is the core named path capability. Without it, callers cannot reference a matched path as a value at all — the feature has zero utility until this works.

**Independent Test**: Can be tested by executing a named path Cypher query against a 3-node graph and verifying the result contains the expected node IDs and relationship predicates.

**Acceptance Scenarios**:

1. **Given** a graph with nodes A→B via predicate "KNOWS", **When** a user executes `MATCH p = (a)-[r]->(b) WHERE a.id = 'A' RETURN p`, **Then** the result contains a path object with nodes `[A, B]` and relationships `[KNOWS]`.
2. **Given** a graph with nodes A→B→C, **When** a user executes `MATCH p = (a)-[r1]->(b)-[r2]->(c) RETURN p`, **Then** the result contains a path object with 3 nodes and 2 relationships in traversal order.
3. **Given** a graph with no matching pattern, **When** a user executes `MATCH p = (a:NonExistent)-[r]->(b) RETURN p`, **Then** the result set is empty (zero rows).

---

### User Story 2 - Use Path Functions: length, nodes, relationships (Priority: P1)

A developer wants to extract specific aspects of a matched path — its hop count, the list of node IDs, or the list of relationship predicates — using standard Cypher path functions.

**Why this priority**: Path functions are the primary reason developers bind paths to variables. Without `length(p)`, `nodes(p)`, and `relationships(p)`, the named path variable is opaque and unusable.

**Independent Test**: Can be tested by executing queries with each function against a known graph and verifying the returned values match expected counts and lists.

**Acceptance Scenarios**:

1. **Given** a 3-node chain A→B→C with path `p`, **When** a user executes `RETURN length(p)`, **Then** the result is the integer `2`.
2. **Given** the same path, **When** a user executes `RETURN nodes(p)`, **Then** the result is an ordered list of node IDs: `["A", "B", "C"]`.
3. **Given** the same path, **When** a user executes `RETURN relationships(p)`, **Then** the result is an ordered list of predicate strings in traversal order (e.g., `["KNOWS", "LIKES"]`).
4. **Given** a single-node path (self-match), **When** a user executes `RETURN length(p)`, **Then** the result is `0`.

---

### User Story 3 - Named Path with Property Filters (Priority: P2)

A developer writes a named path query with WHERE clause filters on the nodes or relationships within the bound pattern, and the path functions reflect only the filtered results.

**Why this priority**: Most real-world queries combine path binding with property filters. This validates that named paths integrate correctly with the existing WHERE clause translation.

**Independent Test**: Can be tested by adding a WHERE filter on node properties and verifying only matching paths are returned with correct path function values.

**Acceptance Scenarios**:

1. **Given** multiple paths from A, **When** a user executes `MATCH p = (a)-[r]->(b) WHERE a.name = 'Alice' RETURN nodes(p), length(p)`, **Then** only paths originating from the node with name 'Alice' are returned.
2. **Given** edges with different predicates, **When** a user executes `MATCH p = (a)-[r:KNOWS]->(b) RETURN relationships(p)`, **Then** only paths using the KNOWS predicate are included.

---

### User Story 4 - Named Path with Variable-Length Relationships (Priority: P3)

A developer binds a variable-length pattern to a path variable to capture multi-hop traversals and inspect the full path structure.

**Why this priority**: Variable-length paths are a Phase 2 enhancement that extends the fixed-length MVP. They depend on extending the existing recursive CTE / UNION ALL traversal to also collect path metadata.

**Independent Test**: Can be tested by executing a variable-length path query against a chain graph and verifying the path contains the correct number of intermediate nodes.

**Acceptance Scenarios**:

1. **Given** a chain A→B→C→D, **When** a user executes `MATCH p = (a)-[*1..3]->(d) WHERE a.id = 'A' RETURN nodes(p), length(p)`, **Then** the result contains paths of length 1, 2, and 3 with the appropriate node lists.
2. **Given** the same chain, **When** a user executes `MATCH p = (a)-[*2..2]->(c) RETURN length(p)`, **Then** all returned paths have exactly length 2.

---

### Edge Cases

- What happens when a named path query matches a cycle (A→B→A)? The path includes the full cycle with repeated node IDs.
- What happens when `nodes(p)` or `relationships(p)` is called on an unbound variable? The system raises a clear parse or translation error.
- What happens when a MATCH clause contains both a named path and an unnamed pattern? Both work independently — the unnamed pattern contributes to the WHERE filter scope but is not accessible as a path value.
- What happens when the same path variable name is reused in multiple MATCH clauses? The later binding shadows the earlier one within its scope.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST support the syntax `p = (pattern)` in MATCH clauses to bind a matched graph pattern to a path variable.
- **FR-002**: System MUST translate `RETURN p` (a path variable) into a JSON object containing ordered `nodes` and `relationships` arrays.
- **FR-003**: System MUST support `length(p)` on a path variable, returning the integer count of relationships in the path.
- **FR-004**: System MUST support `nodes(p)` on a path variable, returning an ordered list of node IDs from start to end.
- **FR-005**: System MUST support `relationships(p)` on a path variable, returning an ordered list of predicate strings in traversal order (e.g., `["KNOWS", "WORKS_AT"]`).
- **FR-006**: System MUST produce a parse or translation error when `length()`, `nodes()`, or `relationships()` is called on a variable that is not a path binding.
- **FR-007**: System MUST support named paths combined with WHERE clause property filters on nodes and relationships within the pattern.
- **FR-008**: Named paths MUST work with fixed-length patterns of any explicit hop count (1-hop, 2-hop, etc.).
- **FR-009**: System SHOULD support named paths with variable-length relationship patterns `[*min..max]` (Phase 2).

### Key Entities

- **NamedPath**: A binding between a variable name and a graph pattern. Key attributes: variable name (string), the graph pattern being matched.
- **PathResult**: The output representation of a path in query results. Contains: ordered list of node IDs, ordered list of predicate strings, length (integer hop count).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Developers can bind any fixed-length Cypher pattern to a variable and receive structured path data in query results.
- **SC-002**: All three path functions (`length`, `nodes`, `relationships`) return correct values for paths of 1, 2, and 3 hops.
- **SC-003**: Named path queries execute within 10% of the performance of equivalent unnamed pattern queries on the same data.
- **SC-004**: 100% of existing Cypher unit and e2e tests continue to pass after named path support is added (zero regressions).
- **SC-005**: Named path parsing and translation is covered by at least 10 unit tests and 5 e2e tests.

## Assumptions

- Path results are serialized as JSON objects (`{"nodes": [...], "rels": [...]}`), not as native Cypher Path objects. Callers consuming results in Python receive dicts.
- `RETURN p` for a fixed 2-hop pattern produces a JSON object with 3 node IDs and 2 relationship entries.
- Variable-length named paths (Phase 2) build on the existing recursive CTE traversal infrastructure already present in the translator.
- The `JSON_ARRAY` SQL function is available in the target IRIS version (2023.1+), as confirmed by existing usage in the codebase.

## Scope Boundaries

**In scope (Phase 1)**:
- `NamedPath` AST node
- Parser support for `p = (pattern)` in MATCH
- SQL translation of `RETURN p`, `length(p)`, `nodes(p)`, `relationships(p)` for fixed-length patterns
- Unit and e2e tests

**Out of scope (Phase 2 / future)**:
- Variable-length pattern path bindings (`[*1..3]`)
- Python `PathResult` wrapper class
- Path expressions in WHERE clauses (e.g., `WHERE length(p) > 2`)
- `shortestPath(p)` / `allShortestPaths(p)` named binding (existing functionality, separate enhancement)

## Clarifications

### Session 2026-03-27

- Q: What does `relationships(p)` return per element — predicate string, full edge tuple, or predicate+direction? → A: Predicate string per hop (e.g., `["KNOWS", "WORKS_AT"]`)
