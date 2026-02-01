# Feature Specification: LOS Cypher & API Integration Gaps

**Feature Branch**: `015-los-cypher-api-gaps`  
**Created**: 2026-01-31  
**Status**: Draft  
**Input**: User description: "LOS Integration Gaps - Cypher enhancements and high-level APIs to eliminate direct SQL workarounds in application code"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Retrieve Complete Node Data (Priority: P1)

As a developer building a knowledge graph application, I need to retrieve a node with all its properties and labels using a single Cypher query or API call, so I don't have to write direct SQL queries to fetch labels and properties separately.

**Why this priority**: This is the most fundamental operation in any graph application. Currently every `get_node()` call requires 3 separate queries (Cypher for existence, SQL for labels, SQL for properties), making the code complex and tightly coupled to the database schema.

**Independent Test**: Can be fully tested by creating a node with multiple labels and properties, then retrieving it via a single query and verifying all data is returned.

**Acceptance Scenarios**:

1. **Given** a node exists with id "node-123", labels ["Proposition", "Belief"], and properties {type: "opinion", confidence: "0.8"}, **When** I execute `MATCH (n) WHERE n.id = 'node-123' RETURN n`, **Then** the result contains the complete node object with id, all labels, and all properties.

2. **Given** a node exists with id "node-456", **When** I call `graph_engine.get_node("node-456")`, **Then** I receive a dictionary containing the node's id, labels array, and all properties.

3. **Given** a node exists, **When** I execute `MATCH (n) RETURN n.id, labels(n), properties(n)`, **Then** the `labels()` function returns an array of label strings and `properties()` returns a dictionary of all properties.

4. **Given** a node does not exist, **When** I query for it, **Then** I receive an empty result (not an error).

---

### User Story 2 - Store Node Embeddings via API (Priority: P1)

As a developer implementing semantic search, I need to store vector embeddings for nodes through a high-level API, so I don't have to construct raw SQL with TO_VECTOR() calls and manage the embeddings table directly.

**Why this priority**: Semantic search is a core feature of modern knowledge graphs. Direct SQL for embeddings creates tight coupling and requires developers to understand the internal schema.

**Independent Test**: Can be fully tested by storing an embedding for a node and then performing a KNN search to verify it was stored correctly.

**Acceptance Scenarios**:

1. **Given** a node "node-123" exists, **When** I call `graph_engine.store_embedding("node-123", [0.1, 0.2, ...], {"model": "text-embedding-3"})`, **Then** the embedding is stored and associated with the node.

2. **Given** an embedding already exists for "node-123", **When** I store a new embedding for the same node, **Then** the old embedding is replaced with the new one.

3. **Given** multiple nodes need embeddings, **When** I call `graph_engine.store_embeddings([{node_id, embedding, metadata}, ...])`, **Then** all embeddings are stored efficiently in a batch operation.

4. **Given** a node does not exist, **When** I try to store an embedding for it, **Then** I receive an appropriate error indicating the node must exist first.

---

### User Story 3 - Query with ORDER BY and LIMIT (Priority: P2)

As a developer querying the knowledge graph, I need to sort and limit results directly in Cypher queries, so I don't have to fetch all matching records and sort/slice them in application code.

**Why this priority**: Without ORDER BY/LIMIT, queries that should return 10 records fetch thousands and filter in Python, causing performance degradation that worsens as the graph grows.

**Independent Test**: Can be fully tested by creating multiple nodes with timestamp properties, querying with ORDER BY and LIMIT, and verifying only the expected subset is returned in the correct order.

**Acceptance Scenarios**:

1. **Given** 100 nodes exist with "created_at" properties, **When** I execute `MATCH (n:Checkpoint) RETURN n.id ORDER BY n.created_at DESC LIMIT 10`, **Then** I receive exactly 10 results sorted by created_at in descending order.

2. **Given** nodes with varying values, **When** I use `ORDER BY n.property ASC`, **Then** results are sorted in ascending order.

