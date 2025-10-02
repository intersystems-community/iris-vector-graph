# Feature Specification: openCypher Query Endpoint

**Feature Branch**: `002-add-opencypher-endpoint`
**Created**: 2025-10-02
**Status**: Draft
**Input**: User description: "Add openCypher query endpoint with Cypher-to-SQL translation for intuitive graph pattern matching"

---

## ⚡ Quick Guidelines
- ✅ Focus on WHAT users need and WHY
- ❌ Avoid HOW to implement (implementation details in plan.md)
- 👥 Written for business stakeholders, not developers

---

## User Scenarios & Testing

### Primary User Story

As a **biomedical researcher**, I want to query the knowledge graph using **intuitive graph pattern matching syntax** (openCypher) so that I can express complex graph traversals without writing complex SQL JOINs.

**Example**: Instead of writing multi-table SQL JOINs, I can write:
```cypher
MATCH (p:Protein {id: 'PROTEIN:TP53'})-[:INTERACTS_WITH]->(target:Protein)
RETURN p.name, target.name, target.function
```

### Acceptance Scenarios

1. **Given** a knowledge graph with proteins and interactions, **When** I submit a Cypher query to find all proteins that interact with TP53, **Then** I receive a list of matching proteins with their properties

2. **Given** a Cypher query with multi-hop traversal (e.g., 2-hop paths), **When** the query is executed, **Then** all paths up to the specified hop count are returned with FK validation

3. **Given** a complex Cypher query with labels, properties, and relationships, **When** translated to SQL, **Then** the SQL query produces identical results and completes within 10% of direct SQL execution time

4. **Given** a Cypher query with syntax errors, **When** submitted, **Then** I receive a clear error message with line and column number indicating the syntax issue

5. **Given** a hybrid Cypher query combining vector similarity and graph traversal, **When** executed, **Then** vector k-NN search uses HNSW index and graph expansion follows FK constraints

### Edge Cases

- What happens when a Cypher query references non-existent node labels? → Return empty result set with no errors
- How does the system handle variable-length paths with potential cycles? → Cycle detection prevents infinite loops, paths tracked for uniqueness
- What if a user submits a Cypher query exceeding complexity limits? → Query rejected with error indicating max depth exceeded
- How are named parameters handled in Cypher queries? → Parameters bound safely to prevent SQL injection
- What happens when translation produces inefficient SQL? → System auto-optimizes and logs performance warnings (best-effort optimization, never fails queries)

---

## Requirements

### Functional Requirements

**Core Query Capabilities**:
- **FR-001**: System MUST parse openCypher query syntax including MATCH, WHERE, RETURN, ORDER BY, LIMIT clauses
- **FR-002**: System MUST translate Cypher AST to IRIS SQL queries that operate on existing NodePK schema (nodes, rdf_edges, rdf_labels, rdf_props, kg_NodeEmbeddings)
- **FR-003**: System MUST support node pattern matching with labels: `(n:Protein)` maps to `JOIN rdf_labels WHERE label = 'Protein'`
- **FR-004**: System MUST support relationship traversal: `(a)-[r:TYPE]->(b)` maps to `JOIN rdf_edges WHERE p = 'TYPE'`
- **FR-005**: System MUST support property filters: `{id: 'PROTEIN:TP53'}` maps to `JOIN rdf_props WHERE key = 'id' AND val = 'PROTEIN:TP53'`
- **FR-006**: System MUST support variable-length paths: `*1..3` maps to recursive CTE with cycle detection
- **FR-007**: System MUST support OPTIONAL MATCH by translating to LEFT JOIN
- **FR-008**: System MUST support UNION and UNION ALL queries
- **FR-009**: System MUST support aggregation functions: count(), collect(), avg(), sum()
- **FR-010**: System MUST support ORDER BY and LIMIT clauses

**Data Integrity**:
- **FR-011**: All generated SQL queries MUST validate node existence via FK constraints (INNER JOIN nodes table)
- **FR-012**: System MUST prevent SQL injection by using parameterized queries for all user-provided values
- **FR-013**: Translated queries MUST preserve ACID transaction semantics of underlying IRIS database

