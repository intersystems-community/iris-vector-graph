# Tasks: Triple / Edge Embeddings (065)

**Branch**: `065-edge-embeddings`
**Input**: `specs/065-edge-embeddings/spec.md`, `specs/065-edge-embeddings/plan.md`
**Prerequisites**: No new packages needed. Changes confined to `schema.py`, `engine.py`, and one new test file.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no blocked dependency)
- **[Story]**: Which user story this task belongs to
- All paths relative to repo root

---

## Phase 1: Setup

**Purpose**: Failing E2E test stubs — test-first, nothing passes yet.

- [ ] T000 Create `tests/unit/test_edge_embeddings.py` with module-level imports and fixture wiring (SKIP_IRIS_TESTS guard, iris_connection fixture) so test collection succeeds — file must exist before stubs can fail cleanly
- [ ] T001 [US1] Write failing E2E test `test_schema_creates_kg_edge_embeddings` in `tests/unit/test_edge_embeddings.py` — verifies `initialize_schema()` idempotent and `kg_EdgeEmbeddings` queryable (SC-001)
- [ ] T002 [US2] Write failing E2E test `test_embed_edges_all_default` in `tests/unit/test_edge_embeddings.py` — populates 5 edges, calls `embed_edges()`, asserts 5 rows, second call skips all (SC-002)
- [ ] T003 [P] [US2] Write failing E2E test `test_embed_edges_force_true` in `tests/unit/test_edge_embeddings.py` — pre-populates embeddings, calls `embed_edges(force=True)`, asserts all re-embedded
- [ ] T004 [P] [US3] Write failing E2E test `test_embed_edges_where_filter` in `tests/unit/test_edge_embeddings.py` — asserts only matching-predicate edges get rows (SC-003)
- [ ] T005 [P] [US3] Write failing E2E test `test_embed_edges_custom_text_fn` in `tests/unit/test_edge_embeddings.py` — custom text_fn output drives embeddings
- [ ] T006 [P] [US4] Write failing E2E test `test_edge_vector_search_ranking` in `tests/unit/test_edge_embeddings.py` — results descending by score (SC-004)
- [ ] T007 [P] [US4] Write failing E2E test `test_edge_vector_search_empty_table` in `tests/unit/test_edge_embeddings.py` — returns `[]` without exception (SC-006)
- [ ] T008 [P] [US5] Write failing E2E test `test_edge_embeddings_round_trip` in `tests/unit/test_edge_embeddings.py` — embed → search same text → top result matches, score >= 0.99
- [ ] T009 [P] [US2] Write failing E2E test `test_embed_edges_text_fn_raises_continues` in `tests/unit/test_edge_embeddings.py` — text_fn raises for one edge, others embedded, errors==1 (SC-005)
- [ ] T000-GATE Run `pytest tests/unit/test_edge_embeddings.py -v` and confirm ALL of T001–T009 show FAILED (not ERROR/ImportError) — constitution Principle III gate; do not proceed to T010 until this passes

---

## Phase 2: Foundational — Schema DDL (blocks all other phases)

**Purpose**: Add `kg_EdgeEmbeddings` table to schema. Makes T001 pass.

- [ ] T010 [US1] Add `CREATE TABLE IF NOT EXISTS Graph_KG.kg_EdgeEmbeddings (s VARCHAR(256) %EXACT NOT NULL, p VARCHAR(512) %EXACT NOT NULL, o_id VARCHAR(256) %EXACT NOT NULL, emb VECTOR(DOUBLE, {embedding_dimension}), CONSTRAINT pk_edge_emb PRIMARY KEY (s, p, o_id))` to `GraphSchema.get_base_schema_sql()` in `iris_vector_graph/schema.py` — place after `kg_NodeEmbeddings_optimized` block
- [ ] T011 [US1] Add `"Graph_KG.kg_EdgeEmbeddings"` to the `required_tables` list in `GraphSchema.get_schema_status()` in `iris_vector_graph/schema.py`
- [ ] T012 [US1] Add `CREATE TABLE IF NOT EXISTS Graph_KG.kg_EdgeEmbeddings_optimized` block with same columns as `kg_EdgeEmbeddings` (for HNSW index support) in `iris_vector_graph/schema.py` — follow identical pattern to `kg_NodeEmbeddings_optimized`; wrap in try/except so failure is non-fatal

