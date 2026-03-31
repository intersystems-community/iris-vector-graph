# Feature Specification: Cypher CAST Functions + COUNT(DISTINCT)

**Feature Branch**: `032-cypher-cast-coerce`  
**Created**: 2026-03-31  
**Status**: Draft  
**Source**: docs/cypher-gap-recommendations.md Gap #8 (COUNT DISTINCT) + Gap #10 (Type coercion)

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Type Coercion Functions Work Correctly (Priority: P1)

A developer writing biomedical Cypher queries wants to use type coercion functions like `toInteger()`, `toFloat()`, `toString()`, `toBoolean()` to normalize property values. Currently these are mapped in the translator but emit broken SQL (CAST without target type).

**Why this priority**: P0 quick win — 10-line fix. Unblocks chromosome filtering, confidence scoring, and boolean property normalization.

**Acceptance Scenarios**:

1. **Given** a gene node with `chromosome` property "7", **When** `WHERE toInteger(g.chromosome) = 7`, **Then** only genes on chromosome 7 are returned.
2. **Given** nodes with float-valued properties, **When** `toFloat(n.score) > 0.8`, **Then** the comparison works correctly.
3. **Given** integer properties, **When** `toString(n.count)` is used in RETURN, **Then** the output is a string.
4. **Given** a string property "true" or "1", **When** `toBoolean(n.active)` is used, **Then** it returns a truthy value.

---

### User Story 2 — COUNT(DISTINCT) Returns Deduplicated Counts (Priority: P1)

A developer wants `COUNT(DISTINCT g.name) AS unique_genes` to return deduplicated counts.

**Acceptance Scenarios**:

1. **Given** a gene connected to multiple pathways with duplicate relationships, **When** `RETURN COUNT(DISTINCT p.name) AS unique_pathways`, **Then** the result is the correct deduplicated count.
2. **Given** any aggregation query, **When** `COUNT(DISTINCT expr)` is used, **Then** the generated SQL contains `COUNT(DISTINCT ...)`.

---

### Edge Cases

- `toInteger()` on non-numeric string: IRIS CAST raises error — document as expected behavior, not a bug.
- `toBoolean()` on unexpected values: defaults to 0 (false).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `toInteger(expr)` MUST emit `CAST(expr AS INTEGER)`.
- **FR-002**: `toFloat(expr)` MUST emit `CAST(expr AS DOUBLE)`.
- **FR-003**: `toString(expr)` MUST emit `CAST(expr AS VARCHAR(4096))`.
- **FR-004**: `toBoolean(expr)` MUST emit `CASE WHEN expr IN ('true','1','yes') THEN 1 ELSE 0 END`.
- **FR-005**: `COUNT(DISTINCT expr)` MUST emit `COUNT(DISTINCT ...)` in SQL.

## Success Criteria *(mandatory)*

- **SC-001**: All 4 coercion functions return correct results against live IRIS.
- **SC-002**: `COUNT(DISTINCT)` returns deduplicated counts matching direct SQL verification.
- **SC-003**: Zero regressions on existing Cypher tests (currently 230 unit tests passing).
- **SC-004**: At least 6 unit tests + 2 e2e tests.

## Scope Boundaries

**In scope**: `toInteger`, `toFloat`, `toString`, `toBoolean` CAST fixes; `COUNT(DISTINCT)` verification + tests.
**Out of scope**: `split()` → TVF mapping (deferred), new functions not in `_CYPHER_FN_MAP`.
**Created**: [DATE]  
**Status**: Draft  
**Input**: User description: "$ARGUMENTS"

## User Scenarios & Testing *(mandatory)*

<!--
  IMPORTANT: User stories should be PRIORITIZED as user journeys ordered by importance.
  Each user story/journey must be INDEPENDENTLY TESTABLE - meaning if you implement just ONE of them,
  you should still have a viable MVP (Minimum Viable Product) that delivers value.
  
  Assign priorities (P1, P2, P3, etc.) to each story, where P1 is the most critical.
  Think of each story as a standalone slice of functionality that can be:
  - Developed independently
  - Tested independently
  - Deployed independently
  - Demonstrated to users independently
-->

### User Story 1 - [Brief Title] (Priority: P1)

[Describe this user journey in plain language]

**Why this priority**: [Explain the value and why it has this priority level]

**Independent Test**: [Describe how this can be tested independently - e.g., "Can be fully tested by [specific action] and delivers [specific value]"]

**Acceptance Scenarios**:

1. **Given** [initial state], **When** [action], **Then** [expected outcome]
2. **Given** [initial state], **When** [action], **Then** [expected outcome]

---

### User Story 2 - [Brief Title] (Priority: P2)

[Describe this user journey in plain language]

**Why this priority**: [Explain the value and why it has this priority level]

**Independent Test**: [Describe how this can be tested independently]

**Acceptance Scenarios**:

1. **Given** [initial state], **When** [action], **Then** [expected outcome]

---

### User Story 3 - [Brief Title] (Priority: P3)

[Describe this user journey in plain language]

**Why this priority**: [Explain the value and why it has this priority level]

**Independent Test**: [Describe how this can be tested independently]

**Acceptance Scenarios**:

1. **Given** [initial state], **When** [action], **Then** [expected outcome]

---

[Add more user stories as needed, each with an assigned priority]

### Edge Cases

<!--
  ACTION REQUIRED: The content in this section represents placeholders.
  Fill them out with the right edge cases.
-->

- What happens when [boundary condition]?
- How does system handle [error scenario]?

## Requirements *(mandatory)*

<!--
  ACTION REQUIRED: The content in this section represents placeholders.
  Fill them out with the right functional requirements.
-->

### Functional Requirements

- **FR-001**: System MUST [specific capability, e.g., "allow users to create accounts"]
- **FR-002**: System MUST [specific capability, e.g., "validate email addresses"]  
- **FR-003**: Users MUST be able to [key interaction, e.g., "reset their password"]
- **FR-004**: System MUST [data requirement, e.g., "persist user preferences"]
- **FR-005**: System MUST [behavior, e.g., "log all security events"]

*Example of marking unclear requirements:*

- **FR-006**: System MUST authenticate users via [NEEDS CLARIFICATION: auth method not specified - email/password, SSO, OAuth?]
- **FR-007**: System MUST retain user data for [NEEDS CLARIFICATION: retention period not specified]

### Key Entities *(include if feature involves data)*

- **[Entity 1]**: [What it represents, key attributes without implementation]
- **[Entity 2]**: [What it represents, relationships to other entities]

## Success Criteria *(mandatory)*

<!--
  ACTION REQUIRED: Define measurable success criteria.
  These must be technology-agnostic and measurable.
-->

### Measurable Outcomes

- **SC-001**: [Measurable metric, e.g., "Users can complete account creation in under 2 minutes"]
- **SC-002**: [Measurable metric, e.g., "System handles 1000 concurrent users without degradation"]
- **SC-003**: [User satisfaction metric, e.g., "90% of users successfully complete primary task on first attempt"]
- **SC-004**: [Business metric, e.g., "Reduce support tickets related to [X] by 50%"]
