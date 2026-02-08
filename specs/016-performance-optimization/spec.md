# Feature Specification: Performance Optimization & Scalability

**Feature Branch**: `016-performance-optimization`  
**Created**: 2026-02-08  
**Status**: Draft  
**Input**: User description: "Address the performance bottlenecks in iris-vector-graph: Batch get_nodes(), Substring indexing, Transaction batching, and Large property support."

## User Scenarios & Testing

### User Story 1 - Batch Node Retrieval (Priority: P1)

As a developer building a graph UI, I need to fetch data for hundreds of nodes at once without triggering N+1 database queries, so that the application remains responsive even when displaying complex neighborhoods.

**Why this priority**: Individual `get_node()` calls in a loop create massive overhead. Batching is essential for GraphQL performance (DataLoaders) and UI rendering.

**Acceptance Scenarios**:
1. **Given** 100 nodes exist in the database, **When** I call `engine.get_nodes(node_ids)`, **Then** exactly 3 SQL queries are executed (nodes, labels, props) regardless of the number of IDs.
2. **Given** some requested IDs do not exist, **When** I call `get_nodes()`, **Then** the results only contain entries for the nodes that actually exist.
3. **Given** a list of IDs, **When** I retrieve them, **Then** all labels and properties for each node are correctly populated in the result dictionary.

---

### User Story 2 - Fast Property Substring Search (Priority: P1)

As a researcher searching for entities by partial names or descriptions, I need substring queries to be fast even as the property table grows to millions of rows.

**Why this priority**: `WHERE prop CONTAINS 'xyz'` usually results in a full table scan. Without proper indexing, this becomes unusable at scale.

**Acceptance Scenarios**:
1. **Given** 1M rows in `rdf_props`, **When** I execute a Cypher query with `CONTAINS` or `STARTS WITH`, **Then** the query completes in <50ms.
2. **Given** the `rdf_props` table, **When** I check the schema, **Then** an IRIS iFind index is present on the `val` column.

---

### User Story 3 - High-Throughput Mutations (Priority: P2)

As a data engineer loading external data into the graph, I need to create nodes and edges in batches with transactional safety, so that the load process is fast and consistent.

**Why this priority**: Individual `CREATE` statements with their own transactions are slow (disk sync overhead). Transactional batching improves throughput by orders of magnitude.

**Acceptance Scenarios**:
1. **Given** a batch of 1,000 node creation requests, **When** I call `create_node` in a loop or use a batch API, **Then** all data is committed in a single transaction.
2. **Given** an error occurs halfway through a batch creation, **When** the operation fails, **Then** no partial data remains in the database (Rollback).

---

### User Story 4 - Large Property Support (Priority: P2)

As a developer storing rich metadata (like long descriptions or serialized JSON), I need to store values up to 64KB per property without losing indexing capabilities.

**Why this priority**: Default VARCHAR limits (often 4000) are too small for modern metadata. 64KB provides a good balance between capacity and performance in IRIS.

**Acceptance Scenarios**:
1. **Given** a property value of 50,000 characters, **When** I store it in `rdf_props.val`, **Then** it is saved without truncation.
2. **Given** large values in the database, **When** I perform a substring search, **Then** the index still functions correctly and finds matches within the large strings.

## Requirements

### Functional Requirements

**Batch Node Retrieval**
- **FR-001**: System MUST provide `IRISGraphEngine.get_nodes(node_ids: List[str]) -> List[Dict]` method.
- **FR-002**: System MUST implement batching for GraphQL using `DataLoader` pattern (GenericNodeLoader).
- **FR-003**: System MUST fetch all requested node data in a fixed number of queries (independent of N).

**Property Indexing**
- **FR-004**: System MUST use IRIS iFind indexing on `rdf_props.val` for substring search.
- **FR-005**: System MUST support `JSON_VALUE` functional indexing for common metadata fields (like confidence).

**Transactional Mutations**
- **FR-006**: System MUST support `create_node` with internal batching of labels and properties in a single transaction.
- **FR-007**: System MUST provide high-level batch APIs for atomic graph updates.

**Large Value Support**
- **FR-008**: System MUST upgrade `rdf_props.val` to `VARCHAR(64000)`.
- **FR-009**: System MUST ensure that `REPLACE` and other string functions remain compatible with the larger column size.

### Success Criteria

- **SC-001**: GraphQL `nodes()` query for 100 entities executes in <100ms.
- **SC-002**: Substring search on 100k properties executes in <20ms.
- **SC-003**: Bulk loading 10k nodes with properties takes <2 seconds.
- **SC-004**: No regressions in existing Cypher or GraphQL functionality.

## Assumptions

- InterSystems IRIS 2024.1+ is used (supporting advanced JSON and Vector features).
- The application uses `intersystems-irispython` for database connectivity.
- Functional indexes are supported by the IRIS license used in production.
