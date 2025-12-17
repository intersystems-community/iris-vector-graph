# Feature Specification: Bidirectional Personalized PageRank

**Feature Branch**: `005-bidirectional-ppr`
**Created**: 2025-12-15
**Status**: Draft
**Input**: Add bidirectional edge traversal to Personalized PageRank for improved multi-hop reasoning in knowledge graphs with asymmetric relationships

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Bidirectional Graph Traversal (Priority: P1)

As a knowledge graph user, I want to discover entities connected through incoming edges so that I can find related entities regardless of edge direction. For example, when querying "Who was Ewan MacColl married to?", the system should find "Peggy Seeger" even though the stored edge points from Peggy to Ewan (Peggy Seeger --[married to]--> Ewan MacColl).

**Why this priority**: This is the core capability that enables multi-hop reasoning in graphs with asymmetric relationships. Without bidirectional traversal, many valid query answers are unreachable, limiting the usefulness of the knowledge graph for question answering.

**Independent Test**: Can be fully tested by running a PageRank query with a known asymmetric edge and verifying the source entity is reachable from the target entity.

**Acceptance Scenarios**:

1. **Given** a graph with edge A→B, **When** user runs PageRank with seed=B and bidirectional=true, **Then** entity A appears in results with a score greater than zero
2. **Given** a graph with edge A→B, **When** user runs PageRank with seed=B and bidirectional=false (default), **Then** entity A does not appear in results (backward compatible behavior)
3. **Given** a graph with edge A→B, **When** user runs PageRank with seed=A and bidirectional=true, **Then** entity B appears in results (forward edges still work)

---

### User Story 2 - Weighted Reverse Edge Control (Priority: P2)

As a knowledge graph user, I want to control how much weight reverse edges contribute to PageRank scores so that I can tune the algorithm for different use cases where reverse relationships may be less relevant than forward relationships.

**Why this priority**: While bidirectional traversal is essential, the ability to weight reverse edges differently provides fine-grained control for domain-specific tuning. Some relationships are inherently directional and reverse traversal should contribute less.

**Independent Test**: Can be fully tested by running PageRank with different reverse_edge_weight values and comparing the resulting scores for reverse-reachable entities.

**Acceptance Scenarios**:

1. **Given** a graph with edge A→B, **When** user runs PageRank with seed=B, bidirectional=true, and reverse_edge_weight=1.0, **Then** entity A receives full contribution from the reverse edge
2. **Given** a graph with edge A→B, **When** user runs PageRank with seed=B, bidirectional=true, and reverse_edge_weight=0.5, **Then** entity A receives approximately half the score compared to weight=1.0
3. **Given** reverse_edge_weight=0.0, **When** user runs PageRank with bidirectional=true, **Then** behavior is equivalent to bidirectional=false (reverse edges contribute nothing)

---

### User Story 3 - Performance Within Acceptable Bounds (Priority: P3)

As a knowledge graph user, I want bidirectional PageRank to complete within acceptable time limits so that interactive query experiences are not degraded when enabling bidirectional mode.

**Why this priority**: Performance is critical for interactive applications, but it's a constraint rather than the primary feature. The feature must work correctly first, then perform acceptably.

**Independent Test**: Can be tested by benchmarking PageRank with and without bidirectional mode on representative graph sizes and verifying the overhead is within acceptable bounds.

**Acceptance Scenarios**:

1. **Given** a graph with 10,000 nodes with idx_edges_oid index present, **When** user runs PageRank with bidirectional=true, **Then** query completes in under 15 milliseconds
2. **Given** a graph with 10,000 nodes without idx_edges_oid index (table scan fallback), **When** user runs PageRank with bidirectional=true, **Then** query completes in under 300 milliseconds
3. **Given** bidirectional=false, **When** user runs PageRank, **Then** performance is identical to current behavior (no regression)

---

### Edge Cases

- What happens when a graph has no edges? System returns only seed entities with their initial scores.
- What happens when reverse_edge_weight is negative? System rejects invalid weight values with a clear error message.
- What happens when reverse_edge_weight is greater than 1.0? System accepts values >1.0, allowing reverse edges to contribute more than forward edges if desired.
- What happens when a bidirectional query is run on a cyclic graph? System handles cycles correctly via PageRank's damping factor, preventing infinite loops.
- What happens when seed entities don't exist in the graph? System returns empty results with no error (current behavior preserved).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST support a bidirectional mode that traverses edges in both directions (forward: subject→object, reverse: object→subject)
- **FR-002**: System MUST default to forward-only traversal (bidirectional=false) to maintain backward compatibility with existing behavior
- **FR-003**: System MUST allow users to specify a weight multiplier for reverse edges (reverse_edge_weight parameter)
- **FR-004**: System MUST apply reverse_edge_weight as a multiplier to the contribution of reverse edges during PageRank computation
- **FR-005**: System MUST validate that reverse_edge_weight is a non-negative number and reject invalid values with a clear error message
- **FR-006**: System MUST preserve all existing PageRank parameters and behavior when bidirectional mode is disabled
- **FR-007**: System MUST work correctly with both optimized index paths and fallback computation paths
- **FR-008**: System MUST log bidirectional and reverse_edge_weight parameter values in existing query logs for observability

### Key Entities

- **Edge**: A directed relationship between two nodes (subject→object), with properties including predicate type and optional weight
- **Reverse Edge**: A virtual edge derived from an existing edge, traversing object→subject with weight adjusted by reverse_edge_weight
- **PageRank Score**: A floating-point value representing the relative importance/relevance of a node based on graph structure and seed proximity

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Users can discover entities connected via incoming edges that were previously unreachable (100% of valid reverse paths now traversable)
- **SC-002**: Query results include reverse-reachable entities with scores proportional to the configured reverse_edge_weight
- **SC-003**: Bidirectional queries on 10,000-node graphs complete within 150% of the time required for forward-only queries
- **SC-004**: All existing tests pass without modification when bidirectional mode is disabled (zero regression)
- **SC-005**: Users can tune reverse edge contribution from 0% to 100%+ of forward edge weight

## Clarifications

### Session 2025-12-15

- Q: What observability requirements should apply to bidirectional mode? → A: Log bidirectional parameter and reverse_edge_weight in existing query logs

## Assumptions

- The existing PageRank implementation supports iteration limits and convergence tolerance to prevent infinite computation on cyclic graphs
- Performance targets assume similar graph density to current test datasets
- The optimized index path and fallback path will both support bidirectional mode
