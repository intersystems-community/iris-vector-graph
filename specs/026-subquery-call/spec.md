# Feature Specification: Subquery Clauses (CALL { ... })

**Feature Branch**: `026-subquery-call`  
**Created**: 2026-03-27  
**Status**: Draft  
**Input**: User description: "docs/enhancements/003-subquery-call.md"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Run an Independent Subquery with Aggregation (Priority: P1)

A developer wants to encapsulate a query fragment that runs in its own scope and feeds aggregated results into the outer query. They write a `CALL { MATCH ... RETURN ... }` block that executes independently, with its output variables available to the outer RETURN clause.

**Why this priority**: Independent subqueries are the simplest variant and the most common use case — aggregation within a subquery that feeds outer query logic. This is the foundation that must work before correlated subqueries.

**Independent Test**: Can be tested by executing an independent subquery against a graph with labeled nodes, verifying the subquery output variables appear in the outer result set with correct aggregated values.

**Acceptance Scenarios**:

1. **Given** a graph with Drug-labeled nodes, **When** a user executes `CALL { MATCH (n:Drug) RETURN n.name AS name } RETURN name`, **Then** the result contains one row per Drug node with the correct name value.
2. **Given** a graph with multiple entity types, **When** a user executes `CALL { MATCH (n:Gene) RETURN count(n) AS gene_count } RETURN gene_count`, **Then** the result contains a single row with the correct count.
3. **Given** an independent subquery with no matching data, **When** the subquery returns zero rows, **Then** the outer query also returns zero rows.

---

### User Story 2 - Run a Correlated Subquery with Imported Variable (Priority: P2)

A developer wants to compute a per-node metric (e.g., neighbor count) by importing an outer variable into a subquery via WITH. The subquery runs once per outer row, and its output is joined back to the outer result.

**Why this priority**: Correlated subqueries enable per-row computation patterns like degree counting, local aggregation, and conditional expansion. They depend on the independent subquery infrastructure being in place first.

**Independent Test**: Can be tested by executing a correlated subquery that counts neighbors for each node, verifying each outer row has the correct computed value.

**Acceptance Scenarios**:

1. **Given** a graph with Protein nodes connected by INTERACTS_WITH edges, **When** a user executes `MATCH (p:Protein) CALL { WITH p MATCH (p)-[:INTERACTS_WITH]->(partner) RETURN count(partner) AS degree } RETURN p.id, degree`, **Then** each Protein row includes the correct interaction count.
2. **Given** a node with zero outgoing edges, **When** the correlated subquery counts neighbors, **Then** the degree value is 0 (not NULL, not missing row).
3. **Given** a correlated subquery that does NOT import the outer variable via WITH, **When** the query references the outer variable inside the subquery, **Then** the system raises a scope error.

---

### User Story 3 - Parse IN TRANSACTIONS Hint (Priority: P3)

A developer writes a batch-delete pattern using `CALL { ... } IN TRANSACTIONS OF N ROWS`. The system parses this syntax correctly and executes the subquery as a single transaction (Phase 1 no-op behavior).

**Why this priority**: `IN TRANSACTIONS` is a Neo4j compatibility feature for batch DML. Phase 1 only needs to parse it without error — actual batched execution is Phase 2.

**Independent Test**: Can be tested by parsing a query with `IN TRANSACTIONS OF 500 ROWS` and verifying the AST captures the flag and batch size without execution errors.

**Acceptance Scenarios**:

1. **Given** a query with `CALL { MATCH (n:OldNode) DELETE n } IN TRANSACTIONS OF 500 ROWS`, **When** the system parses this query, **Then** the AST contains `in_transactions=True` and `transactions_batch_size=500`.
2. **Given** the same query, **When** the system executes it, **Then** it runs as a single transaction (Phase 1 no-op) without error.
3. **Given** `CALL { ... } IN TRANSACTIONS` without a batch size, **When** parsed, **Then** `in_transactions=True` and `transactions_batch_size=None`.

---

### Edge Cases

