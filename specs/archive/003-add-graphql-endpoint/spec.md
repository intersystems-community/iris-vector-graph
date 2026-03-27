# Feature Specification: GraphQL API Endpoint

**Feature Branch**: `003-add-graphql-endpoint`
**Created**: 2025-10-02
**Status**: Draft
**Input**: User description: "Add GraphQL API endpoint with type-safe resolvers for client-driven graph queries"

---

## ⚡ Quick Guidelines
- ✅ Focus on WHAT users need and WHY
- ❌ Avoid HOW to implement (implementation details in plan.md)
- 👥 Written for business stakeholders, not developers

---

## User Scenarios & Testing

### Primary User Story

As a **web/mobile application developer**, I want to query the knowledge graph using **GraphQL** so that I can fetch exactly the data I need in a single request, with strong typing and auto-generated documentation, without over-fetching or making multiple API calls.

**Example**: Fetch protein with nested interactions in one request:
```graphql
query {
  protein(id: "PROTEIN:TP53") {
    name
    function
    interactsWith(first: 5) {
      name
      function
    }
  }
}
```

### Acceptance Scenarios

1. **Given** a GraphQL schema with Protein type, **When** I query `protein(id: "PROTEIN:TP53") { name, function }`, **Then** I receive exactly those two fields with no extra data

2. **Given** a GraphQL query with nested relationships, **When** I fetch `protein { interactsWith { name } }`, **Then** the system executes efficient batched SQL queries (not N+1 queries) and returns nested results

3. **Given** a GraphQL mutation to create a new protein, **When** I submit `createProtein(input: {id, name, embedding})`, **Then** the system validates FK constraints, inserts into nodes/labels/props/embeddings tables, and returns the created protein

4. **Given** a GraphQL subscription to `proteinCreated`, **When** a new protein is created via mutation, **Then** all subscribed clients receive real-time updates via WebSocket

5. **Given** an invalid GraphQL query with type mismatch, **When** submitted, **Then** I receive a clear validation error before execution, leveraging GraphQL's strong typing

### Edge Cases

- What happens when a resolver fetches related nodes that don't exist? → Return null for optional fields, empty array for lists
- How does the system prevent N+1 query problems with nested resolvers? → DataLoader batching is mandatory (per NFR-004), reduces N+1 queries to ≤2 SQL queries
- What if a client requests deeply nested queries (>5 levels)? → Query depth limit: 10 levels (configurable), queries exceeding limit rejected with validation error
- How are concurrent mutations to the same entity handled? → IRIS ACID transactions ensure consistency, last-write-wins
- What happens when a subscription client disconnects during an update? → WebSocket cleanup, no memory leaks

---

## Requirements

### Functional Requirements

**Core GraphQL Capabilities**:
- **FR-001**: System MUST expose GraphQL schema with types representing graph entities: Protein, Gene, Pathway, etc.
- **FR-002**: System MUST support GraphQL queries with nested field selection
- **FR-003**: System MUST support GraphQL mutations for create, update, delete operations
- **FR-004**: System MUST support GraphQL subscriptions for real-time updates via WebSocket
- **FR-005**: System MUST provide GraphQL Playground (interactive UI) for query exploration and testing
- **FR-006**: System MUST support GraphQL introspection for auto-generated documentation

**Schema & Type System**:
- **FR-007**: System MUST define GraphQL types matching graph entities with properties from rdf_props table
- **FR-008**: System MUST define interface `Node` implemented by all entity types (Protein, Gene, Pathway)
- **FR-009**: System MUST support GraphQL relationships as nested fields: `interactsWith`, `regulatedBy`, `participatesIn`
- **FR-010**: System MUST support pagination arguments: `first`, `offset` for list fields
- **FR-011**: System MUST support filter arguments: `where` clauses for property-based filtering
- **FR-012**: System MUST define custom scalar types: `JSON`, `DateTime` for complex fields

**Query Resolvers**:
- **FR-013**: System MUST resolve node lookups by ID: `protein(id: ID!)` queries nodes + rdf_labels tables
- **FR-014**: System MUST resolve property fields by querying rdf_props table
- **FR-015**: System MUST resolve relationship fields by querying rdf_edges table with FK validation
- **FR-016**: System MUST batch related entity fetches using DataLoader pattern to prevent N+1 queries (mandatory, not optional)
- **FR-017**: System MUST support vector similarity resolver: `similar(limit: Int, threshold: Float)` using HNSW index

