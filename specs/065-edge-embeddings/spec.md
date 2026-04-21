# Feature Specification: Triple / Edge Embeddings

**Feature Branch**: `065-edge-embeddings`
**Created**: 2026-04-21
**Status**: Draft

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Schema initialization creates edge embedding table (Priority: P1)

A developer calls `initialize_schema()` on a fresh namespace. The system creates `kg_EdgeEmbeddings` with a composite primary key on `(s, p, o_id)` and an HNSW-compatible vector index on `emb`. Subsequent calls are idempotent — no error, no duplicate table.

**Why this priority**: All other user stories depend on the table existing.

**Independent Test**: Call `initialize_schema()` twice on a fresh namespace. Verify `kg_EdgeEmbeddings` is queryable after the first call and the second call raises no error.

**Acceptance Scenarios**:

1. **Given** a namespace with no `kg_EdgeEmbeddings` table, **When** `initialize_schema()` is called, **Then** `SELECT TOP 1 * FROM Graph_KG.kg_EdgeEmbeddings` succeeds
2. **Given** `kg_EdgeEmbeddings` already exists, **When** `initialize_schema()` is called again, **Then** no exception is raised and the table still exists with its data intact
3. **Given** `initialize_schema()` has run, **When** the HNSW vector index DDL is attempted, **Then** it either succeeds or is silently skipped (idempotent — same pattern as node embeddings)

---

### User Story 2 — Embed all edges with default text (Priority: P1)

A developer with a populated graph calls `embed_edges()` with no arguments. The system serializes every edge as `"{s} {p} {o_id}"`, computes embeddings in batches, and writes them to `kg_EdgeEmbeddings`. A second call with `force=False` skips all already-embedded edges.

**Why this priority**: Core value — semantic edge search is impossible without this pipeline.

**Independent Test**: Populate 5 edges, call `embed_edges()`, query `kg_EdgeEmbeddings` — expect 5 rows. Call again, expect `{"embedded": 0, "skipped": 5, ...}`.

**Acceptance Scenarios**:

1. **Given** a graph with N edges and an empty `kg_EdgeEmbeddings`, **When** `embed_edges()` is called with defaults, **Then** `result["embedded"] == N`, `result["skipped"] == 0`, `result["errors"] == 0`
2. **Given** `kg_EdgeEmbeddings` already has all N edges, **When** `embed_edges(force=False)` is called, **Then** `result["embedded"] == 0`, `result["skipped"] == N`
3. **Given** some edges are already embedded, **When** `embed_edges(force=True)` is called, **Then** all edges are re-embedded and `result["embedded"] == N`

---

### User Story 3 — Embed a filtered subset with custom text (Priority: P2)

A developer calls `embed_edges(where="p = 'is_a'", text_fn=lambda s,p,o: f"{s.lower()} {p} {o.lower()}")`. Only matching edges are processed; existing embeddings for other predicates are untouched.

**Why this priority**: Needed for incremental updates and mixed-predicate graphs.

**Independent Test**: Populate edges with predicates `PRED_A` and `PRED_B`. Call `embed_edges(where="p = 'PRED_A'")`. Verify only `PRED_A` edges appear in `kg_EdgeEmbeddings`.

**Acceptance Scenarios**:

1. **Given** edges with predicates `PRED_A` and `PRED_B`, **When** `embed_edges(where="p = 'PRED_A'")` is called, **Then** only `PRED_A` edges are in `kg_EdgeEmbeddings`
2. **Given** a custom `text_fn`, **When** `embed_edges(text_fn=fn)` is called, **Then** the fn's output is what gets embedded
3. **Given** `text_fn` returns `None` or `""` for an edge, **When** that edge is processed, **Then** it is counted in `result["skipped"]`, not `result["errors"]`

---

### User Story 4 — Search edges by semantic similarity (Priority: P1)

A developer calls `edge_vector_search(query_embedding, top_k=5)`. The system returns up to 5 edges sorted by cosine similarity descending, each as `{"s": ..., "p": ..., "o_id": ..., "score": ...}`.