3. **Given** a LIMIT of 5, **When** fewer than 5 nodes match, **Then** all matching nodes are returned without error.

---

### User Story 4 - Filter with Comparison Operators (Priority: P2)

As a developer filtering knowledge graph data, I need to use comparison operators (>=, <=, >, <, !=) in WHERE clauses, so I can filter data at the database level instead of fetching all records and filtering in Python.

**Why this priority**: Filtering in application code is inefficient and doesn't scale. Comparison operators are essential for queries like "find high-confidence evidence" or "get recent items".

**Independent Test**: Can be fully tested by creating nodes with numeric properties and verifying that comparison queries return only the correctly filtered subset.

**Acceptance Scenarios**:

1. **Given** nodes with confidence values [0.3, 0.5, 0.7, 0.9], **When** I execute `MATCH (n:Evidence) WHERE n.confidence >= 0.7 RETURN n.id`, **Then** only nodes with confidence 0.7 and 0.9 are returned.

2. **Given** nodes with priority values [1, 5, 10], **When** I execute `WHERE n.priority > 3 AND n.priority <= 10`, **Then** nodes with priority 5 and 10 are returned.

3. **Given** property values are stored as strings, **When** I use numeric comparisons, **Then** the system handles type coercion appropriately for valid numeric strings.

---

### User Story 5 - Search with String Pattern Matching (Priority: P3)

As a developer searching for nodes by text patterns, I need CONTAINS, STARTS WITH, and ENDS WITH operators in Cypher, so I can find nodes by partial name matches without fetching all nodes and filtering in Python.

**Why this priority**: Pattern matching is common but can be worked around with Python filtering. It's a convenience improvement rather than a blocker.

**Independent Test**: Can be fully tested by creating nodes with various name values and verifying pattern matching queries return the correct subsets.

**Acceptance Scenarios**:

1. **Given** nodes named ["UserController", "AuthController", "UserService"], **When** I execute `WHERE n.name CONTAINS 'Controller'`, **Then** UserController and AuthController are returned.

2. **Given** the same nodes, **When** I execute `WHERE n.name STARTS WITH 'User'`, **Then** UserController and UserService are returned.

3. **Given** the same nodes, **When** I execute `WHERE n.name ENDS WITH 'Service'`, **Then** only UserService is returned.

---

### User Story 6 - Get Relationship Type (Priority: P3)

As a developer traversing the graph, I need the `type(r)` function to reliably return the relationship type string, so I can identify edge types in query results.

**Why this priority**: This may already work; needs verification. Lower priority as current workaround exists.

**Independent Test**: Can be fully tested by creating relationships of different types and verifying type(r) returns the correct type string.

**Acceptance Scenarios**:

1. **Given** an edge from A to B with type "KNOWS", **When** I execute `MATCH (a)-[r]->(b) RETURN type(r)`, **Then** the result is "KNOWS".

2. **Given** multiple relationship types, **When** I query edges from a node, **Then** each relationship's type is correctly returned.

---

### Edge Cases

- Nodes with no labels → `labels(n)` returns an empty array.
- Nodes with no properties → `properties(n)` returns an empty dictionary.
- ORDER BY on missing property → rows with missing values sort last (NULLS LAST).
- CONTAINS with empty string → returns all non-null values.
- CONTAINS with special characters → treated as literal match (no regex semantics).
- Numeric comparison on non-numeric string → node excluded from comparison (skip).
- Embedding with wrong dimensions → error returned; no write.
- Batch embedding with any invalid node/dimension → entire batch rejected (atomic).

## Requirements *(mandatory)*

### Functional Requirements

**Node Data Retrieval (Gap 1)**
- **FR-001**: System MUST support `RETURN n` syntax that returns a complete node object including id, labels, and properties
- **FR-002**: System MUST provide a `labels(n)` function that returns an array of all labels for a node
- **FR-003**: System MUST provide a `properties(n)` function that returns a dictionary of all properties for a node
- **FR-004**: System MUST provide `IRISGraphEngine.get_node(node_id)` method returning complete node data

