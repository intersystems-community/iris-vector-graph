# Feature Specification: Cypher Relationship Pattern Support

**Feature Branch**: `001-cypher-relationship-patterns`  
**Created**: 2026-01-25  
**Status**: Draft  
**Input**: User description: "Fix Cypher MVP parser to support relationship patterns in queries"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Query Transactions with Relationship Types (Priority: P1)

As a fraud analyst, I want to write Cypher queries that traverse relationships between nodes (like transactions connecting to accounts) so that I can detect suspicious patterns in the data.

**Why this priority**: This is the core functionality that is currently broken. Without relationship pattern support, users cannot query graph traversals, which is the primary purpose of a Cypher interface.

**Independent Test**: Can be tested by submitting a Cypher query with a relationship pattern via the API and receiving correct results matching the graph data.

**Acceptance Scenarios**:

1. **Given** a database with Transaction nodes connected to Account nodes via FROM_ACCOUNT relationships, **When** I submit the query `MATCH (t:Transaction)-[r:FROM_ACCOUNT]->(a:Account) RETURN t, a LIMIT 5`, **Then** the system returns up to 5 transaction-account pairs connected by FROM_ACCOUNT relationships.

2. **Given** a database with nodes and relationships, **When** I submit a query with a relationship variable like `MATCH (t:Transaction)-[r:FROM_ACCOUNT]->(a:Account) RETURN r`, **Then** the system returns the relationship details.

3. **Given** a database with Transaction and Account nodes, **When** I submit a query with relationship and node properties like `MATCH (t:Transaction)-[r:FROM_ACCOUNT]->(a:Account) RETURN t.amount, a.status`, **Then** the system returns the specified properties from both nodes.

---

### User Story 2 - Query with Multiple Relationship Types (Priority: P2)

As a fraud analyst, I want to query relationships matching multiple types (e.g., FROM_ACCOUNT or TO_ACCOUNT) in a single pattern so that I can find all transactions involving an account regardless of direction.

**Why this priority**: Multi-type relationship patterns are common in graph queries and significantly reduce the need for UNION queries or multiple API calls.

**Independent Test**: Can be tested by submitting a Cypher query with pipe-separated relationship types and verifying results include matches for all specified types.

**Acceptance Scenarios**:

1. **Given** a database with Transaction nodes connected to Account nodes via both FROM_ACCOUNT and TO_ACCOUNT relationships, **When** I submit the query `MATCH (t:Transaction)-[:FROM_ACCOUNT|TO_ACCOUNT]->(a:Account) RETURN t, a`, **Then** the system returns transaction-account pairs for both relationship types.

2. **Given** a database with multiple relationship types, **When** I submit a query with 3+ relationship types like `[:TYPE_A|TYPE_B|TYPE_C]`, **Then** the system returns matches for all specified types.

---

### User Story 3 - Query Any Relationship Type (Priority: P3)

As a data analyst, I want to query relationships without specifying a type so that I can explore the graph structure and discover connection patterns.

**Why this priority**: Type-less relationship queries are useful for exploration but less common in production queries. Supporting this enables graph discovery workflows.

**Independent Test**: Can be tested by submitting a Cypher query without a relationship type and verifying it returns relationships of any type.

**Acceptance Scenarios**:

1. **Given** a database with multiple relationship types between nodes, **When** I submit the query `MATCH (a)-[r]->(b) RETURN a, type(r), b LIMIT 10`, **Then** the system returns node pairs with relationships of any type.

2. **Given** a database with edges, **When** I submit a pattern with just a relationship variable `MATCH (a)-[r]->(b)`, **Then** the system matches all outgoing relationships regardless of predicate.

---

### Edge Cases

- What happens when a relationship type doesn't exist in the database? (Should return empty results, not error)
- What happens when node labels in the pattern don't match any nodes? (Should return empty results)
- How does the system handle queries with multiple relationship patterns in sequence (e.g., `(a)-[r1]->(b)-[r2]->(c)`)? (Should support chain traversals)
- What happens when a relationship pattern uses a type with special characters or spaces? (Should either support quoting or return a clear parsing error)
- How does the system handle relationship patterns with variable-length paths like `[:TYPE*1..3]`? (Existing support should be preserved)

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST parse Cypher relationship patterns with a single type (e.g., `-[:FROM_ACCOUNT]->`)
- **FR-002**: System MUST parse Cypher relationship patterns with multiple types separated by pipe (e.g., `-[:FROM_ACCOUNT|TO_ACCOUNT]->`)
- **FR-003**: System MUST parse Cypher relationship patterns without a specified type (e.g., `-[r]->` or `-[]->`)
- **FR-004**: System MUST parse relationship patterns with a variable binding (e.g., `-[r:TYPE]->`)
- **FR-005**: System MUST translate parsed relationship patterns into valid database queries that return correct results
- **FR-006**: System MUST preserve existing support for variable-length relationship patterns (e.g., `[:TYPE*1..3]`)
- **FR-007**: System MUST return appropriate error messages for malformed relationship patterns
- **FR-008**: System MUST support chained relationship patterns (e.g., `(a)-[r1]->(b)-[r2]->(c)`)
- **FR-009**: System MUST correctly join source and target nodes when translating relationship patterns to queries

### Key Entities

- **Relationship Pattern**: A Cypher syntax element representing a directed connection between two nodes, consisting of optional variable binding, optional type(s), and optional variable-length specification
- **Node Pattern**: A Cypher syntax element representing a graph node with optional variable binding, label, and property constraints
- **Graph Pattern**: A complete MATCH clause pattern consisting of alternating node and relationship patterns forming a traversal path

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All Cypher queries with single-type relationship patterns execute successfully and return correct results
- **SC-002**: All Cypher queries with multi-type relationship patterns (using `|` separator) execute successfully and return results matching any of the specified types
- **SC-003**: All Cypher queries with untyped relationship patterns execute successfully and return results for all relationship types
- **SC-004**: 100% of existing Cypher query tests continue to pass (no regression)
- **SC-005**: Relationship pattern queries complete within the same time bounds as equivalent direct queries (no significant performance degradation)
- **SC-006**: Error messages for invalid relationship patterns clearly indicate the syntax issue and expected format

## Assumptions

- The underlying database schema (rdf_edges with s, p, o_id columns) remains unchanged
- The relationship direction in Cypher (`->`) maps to the source-predicate-target structure in rdf_edges
- Relationship properties are not required for this feature (focus is on type and direction)
- Incoming relationships (`<-[]-`) and bidirectional relationships (`-[]-`) are out of scope for this iteration
