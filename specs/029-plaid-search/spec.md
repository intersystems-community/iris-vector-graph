# Feature Specification: PLAID Multi-Vector Retrieval

**Feature Branch**: `029-plaid-search`  
**Created**: 2026-03-29  
**Status**: Draft  
**Input**: PLAID algorithm (Santhanam et al., NAACL 2022) implemented as pure ObjectScript + $vectorop SIMD

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Build a PLAID Index from Token Embeddings (Priority: P1)

A developer has ColBERT-style multi-vector embeddings (multiple token vectors per document) and wants to build a searchable index. They call a method that clusters the token embeddings into centroids, assigns each token to its nearest centroid, and stores the mapping for fast retrieval.

**Why this priority**: Without the index, no search is possible. The centroid structure is the foundation that enables sub-10ms retrieval instead of brute-force MaxSim over all documents.

**Independent Test**: Can be tested by inserting 500 documents with 50 tokens each (25,000 vectors), building the index, and verifying centroid count (~160), token assignments, and global structure.

**Acceptance Scenarios**:

1. **Given** 500 documents with ~50 token embeddings each (128-dim), **When** a developer calls `plaid_build(name, docs)`, **Then** the Python side runs K-means to compute ~√N centroids, stores them as `$vector` in `^PLAID` globals, assigns each token to its nearest centroid, and builds the inverted index `^PLAID("docCentroid", centroidId, docId)`.
2. **Given** a built index, **When** a developer calls `plaid_info(name)`, **Then** the result includes document count, centroid count, dimension, and total token count.
3. **Given** an empty document list, **When** building an index, **Then** the system returns an error or empty index (no crash).

---

### User Story 2 - Search with Multi-Vector Query (Priority: P1)

A developer has a multi-vector query (e.g., 4 ColBERT token embeddings from a search query) and wants to find the most relevant documents using the PLAID three-stage pipeline: centroid scoring → candidate generation → exact MaxSim. The entire pipeline runs in a single server-side call.

**Why this priority**: This is the core search capability — the reason PLAID exists. Without it, the index is useless.

**Independent Test**: Can be tested by building an index, searching with a known query, and verifying the top result matches the expected document by MaxSim score.

**Acceptance Scenarios**:

1. **Given** a built PLAID index with 500 documents, **When** a developer calls `plaid_search(name, query_tokens, k=10)` with 4 query token embeddings, **Then** the result contains up to 10 ranked documents with MaxSim scores, returned in under 15ms.
2. **Given** the same index and query, **When** comparing PLAID results to brute-force MaxSim over all documents, **Then** recall@10 is at least 90%.
3. **Given** a query with no relevant documents (orthogonal vectors), **When** searching, **Then** the result contains documents with low scores (not empty — PLAID always returns top-k).

---

### User Story 3 - Insert Documents Incrementally (Priority: P2)

A developer wants to add new documents to an existing PLAID index without rebuilding from scratch. They insert token embeddings for a new document, which are assigned to the nearest existing centroids.

**Why this priority**: Production systems need incremental updates. Full rebuild is acceptable for initial load but not for ongoing ingestion.

**Independent Test**: Can be tested by building an index, inserting a new document, and verifying it appears in search results without rebuild.

**Acceptance Scenarios**:

1. **Given** a built PLAID index, **When** a developer calls `plaid_insert(name, doc_id, token_embeddings)`, **Then** the new document's tokens are assigned to the nearest existing centroids and the document is immediately searchable.
2. **Given** many incremental inserts (100+), **When** searching, **Then** recall does not degrade below 85% (centroid assignments are still valid without rebuild).

---

### Edge Cases

