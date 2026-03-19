# Feature Specification: kg_SUBGRAPH — Bounded Subgraph Extraction

**Feature Branch**: `023-kg-subgraph`  
**Created**: 2026-03-19  
**Status**: Draft  
**Depends on**: 022-wire-up-operators (provides ^KG global, kg_NEIGHBORS, kg_PPR, BFSFast)

---

## Problem Statement

Developers building RAG pipelines and ML workflows need to extract a complete bounded subgraph — all nodes, edges, properties, and embeddings within k hops of seed nodes — in a single call. Today this requires 4+ separate SQL queries, manual assembly, and produces no standard format.

Graph databases are typically optimized for *query* (filter, join, aggregate) rather than *extraction* (give me the raw adjacency and features for this neighborhood). The MindWalk pipeline demonstrates the need: after vector search finds seed articles and neighbor expansion finds anchor entities, the next step is "give me the full local graph around these anchors" — and there's no single method for that.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Complete Subgraph for RAG Context (Priority: P0)

As a developer building a retrieval pipeline, I want to extract all nodes, edges, properties, and labels within k hops of seed nodes in a single call, so I can provide rich graph context to an LLM without hand-rolling SQL.

**Why this priority**: This is the core value proposition — eliminating the 4+ round-trip workaround.

**Independent Test**: Insert a graph with known topology (10 nodes, 15 edges, properties, labels). Extract 2-hop subgraph from one seed. Verify all reachable nodes/edges/properties are present. Verify unreachable nodes are absent.

**Acceptance Scenarios**:

1. **Given** a graph A->B->C->D (chain) and A->E (branch), **When** subgraph is extracted from A with k_hops=2, **Then** result contains nodes {A,B,C,E}, edges {A->B, B->C, A->E}, and all their properties/labels. D is excluded (3 hops away).
2. **Given** the same graph, **When** extracted from A with k_hops=1, **Then** result contains only {A,B,E} and edges {A->B, A->E}.
3. **Given** multiple seeds [A, D], **When** extracted with k_hops=1, **Then** result is the union of both 1-hop neighborhoods.
4. **Given** a seed that doesn't exist in the graph, **When** extracted, **Then** that seed is silently excluded — no error, other seeds still work.

---

### User Story 2 — Edge Type Filtering (Priority: P1)

As a developer, I want to filter subgraph extraction by edge type so I can extract only the relevant portion of the graph structure (e.g., only MENTIONS edges, not CITES).

**Why this priority**: Different ML tasks need different graph projections. A biomedical RAG pipeline needs MENTIONS edges; a citation analysis needs CITES edges.

**Independent Test**: Insert graph with mixed edge types (MENTIONS, CITES, INTERACTS). Extract with edge_types=["MENTIONS"]. Verify only MENTIONS edges appear. Verify nodes reachable only via excluded edge types are not in the subgraph.

**Acceptance Scenarios**:

1. **Given** A-MENTIONS->B, A-CITES->C, B-MENTIONS->D, **When** extracted from A with k_hops=2 and edge_types=["MENTIONS"], **Then** result contains {A,B,D} with edges {A-MENTIONS->B, B-MENTIONS->D}. C is excluded.
2. **Given** edge_types=None (default), **When** extracted, **Then** all edge types are included.

---

### User Story 3 — Safety Limits (Priority: P1)

As a developer, I want a max_nodes parameter that caps the subgraph size so I don't accidentally try to extract millions of nodes from a dense graph.

**Why this priority**: Without safety limits, a single high-degree hub node can cause memory exhaustion.

**Independent Test**: Insert a dense graph (hub connected to 500+ nodes). Extract with max_nodes=50. Verify result has ≤50 nodes. Verify no error — just truncation.

**Acceptance Scenarios**:

1. **Given** a hub node with 500 neighbors, **When** extracted with k_hops=1 and max_nodes=50, **Then** result contains at most 50 nodes (hub + up to 49 neighbors) and BFS stops expanding the frontier.
2. **Given** max_nodes=10000 (default), **When** graph is smaller, **Then** all reachable nodes are included.

---

### User Story 4 — Include Embeddings for ML (Priority: P1)

As an ML engineer, I want to include node embedding vectors in the extracted subgraph so I can construct feature matrices for GNN input without a separate query.

**Why this priority**: Embedding extraction is the most expensive separate query (bulk vector reads). Including it in subgraph extraction eliminates a critical round-trip.

**Independent Test**: Insert nodes with embeddings. Extract subgraph with include_embeddings=True. Verify embedding vectors are present for nodes that have them. Verify nodes without embeddings are still in the subgraph.

**Acceptance Scenarios**:

1. **Given** nodes A (has embedding), B (has embedding), C (no embedding) in a chain, **When** extracted with include_embeddings=True, **Then** result includes embeddings for A and B as float arrays, C is in nodes but not in embeddings dict.
2. **Given** include_embeddings=False (default), **Then** no embeddings are returned regardless of availability.

---

### User Story 5 — Server-Side Execution (Priority: P0)

As a developer, I want subgraph extraction to execute server-side (not via multiple SQL round-trips from Python) so that extraction is fast enough for interactive use.

**Why this priority**: Multiple SQL round-trips from Python add 10-50ms per query. Server-side execution over the adjacency index completes in <10ms for typical subgraphs.

**Independent Test**: Call the server-side method directly. Verify JSON result matches Python-side extraction. Time it on a 10K-node graph — must complete in under 100ms for 2-hop extraction.

