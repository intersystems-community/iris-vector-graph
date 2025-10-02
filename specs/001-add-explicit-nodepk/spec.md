# Feature Specification: Explicit Node Identity Table

**Feature Branch**: `001-add-explicit-nodepk`
**Created**: 2025-09-30
**Status**: Draft
**Input**: User description: "add explicit NodePK table with foreign key constraints"

---

## ⚡ Quick Guidelines
- ✅ Focus on WHAT users need and WHY
- ❌ Avoid HOW to implement (no tech stack, APIs, code structure)
- 👥 Written for business stakeholders, not developers

---

## User Scenarios & Testing

### Primary User Story
As a graph database administrator, I need the system to enforce referential integrity across all graph entities so that data consistency is guaranteed and orphaned references are prevented. When I add nodes, edges, labels, properties, or embeddings, the system must validate that referenced nodes actually exist and prevent deletion of nodes that are still referenced by other entities.

### Acceptance Scenarios

1. **Given** a new edge is being created, **When** the source or destination node ID does not exist in the nodes table, **Then** the system rejects the edge creation with a clear foreign key constraint violation error

2. **Given** a node has associated edges, labels, properties, or embeddings, **When** an attempt is made to delete that node, **Then** the system prevents deletion and reports which dependent entities are blocking the operation

3. **Given** multiple processes are inserting graph data concurrently, **When** they reference the same nodes, **Then** the system ensures all nodes are created exactly once with no duplicates or race conditions

4. **Given** a data import process loads nodes and edges, **When** edges reference non-existent nodes, **Then** the system reports all invalid references before any data is committed

5. **Given** existing graph data with implicit node identity, **When** migrating to explicit node identity, **Then** all existing nodes are discovered, deduplicated, and validated without data loss

### Edge Cases

- What happens when a node ID appears in edges but not in labels or properties tables? System must create the node record automatically or reject the orphaned edge.
- What happens when importing large datasets with circular dependencies between nodes and edges? System must support deferred constraint validation or require nodes-first ordering.
- What happens when a node exists but has no labels, properties, or embeddings? System must allow "bare" nodes as valid graph entities.
- What happens during migration if duplicate node IDs exist across different tables? System must detect and report duplicates with resolution strategy.

## Requirements

### Functional Requirements

- **FR-001**: System MUST maintain a central nodes table containing all unique node identifiers in the graph
- **FR-002**: System MUST enforce uniqueness of node identifiers to prevent duplicate nodes
- **FR-003**: System MUST reject creation of edges, labels, properties, or embeddings that reference non-existent nodes via foreign key constraints
- **FR-004**: System MUST prevent deletion of nodes that are referenced by edges, labels, properties, or embeddings
- **FR-005**: System MUST provide clear error messages when foreign key constraint violations occur, identifying the specific constraint and conflicting data
- **FR-006**: System MUST support cascading operations when explicitly requested (e.g., delete node and all dependent entities)
- **FR-007**: System MUST detect and report orphaned references during data validation
- **FR-008**: System MUST provide migration capability to convert existing implicit node identity to explicit node identity without data loss
- **FR-009**: System MUST maintain referential integrity even under concurrent write operations
- **FR-010**: System MUST allow querying the complete list of nodes independent of whether they have labels, properties, or edges

### Performance Requirements

- **PR-001**: Node existence validation MUST complete in under 1ms for single lookups
- **PR-002**: Bulk node insertion MUST support at least 1000 nodes per second
- **PR-003**: Foreign key constraint checking MUST not degrade edge insertion performance by more than 10% compared to current unconstrained implementation
- **PR-004**: Migration from implicit to explicit node identity MUST process at least 10,000 nodes per second

### Data Integrity Requirements

- **DI-001**: System MUST guarantee that every edge references valid source and destination nodes
- **DI-002**: System MUST guarantee that every label references a valid node
- **DI-003**: System MUST guarantee that every property references a valid node
- **DI-004**: System MUST guarantee that every embedding references a valid node
- **DI-005**: System MUST prevent creation of edges where source and destination are identical unless explicitly allowed
- **DI-006**: System MUST maintain referential integrity across transaction boundaries

### Key Entities

- **Node**: The fundamental graph entity representing a unique identifier in the graph. A node may have zero or more labels, properties, edges, and embeddings. Nodes exist independently and are the primary key for all other graph structures.

- **Edge**: A directed relationship between two nodes (source and destination) with an edge type/predicate. Every edge MUST reference exactly two valid nodes.

- **Label**: A type classification attached to a node. Multiple labels can apply to the same node. Every label assignment MUST reference a valid node.

- **Property**: A key-value attribute attached to a node. Multiple properties can apply to the same node. Every property MUST reference a valid node.

- **Embedding**: A vector representation of a node for similarity search. Each node may have zero or one embedding. Every embedding MUST reference a valid node.

---

## Review & Acceptance Checklist

### Content Quality
- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

### Requirement Completeness
- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

---

## Execution Status

- [x] User description parsed
- [x] Key concepts extracted
- [x] Ambiguities marked
- [x] User scenarios defined
- [x] Requirements generated
- [x] Entities identified
- [x] Review checklist passed

---

## Clarifications

### Session 2025-10-02

- Q: Should NodePK spec include hybrid query patterns (vector + graph), or focus purely on referential integrity? → A: Referential integrity only - NodePK is foundational infrastructure

---

## Architectural Context

This feature provides the **foundational infrastructure** for the IRIS Vector Graph system by establishing explicit node identity and referential integrity. While NodePK itself focuses solely on data consistency, it enables higher-level capabilities defined in the system constitution:

**Enabled Use Cases** (defined in constitution, not part of this feature):
- **Hybrid Search** (Constitution Principle IV): Vector similarity + text search + graph traversal with RRF fusion
- **Graph Analytics**: PageRank, Connected Components, BFS on integrity-validated graph structures
- **Vector-Guided Graph Operations**: HNSW k-NN search (SQL-based) → graph expansion → analytics (embedded Python)

**Architectural Relationship**:
```
NodePK (this feature)
  ↓ Provides: Explicit node identity + FK constraints + validated references
Constitution Principles
  ↓ Define: Hybrid search patterns, IRIS-native development, performance targets
Higher-Level Features
  ↓ Implement: Vector-guided PageRank, hybrid RAG, semantic graph traversal
```

**Critical Constraint** (from `docs/architecture/embedded_python_architecture.md`):
- HNSW vector operations MUST use SQL (tightly coupled to query planner)
- Pure graph operations CAN use embedded Python with global access (10-50x faster)
- FK constraints (from NodePK) validate all node references in both patterns

**Reference**: See `docs/architecture/embedded_python_architecture.md` for detailed hybrid query architecture.

---

## Dependencies and Assumptions

### Dependencies
- Current RDF-style schema with rdf_edges, rdf_labels, rdf_props tables
- Current kg_NodeEmbeddings table for vector storage
- Existing graph data may be present and requires migration

### Assumptions
- Node identifiers are string-based (VARCHAR) as currently implemented
- Database supports foreign key constraints with standard SQL syntax
- System can tolerate brief downtime during schema migration
- Existing data quality is sufficient (no widespread orphaned references that would block migration)

---

## Out of Scope

The following are explicitly NOT part of this feature:
- Changes to node identifier format or structure
- Implementation of node metadata beyond identity (creation time, modification time, etc.)
- Graph versioning or temporal tracking of node existence
- Soft deletion or archival of nodes
- Node-level access control or security policies
- Performance optimization of existing queries (unless degraded by constraints)