**Embedding Storage (Gap 2)**
- **FR-005**: System MUST provide `IRISGraphEngine.store_embedding(node_id, embedding, metadata)` method with strict dimension validation (error if dimensions don't match)
- **FR-006**: System MUST provide `IRISGraphEngine.store_embeddings(items)` method for batch operations with atomic semantics (all-or-nothing: reject entire batch if any node doesn't exist or dimension validation fails)
- **FR-007**: System MUST validate that the node exists before storing an embedding
- **FR-008**: System MUST replace existing embeddings when storing for a node that already has one

**Query Clauses (Gap 3)**
- **FR-009**: Cypher parser MUST support `ORDER BY` clause with property references
- **FR-010**: Cypher parser MUST support `ASC` and `DESC` sort directions (default: ASC)
- **FR-011**: Cypher parser MUST support `LIMIT` clause with integer values
- **FR-012**: Cypher translator MUST generate appropriate SQL for ORDER BY and LIMIT, implementing NULLS LAST behavior (nodes missing the property appear at the end of results)

**Comparison Operators (Gap 4)**
- **FR-013**: Cypher parser MUST support comparison operators: `>`, `<`, `>=`, `<=`, `<>` (or `!=`)
- **FR-014**: Cypher translator MUST handle type coercion for numeric comparisons on string-stored values and skip nodes with non-numeric values

**String Pattern Matching (Gap 5)**
- **FR-015**: Cypher parser MUST support `CONTAINS` operator for substring matching
- **FR-016**: Cypher parser MUST support `STARTS WITH` operator for prefix matching
- **FR-017**: Cypher parser MUST support `ENDS WITH` operator for suffix matching
- **FR-018**: Cypher translator MUST convert pattern operators to appropriate SQL LIKE clauses

**Relationship Functions (Gap 6)**
- **FR-019**: System MUST support `type(r)` function returning the relationship type as a string

### Key Entities

- **Node**: Graph vertex with unique id, zero or more labels, and zero or more key-value properties
- **Embedding**: Vector representation of a node, stored with optional metadata (source, model, timestamp)
- **Relationship**: Directed edge between nodes with a type string and optional properties

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Developers can retrieve complete node data (id, labels, properties) with a single query instead of 3 separate queries
- **SC-002**: Developers can store and update embeddings without writing any direct SQL
- **SC-003**: Queries with ORDER BY and LIMIT execute at the database level, reducing data transfer by 90%+ for large result sets
- **SC-004**: Comparison queries filter at the database level, eliminating fetch-all-then-filter patterns
- **SC-005**: All 7 identified gaps have corresponding test cases that pass
- **SC-006**: LOS application code eliminates all direct SQL workarounds (5 files affected)
- **SC-007**: New Cypher features are documented with examples
- **SC-008**: Cypher translation adds no more than 10% overhead compared to equivalent SQL query execution

## Clarifications

### Session 2026-01-31

- Q: How should batch embedding storage handle partial failures (some nodes don't exist)? → A: Atomic - all-or-nothing. Reject entire batch if any node doesn't exist.
- Q: How should ORDER BY handle null/missing property values? → A: NULLS LAST behavior (nodes missing the property appear at the end).
- Q: How should the system handle embedding dimension validation? → A: Strict validation - error if dimensions do not match the expected dimension of the target embedding table.
- Q: How should numeric comparisons handle non-numeric string values? → A: Skip the node for that comparison (non-numeric values are excluded).

## Assumptions

- Property values are stored as strings in rdf_props.val; numeric comparisons will use CAST or similar type coercion
- Embedding dimensions are consistent across the application (no mixed-dimension vectors)
- The existing RDF schema (nodes, rdf_labels, rdf_props, rdf_edges, kg_NodeEmbeddings) remains unchanged
- Batch operations should support reasonable sizes (hundreds to thousands of items) but are not expected to handle millions in a single call