**Acceptance Scenarios**:

1. **Given** a 10K-node graph with average degree 10, **When** 2-hop subgraph is extracted server-side, **Then** result is returned in under 100ms.
2. **Given** the same graph, **When** server-side and Python-side results are compared, **Then** they contain identical nodes and edges.

---

### User Story 6 — PyG-Compatible Tensor Output (Priority: P2, stretch)

As an ML engineer, I want a tensor output format compatible with PyTorch Geometric so I can directly construct `Data` objects for GNN training without manual conversion.

**Why this priority**: Stretch goal. The structured dict from US1 can be converted client-side. Native tensor output is a convenience optimization.

**Independent Test**: Extract subgraph tensors. Verify edge_index shape is [2, E]. Verify node feature matrix shape is [N, D]. Verify index mapping is consistent.

**Acceptance Scenarios**:

1. **Given** a subgraph with N nodes and E edges, **When** extracted as tensors, **Then** edge_index is a numpy array of shape [2, E] and x is [N, D] where D is the embedding dimension.

---

### User Story 7 — Cypher Procedure (Priority: P2, stretch)

As a Cypher user, I want `CALL ivg.subgraph($seeds, 2) YIELD nodes, edges` to work.

**Why this priority**: Stretch goal. Extends the Cypher procedure catalog (alongside ivg.vector.search, ivg.neighbors, ivg.ppr).

**Independent Test**: Parse and translate the Cypher. Verify SQL/CTE generation is correct.

---

### Edge Cases

- Seeds list is empty → return empty subgraph, no error
- All seeds are nonexistent → return empty subgraph, no error
- k_hops=0 → return only the seed nodes themselves (no edges, no expansion)
- Cyclic graph (A->B->A) → nodes appear once, edges appear once (deduplication)
- Disconnected seeds → result is the union of independent neighborhoods
- Node with no outgoing edges (leaf/sink) → included in nodes, no edges from it
- edge_types contains a predicate that doesn't exist → empty subgraph (no matching edges to traverse)
- Very large embeddings (high dimensionality) → include_embeddings=True still works, just larger payload

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST extract all nodes reachable within k hops of the seed nodes via BFS
- **FR-002**: System MUST return complete edge triples (source, predicate, target) for all edges between subgraph nodes
- **FR-003**: System MUST include node properties and labels by default (configurable via include_properties)
- **FR-004**: System MUST include node embedding vectors when include_embeddings=True
- **FR-005**: System MUST support filtering traversal by edge type (predicate) via edge_types parameter
- **FR-006**: System MUST enforce a max_nodes safety limit that stops BFS expansion when reached
- **FR-007**: System MUST deduplicate nodes and edges in the result
- **FR-008**: System MUST silently exclude nonexistent seed nodes (no error)
- **FR-009**: System MUST be read-only (no graph mutations)
- **FR-010**: System MUST support a server-side execution path for performance

### Key Entities

- **SubgraphData**: The result container — nodes, edges, properties, labels, embeddings, and the original seed list
- **Seed nodes**: Starting points for BFS expansion — any valid node IDs
- **Edge triples**: (source, predicate, target) relationships between subgraph nodes
- **Node embeddings**: Fixed-dimension vector representations attached to nodes

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A developer can extract a complete 2-hop subgraph with a single method call — no manual SQL assembly required
- **SC-002**: Subgraph extraction on a 10K-node graph completes in under 100ms
- **SC-003**: Edge type filtering produces subgraphs containing only the specified relationship types
- **SC-004**: Safety limits prevent extraction of more than the specified maximum number of nodes
- **SC-005**: Embedding vectors are included in the result when requested, enabling direct construction of ML feature matrices
- **SC-006**: All existing library tests continue to pass — no regressions
- **SC-007**: Results from server-side and client-side extraction paths are identical for the same input

---

## Out of Scope

- Batch subgraph sampling for GNN training loops (1000s of seeds in parallel — future: kg_BATCH_SUBGRAPHS)
- Arno Rust FFI acceleration (future: when Arno graph layer matures)
- Commit-scoped or versioned subgraph extraction (future: when graph versioning lands)
- Weighted shortest-path subgraphs (SSSP is a different algorithm)
- Incremental / streaming subgraph updates (CDC-style)
- Graph sampling strategies beyond BFS (random walk, importance sampling)

---

## Assumptions

- The adjacency index is populated and available for server-side traversal (spec 021/022 dependency)
- Node embeddings are stored in a queryable table alongside the graph schema
- The server-side implementation follows the same pure-ObjectScript pattern established in spec 022 (PageRank.RunJson, BFSFast)
- The Python API result format (SubgraphData) will be a dataclass or named structure, not a raw dict
- Initial implementation: server-side returns graph structure (nodes, edges, properties, labels) from adjacency globals; Python layer fetches embeddings via a single SQL query for the returned node IDs. The server-side method is not prohibited from using embedded SQL in the future — IRIS SQL is globals underneath and has negligible overhead — but the v1 split keeps the traversal hot path simple and independently testable.

---

## Clarifications

### Session 2026-03-19

- Q: Should the server-side method fetch embeddings (requiring embedded SQL), or should Python fetch embeddings separately after receiving the node list? → A: Option B — server-side returns structure only; Python fetches embeddings in one SQL query. But the architecture should not prohibit server-side embedded SQL in future iterations since IRIS SQL is globals underneath with negligible overhead.