**Why this priority**: The retrieval half of the feature.

**Independent Test**: Embed 3 edges where one clearly matches a test query. Verify the top result is the expected edge and `score` is in `[0, 1]`.

**Acceptance Scenarios**:

1. **Given** N embedded edges and a query embedding, **When** `edge_vector_search(query_embedding, top_k=K)` is called, **Then** the result is at most K dicts with keys `s`, `p`, `o_id`, `score` sorted descending by `score`
2. **Given** `score_threshold=0.8`, **When** `edge_vector_search()` is called, **Then** only edges with cosine similarity >= 0.8 are returned
3. **Given** an empty `kg_EdgeEmbeddings`, **When** `edge_vector_search()` is called, **Then** an empty list is returned with no exception

---

### User Story 5 — Round-trip correctness (Priority: P2)

A developer embeds a small graph, then searches with the exact serialized text of one edge and verifies the top result matches that triple.

**Why this priority**: Correctness guarantee — catches subtle bugs in vector encoding or SQL query shape.

**Independent Test**: Embed edge `("hla-b27", "associated_with", "spondyloarthritis")`. Encode the same text and search. Top result must be that triple with score >= 0.99.

**Acceptance Scenarios**:

1. **Given** edge `(s, p, o)` embedded via `embed_edges()`, **When** the same text `f"{s} {p} {o}"` is encoded and passed to `edge_vector_search()`, **Then** the top result's `(s, p, o_id)` equals `(s, p, o)` and `score >= 0.99`

---

### Edge Cases

- `embed_edges()` on a namespace with no `rdf_edges` rows returns `{"embedded": 0, "skipped": 0, "errors": 0, "total": 0}` without error
- `edge_vector_search()` with `top_k` greater than row count returns all available rows
- A `where` clause containing `;`, `--`, or `/*` is rejected with `ValueError` before any DB call
- `text_fn` that raises for a specific edge: that edge is counted in `errors`, pipeline continues
- `model` string loads that model for the call duration only; original embedder is restored in `finally`
- No FK from `kg_EdgeEmbeddings` to `rdf_edges` — orphan embeddings are allowed (edges may be deleted after embedding)
- If the same `(s, p, o_id)` triple exists in multiple named graphs in `rdf_edges`, `embed_edges()` produces one embedding row (the last batch write wins). Named-graph-aware edge embeddings — where each `(s, p, o_id, graph_id)` tuple gets its own embedding — are out of scope for this spec.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `initialize_schema()` MUST create `Graph_KG.kg_EdgeEmbeddings(s VARCHAR(256) NOT NULL, p VARCHAR(512) NOT NULL, o_id VARCHAR(256) NOT NULL, emb VECTOR(DOUBLE, {dim}), CONSTRAINT pk_edge_emb PRIMARY KEY (s, p, o_id))` using `CREATE TABLE IF NOT EXISTS`
- **FR-002**: `initialize_schema()` MUST attempt to create an HNSW-compatible vector index on `kg_EdgeEmbeddings.emb`; failures MUST be silently suppressed (non-fatal), consistent with existing behavior for optional indexes
- **FR-002b**: `GraphSchema.get_schema_status()` MUST include `Graph_KG.kg_EdgeEmbeddings` in its required tables list
- **FR-002c**: `save_snapshot()` MUST export `kg_EdgeEmbeddings` rows as NDJSON (same pattern as `kg_NodeEmbeddings`); `restore_snapshot()` MUST import them, using UPSERT on `(s, p, o_id)` PK when `merge=True`
- **FR-003**: `IRISGraphEngine.embed_edges(model, text_fn, where, batch_size, force, progress_callback)` MUST be added with the exact signature in the feature description
- **FR-004**: `embed_edges` MUST query `Graph_KG.rdf_edges` for `(s, p, o_id)` columns, optionally filtered by a validated SQL `WHERE` fragment; injection characters MUST be rejected before any DB call
- **FR-005**: Default `text_fn` MUST produce `f"{s} {p} {o_id}"` when no `text_fn` is passed
- **FR-006**: With `force=False`, `embed_edges` MUST load all existing `(s, p, o_id)` tuples from `kg_EdgeEmbeddings` into a Python `set` and skip any edge whose tuple is in that set; with `force=True` MUST re-embed all matched edges (DELETE + INSERT). This mirrors the `embed_nodes` pattern and is correct for graphs up to ~1M edges.
- **FR-007**: `embed_edges` MUST write each edge via `DELETE FROM kg_EdgeEmbeddings WHERE s=? AND p=? AND o_id=?` followed by `INSERT INTO kg_EdgeEmbeddings (s, p, o_id, emb) VALUES (?, ?, ?, TO_VECTOR(?))`. The DELETE is always executed before INSERT to make each write idempotent regardless of `force` value. Edges in the skip-set (when `force=False`) are never touched — DELETE + INSERT only runs for edges that are being embedded in this call.
- **FR-008**: `embed_edges` MUST commit per batch and call `progress_callback(n_done, n_to_embed)` after each batch if provided; MUST return `{"embedded": int, "skipped": int, "errors": int, "total": int}`; MUST restore original embedder in `finally`
- **FR-009**: `IRISGraphEngine.edge_vector_search(query_embedding, top_k, score_threshold)` MUST be added
- **FR-010**: `edge_vector_search` MUST query `kg_EdgeEmbeddings` using `VECTOR_COSINE(emb, TO_VECTOR(?, DOUBLE, {dim}))`, return `list[dict]` with keys `s`, `p`, `o_id`, `score` sorted descending by score; apply `score_threshold` as `HAVING score >= threshold` when provided
- **FR-011**: `edge_vector_search` MUST return `[]` on empty table without raising an exception