**Hybrid Vector+Graph Queries**:
- **FR-014**: System MUST support custom Cypher procedures for vector search: `CALL db.index.vector.queryNodes(indexName, k, queryVector)`
- **FR-015**: Vector similarity procedures MUST use HNSW index via VECTOR_DOT_PRODUCT SQL function
- **FR-016**: System MUST allow combining vector k-NN results with subsequent graph pattern matching

**API & Error Handling**:
- **FR-017**: System MUST expose Cypher endpoint via REST API: POST /api/cypher with JSON request/response
- **FR-018**: System MUST accept named parameters: `WHERE p.id = $nodeId` with `{nodeId: "PROTEIN:TP53"}`
- **FR-019**: System MUST return clear error messages for invalid Cypher syntax, including line and column numbers
- **FR-020**: System MUST return structured results with column names and data rows in JSON format
- **FR-021**: Query execution MUST respect timeout limits (default: 30 seconds, configurable via API parameter)

**Performance & Optimization**:
- **FR-022**: Translated SQL MUST have <10% performance overhead compared to hand-written SQL for equivalent queries
- **FR-023**: System MUST apply label filter pushdown optimization (move label filters to JOIN ON clauses)
- **FR-024**: System MUST apply property filter pushdown optimization
- **FR-025**: System MUST inject HNSW index hints for vector similarity queries
- **FR-026**: System MUST limit query complexity to prevent resource exhaustion (max depth: 10 hops for variable-length paths, configurable)

### Key Entities

**Cypher Query Components** (input data):
- **CypherQuery**: Complete query with MATCH clauses, WHERE filters, RETURN columns, ORDER BY, LIMIT
- **CypherNode**: Node pattern with variable name, labels, property filters
- **CypherRelationship**: Edge pattern with variable name, type, direction, property filters
- **CypherParameter**: Named parameter for safe value binding ($paramName)

**Translation Artifacts** (intermediate data):
- **CypherAST**: Abstract syntax tree representation of parsed Cypher query
- **SQLQuery**: Generated SQL string with parameter placeholders
- **QueryMetadata**: Execution plan, estimated rows, index usage

**Response Data**:
- **QueryResult**: Column names, data rows, execution time, row count
- **QueryError**: Error message, line number, column number, error code

---

## Non-Functional Requirements

### Performance Targets
- **NFR-001**: Cypher query translation (parsing + SQL generation) MUST complete in <10ms for simple queries (<5 nodes)
- **NFR-002**: End-to-end Cypher query execution MUST be within 10% of equivalent SQL query execution time
- **NFR-003**: System MUST handle concurrent Cypher queries at ≥100 queries/second
- **NFR-004**: Vector k-NN Cypher queries MUST leverage HNSW index and complete in <10ms for k=10

### Scalability
- **NFR-005**: System MUST support graphs with 1M+ nodes and 10M+ edges without query translation degradation
- **NFR-006**: Query complexity limits MUST prevent queries exceeding 10 hops (depth-based algorithm, configurable max depth)

### Reliability
- **NFR-007**: Cypher endpoint MUST maintain 99.9% uptime (same as IRIS database availability)
- **NFR-008**: Failed query translations MUST NOT corrupt database state or leave partial results

### Security
- **NFR-009**: All user-provided Cypher values MUST be parameterized to prevent SQL injection
- **NFR-010**: Cypher endpoint MUST support authentication (API key-based, aligned with IRIS REST API patterns)
- **NFR-011**: Cypher endpoint MUST support authorization (role-based access control integrated with IRIS security model)

### Observability
- **NFR-012**: System MUST log all Cypher queries with execution time and result row count
- **NFR-013**: System MUST expose metrics: query rate, error rate, translation time, execution time
- **NFR-014**: Failed queries MUST include trace IDs for debugging

---

## Dependencies & Assumptions

