# Feature Specification: Graph Analytics Kernels — Global PageRank, WCC, CDLP

**Feature Branch**: `024-graph-kernels`  
**Created**: 2026-03-19  
**Status**: Draft  
**Depends on**: 022-wire-up-operators (^KG global, pure ObjectScript pattern)

---

## Problem Statement

The library has Personalized PageRank (seed-biased, local neighborhood) but no whole-graph analytics. Standard graph benchmarks define six core algorithms that validate a system as a graph analytics engine. Three of these — Global PageRank, Weakly Connected Components (WCC), and Community Detection via Label Propagation (CDLP) — share the same iterative computation pattern already proven in the PPR implementation at 62ms on 143K nodes.

Adding these three kernels validates the library as capable of both hybrid retrieval (vector + graph + text) AND graph analytics — not just a query engine but an analysis engine.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Global PageRank for Node Importance (Priority: P0)

As a developer analyzing a knowledge graph, I want to compute global importance scores for all nodes so I can identify the most central entities without specifying seed nodes.

**Why this priority**: PageRank is the most widely recognized graph centrality measure. Global PageRank (uniform teleport to all nodes) complements the existing Personalized PageRank (seed-biased). Together they cover both "what's important globally" and "what's important relative to my query."

**Independent Test**: Build a star graph (hub connected to 4 spokes with bidirectional edges). Run global PageRank. Verify the hub has the highest score. Verify all 5 nodes receive scores. Verify scores sum to approximately 1.0.

**Acceptance Scenarios**:

1. **Given** a star graph (H↔S1, H↔S2, H↔S3, H↔S4), **When** global PageRank is computed with damping=0.85, **Then** hub H has the highest score.
2. **Given** the same graph, **When** results are returned, **Then** all 5 nodes have non-zero scores and scores sum to approximately 1.0.
3. **Given** a 10K-node graph, **When** global PageRank is computed, **Then** it completes in under 500ms.
4. **Given** a graph where PageRank converges early, **When** max_iterations=100 is specified, **Then** computation terminates early (does not run all 100 iterations).

---

### User Story 2 — Weakly Connected Components (Priority: P0)

As a developer, I want to identify all disconnected subgraphs in my knowledge graph so I can measure connectivity and find isolated clusters.

**Why this priority**: WCC is the fundamental graph structure algorithm — it answers "how many separate graphs are in my data?" Essential for data quality assessment and partitioning.

**Independent Test**: Build two disconnected clusters (A-B-C and D-E, no edges between them). Run WCC. Verify A/B/C share one component label. Verify D/E share a different label. Verify exactly 2 distinct components.

**Acceptance Scenarios**:

1. **Given** two disconnected clusters {A-B-C} and {D-E}, **When** WCC is computed, **Then** A, B, C share one component ID and D, E share a different component ID.
2. **Given** the same graph, **When** results are returned, **Then** exactly 2 distinct component IDs exist.
3. **Given** a fully connected graph, **When** WCC is computed, **Then** all nodes share the same component ID.
4. **Given** an isolated node with no edges, **When** WCC is computed, **Then** it gets its own unique component ID.
5. **Given** a 10K-node graph, **When** WCC is computed, **Then** it completes in under 1 second.

---

### User Story 3 — Community Detection via Label Propagation (Priority: P1)

As a developer, I want to detect densely connected communities within my knowledge graph so I can understand its cluster structure.

**Why this priority**: Community detection reveals the internal organization of a connected graph. While WCC finds disconnected pieces, CDLP finds densely connected groups within a single component.

**Independent Test**: Build two dense clusters (3 nodes each, fully interconnected within cluster) connected by a single sparse bridge edge. Run CDLP. Verify nodes within each cluster share a community label. Verify the two clusters have different labels.

**Acceptance Scenarios**:

1. **Given** two dense clusters connected by one bridge edge, **When** CDLP is computed, **Then** nodes within each cluster share a community label.
2. **Given** the same graph, **When** results are returned, **Then** at least 2 distinct community labels exist.
3. **Given** a 10K-node graph, **When** CDLP is computed, **Then** it completes in under 1 second.