- What happens when a document has only 1 token? It is indexed normally — MaxSim degenerates to a single dot product.
- What happens when two documents have identical token embeddings? Both appear in results with identical MaxSim scores.
- What happens when the index has fewer documents than centroids? The system uses document count as centroid count (1 centroid per document, effectively brute force).
- What happens when query tokens are all zero vectors? MaxSim scores are all 0; results are returned in arbitrary order.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST provide a `PLAIDSearch` ObjectScript class with `Build`, `Search`, `Insert`, `Info`, and `Drop` class methods.
- **FR-002**: `Build` MUST accept pre-computed centroids from Python (via sklearn K-means) and store them as `$vector` values in `^PLAID` globals.
- **FR-003**: `Build` MUST construct the inverted index `^PLAID("docCentroid", centroidId, docId)` from token-centroid assignments provided by Python.
- **FR-004**: `Search` MUST implement the three-stage PLAID pipeline: centroid scoring → candidate generation → exact MaxSim.
- **FR-005**: `Search` MUST execute entirely server-side in a single `classMethodValue` call (zero SQL round-trips).
- **FR-006**: `Search` MUST use `$vectorop` SIMD operations for all dot product and distance computations.
- **FR-007**: `Insert` MUST assign new document tokens to the nearest existing centroids without requiring rebuild.
- **FR-008**: System MUST provide Python wrappers on `IRISGraphEngine`: `plaid_build`, `plaid_search`, `plaid_insert`, `plaid_info`, `plaid_drop`.
- **FR-009**: All `$vectorop` operations MUST use only public IRIS API (available since 2024.1, all license tiers).
- **FR-010**: The `^PLAID` global structure MUST be independent of `^KG` and `^VecIdx` (separate namespace, no conflicts).

### Key Entities

- **PLAID Index**: A centroid-based multi-vector index stored in `^PLAID` globals. Contains: centroids (K-means cluster centers), document-centroid mappings (which docs have tokens in which centroid), document token embeddings (for exact MaxSim scoring).
- **Centroid**: A cluster center computed via K-means over all token embeddings. Used for fast first-stage filtering.
- **MaxSim Score**: The sum of maximum dot products between each query token and all tokens of a candidate document. The standard ColBERT relevance metric.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Full PLAID search (centroid scoring + candidate generation + MaxSim) completes in under 15ms for 500 documents with 4 query tokens.
- **SC-002**: Recall@10 against brute-force MaxSim is at least 90% with default parameters.
- **SC-003**: Index build for 500 documents (25,000 token vectors, 128-dim) completes in under 10 seconds.
- **SC-004**: Incremental insert of a single document completes in under 5ms.
- **SC-005**: 100% of existing tests continue to pass after PLAID is added (zero regressions).
- **SC-006**: PLAID search and indexing are covered by at least 6 unit tests and 4 e2e tests.

## Assumptions

- Token embeddings are provided as lists of float vectors (JSON arrays), not generated by IVG. The ColBERT model runs externally (in Python or via the arno pipeline).
- K-means clustering runs in Python (sklearn) during build. Centroids are passed to ObjectScript as JSON arrays and stored as `$vector` in `^PLAID("centroid", k)`. The query path reads centroids via `$Get` (0.005ms per read) — no SQL query plan overhead.
- The build is hybrid: Python for K-means (compute-intensive batch), ObjectScript for inverted index construction (`$ORDER`-based). The query is pure ObjectScript + `$vectorop` — single `classMethodValue` call, zero Python involvement.
- The `^PLAID` global structure uses process-private globals (`^||`) for intermediate computation and persistent globals for the index.
- `$vectorop` provides sufficient SIMD acceleration for the dot product workload. No external C/Rust library needed.
- The number of centroids defaults to `$zsqr(totalTokenCount)` (√N), matching the PLAID paper's recommendation.

## Scope Boundaries

**In scope (Phase 1)**:
- `Graph.KG.PLAIDSearch.cls` — ObjectScript stored procedure
- `^PLAID` global structure (centroids, docCentroids, docTokens, metadata)
- Python wrappers on `IRISGraphEngine`
- K-means centroid computation in Python (sklearn), centroids stored as $vector in globals
- Three-stage search (centroid scoring → candidate gen → MaxSim)
- Unit and e2e tests

**Out of scope (Phase 2 / future)**:
- GPU-accelerated $vectorop (if IRIS ever supports it)
- Residual compression (PLAID's compression optimization for storage)
- ColBERT model inference inside IRIS (token generation stays external)
- Integration with iris-vector-rag's ColBERT pipeline (arno handles orchestration)
- Centroid re-training on incremental inserts (Phase 1 uses fixed centroids)

## Clarifications

### Session 2026-03-29

- Q: Should K-means run in ObjectScript server-side or Python? → A: Option B — Python K-means (sklearn), store centroids as $vector in globals via JSON. Query path is pure ObjectScript $vectorop. Build is hybrid (Python batch + ObjectScript inverted index). Query is single classMethodValue call.
