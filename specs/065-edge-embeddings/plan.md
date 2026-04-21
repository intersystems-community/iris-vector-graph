# Implementation Plan: Triple / Edge Embeddings

**Branch**: `065-edge-embeddings` | **Date**: 2026-04-21 | **Spec**: `specs/065-edge-embeddings/spec.md`

## Summary

Add `kg_EdgeEmbeddings` table to the IRIS schema and implement `embed_edges()` + `edge_vector_search()` on `IRISGraphEngine`, mirroring the existing `embed_nodes()` / `vector_search()` pipeline. Each edge `(s, p, o_id)` is serialized to text (default: `"{s} {p} {o_id}"`), embedded via `self.embed_text()`, and stored as `VECTOR(DOUBLE, {dim})`. Search uses `VECTOR_COSINE` with `TO_VECTOR(?, DOUBLE, {dim})`. The table is included in `get_schema_status()` and snapshot save/restore.

## Technical Context

**Language/Version**: Python 3.11, ObjectScript (IRIS 2024.1+)
**Primary Dependencies**: `intersystems-irispython`, `iris-devtester` (test only), `sentence-transformers` (optional, auto-loaded)
**Storage**: InterSystems IRIS — `Graph_KG` schema, new table `kg_EdgeEmbeddings`
**Testing**: pytest, `IRISContainer.attach("iris_vector_graph")`, `SKIP_IRIS_TESTS=false`
**Target Platform**: IRIS 2024.1+ (Community edition sufficient — VECTOR(DOUBLE) is available)
**Performance Goals**: `embed_edges()` throughput matches `embed_nodes()` (batch_size=500, ~embed_text latency-bound); `edge_vector_search()` uses IRIS HNSW auto-index, sub-5ms for <100K edges
**Constraints**: No new Python dependencies; no surrogate key (composite PK only); no FK to `rdf_edges` (orphan-tolerant); `force=False` skip-set in-memory (suitable for ≤1M edges)
**Scale/Scope**: Same scale as `kg_NodeEmbeddings` — hundreds of thousands of triples

## Constitution Check

- [x] Dedicated named IRIS container `iris_vector_graph` managed by `iris-devtester`
- [x] E2E test phase (non-optional) covers all 5 user stories (schema init, embed all, embed filtered, search, round-trip)
- [x] `SKIP_IRIS_TESTS` defaults to `"false"` in new test file
- [x] No hardcoded IRIS ports — all resolved via `IRISContainer.attach("iris_vector_graph").get_exposed_port(1972)`

**Principle IV — `tests/integration/` exception (documented)**:
Constitution Principle IV requires SQL-layer changes to include integration tests in `tests/integration/`. This project has an established convention of co-locating all E2E/integration tests that require a live IRIS container in `tests/unit/` (governed by the shared `iris_connection` + `iris_test_container` fixtures in `tests/conftest.py`). There is no `tests/integration/` directory in this repo; splitting the test tree would require duplicating the conftest fixture infrastructure. The E2E tests in `tests/unit/test_edge_embeddings.py` cover the SQL layer (schema DDL, INSERT via `TO_VECTOR`, `VECTOR_COSINE` search, snapshot roundtrip) against a live IRIS container and satisfy the *intent* of Principle IV. This exception is documented here and does not require a constitution amendment.

No violations.

## Project Structure

### Documentation (this feature)

```text
specs/065-edge-embeddings/
├── plan.md          ← this file
├── research.md
├── data-model.md
└── tasks.md         ← /speckit.tasks output
```

### Source Code Changes

```text
iris_vector_graph/
├── schema.py              ← add kg_EdgeEmbeddings DDL + get_schema_status() entry
└── engine.py              ← add embed_edges(), edge_vector_search(), snapshot wiring

tests/unit/
└── test_edge_embeddings.py   ← new test file (unit + E2E)
```

**No new files other than the test.** All changes go into existing `schema.py` and `engine.py`.

## Phase 0: Research

### R-001: Vector type consistency

**Decision**: `VECTOR(DOUBLE, {dim})` — same as `kg_NodeEmbeddings`.
**Rationale**: Avoids `TO_VECTOR` type mismatch SQLCODE errors. IRIS `VECTOR_COSINE` is type-sensitive; mixing FLOAT/DOUBLE causes silent failures.
**Alternative rejected**: VECTOR(FLOAT) — smaller storage but wrong type for existing `embed_text()` output (float64 Python list → DOUBLE).

### R-002: Skip-set strategy

**Decision**: Load all existing `(s, p, o_id)` PKs into a Python `frozenset` of 3-tuples before the fetch loop.
**Rationale**: Matches `embed_nodes` exactly. Single SELECT upfront, O(1) per-edge lookup, no per-row round-trips. Acceptable for ≤1M edges (~100MB worst case at 100 bytes/key).
**Alternative rejected**: Per-row `WHERE NOT EXISTS` subquery — N round-trips, unacceptably slow for bulk ingest.

### R-003: Snapshot integration

**Decision**: Add `kg_EdgeEmbeddings` to `save_snapshot()` using the same special-case VECTOR export block as `kg_NodeEmbeddings` (SELECT s, p, o_id, emb → NDJSON with emb as string). Add to `restore_snapshot()` UPSERT path. Add to `get_schema_status()` required tables list.
**Rationale**: Avoids silent data loss after restore. Consistent with existing pattern.

### R-004: HNSW index DDL

**Decision**: Use identical pattern to `kg_NodeEmbeddings_optimized` — a separate `CREATE TABLE IF NOT EXISTS Graph_KG.kg_EdgeEmbeddings_optimized` with same columns plus HNSW index. IRIS HNSW is defined at the table level via class definition, not as a `CREATE INDEX` statement.
**Alternative**: If IRIS build doesn't support the optimized variant, `initialize_schema()` silently skips (already the behavior for optional indexes). The plain `kg_EdgeEmbeddings` table alone is sufficient for correctness; HNSW is a performance optimization.

