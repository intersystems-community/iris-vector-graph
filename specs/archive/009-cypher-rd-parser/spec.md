# Feature Specification: Recursive-Descent Cypher Parser

**Feature Branch**: `001-cypher-rd-parser`  
**Created**: 2026-01-25  
**Status**: Draft  
**Input**: User description: "implement recursive-descent parser for cypher to replace the temporary regex-based parser with a flexible recursive-descent implementation supporting WITH clauses, aggregations, and built-in functions."

## Clarifications

### Session 2026-01-25
- Q: Variable Scoping in WITH clauses → A: Strict openCypher: Only variables in `WITH` are carried over.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Standard Graph Retrieval (Priority: P1)

As a data analyst, I want to execute standard Cypher queries to find nodes and relationships so that I can perform basic graph exploration without regressions from the previous parser.

**Why this priority**: Essential baseline functionality. The new parser must at least do what the old one did to ensure continuity for existing users.

**Independent Test**: Can be fully tested by running the existing fraud detection demo and verifying all 6 steps pass without modification to the demo scripts.

**Acceptance Scenarios**:

1. **Given** a fraud detection dataset, **When** I run `MATCH (a:Account) RETURN a.account_type LIMIT 10`, **Then** I receive exactly 10 results containing account types.
2. **Given** a relationship query, **When** I run `MATCH (t:Transaction)-[:FROM_ACCOUNT]->(a:Account) RETURN t.amount`, **Then** the system returns the correct transaction amounts connected by the specified relationship.

---

### User Story 2 - Chained Query Logic with WITH (Priority: P2)

As a fraud analyst, I want to pipe results from one query pattern into another using the `WITH` clause so that I can perform multi-stage traversals and filter by intermediate results (e.g., find accounts with many transactions and then explore their specific neighbors).

**Why this priority**: This addresses the most significant functional gap identified in the project assessment. It enables complex fraud pattern detection and iterative graph analysis.

**Independent Test**: Can be tested by executing a multi-part query such as `MATCH (a:Account)-[r]->(t:Transaction) WITH a, count(t) AS txn_count WHERE txn_count > 5 MATCH (a)-[:OWNED_BY]->(p:Person) RETURN p.name`.

**Acceptance Scenarios**:

1. **Given** accounts with varying transaction counts, **When** I use `WITH` to filter by an aggregation (e.g., `count(t) > 5`) and then continue matching, **Then** only the results passing the intermediate filter are returned.

---

### User Story 3 - Analytical Aggregations (Priority: P3)

As a business user, I want to calculate summary statistics like counts, sums, and averages directly in Cypher so that I can perform analytical reporting without manual post-processing of results.

**Why this priority**: High value for reporting and identifying outliers (e.g., total transaction volume per account) directly from the query interface.

**Independent Test**: Can be tested by running queries using standard Cypher aggregation functions like `sum()`, `avg()`, and `count()`.

**Acceptance Scenarios**:

1. **Given** multiple transactions in the database, **When** I execute `MATCH (t:Transaction) RETURN sum(t.amount), avg(t.amount)`, **Then** the system returns the correct mathematical sum and average of the amounts.

---

### Edge Cases

- **Malformed Patterns**: How does the system handle syntactically invalid patterns (e.g., missing brackets in `(n:Label-[]->(m)`)?
- **Invalid Functions**: How does the system handle calls to built-in functions that do not exist or are called with incorrect argument types?
- **Empty Intermediate Results**: What happens if a query stage returns no results but is followed by a `WITH` and further `MATCH` clauses?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST implement a recursive-descent parser for the Cypher query language.
- **FR-002**: System MUST support the `WITH` clause for chaining multiple query stages.
- **FR-003**: System MUST support standard aggregation functions: `COUNT`, `SUM`, `AVG`, `MIN`, `MAX`.
- **FR-004**: System MUST support built-in graph functions: `id()`, `type()`, `labels()`.
- **FR-005**: System MUST support multiple `MATCH` clauses within a single query stage.
- **FR-006**: System MUST support nested expressions in the `WHERE` clause using parentheses for logical grouping.
- **FR-007**: System MUST provide clear syntax error messages identifying the line and column position of the error.
- **FR-008**: System MUST support incoming (`<-[]-`) and bidirectional (`-[]-`) relationship patterns in `MATCH` clauses.
- **FR-009**: System MUST enforce strict openCypher variable scoping: only variables explicitly named in the `WITH` clause are available to the subsequent part of the query.

### Key Entities *(include if feature involves data)*

- **Lexer**: Responsible for breaking the Cypher query text into a stream of typed tokens (keywords, literals, operators).
- **Abstract Syntax Tree (AST)**: A structural representation of the query used for translation and validation.
- **Statement**: The root entity of the AST, representing the entire query potentially composed of multiple `QueryParts`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of queries currently supported by `SimpleCypherParser` produce identical results with the new parser.
- **SC-002**: Queries containing at least 3 `WITH` clause stages are parsed and translated successfully.
- **SC-003**: Parsing overhead for a standard query (under 500 characters) is less than 10ms.
- **SC-004**: Invalid queries trigger an error message that accurately points to the character position of the failure.