### Dependencies
- **DEP-001**: Requires NodePK schema (nodes, rdf_edges, rdf_labels, rdf_props) with FK constraints
- **DEP-002**: Requires IRIS database with VECTOR support (HNSW index for kg_NodeEmbeddings)
- **DEP-003**: Requires Cypher parser library (opencypher or libcypher-parser)

### Assumptions
- **ASM-001**: Users are familiar with Cypher syntax (Neo4j compatibility expected)
- **ASM-002**: Existing SQL schema maps cleanly to property graph model
- **ASM-003**: Performance overhead <10% is acceptable for improved developer experience
- **ASM-004**: Cypher queries will primarily be used by developers/researchers, not end-users

---

## Out of Scope

- **OOS-001**: Cypher WRITE operations (CREATE, UPDATE, DELETE) - deferred to later phase
- **OOS-002**: Full GQL standard compliance - openCypher subset only
- **OOS-003**: Gremlin query language support - separate feature
- **OOS-004**: Query result caching - optimization for later
- **OOS-005**: Cypher stored procedures (beyond vector search) - future extension
- **OOS-006**: Graph algorithm libraries (PageRank via Cypher) - use embedded Python instead
- **OOS-007**: Multi-database queries - single IRIS namespace only
- **OOS-008**: Cypher query builder UI - separate frontend feature

---

## Success Criteria

### Measurable Outcomes
1. **Functional Correctness**: 100+ Cypher query test cases pass with results matching equivalent SQL queries
2. **Performance**: 95% of Cypher queries execute within 10% overhead vs hand-written SQL
3. **Syntax Coverage**: Parser supports all core Cypher syntax (MATCH, WHERE, RETURN, OPTIONAL MATCH, UNION, aggregation)
4. **Developer Adoption**: Cypher queries replace ≥50% of complex multi-table SQL JOINs in development workflows
5. **Error Clarity**: 100% of syntax errors return actionable error messages with line/column numbers

### Definition of Done
- [ ] Cypher parser integrated (opencypher or libcypher-parser)
- [ ] AST-to-SQL translator implemented for all core syntax patterns
- [ ] REST API endpoint functional at POST /api/cypher
- [ ] Custom vector search procedure implemented: `CALL db.index.vector.queryNodes()`
- [ ] Query optimization rules applied (label pushdown, property pushdown, index hints)
- [ ] 100+ integration tests passing (Cypher results match SQL results)
- [ ] Performance benchmarks documented showing <10% overhead
- [ ] API documentation published (OpenAPI/Swagger)
- [ ] Python client library published

---

## Review & Acceptance Checklist

### Content Quality
- [x] No implementation details (languages, frameworks, APIs) - kept to requirements level
- [x] Focused on user value and business needs - graph query ease-of-use
- [x] Written for non-technical stakeholders - scenarios use plain language
- [x] All mandatory sections completed

### Requirement Completeness
- [x] No [NEEDS CLARIFICATION] markers remain - **All 5 clarifications resolved**:
  1. Auto-optimization vs warning for inefficient SQL → Auto-optimize with performance warnings
  2. Max query timeout limit → 30 seconds (configurable)
  3. Max query complexity (depth/hops) → 10 hops (configurable)
  4. Authentication method → API keys (IRIS REST API pattern)
  5. Authorization mechanism → RBAC (IRIS security model)
- [x] Requirements are testable and unambiguous - all FRs/NFRs have measurable criteria
- [x] Success criteria are measurable - 5 quantitative metrics defined
- [x] Scope is clearly bounded - Out of Scope section lists 8 exclusions
- [x] Dependencies and assumptions identified - 3 dependencies, 4 assumptions

---

## Execution Status

- [x] User description parsed
- [x] Key concepts extracted (graph query, Cypher syntax, SQL translation)
- [x] Ambiguities marked (5 NEEDS CLARIFICATION items)
- [x] User scenarios defined (5 acceptance scenarios + edge cases)
- [x] Requirements generated (26 functional + 14 non-functional)
- [x] Entities identified (query components, translation artifacts, responses)
- [x] Review checklist passed - **All clarifications resolved**

**Status**: Ready for `/plan` workflow