**Gate**: T001 passes after T010–T012 complete. Run `pytest tests/unit/test_edge_embeddings.py::test_schema_creates_kg_edge_embeddings`.

---

## Phase 3: User Story 2 — `embed_edges()` method (P1)

**Goal**: Embed all edges with default text; idempotent re-run with `force=False`.

- [ ] T013 [US2] Implement `embed_edges(model, text_fn, where, batch_size, force, progress_callback)` method on `IRISGraphEngine` in `iris_vector_graph/engine.py` — exact signature per spec; place after `embed_nodes` method (~line 3426)
- [ ] T014 [US2] Inside `embed_edges`: add WHERE clause injection guard (same `;`, `--`, `/*`, `XP_`, `EXEC`, `EXECUTE` check as `embed_nodes`) — raise `ValueError` on match
- [ ] T015 [US2] Inside `embed_edges`: implement model override + restore in `try/finally` block — mirror `embed_nodes` pattern exactly
- [ ] T016 [US2] Inside `embed_edges`: fetch `(s, p, o_id)` from `Graph_KG.rdf_edges` with optional WHERE, build `to_embed` list; with `force=False` load existing PKs from `kg_EdgeEmbeddings` into a `frozenset` of `(s, p, o_id)` tuples and filter
- [ ] T017 [US2] Inside `embed_edges`: implement batch loop — for each edge call `text_fn(s, p, o_id)` (default `f"{s} {p} {o_id}"`), call `self.embed_text(text)`, DELETE + INSERT into `kg_EdgeEmbeddings` via `TO_VECTOR(?)`, increment counters, commit per batch, call `progress_callback` if provided
- [ ] T018 [US2] Inside `embed_edges`: handle `text_fn` returning `None`/`""` → `skipped += 1`; handle `text_fn` exception → `errors += 1`, log warning, continue; handle `embed_text`/INSERT exception → `errors += 1`, log warning, continue
- [ ] T019 [US2] Return `{"embedded": embedded, "skipped": skipped, "errors": errors, "total": n_total}` from `embed_edges`

**Gate**: T002, T003, T009 pass after T013–T019. Run `pytest tests/unit/test_edge_embeddings.py -k "embed_edges"`.

---

## Phase 4: User Story 3 — `embed_edges()` with `where` filter and custom `text_fn` (P2)

**Goal**: Filter edges by SQL predicate; use caller-supplied text builder.

*No new implementation needed beyond T013–T019 — `where` and `text_fn` are already wired. Phase 4 validates they work correctly.*

- [ ] T020 [US3] Verify `test_embed_edges_where_filter` passes — if not, debug the WHERE injection into the SELECT query in `embed_edges` (the `WHERE {where}` clause appended to the `rdf_edges` fetch)
- [ ] T021 [US3] Verify `test_embed_edges_custom_text_fn` passes — if not, debug the `text_fn` call path in T017
- [ ] T022 [US3] Write unit test `test_embed_edges_unsafe_where_raises` in `tests/unit/test_edge_embeddings.py` (no IRIS) — assert `ValueError` on `;` in where; verify it passes immediately

**Gate**: T004, T005, T022 pass.

---

## Phase 5: User Story 4 — `edge_vector_search()` (P1)

**Goal**: Semantic search over embedded edges, descending by cosine similarity.

- [ ] T023 [US4] Implement `edge_vector_search(query_embedding, top_k=10, score_threshold=None)` method on `IRISGraphEngine` in `iris_vector_graph/engine.py` — place after `embed_edges`
- [ ] T024 [US4] Inside `edge_vector_search`: serialize `query_embedding` to comma-string if list; detect `dim = len(query_embedding)` if list else count commas + 1; build `query_cast = f"TO_VECTOR(?, DOUBLE, {dim})"` — mirror `vector_search` pattern
- [ ] T025 [US4] Inside `edge_vector_search`: build and execute SQL: `SELECT TOP {top_k} s, p, o_id, VECTOR_COSINE(emb, {query_cast}) AS score FROM Graph_KG.kg_EdgeEmbeddings ORDER BY score DESC [HAVING score >= threshold]`
- [ ] T026 [US4] Inside `edge_vector_search`: return `[{"s": row[0], "p": row[1], "o_id": row[2], "score": float(row[3])} for row in cursor.fetchall()]`; return `[]` on empty table (catch empty result, not exception)
- [ ] T027 [P] [US4] Write unit test `test_edge_vector_search_sql_shape` in `tests/unit/test_edge_embeddings.py` (no IRIS, patch cursor) — assert generated SQL contains `VECTOR_COSINE`, `TO_VECTOR`, `kg_EdgeEmbeddings`, `ORDER BY score DESC`

