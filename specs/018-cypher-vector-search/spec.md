# Feature Specification: Cypher Vector Search

**Feature Branch**: `018-cypher-vector-search`  
**Created**: 2026-02-20  
**Status**: Draft  
**Input**: User description: "define custom cypher vector search. look at ../arno for potential hints"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Semantic Search via Cypher (Priority: P1)

As a developer, I want to perform vector similarity searches directly within Cypher queries using a stored procedure syntax, so that I can easily combine semantic search with graph pattern matching in a single query.

**Why this priority**: This is the core requirement. It eliminates the need for manual SQL/Python hybrid workarounds and enables high-performance semantic retrieval that scales with the database.

**Independent Test**: Can be fully tested by calling the vector search procedure in a Cypher query and verifying that nodes with similar embeddings are returned correctly with their similarity scores.

**Acceptance Scenarios**:

1. **Given** nodes with stored embeddings and an HNSW index, **When** a Cypher query `CALL ivg.vector.search('Label', 'property', $vector, 10) YIELD node, score` is executed, **Then** the top 10 most similar nodes are returned with their respective scores.
2. **Given** a query vector, **When** the procedure is called with a label that does not exist, **Then** an empty result set is returned without error.

---

### User Story 2 - Composable Graph Retrieval (Priority: P2)

As a developer, I want to use the output of a vector search as the starting point for a graph traversal, so that I can find "similar nodes and their related entities" in a single pass.

**Why this priority**: Enables the "Hybrid Retrieval" power of graph databases. This is what separates `iris-vector-graph` from pure vector stores.

**Independent Test**: Run a query that yields nodes from vector search and then `MATCH`es relationships from those nodes. Verify the final result contains data from both stages.

**Acceptance Scenarios**:

1. **Given** a semantic search result, **When** composed with a `MATCH (node)-[:REL]->(other)`, **Then** the final results correctly filter and return the related entities.

---

### User Story 3 - Configurable Similarity Metrics (Priority: P3)

As a developer, I want to specify which similarity metric (Cosine, Euclidean, Dot Product) to use for the vector search, so that I can align the search behavior with my specific embedding model.

**Why this priority**: Different models require different distance metrics for accuracy.

**Independent Test**: Execute the same search with different metrics and verify the scores and order change appropriately.

**Acceptance Scenarios**:

1. **Given** a search query, **When** the options map contains `{similarity: 'cosine'}`, **Then** the underlying SQL uses `VECTOR_COSINE`.
2. **Given** a search query, **When** the options map contains `{similarity: 'dot_product'}`, **Then** the underlying SQL uses `VECTOR_DOT_PRODUCT`.
3. **Given** a search query, **When** an unsupported metric such as `euclidean` is provided, **Then** the system returns a clear error listing the two valid options.

---

### Edge Cases

- **Large Query Vectors**: How does the system handle vectors exceeding 2000 elements? (Assumption: Handled by IRIS native `VECTOR` limits).
- **Missing Embeddings**: What happens if a node matches the label but has no embedding? (Expected: Ignored by the vector search procedure).
- **Empty Top-K**: Passing `limit: 0` should return 0 results.
- **SQL Injection**: Attempting to pass malicious strings into the label or property parameters. (Assumption: These will be sanitized using existing `validate_table_name` logic).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST support the Cypher `CALL` clause for invoking stored procedures.
- **FR-002**: System MUST implement a procedure named `ivg.vector.search` (or similar namespace).
- **FR-003**: The vector search procedure MUST accept: Label (String), Property Name (String), Query Vector (List of Floats), and Limit (Integer).
- **FR-004**: The procedure MUST `YIELD` at least a `node` variable and a `score` variable.
- **FR-005**: The system MUST translate the Cypher procedure call into an optimized database query that leverages available vector indices.
- **FR-006**: System MUST support an optional configuration map for specifying similarity metrics. Allowed values are `cosine` and `dot_product`, matching the two vector similarity functions natively supported by IRIS (`VECTOR_COSINE` and `VECTOR_DOT_PRODUCT`). Euclidean distance is NOT supported. Default metric is `cosine`.
- **FR-007**: Results MUST be ordered by similarity score descending by default.

### Key Entities *(include if feature involves data)*

- **Vector Embedding**: A high-dimensional numeric array associated with a graph entity.
- **Similarity Score**: A value representing the semantic proximity between a query and an entity.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Semantic searches return results in under 100ms for datasets up to 1 million entities on recommended infrastructure.
- **SC-002**: Use of the pure Cypher procedure reduces the amount of application-side integration code by at least 50% compared to existing multi-language workarounds.
- **SC-003**: 100% of standard graph query operations can be successfully composed with vector search results.
- **SC-004**: System supports vector dimensions up to the maximum limit of the underlying data store.

## Assumptions

- We assume the user wants to follow the `posos` proposal for the `ivg.vector.search` naming.
- We assume the existing `kg_NodeEmbeddings` table structure is the primary source for these searches.
- We assume that "looking at arno for hints" implies we should consider how `arno` handles `SoftJoin` or `TIR` lowering, but the user specifically requested a Cypher procedure syntax for this library.
