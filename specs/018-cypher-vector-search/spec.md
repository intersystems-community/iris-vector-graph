# Feature Specification: Cypher Vector Search

**Feature Branch**: `018-cypher-vector-search`  
**Created**: 2026-02-20  
**Status**: Draft  
**Input**: User description: "define custom cypher vector search. look at ../arno for potential hints"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Semantic Search via Cypher (Priority: P1)

As a developer, I want to perform vector similarity searches directly within Cypher queries using a stored procedure syntax, so that I can easily combine semantic search with graph pattern matching in a single query — either by supplying a pre-computed vector or a raw text string that the system vectorizes automatically.

**Why this priority**: This is the core requirement. It eliminates the need for manual SQL/Python hybrid workarounds and enables high-performance semantic retrieval that scales with the database. Supporting both input modes removes the need for `iris-vector-rag` as a dependency when auto-vectorization is needed.

**Independent Test**: Can be fully tested by calling the vector search procedure in a Cypher query (once with a pre-computed vector, once with a text string) and verifying that nodes with similar embeddings are returned correctly with their similarity scores.

**Acceptance Scenarios**:

1. **Given** nodes with stored embeddings and an HNSW index, **When** a Cypher query `CALL ivg.vector.search('Label', 'property', $vector, 10) YIELD node, score` is executed with `$vector` bound to a `list[float]`, **Then** the top 10 most similar nodes are returned with their respective scores.
2. **Given** IRIS 2024.3+ with a registered embedding config (`%Embedding.Config`), **When** `CALL ivg.vector.search('Label', 'property', $text, 10, {embedding_config: 'minilm'}) YIELD node, score` is executed with `$text` bound to a string, **Then** IRIS `EMBEDDING($text, 'minilm')` is used for query-time vectorization with no Python embedding code in the caller.
3. **Given** an IRIS version prior to 2024.3 (no `EMBEDDING()` support), **When** a text string is passed as the query, **Then** the system raises a clear `UnsupportedOperationError` indicating that `embedding_config` requires IRIS 2024.3+.
4. **Given** a query vector or text, **When** the procedure is called with a label that does not exist, **Then** an empty result set is returned without error.

---

### User Story 2 - Composable Graph Retrieval (Priority: P2)

As a developer, I want to use the output of a vector search as the starting point for a graph traversal, so that I can find "similar nodes and their related entities" in a single pass.

**Why this priority**: Enables the "Hybrid Retrieval" power of graph databases. This is what separates `iris-vector-graph` from pure vector stores.

**Independent Test**: Run a query that yields nodes from vector search and then `MATCH`es relationships from those nodes. Verify the final result contains data from both stages.

**Acceptance Scenarios**:

1. **Given** a semantic search result, **When** composed with a `MATCH (node)-[:REL]->(other)`, **Then** the final results correctly filter and return the related entities.
2. **Given** `CALL ivg.vector.search(...) YIELD node, score MATCH (node)-[:REL]->(other)`, **When** executed, **Then** `node` and `score` are available in the outer scope without an explicit `WITH` clause (standard Cypher `CALL` scoping semantics).

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

### User Story 4 - IRIS-native Auto-Vectorization (Priority: P2)

As a developer using IRIS 2024.3+, I want to pass a raw text string to the vector search procedure and have IRIS vectorize it automatically using a registered embedding config, so that I do not need to manage an embedding model in my application code or add a dependency on `iris-vector-rag`.

**Why this priority**: Removes the biggest friction point for adopters: the need to wire up a separate embedding pipeline just to do semantic search.

**Independent Test**: Execute `CALL ivg.vector.search('Label', 'prop', $text, 5, {embedding_config: 'minilm'})` with a registered `%Embedding.Config` named `minilm`; verify results are equivalent to passing the pre-computed vector for the same text.

**Acceptance Scenarios**:

1. **Given** a registered `%Embedding.Config` entry, **When** the procedure receives a text string and the config name, **Then** the generated SQL contains `EMBEDDING($text, 'minilm')` and no Python vectorization code is invoked.
2. **Given** the same text passed both as a pre-computed vector (Mode 1) and as a string with config (Mode 2), **When** both queries are run against the same dataset, **Then** the top-K results and relative ordering are identical (within floating-point tolerance).

---

### Edge Cases

