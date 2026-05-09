# Tasks: Streaming BFS for Unbounded Variable-Length Path Queries

**Branch**: `150-streaming-bfs-unbounded`
**Spec**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md)

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

- [ ] T001 Verify container `iris_vector_graph` and port `1972` in `docker-compose.yml`
- [ ] T002 Confirm `max_results` extraction from `vl` dict in `_execute_var_length_cypher` in `iris_vector_graph/engine.py`
- [ ] T003 [P] Create empty `tests/e2e/test_streaming_bfs.py`

**Checkpoint**: Ready to write tests.

---

## Phase 2: Test-First (Constitution Principle III)

> **WRITE THESE TESTS FIRST — they should FAIL until Phase 3 is complete.**

- [ ] T004 [US1] Write e2e test `test_unbounded_bfs_large_result_set_completes` in `tests/e2e/test_streaming_bfs.py` — seed 100 nodes with hub connectivity, run unbounded `MATCH (s)-[:R*1..2]->(n) RETURN n.node_id` (no LIMIT), assert all results returned, no error
- [ ] T005 [US1] Write e2e test `test_unbounded_bfs_empty_result_completes` in `tests/e2e/test_streaming_bfs.py` — isolated node, unbounded query, assert empty result no crash
- [ ] T006 [US2] Write e2e test `test_bounded_bfs_limit_uses_fast_path` in `tests/e2e/test_streaming_bfs.py` — LIMIT 10 query, assert returns ≤10 results
- [ ] T007 [US1] Confirm T004 currently FAILS (ReadBFSResults path is taken for unbounded queries)

**Checkpoint**: 3 tests written, T004 confirmed failing.

---

## Phase 3: Engine Fix (Constitution Principle III — implement after tests)

- [ ] T008 [US1] Read `_execute_var_length_cypher` in `iris_vector_graph/engine.py` (~line 1553) to locate the `ReadBFSResults` vs `_bfs_stream_pages` routing decision
- [ ] T009 [US1] Update routing in `_execute_var_length_cypher`: when `max_results == 0` (unbounded), use `_bfs_stream_pages`; when `max_results > 0` (LIMIT present), use `ReadBFSResults`
- [ ] T010 [US1] Verify T004 and T005 now pass
- [ ] T011 [US2] Verify T006 passes (bounded path unchanged)

**Checkpoint**: All 3 new tests pass.

---

## Phase 4: End-to-End Validation (Constitution Principle IV — Non-Optional)

- [ ] T012 [US2] Run existing VL path tests: `pytest tests/e2e/test_cypher_vl_path_bfs.py -q` — zero regressions
- [ ] T013 [P] Run `pytest tests/unit/test_cypher_parser.py tests/unit/test_cypher_translator.py -q` — zero regressions
- [ ] T014 Run `pytest tests/e2e/test_streaming_bfs.py -v` — all 3 tests pass
- [ ] T015 Verify `from iris_vector_graph.engine import _bfs_stream_pages` is the only streaming path (no duplicate logic)

**Checkpoint**: All acceptance scenarios from spec.md verified.

---

## Phase 5: Polish

- [ ] T016 Update `ENGINEERING_DEBT.md` — mark Streaming BFS P0 resolved
- [ ] T017 Bump version to `1.85.0` in `pyproject.toml` and publish

---

## Dependencies & Execution Order

- Phase 1 → Phase 2 (tests written before impl) → Phase 3 (impl after tests) → Phase 4 → Phase 5
- T004 MUST fail before T009 is written (Constitution Principle III)
- T012 and T013 can run in parallel

---

## Notes

- Constitution Principle III: T007 must confirm T004 fails before Phase 3 begins.
- Constitution Principle IV: Phase 4 is non-optional.
- Constitution Principle VI: Container `iris_vector_graph` from `docker-compose.yml:4`, port `1972` from `docker-compose.yml:5`.
- This is a **4-line fix** — the bulk of effort is test infrastructure.
- Do NOT use LDBC SF10 data for the test — synthetic graph on community IRIS is sufficient and portable.
