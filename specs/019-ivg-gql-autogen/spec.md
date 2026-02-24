# Feature Specification: Auto-Generating GraphQL Layer for IRIS Graph Stores

**Feature Branch**: `019-ivg-gql-autogen`  
**Created**: 2026-02-24  
**Status**: Draft  
**Input**: User description: "Implement the iris_vector_graph.gql module — a generic, auto-generating GraphQL layer over any IRIS graph store powered by iris-vector-graph."

## Clarifications

### Session 2026-02-24
- Q: Should the system auto-expose incoming edges for bi-directional navigation? → A: Bi-directional (expose both outgoing and incoming relationships).
- Q: How should the auto-generator discover available properties in large datasets? → A: Sampling (scan a subset of nodes, e.g., first 1,000 per label, to discover keys).
- Q: Should discovered properties be exposed as top-level fields? → A: Top-level Fields (map each unique property key to a GraphQL field on the node type).
- Q: How should the system handle database connections for concurrent requests? → A: Pooling (implement connection reuse/pooling to handle concurrency within license limits).
- Q: Should filtering support rich logical operators or only equality? → A: Equality Only (filters only support strict matching for the initial release).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Zero-Config GraphQL Server (Priority: P1)

As a developer using the graph library, I want to start a fully functional GraphQL server against my graph store with a single function call, so that I can query my data immediately without writing any manual schema or resolver code.

**Why this priority**: This is the core value proposition. If data is loaded into a graph, it should be exposable via a standard API without additional boilerplate.

**Independent Test**: Can be fully tested by pointing the server at a populated graph store, starting the service, and verifying that the API endpoint is reachable and returns data for introspection queries and basic node queries — without the developer having defined any types or logic.

**Acceptance Scenarios**:

1. **Given** a graph store with nodes of several labels and various properties, **When** a developer starts the server with default settings, **Then** an API endpoint is available with a schema reflecting the node types in the graph.
2. **Given** a running auto-generated server, **When** a client sends an introspection query, **Then** the response lists query fields for each node type present in the graph.
3. **Given** a running server, **When** the underlying graph structure changes (new labels or properties added), **Then** the server reflects those changes on next startup without any code modifications.

---

### User Story 2 - Node Queries by Label and Property (Priority: P2)

As an API client, I want to query nodes by their type (label) and filter by specific property values, so that I can retrieve specific entities from the graph using standard API patterns.

**Why this priority**: This is the most common data retrieval pattern. While semantic search is powerful, basic lookup by type and property is essential for most integration needs.

**Independent Test**: Can be tested by querying for nodes of a specific type, filtering by a known property value, and verifying the returned records match the stored data.

**Acceptance Scenarios**:

1. **Given** a graph containing specific entity types and properties, **When** a client queries for a type with a limit, **Then** the requested number of nodes are returned with all their associated properties.
2. **Given** a graph with property-rich nodes, **When** a client queries with a property filter, **Then** only nodes matching that property value are returned.
3. **Given** a specific node identifier, **When** a client queries for that individual node, **Then** the full record is returned including all its types and properties.

---

### User Story 3 - Semantic Search (Priority: P3)

As an API client, I want to find nodes by natural language similarity so that I can find relevant entities without needing to know exact property values.

**Why this priority**: This leverages the distinctive capability of the underlying vector storage. It allows finding "related" things even when exact matches are missing.

**Independent Test**: Can be tested by running a natural language query against a graph with stored embeddings and verifying that the top results are relevant to the query text.

**Acceptance Scenarios**:

1. **Given** a graph with stored similarity data, **When** a client provides a text query for a specific type, **Then** the response returns the most similar nodes with a numerical relevance score.
2. **Given** a similarity search query, **When** results are returned, **Then** each result includes a score field indicating how closely it matched the query.
3. **Given** a graph where only some nodes have similarity data, **When** a search is executed, **Then** only compatible nodes are considered and no errors are raised for nodes lacking that data.

---

### User Story 4 - Graph Traversal (Priority: P4)

As an API client, I want to follow relationships from a node to its neighbors so that I can navigate the graph structure without writing custom traversal logic.

**Why this priority**: Navigation of relationships is the primary benefit of a graph structure. This allows clients to explore connections between entities.

**Independent Test**: Can be tested against a graph with known relationships: query a node, request its neighbors for a specific relationship type, and verify the correct neighbor nodes are returned.

**Acceptance Scenarios**:

1. **Given** a node with defined relationships to other nodes, **When** a client queries for neighbors of a specific relationship type, **Then** all directly connected neighbor nodes are returned.
2. **Given** a node with no outgoing relationships of a requested type, **When** a client queries for neighbors, **Then** an empty list is returned without error.
3. **Given** a node, **When** a client queries for all its connections, **Then** all outgoing and incoming relationships are listed with their types and the identifiers of the connected nodes.

---