**Gate**: T006, T007, T027 pass. Run `pytest tests/unit/test_edge_embeddings.py -k "search"`.

---

## Phase 6: User Story 5 — Round-trip correctness (P2)

*No new implementation. T008 validates end-to-end with exact-match query.*

- [ ] T028 [US5] Verify `test_edge_embeddings_round_trip` passes — if score < 0.99, check that `embed_edges` default text_fn and `edge_vector_search` query use identical serialization format (`"{s} {p} {o_id}"`)
- [ ] T029 [P] [US5] Write unit test `test_default_text_fn_format` in `tests/unit/test_edge_embeddings.py` — asserts that for `s="A"`, `p="REL"`, `o_id="B"` the default produces `"A REL B"` exactly (no trailing space, no punctuation)

**Gate**: T008, T029 pass.

---

## Phase 7: Snapshot Integration

**Goal**: `save_snapshot()` exports edge embeddings; `restore_snapshot()` reimports them (FR-002c, SC-007).

- [ ] T030 Add `Graph_KG.kg_EdgeEmbeddings` VECTOR export block to `save_snapshot()` in `iris_vector_graph/engine.py` — after the `kg_NodeEmbeddings` export block (~line 2795); SELECT `s, p, o_id, emb`, serialize emb as string, write to `sql_data["Graph_KG.kg_EdgeEmbeddings"]`
- [ ] T031 Add `Graph_KG.kg_EdgeEmbeddings` import block to `restore_snapshot()` in `iris_vector_graph/engine.py` — after the `kg_NodeEmbeddings` restore block; use `INSERT ... SELECT ?, ?, ?, TO_VECTOR(?, DOUBLE) WHERE NOT EXISTS (...)` for merge mode; plain `INSERT` for destructive restore
- [ ] T032 Write E2E test `test_snapshot_round_trip_edge_embeddings` in `tests/unit/test_edge_embeddings.py` — embed edges, save snapshot, drop+restore, verify `kg_EdgeEmbeddings` row count matches and `edge_vector_search` returns correct results

**Gate**: T032 passes.

---

## Phase 8: Polish

- [ ] T033 [P] Run full unit test suite `pytest tests/unit/ -q` and fix any regressions introduced by schema/engine changes
- [ ] T034 [P] Update `README.md` — add `edge_vector_search` to the capability table and add `embed_edges` / `edge_vector_search` API examples to the "Vector Search" section

---

## Dependencies

```
T010–T012 (schema DDL)
    → T001 unblocked (schema test passes)
    → T013–T019 (embed_edges impl)
        → T002, T003, T009 unblocked
        → T020–T022 (where/text_fn verification)
        → T023–T026 (edge_vector_search impl)
            → T006, T007, T027 unblocked
            → T028–T029 (round-trip)
                → T030–T032 (snapshot)

T004, T005 unblocked after T013 (where + text_fn parallel with T015–T018)
T033, T034 after all preceding phases
```

## Parallel Execution Opportunities

- T001–T009 (test stubs): all can be written together in one pass on `test_edge_embeddings.py`
- T010–T012 (schema): three edits to `schema.py`, no interdependency
- T013–T019 (embed_edges body): sequential within the method, but method can be written end-to-end in one pass
- T020–T022 (filter verification): run in parallel once embed_edges exists
- T023–T027 (edge_vector_search): independent from embed_edges implementation

## Implementation Strategy

**MVP** (minimum viable increment — delivers search value):
1. T001–T002 (failing tests for schema + embed_all)
2. T010–T012 (schema DDL)
3. T013–T019 (embed_edges)
4. T023–T026 (edge_vector_search)

At MVP gate: `embed_edges()` and `edge_vector_search()` work end-to-end. Everything else (filter, text_fn, snapshot, round-trip) can follow.

**Total tasks**: 34
**E2E tests**: 9 (T001–T009, T032)
**Unit tests (no IRIS)**: 3 (T022, T027, T029)
**Parallel opportunities**: T001–T009, T010–T012, T020–T022, T033–T034