- What happens when a subquery RETURN clause uses a variable name that conflicts with an outer variable? The subquery output shadows the outer variable in subsequent clauses.
- What happens when a subquery contains no RETURN clause? The system raises a parse error — subqueries must yield results.
- What happens when a correlated subquery imports a variable that doesn't exist in the outer scope? The system raises a scope/translation error.
- What happens when an independent subquery references an outer variable without WITH? The system raises a scope error — outer variables are not visible without explicit import.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST support `CALL { MATCH ... RETURN ... }` syntax for independent subqueries that run in their own scope.
- **FR-002**: System MUST make subquery output variables (from RETURN) available to the outer query's RETURN, WHERE, and WITH clauses.
- **FR-003**: System MUST support `CALL { WITH x MATCH ... RETURN ... }` syntax for correlated subqueries that import outer variables.
- **FR-004**: Outer variables MUST NOT be visible inside an independent subquery (no WITH import).
- **FR-005**: Subquery-internal variables MUST NOT leak into the outer scope.
- **FR-006**: System MUST parse `IN TRANSACTIONS` and `IN TRANSACTIONS OF N ROWS` syntax without error.
- **FR-007**: In Phase 1, `IN TRANSACTIONS` MUST be treated as a no-op (single transaction execution).
- **FR-008**: System MUST raise a clear error when a subquery is missing a RETURN clause.
- **FR-009**: Correlated subqueries with single-column aggregates MUST work on all supported target platforms.
- **FR-010**: Independent subqueries MUST work with aggregation functions (count, sum, avg, collect) in their RETURN clause.
- **FR-011**: Correlated subqueries MUST preserve the outer row when the subquery returns zero results, with 0 or NULL for the subquery output columns (LEFT JOIN semantics, Neo4j-compatible).

### Key Entities

- **SubqueryCall**: A clause representing a `CALL { ... }` block. Key attributes: the inner query, list of imported variables, IN TRANSACTIONS flag, optional batch size.
- **Scope Boundary**: The separation between outer and inner variable namespaces. Outer variables are only accessible inside the subquery if explicitly imported via WITH.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Developers can execute independent subqueries that return aggregated results to the outer query.
- **SC-002**: Developers can execute correlated subqueries that compute per-row metrics using imported outer variables.
- **SC-003**: Variable scope isolation is enforced — outer variables are not accessible without explicit WITH import.
- **SC-004**: 100% of existing Cypher unit and e2e tests continue to pass after subquery support is added (zero regressions).
- **SC-005**: Subquery parsing and translation is covered by at least 8 unit tests and 4 e2e tests.

## Assumptions

- Independent subqueries translate to CTEs (Common Table Expressions) in the generated SQL.
- Correlated subqueries translate to scalar subqueries in the SELECT list for single-column aggregates (Phase 1). Full LATERAL join support is Phase 2.
- `IN TRANSACTIONS` is parsed but treated as a no-op in Phase 1 — all execution is single-transaction.
- The `{` and `}` tokens already exist in the lexer for property map parsing and can be reused for subquery delimiting. Context disambiguation (property map vs subquery block) is handled by the parser based on the preceding `CALL` keyword.
- Nested subqueries (`CALL { CALL { ... } }`) are out of scope for Phase 1.

## Scope Boundaries

**In scope (Phase 1)**:
- `SubqueryCall` AST node
- Parser support for `CALL { ... }` and `CALL { WITH x ... }`
- Independent subquery → CTE translation
- Correlated single-column aggregate → scalar subquery in SELECT
- `IN TRANSACTIONS` parsing (no-op execution)
- Variable scope isolation enforcement
- Unit and e2e tests

**Out of scope (Phase 2 / future)**:
- Full LATERAL join for multi-column correlated subqueries
- `IN TRANSACTIONS OF N ROWS` batched execution
- Nested subqueries (`CALL { CALL { ... } }`)
- Subqueries with UNION inside the CALL block
- Write-only subqueries (CALL with no RETURN — Neo4j extension, not openCypher)

## Clarifications

### Session 2026-03-27

- Q: When a correlated subquery returns zero rows for a specific outer row, should the outer row be preserved or dropped? → A: Preserve outer row with 0/NULL (LEFT JOIN semantics, Neo4j-compatible)
