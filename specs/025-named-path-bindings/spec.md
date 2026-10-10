<!-- markdownlint-disable MD013 -->

# Feature Specification: Named Path Bindings

**Feature Branch**: `025-named-path-bindings`  
**Created**: 2026-03-27  
**Status**: Draft (amended 2026-10-07: variable-length path functions, see Clarifications)  
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

**Amendment 2026-10-07 — path functions on a variable-length path whose source is named by a property or id.** The scenarios above name the source only by its position in the pattern. Callers more often pick the source by a property (`{id: 'x'}`) or by `node_id`, with or without a label, and then ask for path functions. That combination returned **no rows and no error** (evidence: `docs/research/length-path-empty-result-report.md`). The fixture is a graph with 5,000 `Proposition` nodes, each with a property `id` equal to its node id and three outgoing `depends_on` edges, so the source `prop23` reaches 39 distinct targets within three hops.

1. **Given** that graph, **When** a user executes `MATCH path = (p:Proposition {id: 'prop23'})-[:depends_on*1..3]->(dep) RETURN dep.id, length(path) AS depth`, **Then** the result has two columns (`dep.id`, `depth`) and one row per path found, and every `depth` is between 1 and 3.
2. **Given** the same graph, **When** the user omits the path function (`RETURN dep.id`), **Then** the set of `dep_id` values is the same set as in scenario 3.
3. **Given** the same graph, **When** the user returns only the path function (`RETURN length(path) AS d`), **Then** the result has one column `d` and the same number of rows as scenario 3.
4. **Given** the same graph, **When** the user returns `dep.id, nodes(path)` or `dep.id, relationships(path)`, **Then** every row carries the ordered list for its own path, starting at `prop23`.
5. **Given** the same graph, **When** the pattern is `*1..1`, **Then** exactly the three direct `depends_on` targets are returned with `length(path)` equal to 1.
6. **Given** the same graph, **When** the source is named by `{node_id: 'prop23'}` with no label, **Then** the result equals scenario 3, including the path-function column (it used to return the target ids with that column missing).
7. **Given** the same graph, **When** the source is named by `{node_id: 'prop23'}` and the label `Proposition`, **Then** the result equals scenario 3.
8. **Given** the source node has no outgoing edge of the named type, **When** the query runs, **Then** the result is empty with its columns intact (a real empty answer).
9. **Given** an undirected variable-length pattern from the same source with a path function, **When** the query runs, **Then** each row's `length(path)` is the shortest hop count at which that target is reached and the columns are those of the RETURN clause.

---

### Edge Cases

- What happens when a named path query matches a cycle (A→B→A)? The path includes the full cycle with repeated node IDs.
- What happens when `nodes(p)` or `relationships(p)` is called on an unbound variable? The system raises a clear parse or translation error.
- What happens when a MATCH clause contains both a named path and an unnamed pattern? Both work independently — the unnamed pattern contributes to the WHERE filter scope but is not accessible as a path value.
- What happens when the same path variable name is reused in multiple MATCH clauses? The later binding shadows the earlier one within its scope.
- What happens when more than 1,000 nodes carry the source's label? The query still resolves its source from the id or property binding and returns the same rows as on a small graph; the size of the label's population is irrelevant to the answer.
- What happens when the source cannot be resolved (the lookup that finds it fails, or the id names no node)? A failed lookup is an error that reaches the caller; an id that names no node is an empty result. The two are never the same outcome.
- What happens when the search from the source finds nothing? The result is empty and carries the columns the RETURN clause asked for, including the path-function columns.
- What happens when an undirected pattern reaches a target by two routes? One row per target at its shortest hop count, as for the other variable-length forms.
- What happens when the label parameter and the source id are both strings in the query's bound parameters? The source is taken from the pattern's own binding, never inferred from the position of a bound value.

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
- **FR-009**: System SHOULD support named paths with variable-length relationship patterns `[*min..max]` (Phase 2). *Amended 2026-10-07: now required for the cases in User Story 4, scenarios 3 to 11; see FR-010 to FR-015.*
- **FR-010**: A variable-length pattern whose RETURN uses `length(p)`, `nodes(p)` or `relationships(p)` MUST return one column per RETURN item, in order, including the path-function columns, whether or not other items accompany them.
- **FR-011**: The source of a variable-length pattern MUST be the node the pattern itself names, whether by `node_id`, by a property map, or by a bound parameter, with or without a label. A label MUST narrow a source named by id or property and MUST NOT replace it.
- **FR-012**: When the pattern names no source beyond a label, the sources are all nodes with that label, and the result MUST be the same on a graph with more than 1,000 such nodes as on a small one.
- **FR-013**: A failure to resolve the source nodes MUST surface to the caller as an error. It MUST NOT be logged at a level callers do not see and answered as an empty result.
- **FR-014**: An empty traversal MUST return an empty result with the columns the RETURN clause asked for.
- **FR-015**: For each row, `length(p)` MUST equal the number of relationships on the path that produced the row, `nodes(p)` MUST start at the source and end at the row's target, and `relationships(p)` MUST list that path's predicates in order.

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
- **SC-006**: For every case in User Story 4 scenarios 3 to 11, the returned rows and columns equal those of the same query on a graph small enough that no label lookup is large, on a graph with more than 1,000 nodes of the source's label.
- **SC-007**: No variable-length query with a path function returns an empty result when the source node exists and has qualifying paths (zero silent empty results across the User Story 4 cases).
- **SC-008**: A forced failure of the source lookup is reported to the caller in 100% of cases, never as an empty result.
- **SC-009**: The columns of a variable-length result match the RETURN clause in count and order in 100% of the User Story 4 cases.