**Mutation Resolvers**:
- **FR-018**: System MUST validate FK constraints before mutations (ensure node exists in nodes table)
- **FR-019**: Create mutations MUST insert into nodes, rdf_labels, rdf_props, and optionally kg_NodeEmbeddings tables
- **FR-020**: Update mutations MUST modify existing rdf_props entries or insert new properties
- **FR-021**: Delete mutations MUST cascade via FK constraints or fail with error if references exist
- **FR-022**: Batch mutations MUST support creating multiple entities in single request

**Subscription Resolvers**:
- **FR-023**: System MUST publish events when entities are created: `proteinCreated`
- **FR-024**: System MUST publish events when entities are updated: `proteinUpdated(id: ID)`
- **FR-025**: System MUST publish events when interactions are created: `interactionCreated`
- **FR-026**: Subscriptions MUST filter events by parameters (e.g., only updates to specific protein ID)

**Hybrid Vector+Graph Queries**:
- **FR-027**: System MUST support vector similarity queries: `similarProteins(queryVector: [Float!]!, limit: Int)`
- **FR-028**: System MUST support hybrid queries combining vector k-NN with graph expansion: `proteinNeighborhood(id: ID!, hops: Int)`
- **FR-029**: Vector queries MUST use HNSW index via VECTOR_DOT_PRODUCT SQL function

**API & Error Handling**:
- **FR-030**: System MUST expose GraphQL endpoint at POST /graphql accepting JSON requests
- **FR-031**: System MUST return structured errors with GraphQL error format (message, locations, path)
- **FR-032**: System MUST validate input types before execution and return validation errors
- **FR-033**: System MUST support CORS for cross-origin web requests (configurable via environment variable: `*` for development, explicit domains for production)

**Performance & Optimization**:
- **FR-034**: System MUST limit query complexity to prevent expensive operations (depth-based algorithm, max depth: 10 levels, configurable)
- **FR-035**: System MUST implement resolver-level caching (60-second TTL, manual invalidation on mutations)
- **FR-036**: System MUST execute batched queries for related entities (DataLoader pattern mandatory)

### Key Entities

**GraphQL Schema Types** (exposed to clients):
- **Node Interface**: `id`, `labels`, `properties`, `createdAt` (common fields)
- **Protein Type**: extends Node with `name`, `function`, `organism`, `confidence`, `interactsWith`, `similar`
- **Gene Type**: extends Node with `name`, `chromosome`, `position`, `encodes`, `variants`
- **Pathway Type**: extends Node with `name`, `description`, `proteins`, `genes`
- **Interaction Type**: `source`, `target`, `type`, `confidence`, `qualifiers`
- **SimilarProtein Type**: `protein`, `similarity`, `distance`

**GraphQL Input Types** (mutations):
- **CreateProteinInput**: `id`, `name`, `function`, `organism`, `embedding`
- **UpdateProteinInput**: `name`, `function`, `confidence`

**GraphQL Query Results**:
- **ProteinNeighborhood**: `center`, `neighbors`, `interactions`
- **Path**: `nodes`, `edges`, `length`
- **GraphStats**: `totalNodes`, `totalEdges`, `nodesByLabel`, `edgesByType`

---

## Non-Functional Requirements

### Performance Targets
- **NFR-001**: Simple GraphQL queries (<3 levels deep) MUST complete in <10ms
- **NFR-002**: GraphQL query execution MUST be within 10% of equivalent SQL query performance
- **NFR-003**: System MUST handle ≥100 concurrent GraphQL queries/second
- **NFR-004**: DataLoader batching MUST reduce N+1 queries to ≤2 SQL queries for nested fetches
- **NFR-005**: Vector similarity GraphQL queries MUST leverage HNSW index and complete in <10ms for k=10

### Scalability
- **NFR-006**: System MUST support graphs with 1M+ nodes without resolver performance degradation
- **NFR-007**: WebSocket subscriptions MUST support ≥1000 concurrent connections (configurable limit via environment variable)

### Reliability
- **NFR-008**: GraphQL endpoint MUST maintain 99.9% uptime (same as IRIS database availability)
- **NFR-009**: Failed mutations MUST NOT leave partial data (ACID transaction guarantees)
- **NFR-010**: Subscription clients MUST reconnect automatically on connection loss

### Security
- **NFR-011**: GraphQL endpoint MUST support authentication (API key-based, aligned with IRIS REST API patterns)
- **NFR-012**: GraphQL endpoint MUST support field-level authorization (role-based access control integrated with IRIS security model)
- **NFR-013**: All mutation inputs MUST be sanitized to prevent injection attacks
- **NFR-014**: Query complexity limits MUST prevent denial-of-service attacks