### User Story 5 - Advanced Query Passthrough (Priority: P5)

As a power user, I want to execute raw graph queries through the API so that I can access advanced capabilities not exposed by the standard auto-generated fields.

**Why this priority**: This ensures that advanced users are never blocked by the limitations of the abstraction layer.

**Independent Test**: Can be tested by submitting a raw graph query and verifying the result set matches the expected data.

**Acceptance Scenarios**:

1. **Given** a running API server, **When** a client executes a raw graph query string, **Then** the response contains the resulting data rows in a standard format.
2. **Given** an invalid query string, **When** it is submitted, **Then** a structured error is returned with a descriptive message rather than a server failure.

---

### Edge Cases

- **Empty graph**: If the server starts against an empty store, it should provide a valid (though empty) schema rather than crashing.
- **Label collisions**: Handling properties that might conflict with API reserved words (e.g., a property named "id" vs the system identifier) by automatically prefixing the field name (e.g., `p_id`).
- **Missing Similarity Data**: Graceful handling when semantic search is requested but the required vector data hasn't been generated for the target nodes.
- **Large Data Volumes**: Ensuring the server remains responsive when returning nodes with many properties or when handling deep traversals.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST provide a single entry point to start a web server backed by an existing graph engine.
- **FR-002**: The system MUST automatically build the API schema by inspecting the labels and properties currently in the graph store, using a sampling strategy (e.g., first 1,000 nodes per label) to ensure startup performance on large datasets.
- **FR-003**: The system MUST provide query fields for every node type discovered in the graph, with discovered property keys exposed as top-level fields on those types.
- **FR-004**: The system MUST support equality-based filtering for nodes by property keys and values (strict matching).
- **FR-005**: The system MUST support natural language semantic search for nodes that have associated vector data.
- **FR-006**: The system MUST allow following one-hop relationships (outgoing and incoming) between nodes.
- **FR-007**: The system MUST provide a way to execute raw graph query strings and receive structured results.
- **FR-008**: The system MUST hide internal data like raw embedding vectors from the standard API responses.
- **FR-009**: The system MUST return structured error responses for invalid requests or internal failures.
- **FR-010**: The system MUST work "out of the box" without requiring any project-specific code or configuration from the user.
- **FR-011**: The system MUST implement connection pooling to multiplex concurrent GraphQL requests over a limited set of physical database connections, ensuring stability in environments with low connection limits (e.g., IRIS Community Edition).

### Key Entities

- **Node**: A discrete entity in the graph characterized by a unique identifier, one or more types (labels), and a set of properties exposed as top-level fields.
- **Relationship**: A directed connection between two nodes with a specific type (predicate) and optional properties.
- **Search Result**: A Node paired with a relevance score from a similarity search.
- **Query Result**: A raw data row returned from a passed-through graph query.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A user can expose a populated graph via GraphQL using 3 lines of code or fewer.
- **SC-002**: API queries for specific node types return results in under 500ms for datasets up to 10,000 nodes.
- **SC-003**: Semantic search queries complete in under 2 seconds for datasets up to 10,000 embeddings.
- **SC-004**: The auto-generated API schema covers 100% of the node types found in the graph at startup.
- **SC-005**: A client with no prior knowledge of the graph can discover all available entity types and search options using standard API introspection.
- **SC-006**: Existing hand-rolled API implementations can be replaced by this automated layer, reducing their custom code base by at least 60%.
- **SC-007**: The system handles 10 concurrent requests without failure or data corruption, even in environments with a 5-connection license limit, through efficient connection pooling.

## Phase 2: Advanced GraphQL Features (Future)

These features are planned for future iterations and are not required for the initial auto-generating MVP.

- **Mutations (CRUD)**: System SHOULD support GraphQL mutations for create, update, delete operations on graph entities.
- **Subscriptions (Real-time)**: System SHOULD support GraphQL subscriptions for real-time updates via WebSocket (e.g., nodeCreated, nodeUpdated events).
- **Multi-hop Traversal**: System SHOULD support navigating relationships beyond a single hop in a single query.
- **Authentication & Security**: System SHOULD integrate with IRIS security for API key-based authentication and field-level authorization (RBAC).
- **Custom Scalars**: Support for `JSON` and `DateTime` scalar types.
- **Batch Operations**: Support for creating or updating multiple entities in a single request.

## Assumptions

- The underlying graph store is populated using the standard library patterns.
- Semantic search assumes vector embeddings are present for the target nodes.
- Property values are primarily text-based or can be represented as strings.
- The sampling strategy for schema discovery uses a default limit of 1,000 nodes per label to balance startup speed and schema completeness.
- The web server environment has access to the necessary computational resources for vector similarity calculations.

## Out of Scope

- Schema stitching with external GraphQL APIs.
- Belgium/Dutch language support (inherited from parent project scope).
- Automatic hot-reloading of the schema while the server is running.