---

### User Story 4 — Performance on Production Data (Priority: P1)

As a developer running graph analytics on the MindWalk 143K-node dataset, I want all three kernels to complete in reasonable time so they're usable in interactive workflows.

**Why this priority**: Performance on real data validates that the algorithms scale beyond toy test graphs.

**Independent Test**: Run all three kernels on MindWalk. Assert completion times. Verify result counts match expected node count.

**Acceptance Scenarios**:

1. **Given** the MindWalk dataset (143K nodes, 240K edges), **When** global PageRank is computed, **Then** it completes in under 5 seconds and returns scores for all nodes.
2. **Given** the same dataset, **When** WCC is computed, **Then** it completes in under 10 seconds.
3. **Given** the same dataset, **When** CDLP is computed, **Then** it completes in under 10 seconds.

---

### User Story 5 — Cypher Procedures (Priority: P2, stretch)

As a Cypher user, I want graph analytics available as procedures so I can invoke them from Cypher queries.

**Independent Test**: Parse and translate Cypher `CALL ivg.pagerank(0.85, 20) YIELD node, score`. Verify SQL/CTE generation.

---

### Edge Cases

- Empty graph (no nodes/edges) → all three return empty results, no error
- Single node with no edges → PageRank returns that node with score 1.0; WCC returns it as its own component; CDLP returns it with its own label
- Self-loops → handled correctly (contribute to own degree)
- Very large max_iterations with early convergence → terminates early, doesn't waste cycles
- Graph with all edges of one type → works normally (edge types ignored for these algorithms)

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Global PageRank MUST compute scores for ALL nodes using uniform teleport (not seed-biased)
- **FR-002**: Global PageRank MUST support early termination via convergence check (max rank delta < threshold)
- **FR-003**: WCC MUST identify all weakly connected components using bidirectional edge traversal
- **FR-004**: WCC MUST assign each node a component label equal to the minimum node ID in its component
- **FR-005**: CDLP MUST assign community labels via iterative propagation where each node adopts the most frequent label among its neighbors
- **FR-006**: All three MUST return results as structured data callable through the system's API bridge
- **FR-007**: All three MUST have a fast server-side execution path with a slower client-side fallback
- **FR-008**: Results MUST be correct on known graph topologies (verified by test)
- **FR-009**: All operations MUST be read-only and idempotent
- **FR-010**: Empty graphs MUST return empty results without error

### Key Entities

- **PageRank scores**: Mapping of node ID → importance score (float, sums to ~1.0)
- **Component labels**: Mapping of node ID → component identifier (string, the minimum node ID in the component)
- **Community labels**: Mapping of node ID → community identifier (string, the dominant label after propagation)

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Global PageRank correctly identifies the hub as highest-scoring node in a star graph — verified by e2e test
- **SC-002**: WCC correctly identifies disconnected components — verified by e2e test with known topology
- **SC-003**: CDLP correctly detects dense clusters — verified by e2e test with bridge-connected clusters
- **SC-004**: All three kernels complete on a 10K-node test graph within performance bounds (PageRank <500ms, WCC <1s, CDLP <1s)
- **SC-005**: All existing library tests continue to pass — no regressions

---

## Out of Scope

- LCC (Local Clustering Coefficient) — requires triangle counting, fundamentally different algorithm
- SSSP (Single Source Shortest Path) — requires priority queue / Dijkstra
- Distributed or parallel execution across multiple processes
- Incremental / streaming computation (recompute on edge insert)
- Full LDBC Graphalytics benchmark driver (data format conversion, scoring system)
- Weighted variants of WCC or CDLP

---

## Assumptions

- The adjacency index is populated and available for server-side traversal (^KG globals from BuildKG)
- The iterative power-method pattern proven in PPR (v1.15.0, 62ms on 143K nodes) applies to all three kernels
- Early termination convergence check (max delta < 0.0001) from PPR applies to PageRank and WCC; CDLP uses "no label changes" as convergence criterion
- WCC uses both outgoing and incoming edges for connectivity (weakly connected = ignoring edge direction)