- **Large Query Vectors**: How does the system handle vectors exceeding 2000 elements? (Assumption: Handled by IRIS native `VECTOR` limits).
- **Missing Embeddings**: If a node matches the label but has no embedding, it is silently excluded from results. If the label does not exist at all, an empty result set is returned. Both cases return an empty result silently — no warning, no exception. The caller is responsible for interpreting zero results.
- **Empty Top-K**: Passing `limit: 0` should return 0 results.
- **SQL Injection**: Attempting to pass malicious strings into the label or property parameters. (Assumption: These will be sanitized using existing `validate_table_name` logic).
- **Text input without embedding_config**: System MUST raise a descriptive error; no silent fallback.
- **IRIS version < 2024.3 with text input**: System MUST raise `UnsupportedOperationError`; capability check performed lazily on first text+config invocation, result cached.
- **Unregistered embedding_config name**: IRIS itself will raise an error; `ivg` propagates it without wrapping.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST support the Cypher `CALL` clause for invoking stored procedures. Yielded variables (`node`, `score`) MUST be introduced directly into the outer query scope (standard OpenCypher `CALL` semantics), enabling subsequent `MATCH` clauses to reference them without an explicit `WITH` clause.
- **FR-002**: System MUST implement a procedure named `ivg.vector.search` (or similar namespace).
- **FR-003**: The vector search procedure MUST accept: Label (String), Property Name (String), Query Input (either a `list[float]` pre-computed vector OR a text string for auto-vectorization), and Limit (Integer). An optional configuration map MAY include `embedding_config` (String) naming a registered `%Embedding.Config` entry; this key is required when the query input is a text string.
- **FR-003a**: When the query input is a `list[float]`, the system MUST emit `TO_VECTOR($param)` in the underlying SQL. No external embedding dependency is required.
- **FR-003b**: When the query input is a text string and `embedding_config` is provided, the system MUST emit `EMBEDDING($param, 'config_name')` in the underlying SQL, delegating vectorization to IRIS 2024.3+ native capability. The system MUST NOT add `iris-vector-rag` or any external embedding library as a dependency.
- **FR-003c**: If a text string is passed without `embedding_config`, or if IRIS does not support `EMBEDDING()`, the system MUST raise a descriptive error; it MUST NOT silently fall back to another mode.
- **FR-003d**: IRIS `EMBEDDING()` capability MUST be detected lazily — only when the text+`embedding_config` code path is first invoked. Detection result MUST be cached on the engine instance for subsequent calls. Callers using Mode 1 (pre-computed vector) MUST incur zero detection overhead.
- **FR-004**: The procedure MUST `YIELD` at least a `node` variable and a `score` variable. The `node` variable MUST be a full node dict with shape `{"id": ..., "labels": [...], "properties": {...}}`, consistent with the shape returned by `get_nodes()`. The `score` variable MUST be a float in the range `[0.0, 1.0]` for cosine, or an unbounded float for dot product.
- **FR-005**: The system MUST translate the Cypher procedure call into an optimized database query that leverages available vector indices.
- **FR-006**: System MUST support an optional configuration map for specifying similarity metrics. Allowed values are `cosine` and `dot_product`, matching the two vector similarity functions natively supported by IRIS (`VECTOR_COSINE` and `VECTOR_DOT_PRODUCT`). Euclidean distance is NOT supported. Default metric is `cosine`.
- **FR-007**: Results MUST be ordered by similarity score descending by default.

### Key Entities *(include if feature involves data)*

- **Vector Embedding**: A high-dimensional numeric array associated with a graph entity.
- **Similarity Score**: A float representing semantic proximity between a query and an entity. Range is `[0.0, 1.0]` for cosine similarity; unbounded for dot product.
- **Node Dict**: The canonical node representation: `{"id": str, "labels": list[str], "properties": dict}`. This is the shape returned by both `get_nodes()` and the `YIELD node` output of `ivg.vector.search`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Semantic searches return results in under 100ms for datasets up to 1 million entities on recommended infrastructure.
- **SC-002**: Use of the pure Cypher procedure reduces the amount of application-side integration code by at least 50% compared to existing multi-language workarounds.
- **SC-003**: 100% of standard graph query operations can be successfully composed with vector search results.
- **SC-004**: System supports vector dimensions up to the maximum limit of the underlying data store.

## Clarifications

### Session 2026-02-20

- Q: How do `CALL ... YIELD` variables bind into subsequent `MATCH` clauses — direct outer-scope injection or required explicit `WITH`? → A: Option A — `CALL` yields directly into outer scope; subsequent `MATCH` reuses the yielded variable without an explicit `WITH` (standard OpenCypher `CALL` semantics).
- Q: Does the procedure accept only pre-computed `list[float]` vectors, or also raw text strings for auto-vectorization? → A: Both. Mode 1 (always available): `list[float]` → `TO_VECTOR()`. Mode 2 (IRIS 2024.3+, no external dependency): text string + `embedding_config` name → IRIS native `EMBEDDING()`. `iris-vector-rag` MUST NOT be added as a dependency. Missing config or unsupported IRIS version → explicit error, no silent fallback.
- Q: How should IRIS `EMBEDDING()` capability be detected — eager at init, lazy at invocation, caught from SQL error, or user-declared? → A: Option B — lazy detection on first text+config invocation, result cached on the engine instance. Zero overhead for Mode 1 (pre-computed vector) callers.
- Q: What shape does the `YIELD node` variable have? → A: Option A — full node dict `{"id": ..., "labels": [...], "properties": {...}}`, consistent with `get_nodes()`. Score is `float` in `[0.0, 1.0]` for cosine, unbounded for dot product.
- Q: Should label-not-found vs label-found-but-no-embeddings be distinguished (warning/error vs silent empty)? → A: Option A — both cases return an empty result set silently. No warning, no exception. Caller interprets zero results.

## Assumptions

- We assume the user wants to follow the `posos` proposal for the `ivg.vector.search` naming.
- We assume the existing `kg_NodeEmbeddings` table structure is the primary source for these searches.
- We assume that "looking at arno for hints" implies we should consider how `arno` handles `SoftJoin` or `TIR` lowering, but the user specifically requested a Cypher procedure syntax for this library.
- We assume `iris-vector-rag` is a sibling package, not a dependency of `iris-vector-graph`. Auto-vectorization uses IRIS native `EMBEDDING()` (requires `%Embedding.Config` registration and IRIS 2024.3+), not any Python embedding library.
- We assume the `%USE_EMBEDDING` privilege is granted to the IRIS user connecting via `irispython`. Privilege errors from IRIS are propagated as-is.