### R-005: `rdf_edges` unique constraint and `o_id`

**Decision**: `rdf_edges` has `UNIQUE (s, p, o_id)` (plus `graph_id` in named-graph builds). `kg_EdgeEmbeddings` composite PK is `(s, p, o_id)` only — one embedding per logical triple regardless of named graph. If the same `(s, p, o_id)` appears in multiple named graphs, only one embedding row exists (the last one written wins on re-embed with `force=True`). This is acceptable for the initial spec; named-graph-aware edge embeddings are out of scope.

## Phase 1: Design

### Data Model

See `data-model.md` below (inlined for simplicity given small scope).

**Table: `Graph_KG.kg_EdgeEmbeddings`**

| Column | Type | Constraint | Notes |
|--------|------|-----------|-------|
| `s` | `VARCHAR(256) %EXACT NOT NULL` | PK component | Source node ID |
| `p` | `VARCHAR(512) %EXACT NOT NULL` | PK component | Predicate / relationship type |
| `o_id` | `VARCHAR(256) %EXACT NOT NULL` | PK component | Object node ID |
| `emb` | `VECTOR(DOUBLE, {embedding_dimension})` | — | DOUBLE, dimension-parameterized |

Primary key: `CONSTRAINT pk_edge_emb PRIMARY KEY (s, p, o_id)`
No FK to `rdf_edges` (orphan-tolerant by design).

### API Contracts

**`embed_edges(model, text_fn, where, batch_size, force, progress_callback) -> dict`**

- Input: optional model override, optional text builder, optional SQL WHERE fragment on `(s, p, o_id)`, batch_size int, force bool, progress callback
- Output: `{"embedded": int, "skipped": int, "errors": int, "total": int}`
- Side effects: writes to `kg_EdgeEmbeddings`, commits per batch, restores embedder in `finally`
- Errors: `ValueError` on unsafe `where` clause; logs warnings per-edge on `text_fn` failure or embed failure (does not re-raise)

**`edge_vector_search(query_embedding, top_k, score_threshold) -> list[dict]`**

- Input: query_embedding (list[float] or comma-str), top_k int (default 10), score_threshold float|None
- Output: `[{"s": str, "p": str, "o_id": str, "score": float}, ...]` sorted descending by score
- Errors: returns `[]` on empty table; raises on genuine DB errors

### Schema Changes

**`schema.py` — `get_base_schema_sql()`**: Add after `kg_NodeEmbeddings_optimized` block:
```sql
CREATE TABLE IF NOT EXISTS Graph_KG.kg_EdgeEmbeddings (
    s     VARCHAR(256) %EXACT NOT NULL,
    p     VARCHAR(512) %EXACT NOT NULL,
    o_id  VARCHAR(256) %EXACT NOT NULL,
    emb   VECTOR(DOUBLE, {embedding_dimension}),
    CONSTRAINT pk_edge_emb PRIMARY KEY (s, p, o_id)
);
```

**`schema.py` — `get_schema_status()`**: Add `"Graph_KG.kg_EdgeEmbeddings"` to `required_tables` list.

**`engine.py` — `initialize_schema()`**: The optional HNSW DDL for `kg_EdgeEmbeddings` follows the same `try/except` suppression pattern as `kg_NodeEmbeddings_optimized`.

**`engine.py` — `save_snapshot()`**: Add dedicated VECTOR export block for `kg_EdgeEmbeddings` (SELECT s, p, o_id, emb; serialize emb as string; write to `sql/Graph_KG_kg_EdgeEmbeddings.ndjson`).

**`engine.py` — `restore_snapshot()`**: Add import block for `kg_EdgeEmbeddings` — `INSERT INTO ... (s, p, o_id, emb) SELECT ?, ?, ?, TO_VECTOR(?, DOUBLE) WHERE NOT EXISTS (...)` for merge, plain INSERT for destructive restore.

### Test Design

**File**: `tests/unit/test_edge_embeddings.py`

**Unit tests** (no IRIS, mock embedder):
- `test_schema_ddl_contains_kg_edge_embeddings` — `get_base_schema_sql()` contains the table
- `test_embed_edges_default_text_fn` — `embed_edges()` calls `embed_text` with `"{s} {p} {o_id}"`
- `test_embed_edges_custom_text_fn` — custom `text_fn` output is used
- `test_embed_edges_text_fn_returns_none_counted_as_skipped` — None/empty text → `skipped`
- `test_embed_edges_text_fn_raises_counted_as_error` — exception → `errors`
- `test_embed_edges_unsafe_where_raises` — `;` in where → `ValueError`
- `test_edge_vector_search_sql_shape` — SQL contains `VECTOR_COSINE`, `TO_VECTOR`, `kg_EdgeEmbeddings`

**E2E tests** (IRIS container, `SKIP_IRIS_TESTS=false`):
- `test_schema_creates_kg_edge_embeddings` — `initialize_schema()` idempotent, table queryable (SC-001)
- `test_embed_edges_all_default` — embed N edges, verify N rows, second call skips all (SC-002)
- `test_embed_edges_where_filter` — embed subset by predicate (SC-003)
- `test_edge_vector_search_ranking` — search returns descending scores (SC-004)
- `test_edge_vector_search_empty_table` — returns `[]` (SC-006)
- `test_edge_embeddings_round_trip` — embed → search exact text → top result matches (SC-004/SC-005)
- `test_embed_edges_error_continues` — `text_fn` raises → other edges embedded (SC-005)