### Observability
- **NFR-015**: System MUST log all GraphQL queries with operation name, execution time, and result size
- **NFR-016**: System MUST expose metrics: query rate, error rate, resolver execution time
- **NFR-017**: Failed queries MUST include trace IDs and detailed error paths

---

## Dependencies & Assumptions

### Dependencies
- **DEP-001**: Requires NodePK schema (nodes, rdf_edges, rdf_labels, rdf_props) with FK constraints
- **DEP-002**: Requires IRIS database with VECTOR support (HNSW index for kg_NodeEmbeddings)
- **DEP-003**: Requires GraphQL server library (Strawberry GraphQL or Graphene for Python)
- **DEP-004**: Requires WebSocket support for GraphQL subscriptions

### Assumptions
- **ASM-001**: Clients are web/mobile applications familiar with GraphQL (not graph database experts)
- **ASM-002**: Client applications prefer nested data fetching over multiple REST calls
- **ASM-003**: Strong typing and auto-generated documentation improve developer experience
- **ASM-004**: GraphQL will be used alongside SQL/Cypher (not replacing them)

---

## Out of Scope

- **OOS-001**: GraphQL federation across multiple services - single GraphQL endpoint only
- **OOS-002**: GraphQL persisted queries - all queries dynamic
- **OOS-003**: Custom GraphQL directives (beyond standard @deprecated) - future extension
- **OOS-004**: GraphQL file upload support - separate file API
- **OOS-005**: Offline-first GraphQL with client-side caching (Apollo Client) - client responsibility
- **OOS-006**: Automatic GraphQL schema generation from IRIS tables - manual schema definition
- **OOS-007**: GraphQL rate limiting per user/API key - infrastructure concern
- **OOS-008**: GraphQL query cost analysis beyond depth limits - future optimization

---

## Success Criteria

### Measurable Outcomes
1. **Functional Correctness**: 100+ GraphQL query/mutation test cases pass with expected results
2. **Performance**: 95% of GraphQL queries execute within 10% overhead vs equivalent SQL
3. **N+1 Prevention**: All nested queries execute ≤2 SQL queries via DataLoader batching
4. **Type Safety**: GraphQL schema introspection returns complete type information for all entities
5. **Real-time Updates**: Subscriptions deliver events to clients within 100ms of mutation

### Definition of Done
- [ ] GraphQL schema defined for all core entities (Protein, Gene, Pathway)
- [ ] Query resolvers implemented for node lookups, relationships, vector search
- [ ] Mutation resolvers implemented for create, update, delete with FK validation
- [ ] Subscription resolvers implemented for entity creation/update events
- [ ] DataLoader batching implemented to prevent N+1 queries
- [ ] GraphQL Playground deployed and accessible
- [ ] 100+ integration tests passing
- [ ] Performance benchmarks documented showing <10% overhead
- [ ] API documentation auto-generated from schema
- [ ] Python client library example published

---

## Review & Acceptance Checklist

### Content Quality
- [x] No implementation details (languages, frameworks, APIs) - kept to requirements level
- [x] Focused on user value and business needs - developer experience, nested queries
- [x] Written for non-technical stakeholders - scenarios use plain language
- [x] All mandatory sections completed

### Requirement Completeness
- [x] No [NEEDS CLARIFICATION] markers remain - **All 8 clarifications resolved**:
  1. DataLoader batching mandatory vs optional → Mandatory (N+1 prevention required)
  2. Max query depth limit → 10 levels (configurable)
  3. Query complexity algorithm (depth vs cost) → Depth-based (simpler, measurable)
  4. Resolver-level caching strategy → 60-second TTL with manual invalidation
  5. CORS allowed origins → Configurable (`*` for dev, explicit domains for prod)
  6. WebSocket connection limit → 1000 concurrent (configurable)
  7. Authentication method → API keys (IRIS REST API pattern)
  8. Field-level authorization model → RBAC (IRIS security model)
- [x] Requirements are testable and unambiguous - all FRs/NFRs have measurable criteria
- [x] Success criteria are measurable - 5 quantitative metrics defined
- [x] Scope is clearly bounded - Out of Scope section lists 8 exclusions
- [x] Dependencies and assumptions identified - 4 dependencies, 4 assumptions

---

## Execution Status

- [x] User description parsed
- [x] Key concepts extracted (GraphQL, type-safe, nested queries, subscriptions)
- [x] Ambiguities marked (8 NEEDS CLARIFICATION items)
- [x] User scenarios defined (5 acceptance scenarios + edge cases)
- [x] Requirements generated (36 functional + 17 non-functional)
- [x] Entities identified (schema types, input types, query results)
- [x] Review checklist passed - **All clarifications resolved**

**Status**: Ready for `/plan` workflow
