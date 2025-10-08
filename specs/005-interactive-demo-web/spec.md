# Feature Specification: Interactive IRIS Demo Web Interface

**Feature Branch**: `005-interactive-demo-web`
**Created**: 2025-01-06
**Status**: Draft
**Input**: User description: "Interactive demo web server showcasing IRIS capabilities for IDFS (Financial Services) and Life Sciences teams with FastHTML + HTMX"

## Execution Flow (main)
```
1. Parse user description from Input
   → Feature: Interactive demo for two audiences (IDFS, Life Sciences)
2. Extract key concepts from description
   → Actors: IDFS team members, Life Sciences researchers, sales engineers
   → Actions: Explore fraud detection, query biomedical data, visualize results
   → Data: Fraud transactions, bitemporal audit trails, protein networks
   → Constraints: Must demonstrate both domains, live data, interactive
3. For each unclear aspect:
   → [NEEDS CLARIFICATION: Deployment target - internal only or customer-facing?]
   → [NEEDS CLARIFICATION: Authentication requirements for demo access?]
   → [NEEDS CLARIFICATION: Data privacy - can we show real 130M transaction data or need synthetic?]
4. Fill User Scenarios & Testing section
   ✓ Clear user flows identified for both audiences
5. Generate Functional Requirements
   ✓ Each requirement testable
6. Identify Key Entities
   ✓ Demo sessions, query results, visualizations
7. Run Review Checklist
   ⚠ WARN "Spec has uncertainties" - 3 clarifications needed
8. Return: SUCCESS (spec ready for planning after clarifications)
```

---

## ⚡ Quick Guidelines
- ✅ Focus on WHAT users need and WHY
- ❌ Avoid HOW to implement (no tech stack, APIs, code structure)
- 👥 Written for business stakeholders, not developers

---

## User Scenarios & Testing

### Primary User Story

**As an IDFS Sales Engineer**, I need to demonstrate IRIS fraud detection capabilities to financial services prospects so they can see real-time fraud scoring, bitemporal audit trails, and compliance reporting in action.

**As a Life Sciences Product Manager**, I need to show biomedical researchers how IRIS handles protein interaction networks and pathway analysis so they understand the platform's versatility beyond financial services.

### Acceptance Scenarios

#### Financial Services Scenarios

1. **Given** a prospect wants to see fraud detection in action, **When** they enter transaction details (payer, amount, device), **Then** the system displays a fraud probability score with explanation within 2 seconds

2. **Given** a compliance officer asks "what did we know at approval time?", **When** they select a transaction and specify a historical timestamp, **Then** the system shows the exact fraud score and status as it existed at that moment

3. **Given** a prospect is concerned about settlement delays, **When** they view the late arrival detection dashboard, **Then** the system displays transactions reported >24 hours after occurrence with suspicious patterns highlighted

4. **Given** a regulatory auditor needs to see audit trail preservation, **When** they select a transaction, **Then** the system displays complete version history showing who changed what and when

#### Biomedical Scenarios

5. **Given** a researcher wants to find similar proteins, **When** they enter a protein name or upload a sequence, **Then** the system displays top 10 most similar proteins with similarity scores

6. **Given** a scientist is investigating interaction pathways, **When** they select two proteins, **Then** the system visualizes the shortest interaction path between them with intermediate nodes

7. **Given** a prospect wants to see hybrid search capabilities, **When** they enter both a text query and semantic search criteria, **Then** the system combines results using both methods and explains the fusion ranking

8. **Given** a user wants to explore network connections, **When** they click on a protein in the visualization, **Then** the system expands to show direct interaction partners

### Edge Cases

- What happens when fraud scoring API is unavailable? → System displays cached DEMO_MODE data with notice
- How does system handle protein queries with no results? → Shows "no matches found" with suggestion to broaden search
- What if bitemporal query timestamp is invalid? → Validation error with helpful message about valid date ranges
- How does network visualization perform with >1000 nodes? → [NEEDS CLARIFICATION: Should system limit graph size or use clustering?]

---

## Requirements

### Functional Requirements

#### General Requirements
- **FR-001**: System MUST provide separate demonstration modes for Financial Services and Biomedical Research use cases
- **FR-002**: System MUST display real-time query results within 2 seconds for typical demonstrations
- **FR-003**: System MUST preserve demonstration state during a single session so users can return to previous queries
- **FR-004**: System MUST provide explanatory text for each capability being demonstrated
- **FR-005**: System MUST work with existing fraud detection API (`:8100`) and biomedical graph backend without requiring data migration

