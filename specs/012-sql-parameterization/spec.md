# Feature Specification: SQL Parameterization Security Fix

**Feature Branch**: `012-sql-parameterization`  
**Created**: 2026-01-26  
**Status**: Draft  
**Input**: User description: "Fix SQL interpolation security risk in GraphOperators.cls by using parameterized queries"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Secure Query Execution (Priority: P1)

As a system operator running the knowledge graph in a production environment, I need all SQL queries to use parameterized values so that the system is protected against SQL injection attacks, even if input validation is bypassed or overlooked in other layers.

**Why this priority**: Security vulnerabilities are critical issues that must be addressed immediately. SQL injection can lead to data breach, data corruption, or system compromise.

**Independent Test**: Can be tested by code review confirming no dynamic SQL string interpolation of user-controlled values, and by attempting to inject SQL through the k parameter in kgTXT.

**Acceptance Scenarios**:

1. **Given** the kgTXT method receives a k value of "10; DROP TABLE rdf_edges;--", **When** the query executes, **Then** the system treats the input as invalid rather than executing malicious SQL
2. **Given** a developer reviews GraphOperators.cls, **When** they examine SQL query construction, **Then** all dynamic values use parameter binding (? placeholders) rather than f-string interpolation
3. **Given** kgTXT is called with a valid integer k value, **When** the query executes, **Then** results are identical to the pre-fix behavior

---

### User Story 2 - Input Validation Defense in Depth (Priority: P2)

As a developer, I want the k parameter to be validated as a positive integer within acceptable bounds before query execution so that the system has multiple layers of defense against malformed input.

**Why this priority**: While parameterization prevents SQL injection, input validation provides defense in depth and clearer error messages for legitimate usage errors.

**Independent Test**: Can be tested by passing various invalid k values (negative, zero, extremely large, non-numeric) and verifying appropriate error responses.

**Acceptance Scenarios**:

1. **Given** kgTXT receives a negative k value, **When** validation runs, **Then** a clear error is returned before query execution
2. **Given** kgTXT receives k=0, **When** validation runs, **Then** a clear error is returned indicating k must be positive
3. **Given** kgTXT receives k exceeding the maximum allowed (e.g., k > 1000), **When** validation runs, **Then** either an error is returned or the value is capped

---

### User Story 3 - Consistent Query Patterns Across Codebase (Priority: P3)

As a developer maintaining the codebase, I want all SQL queries across GraphOperators.cls to follow the same parameterization pattern so that the codebase is consistent and easier to audit for security issues.

**Why this priority**: Consistency reduces cognitive load and makes security audits more reliable, but is less critical than fixing the immediate vulnerability.

**Independent Test**: Can be tested by code review confirming all methods follow the same SQL construction pattern.

**Acceptance Scenarios**:

1. **Given** a security auditor reviews GraphOperators.cls, **When** they examine all SQL queries, **Then** every query uses parameterized values for dynamic inputs
2. **Given** a developer adds a new query method, **When** they reference existing patterns, **Then** they find consistent examples to follow

---

### Edge Cases

- What happens when k is passed as a string representation of a number (e.g., "50")?
- How does the system handle k values at the boundary (k=1, k=MAX_LIMIT)?
- What happens when k is a floating-point number?
- How does the system behave if the SQL parameter binding itself fails?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The kgTXT method MUST use SQL parameter binding for the TOP clause value rather than f-string interpolation
- **FR-002**: System MUST maintain backward compatibility - valid integer k values produce identical results
- **FR-003**: System SHOULD validate that k is a positive integer before query execution
- **FR-004**: System SHOULD enforce a maximum value for k to prevent resource exhaustion
- **FR-005**: All SQL queries in GraphOperators.cls MUST use parameter binding for any dynamic values
- **FR-006**: Error messages for invalid k values MUST NOT reveal internal implementation details

### Key Entities

- **Query Parameter k**: The maximum number of results to return from text search (positive integer, bounded)
- **Search Pattern**: The LIKE pattern constructed from queryText (already parameterized correctly)

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Zero SQL queries in GraphOperators.cls use f-string or format() for dynamic values
- **SC-002**: Security scan/review confirms no SQL injection vulnerabilities
- **SC-003**: All existing tests pass without modification
- **SC-004**: Attempted SQL injection payloads in k parameter are safely rejected or neutralized
