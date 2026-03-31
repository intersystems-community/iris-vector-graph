# Feature Specification: Cypher CASE WHEN Expression

**Feature Branch**: `033-cypher-case-when`  
**Created**: 2026-03-31  
**Status**: Draft  
**Source**: docs/cypher-gap-recommendations.md Gap #3

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Conditional Classification in RETURN (Priority: P1)

A developer writing biomedical queries wants to classify entities based on property values in the RETURN clause — e.g., risk stratification of patients, confidence tier assignment for drug targets, severity binning.

**Why this priority**: Sprint 2 item. 1:1 SQL mapping, 50-70 lines, enables clinical conditional logic that is currently impossible in IVG Cypher.

**Acceptance Scenarios**:

1. **Given** conditions with severity properties, **When** `RETURN CASE c.severity WHEN 'CRITICAL' THEN 3 WHEN 'MODERATE' THEN 2 ELSE 1 END AS score`, **Then** each condition gets the correct numeric score.
2. **Given** drug nodes with confidence scores, **When** `RETURN CASE WHEN d.confidence > 0.9 THEN 'high' WHEN d.confidence > 0.5 THEN 'medium' ELSE 'low' END AS tier`, **Then** drugs are correctly tiered.
3. **Given** a CASE expression in a WHERE clause, **When** filtering by `WHERE CASE WHEN n.type = 'A' THEN n.score ELSE 0 END > 0.5`, **Then** only qualifying nodes are returned.

---

### User Story 2 — Simple CASE (Equality-Based) (Priority: P2)

A developer uses the simpler `CASE expr WHEN val1 THEN result1 ...` form for lookup tables.

**Acceptance Scenarios**:

1. **Given** ICD codes, **When** `RETURN CASE icd.chapter WHEN 'J' THEN 'Respiratory' WHEN 'E' THEN 'Endocrine' ELSE 'Other' END AS category`, **Then** codes are correctly categorized.

---

### Edge Cases

- CASE with no ELSE: results in NULL for unmatched cases (standard SQL behavior).
- Nested CASE: `CASE WHEN CASE ... END = 1 THEN ...` — should work via expression recursion.
- CASE in ORDER BY: `ORDER BY CASE WHEN n.type = 'A' THEN 1 ELSE 2 END`.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST support searched CASE: `CASE WHEN condition THEN result [ELSE default] END`.
- **FR-002**: System MUST support simple CASE: `CASE expr WHEN val THEN result [ELSE default] END`.
- **FR-003**: CASE MUST work in RETURN, WHERE, ORDER BY, and WITH clauses.
- **FR-004**: CASE MUST support nested expressions (property references, function calls, literals) in WHEN conditions and THEN results.
- **FR-005**: The generated SQL MUST use standard SQL CASE WHEN (1:1 mapping — no semantic transformation needed).

## Success Criteria *(mandatory)*

- **SC-001**: Searched CASE works in RETURN, WHERE, and ORDER BY.
- **SC-002**: Simple CASE works for equality matching.
- **SC-003**: Zero regressions on existing 230 unit tests.
- **SC-004**: At least 5 unit tests + 3 e2e tests.

## Assumptions

- CASE is a syntactic construct, not a function call. It requires new AST node, new lexer tokens (CASE/WHEN/THEN/ELSE/END), and parser changes.
- The generated SQL is identical to Cypher CASE — IRIS SQL CASE WHEN is 1:1 compatible.

## Scope Boundaries

**In scope**: Searched CASE, Simple CASE, in RETURN/WHERE/ORDER BY.
**Out of scope**: CASE in SET assignments (deferred), list comprehensions that look CASE-like.
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
