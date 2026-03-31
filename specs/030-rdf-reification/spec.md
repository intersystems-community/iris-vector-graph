# Feature Specification: RDF 1.2 Reification for KBAC

**Feature Branch**: `030-rdf-reification`  
**Created**: 2026-03-31  
**Status**: Draft  
**Input**: arno/specs/034-niche-knowledge-graph/ivg-reification-request.md (975 lines)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Reify an Edge with Metadata (Priority: P1)

A developer wants to attach metadata (confidence, provenance, access policy) to a specific edge in the knowledge graph. They call `reify_edge(edge_id)` which creates a reifier node linked to the edge, then attach properties to the reifier as regular graph properties. The reifier and its properties are visible to all graph algorithms (PageRank, BFS, Cypher).

**Why this priority**: This is the core capability — without it, edge metadata stays trapped in the opaque `qualifiers` JSON column. This unlocks KBAC, provenance tracking, and confidence scoring as graph-traversable entities.

**Independent Test**: Can be tested by creating an edge, reifying it with confidence and source properties, then querying the reifier's properties via standard graph traversal.

**Acceptance Scenarios**:

1. **Given** an edge (Aspirin)-[treats]->(Headache), **When** a developer calls `reify_edge(edge_id, props={"confidence": "0.92", "source": "PMID:12345"})`, **Then** a reifier node exists in `Graph_KG.nodes` with label "Reification", the junction row links reifier to edge, and the properties are queryable via `get_node()`.
2. **Given** a reified edge, **When** running `get_reifications(edge_id)`, **Then** the result includes the reifier node ID and all its properties.
3. **Given** a reified edge, **When** running PageRank or BFS starting from the reifier, **Then** the reifier participates in graph traversal like any other node.

---

### User Story 2 - Query Reifications for an Edge (Priority: P1)

A developer wants to find all reifications (metadata annotations) attached to a specific edge. They query by edge_id and receive all reifier nodes with their properties.

**Why this priority**: Query is the complement of creation — you need both for KBAC to work. An authorization check is: "find reifications of this edge where accessPolicy matches the user's permissions."

**Independent Test**: Can be tested by creating multiple reifications for the same edge, querying them, and verifying all are returned with correct properties.

**Acceptance Scenarios**:

1. **Given** an edge with two reifications (one for confidence, one for access policy), **When** querying `get_reifications(edge_id)`, **Then** both reifier nodes are returned with their respective properties.
2. **Given** an edge with no reifications, **When** querying, **Then** an empty list is returned.
3. **Given** a reifier node, **When** deleting the reifier via `delete_reification(reifier_id)`, **Then** the junction row and the reifier node are removed, but the original edge is preserved.

---

### User Story 3 - KBAC Access Check via Graph Walk (Priority: P2)

A developer wants to check if a user has access to a specific edge by walking the graph: user → role → permission → reification → edge. This is a standard graph traversal, not a special-case permission check.

**Why this priority**: This is the payoff of reification — authorization as graph traversal. It depends on US1 (reification exists) and US2 (queryable).

**Independent Test**: Can be tested by creating a user→role→permission chain and an edge reification with an accessPolicy property, then verifying that a graph walk from user to edge succeeds.

**Acceptance Scenarios**:

1. **Given** user "thomas" with role "analyst" with permission "kg_read", and edge E1 reified with `accessPolicy: "kg_read"`, **When** checking access by walking user→role→permission and comparing to reification accessPolicy, **Then** access is granted.
2. **Given** user "guest" with no matching permission, **When** the same check runs, **Then** access is denied (no path exists).

---

### Edge Cases

- What happens when reifying an edge that doesn't exist? The system returns None/error — no orphan reifier created.
- What happens when the same edge is reified multiple times? Each reification gets its own reifier node — multiple independent annotations are supported.
- What happens when deleting a reified edge? The reification junction rows and associated reifier nodes are cleaned up via manual cascade (matching IVG's existing delete_node() pattern). No SQL ON DELETE CASCADE.
- What happens when deleting a reifier node? The junction row is removed, but the edge and other reifiers for that edge remain.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST provide a `Graph_KG.rdf_reifications` table that maps reifier node IDs to edge IDs.
- **FR-002**: System MUST provide `reify_edge(edge_id, reifier_id, label, props)` that creates a reifier node, links it to the edge, and stores metadata as graph properties.
- **FR-003**: System MUST provide `get_reifications(edge_id)` that returns all reifier nodes and their properties for a given edge.
- **FR-004**: System MUST provide `delete_reification(reifier_id)` that removes the junction row, the reifier's properties, labels, and the reifier node itself.
- **FR-005**: Reifier nodes MUST be regular nodes in `Graph_KG.nodes` with standard labels and properties — visible to all graph algorithms (PageRank, BFS, Cypher, etc.).
- **FR-006**: The reifier_id MUST be a `VARCHAR(256)` matching `nodes.node_id` format. Auto-generated as `reif:<edge_id>` if not provided.
- **FR-007**: System MUST support multiple independent reifiers per edge.
- **FR-008**: Zero changes to existing tables (`rdf_edges`, `rdf_labels`, `rdf_props`, `nodes`).
- **FR-009**: Edge deletion MUST cascade to reification junction rows and associated reifier nodes (manual cascade, matching existing `delete_node()` pattern — no SQL ON DELETE CASCADE).

### Key Entities

- **rdf_reifications**: Junction table mapping reifier node IDs to edge IDs. Key attributes: reifier_id (PK, FK→nodes), edge_id (FK→rdf_edges).
- **Reifier Node**: A regular node in Graph_KG.nodes with label "Reification" (default). Its properties (stored in rdf_props) represent metadata about the edge it reifies.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Developers can reify any edge and query its reifications in under 5ms.
- **SC-002**: Reifier nodes participate in graph analytics (PageRank, BFS) without special handling.
- **SC-003**: 100% of existing tests continue to pass after reification is added (zero regressions).
- **SC-004**: Reification is covered by at least 6 unit tests and 4 e2e tests.

## Assumptions

- Reifier nodes use the standard `Graph_KG.nodes` table — no separate node storage.
- Metadata about the reification is stored as regular `rdf_props` on the reifier node — no new property storage mechanism.
- The `qualifiers` JSON column on `rdf_edges` is preserved for lightweight annotations. Reification is for heavy metadata (provenance, access policy, audit).
- W3C RDF 1.2 compliance: reifier is a first-class entity that can be subject of other triples (reification-of-reification supported).

## Scope Boundaries

**In scope (Phase 1)**:
- `Graph_KG.rdf_reifications` table with FK constraints
- `Graph.KG.Reification.cls` ObjectScript class
- Python API: `reify_edge()`, `get_reifications()`, `delete_reification()`
- Add `rdf_reifications` to security allowlist
- Unit and e2e tests

**Out of scope (Phase 2 / future)**:
- Cypher syntax for reification (`MATCH ()-[r]->() WHERE r.confidence > 0.9` via reification join)
- `^KG` global integration for reifications (reifier edges in the adjacency list)
- Bulk reification API
- Automatic reification on edge creation (trigger-based)
- KBAC enforcement layer (the authorization engine that uses reification data)

## Clarifications

### Session 2026-03-31

- Q: When a reified edge is deleted, what happens to reification junction rows? → A: Manual cascade (Option A). Add rdf_reifications cleanup to edge deletion paths, matching IVG's existing delete_node() pattern. No SQL ON DELETE CASCADE.
