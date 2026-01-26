# Feature Specification: Advanced Cypher Features (Write, UNWIND, OPTIONAL MATCH, Paths)

**Feature Branch**: `010-cypher-advanced-features`  
**Created**: 2026-01-25  
**Status**: Draft  
**Input**: User description: "Remaining Gaps (The Final Mile): Write Operations (CREATE, DELETE, MERGE), Advanced Clauses (UNWIND, OPTIONAL MATCH), Path Functions (shortestPath, allShortestPaths)"

## Clarifications

### Session 2026-01-25
- Q: Transaction Isolation for Cypher Writes → A: Read Committed: Prevents dirty reads; standard for IRIS.
- Q: ShortestPath Result when no path exists → A: NULL: Standard Cypher behavior for no path.
- Q: UNWIND behavior for empty collections → A: Terminate: Returns zero rows for the current branch (Standard Cypher).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Transactional Graph Management (Priority: P1)

As a data engineer, I want to create, delete, and merge nodes and relationships using Cypher syntax so that I can manage the graph lifecycle directly through the Cypher API without falling back to SQL or Python code.

**Why this priority**: Transforming the system from a "Graph Retrieval Engine" to a "Transactional Graph Engine" is the most significant functional leap requested. It enables full graph automation.

**Independent Test**: Can be tested by executing a `CREATE` query followed by a `MATCH` query to verify the data exists, and then a `DELETE` query followed by a `MATCH` to verify it's gone.

**Acceptance Scenarios**:

1. **Given** an empty graph, **When** I run `CREATE (a:Account {id: 'ACC1', type: 'Savings'})`, **Then** a new node is created in the `nodes`, `rdf_labels`, and `rdf_props` tables.
2. **Given** an existing node, **When** I run `MERGE (a:Account {id: 'ACC1'}) SET a.status = 'Active'`, **Then** the system updates the existing node rather than creating a duplicate.
3. **Given** a node with relationships, **When** I run `MATCH (a:Account {id: 'ACC1'}) DETACH DELETE a`, **Then** the node and all its connected edges are removed from the database.

---

### User Story 2 - Resilient Traversal with OPTIONAL MATCH (Priority: P2)

As a fraud analyst, I want to traverse the graph even when certain relationships are missing so that I can get partial matches and discover optional attributes without the entire query failing to return rows.

**Why this priority**: `OPTIONAL MATCH` is critical for discovery and reporting where data completeness isn't guaranteed.

**Independent Test**: Execute a query where one `MATCH` would succeed and an `OPTIONAL MATCH` would fail (no relationship exists), and verify that the row is still returned with NULL values for the optional part.

**Acceptance Scenarios**:

1. **Given** an account without a known owner, **When** I run `MATCH (a:Account) OPTIONAL MATCH (a)-[:OWNED_BY]->(p:Person) RETURN a.id, p.name`, **Then** I receive the account ID and a NULL for the person's name.

---

### User Story 3 - Bulk Data Processing with UNWIND (Priority: P3)

As a system integrator, I want to expand list parameters into multiple rows within a single Cypher query so that I can perform bulk creation or filtering operations efficiently.

**Why this priority**: `UNWIND` is the standard Cypher way to handle batch data passed as parameters.

**Independent Test**: Pass a list of identifiers as a parameter and use `UNWIND` to create nodes for each.

**Acceptance Scenarios**:

1. **Given** a list of transaction IDs `['TX1', 'TX2', 'TX3']`, **When** I run `UNWIND $ids AS id CREATE (:Transaction {id: id})`, **Then** three new transaction nodes are created.

---

### User Story 4 - Algorithmic Path Finding (Priority: P4)

As a network analyst, I want to find the shortest path between two specific nodes so that I can identify the degree of separation or the most direct connection in a complex network.

**Why this priority**: Essential graph algorithmic capability that is currently missing from the retrieval engine.

**Independent Test**: Execute `MATCH p = shortestPath((a:Account {id: 'A1'})-[:TRANSFER*..10]->(b:Account {id: 'A2'})) RETURN p`.

**Acceptance Scenarios**:

1. **Given** a chain of transfers A -> B -> C, **When** I request the shortest path from A to C, **Then** the system returns the two-hop path.

---

## Edge Cases

- **Circular Merges**: How does system handle `MERGE` on patterns that form a cycle?
- **UNWIND Empty List**: `UNWIND` on an empty collection MUST return **zero rows** for that query branch, terminating subsequent operations in that branch.
- **No Path Found**: `shortestPath` and `allShortestPaths` MUST return **NULL** if no connection exists within the max hop limit.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST support `CREATE` for nodes and relationships.
- **FR-002**: System MUST support `DELETE` and `DETACH DELETE` for nodes and relationships.
- **FR-003**: System MUST support `MERGE` with `ON CREATE` and `ON MATCH` logic.
- **FR-004**: System MUST support `OPTIONAL MATCH` translated to SQL `LEFT OUTER JOIN`.
- **FR-005**: System MUST support `UNWIND` for expanding collections into rows.
- **FR-006**: System MUST implement `shortestPath()` and `allShortestPaths()` functions.
- **FR-007**: System MUST support `SET` and `REMOVE` clauses for property and label updates.
- **FR-008**: All write operations MUST be executed within an IRIS SQL transaction using **Read Committed** isolation level.

### Key Entities

- **Statement**: Now supports `UpdatingClause` (Create, Merge, Delete, Set, Remove) in addition to `ReadingClause`.
- **Expression**: Support for List literals and collection-based expressions.
- **Path**: A sequence of alternating nodes and relationships returned by path functions.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Users can perform bulk node creation (100+ nodes) in a single Cypher query using `UNWIND`.
- **SC-002**: `OPTIONAL MATCH` queries return the same number of rows as a standard `MATCH` plus any rows where the optional pattern is NULL.
- **SC-003**: All write operations result in consistent data across `nodes`, `rdf_labels`, and `rdf_props` tables.
- **SC-004**: Path finding functions return correct paths for networks up to 10 hops deep within < 500ms on the fraud dataset.
