# Feature Specification: Variable-Length Relationship Patterns [*1..3]

**Feature Branch**: `034-cypher-var-length-paths`  
**Created**: 2026-03-31  
**Status**: Draft  
**Source**: docs/cypher-gap-recommendations.md Gap #1

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Multi-Hop Traversal with Typed Relationships (Priority: P1)

A developer writing drug repurposing or disease mechanism queries wants to traverse multiple hops with a specific relationship type — e.g., find all genes a drug targets within 1-3 hops through protein interactions.

**Why this priority**: Most important missing feature for biomedical graph analytics. AST already has `VariableLength` node parsed — translator just ignores it. BFSFastJson in ObjectScript provides the execution engine.

**Acceptance Scenarios**:

1. **Given** a drug connected to targets, **When** `MATCH (d:Drug {name:'Aspirin'})-[:TARGETS*1..2]->(g:Gene) RETURN g.name`, **Then** returns direct targets AND targets-of-targets.
2. **Given** a disease network, **When** `MATCH (d1:Disease)-[:COMORBID_WITH*1..3]->(d2:Disease) WHERE d1.name='Diabetes' RETURN d2.name`, **Then** returns diseases up to 3 hops away.
3. **Given** a gene ontology, **When** `MATCH (t:Term)-[:is_a*1..5]->(root:Term {name:'biological_process'}) RETURN t.name`, **Then** traverses the is_a hierarchy up to 5 levels.

---

### User Story 2 — Multi-Hop with Any Relationship Type (Priority: P1)

A developer wants unrestricted multi-hop traversal without specifying relationship types — exploring the full graph neighborhood.

**Acceptance Scenarios**:

1. **Given** any starting node, **When** `MATCH (a {id:'TP53'})-[*1..3]->(b) RETURN b.id`, **Then** returns all reachable nodes within 3 hops via any relationship.
2. **Given** a minimum hop constraint, **When** `MATCH (a)-[*2..3]->(b)`, **Then** returns only nodes at exactly 2 or 3 hops (not direct neighbors).

---

### User Story 3 — Variable-Length Path with Result Filtering (Priority: P2)

A developer wants to filter variable-length traversal results by target node properties.

**Acceptance Scenarios**:

1. **Given** a traversal, **When** `MATCH (d:Drug)-[:TARGETS*1..2]->(g:Gene) WHERE g.name STARTS WITH 'BRCA' RETURN g.name`, **Then** returns only BRCA genes reachable within 2 hops.

---

### Edge Cases

- `[*]` (unbounded): defaults to max hops = 10 (configurable constant).
- `[*2..2]` (exact): returns only nodes at exactly 2 hops.
- Bidirectional `[*1..2]-` or `-[*1..2]`: walks both outgoing and incoming edges.
- Large result sets (N > 1000 sources): per-source BFSFast calls, performance may degrade.
- Chained patterns: `MATCH (a)-[:R1]->(b)-[:R2*1..2]->(c)` — fixed JOIN for R1, BFS for R2.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST support `[*min..max]` variable-length relationship patterns in MATCH.
- **FR-002**: System MUST support typed variable-length patterns `[:TYPE*1..3]`.
- **FR-003**: System MUST support untyped variable-length patterns `[*1..3]`.
- **FR-004**: `[*]` (unbounded) MUST use a configurable default maximum hop count.
- **FR-005**: The traversal MUST use `Graph.KG.Traversal.BFSFastJson` as the execution engine (IRIS does not support recursive CTEs).
- **FR-006**: Variable-length patterns MUST work in queries with additional WHERE clauses on source or target nodes.
- **FR-007**: Named path variables on variable-length patterns (`p = (a)-[*1..3]->(b)`) MUST collect the traversal edges.

### Key Entities

- **VariableLength**: Already in AST (`RelationshipPattern.variable_length: Optional[VariableLength]`). Parser already emits it. Translator ignores it — this feature enables the translator to act on it.
- **BFSFastJson**: ObjectScript classmethod on `Graph.KG.Traversal`. Takes `(srcId, predsJson, maxHops, dstLabel)`, returns JSON array of `{s, p, o, w, step}` objects.

## Success Criteria *(mandatory)*

- **SC-001**: Single-source typed traversal completes in under 50ms for graphs up to 300K edges.
- **SC-002**: Minimum hop constraint (`[*2..3]`) correctly excludes direct neighbors.
- **SC-003**: Zero regressions on existing 230 unit tests.
- **SC-004**: At least 8 unit tests + 5 e2e tests covering the test matrix from gap doc.

## Assumptions

- IRIS does NOT support recursive CTEs. BFSFastJson over `^KG` globals is the only viable server-side approach.
- The parser already handles `*min..max` and emits `VariableLength` AST nodes. No parser changes needed except possibly for `+` and `{n,m}` quantifier syntax.
- For multi-source queries (many Drug nodes), traversal runs per source node. N > 1000 sources will be slow — document as known limitation.

## Scope Boundaries

**In scope (Phase 1)**:
- Single-source fixed queries (WHERE source.id = 'X' or similar)
- Multi-source label-based queries (all Drug nodes)
- Typed and untyped patterns
- Min/max hop constraints
- Integration with BFSFastJson via SQL function wrapper

**Out of scope (Phase 2)**:
- Quantified path patterns `->+` `->*` `{n,m}` (desugar after Phase 1)
- REDUCE() over path edges (Python post-processing, separate spec)
- Bidirectional unlimited traversal (performance concern, separate spec)
- Multi-source batching optimization (>1000 sources)
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