### Key Entities

- **kg_EdgeEmbeddings**: One row per embedded triple `(s, p, o_id)`. DOUBLE vector (matches `kg_NodeEmbeddings`). Composite PK. No FK to `rdf_edges` (orphan-tolerant).
- **Edge text**: String fed to the embedding model. Default `"{s} {p} {o_id}"`. Caller-controlled via `text_fn`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `initialize_schema()` is idempotent — two successive calls on any namespace raise no exception and leave `kg_EdgeEmbeddings` queryable
- **SC-002**: `embed_edges()` on a 10-edge graph produces exactly 10 rows in `kg_EdgeEmbeddings`; a second call with `force=False` returns `{"embedded": 0, "skipped": 10, ...}`
- **SC-003**: `embed_edges(where=...)` embeds only matching edges; unmatched edges produce no rows
- **SC-004**: `edge_vector_search()` returns results in descending score order; exact-match query returns the correct triple as top result with `score >= 0.99`
- **SC-005**: `embed_edges()` when `text_fn` raises for one edge continues and counts that edge in `result["errors"]`; all other edges are embedded
- **SC-006**: `edge_vector_search()` on an empty table returns `[]` without exception
- **SC-007**: `save_snapshot()` exports `kg_EdgeEmbeddings` rows; `restore_snapshot()` reimports them; edge vector search on restored graph works without re-running `embed_edges()`

## Clarifications

### Session 2026-04-21

- Q: Vector type for `kg_EdgeEmbeddings.emb` — FLOAT or DOUBLE? → A: DOUBLE, matching `kg_NodeEmbeddings` for consistency and to avoid `TO_VECTOR` type mismatch errors
- Q: `force=False` skip-set strategy — in-memory set or per-row SQL check? → A: In-memory Python `set` of `(s, p, o_id)` tuples, matching `embed_nodes` pattern; correct for graphs up to ~1M edges
- Q: Include `kg_EdgeEmbeddings` in `get_schema_status()` and snapshot save/restore? → A: Yes — add to required tables list, `save_snapshot()` NDJSON export, and `restore_snapshot()` import (same pattern as `kg_NodeEmbeddings`)