#### Financial Services Requirements
- **FR-006**: System MUST allow users to submit transaction details for fraud scoring (payer, payee, amount, device, merchant, IP)
- **FR-007**: System MUST display fraud probability score with human-readable risk classification (low, medium, high, critical)
- **FR-008**: System MUST provide bitemporal time-travel queries allowing users to specify a historical timestamp
- **FR-009**: System MUST display complete audit trail for any transaction showing all versions with reasons for changes
- **FR-010**: System MUST identify and highlight late-arriving transactions (reported >24h after occurrence)
- **FR-011**: System MUST demonstrate chargeback workflow showing status transitions
- **FR-012**: System MUST show compliance reporting scenarios (SOX, MiFID II, Basel III) with sample queries

#### Biomedical Requirements
- **FR-013**: System MUST allow users to search for proteins by name or identifier
- **FR-014**: System MUST display vector similarity results with similarity scores ranked by relevance
- **FR-015**: System MUST visualize protein interaction networks as interactive graphs
- **FR-016**: System MUST support pathway queries showing multi-hop connections between proteins
- **FR-017**: System MUST demonstrate hybrid search combining text keywords and semantic similarity
- **FR-018**: System MUST allow users to expand nodes in graph visualization to explore connections
- **FR-019**: System MUST display query performance metrics (query time, results count, search method used)

#### User Experience Requirements
- **FR-020**: System MUST provide guided tours or sample queries for first-time users
- **FR-021**: System MUST allow users to switch between Financial Services and Biomedical modes without losing session context
- **FR-022**: System MUST display result data in both tabular and visual formats where appropriate
- **FR-023**: System MUST provide export capability for demonstration results [NEEDS CLARIFICATION: Export formats needed - JSON, CSV, PDF?]
- **FR-024**: System MUST include educational tooltips explaining IRIS-specific features (Globals, embedded Python, HNSW indexing)

### Key Entities

- **Demo Session**: Represents a user's interaction with the demo system, tracks selected mode (Financial/Biomedical), query history, and visualization state

- **Fraud Transaction Query**: User-submitted transaction details for fraud scoring including payer, payee, amount, device, merchant, IP address, timestamp

- **Fraud Scoring Result**: System-generated fraud probability, risk classification, contributing factors, and scoring timestamp

- **Bitemporal Query**: User-specified event ID and historical timestamp for time-travel queries

- **Bitemporal Result**: Historical version of transaction data including fraud score, status, and who made changes

- **Protein Query**: User-submitted protein identifier or search criteria for biomedical queries

- **Protein Search Result**: Matching proteins with similarity scores, interaction counts, and metadata

- **Interaction Network**: Graph structure showing protein nodes and edges representing interactions, with visualization state

- **Query Performance Metrics**: Execution time, backend used, result count, and search methods applied

---

## Review & Acceptance Checklist

### Content Quality
- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

### Requirement Completeness
- [ ] No [NEEDS CLARIFICATION] markers remain - **3 clarifications needed**:
  1. Deployment target (internal vs customer-facing)
  2. Data privacy for 130M fraud database
  3. Graph visualization size limits
  4. Export formats needed
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

---

## Execution Status

- [x] User description parsed
- [x] Key concepts extracted
- [x] Ambiguities marked (4 items)
- [x] User scenarios defined (8 acceptance scenarios)
- [x] Requirements generated (24 functional requirements)
- [x] Entities identified (9 key entities)
- [ ] Review checklist passed - **Pending clarifications**

---

## Notes for Planning Phase

**Existing Assets to Leverage**:
- Fraud detection API running on `:8100/fraud/score` (130M transactions, licensed IRIS)
- Bitemporal schema and queries in `examples/bitemporal/`
- Biomedical graph with STRING DB integration
- Vector search and hybrid search (RRF) capabilities

**Key Success Metrics**:
- Demo can showcase both domains within 15 minutes
- Live queries return results in <2 seconds
- Visual explanations make IRIS capabilities clear to non-technical audience
- Demo drives internal adoption and customer interest

**Risks**:
- Performance degradation if 130M fraud database access is slow
- Graph visualization complexity for large protein networks
- Browser compatibility for interactive visualizations
- Need for sanitized demo data if showing to external customers