## Assumptions

- Path results are serialized as JSON objects (`{"nodes": [...], "rels": [...]}`), not as native Cypher Path objects. Callers consuming results in Python receive dicts.
- `RETURN p` for a fixed 2-hop pattern produces a JSON object with 3 node IDs and 2 relationship entries.
- Variable-length named paths (Phase 2) build on the existing recursive CTE traversal infrastructure already present in the translator.
- Amendment 2026-10-07: variable-length paths with a bound source are answered by a breadth-first traversal over the adjacency index, not by the recursive SQL; the fix concerns which node that traversal starts from and which columns it returns, not the traversal itself.
- Amendment 2026-10-07: the 39-target figure in User Story 4 depends on the fixture (3 random `depends_on` edges per node, seed fixed by the test); tests assert equality with an independently computed answer (a SQL walk over the edge table), not the literal 39.
- The `JSON_ARRAY` SQL function is available in the target IRIS version (2023.1+), as confirmed by existing usage in the codebase.

## Scope Boundaries

**In scope (Phase 1)**:

- `NamedPath` AST node
- Parser support for `p = (pattern)` in MATCH
- SQL translation of `RETURN p`, `length(p)`, `nodes(p)`, `relationships(p)` for fixed-length patterns
- Unit and e2e tests

**Added to scope 2026-10-07**:

- Variable-length pattern path bindings (`[*1..3]`) with `length`, `nodes` and `relationships`, for sources named by id, property or label (User Story 4 scenarios 3 to 11, FR-010 to FR-015).

**Out of scope (Phase 2 / future)**:

- Variable-length pattern path bindings for weighted and shortest-path forms, which have their own executors
- Python `PathResult` wrapper class
- Path expressions in WHERE clauses (e.g., `WHERE length(p) > 2`)
- `shortestPath(p)` / `allShortestPaths(p)` named binding (existing functionality, separate enhancement)

## Clarifications

### Session 2026-03-27

- Q: What does `relationships(p)` return per element — predicate string, full edge tuple, or predicate+direction? → A: Predicate string per hop (e.g., `["KNOWS", "WORKS_AT"]`)

### Session 2026-10-07

Found while timing `execute_cypher` on queries shaped like the knowledge-graph caller's dependency-chain query (`docs/research/cypher-stage-timing.md`, `docs/research/length-path-empty-result-report.md`). Three defects in one route:

- Q: Why does `MATCH path = (p:Proposition {id: 'prop23'})-[:depends_on*1..3]->(dep) RETURN dep.id, length(path)` return no rows when the same query without `length(path)` returns 39? → A: The route for path functions starts its search from the first string value in the statement's bound parameters, which here is the label `Proposition`, not the node the pattern names. The search starts from a node that does not exist.
- Q: Why is the `length(path)` column missing when the source is named by `{node_id: ...}` with no label? → A: That route has no handling of path functions and returns only the target id column.
- Q: Why does the label-only route return nothing on a graph with more than about 1,000 nodes of the label? → A: It takes every node with the label and fetches them in one `IN (...)` list, which the database rejects past roughly 1,000 parameters; the error is swallowed at debug level and the empty list is returned as the answer.
- Q: Is an unresolvable source an empty result or an error? → A: An error (FR-013). An id that simply names no node is an empty result.
- Q: Is a result cache or a different traversal route in scope? → A: No. Only which node the search starts from, which columns come back, and what a failed lookup reports.
