# Tasks: initialize_schema() Stored Procedure Installation

**Input**: Design documents from `/specs/020-initialize-schema-stored-procedures/`  
**Branch**: `020-initialize-schema-stored-procedures`  
**Prerequisites**: plan.md ✅, spec.md ✅, data-model.md ✅, contracts/ ✅, research.md ✅, quickstart.md ✅

**Tests**: Included per spec.md (Principle III — Test-First is non-negotiable).

**Organization**: 4 user stories from spec.md, ordered by priority (P0 first).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to
- Paths are relative to repository root (`iris_vector_graph/`, `tests/`)

---

## Phase 1: Setup

**Purpose**: Verify toolchain and grounding before any code changes.

- [ ] T001 Confirm `schema.py` SyntaxError by running `python3 -c "import ast; ast.parse(open('iris_vector_graph/schema.py').read()); print('OK')"` — expected: `SyntaxError`
- [ ] T002 Confirm dead code range: verify lines 436–520 of `iris_vector_graph/schema.py` are the orphaned block (docstring fragment + second `return f"""..."""`); confirm line 434 is `        ]` and line 435 is blank
- [ ] T003 [P] Confirm authoritative infrastructure values: container name `iris_vector_graph` from `docker-compose.yml`, port via `get_exposed_port(1972)` from `tests/conftest.py:255`

**Checkpoint**: Dead code range confirmed, bug reproducible, infrastructure values verified — ready to write failing tests.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Write all failing tests before any implementation. All tests must be RED before Phase 3.

**⚠️ CRITICAL**: No implementation work until this phase is complete and tests are confirmed failing.

**IRIS Constitution Compliance (Principle IV — non-negotiable)**:
- Container: `iris_vector_graph` (verified from `docker-compose.yml`)
- Port: `iris_test_container.get_exposed_port(1972)` (verified from `tests/conftest.py:255`)
- `SKIP_IRIS_TESTS` default: `"false"` in all new test files
- No hardcoded ports

- [ ] T004 [P] Write unit test file `tests/unit/test_schema_procedures.py` with 6 failing tests:
  - `test_schema_py_is_importable` — `ast.parse(open('iris_vector_graph/schema.py').read())` must not raise (currently fails)
  - `test_get_procedures_sql_list_contains_knn_vec` — `"kg_KNN_VEC"` in `get_procedures_sql_list()` output (currently fails, module unimportable)
  - `test_get_procedures_sql_list_uses_dimension` — `"VECTOR(DOUBLE, 384)"` in output when `embedding_dimension=384`
  - `test_get_procedures_sql_list_idempotent_default` — `"VECTOR(DOUBLE, 1000)"` with no dimension arg
  - `test_initialize_schema_raises_on_ddl_failure` — mock cursor raises non-"already exists" → `RuntimeError`
  - `test_initialize_schema_ignores_already_exists` — mock cursor raises "already exists" on BOTH `CREATE SCHEMA iris_vector_graph` AND on a `CREATE OR REPLACE PROCEDURE` statement → no error propagated in either case (covers FR-005: the schema creation clause must be silently ignored, not just the procedure DDL)
- [ ] T005 [P] Write integration test file `tests/integration/test_stored_procedure_install.py` with 5 failing tests, using `iris_connection` fixture and `@pytest.mark.skipif(os.environ.get("SKIP_IRIS_TESTS", "false") == "true", ...)`:
  - `test_initialize_schema_installs_all_procedures` — query `INFORMATION_SCHEMA.ROUTINES` after `initialize_schema()`
  - `test_kg_knn_vec_callable_after_init` — `CALL iris_vector_graph.kg_KNN_VEC(?, ?, ?, ?)` succeeds
  - `test_kg_knn_vec_uses_server_side_path` — monkey-patch `_kg_KNN_VEC_python_optimized` to raise; assert `kg_KNN_VEC()` succeeds
  - `test_initialize_schema_is_idempotent` — call `initialize_schema()` twice; no error on second call
  - `test_initialize_schema_dimension_in_procedure` — engine with `embedding_dimension=384` → procedure DDL contains `VECTOR(DOUBLE, 384)`
- [ ] T006 Run `pytest tests/unit/test_schema_procedures.py tests/integration/test_stored_procedure_install.py -v` and confirm ALL tests are RED (failing). Document the exact failure mode for each test.

**Checkpoint**: All 11 tests exist and are RED — implementation can begin.

---

## Phase 3: User Story 4 — `schema.py` is a valid Python module (Priority: P0) 🎯 MVP Start

**Goal**: Remove the dead code block from `schema.py` so the module is syntactically valid and importable.

**Independent Test**: `python3 -c "from iris_vector_graph.schema import GraphSchema; print('OK')"` exits 0.

**Why US4 first**: It is the blocker for all other user stories. Nothing else can be tested or implemented while `schema.py` has a `SyntaxError`.

- [ ] T007 [US4] Delete dead code lines 436–520 from `iris_vector_graph/schema.py` — the orphaned block starts at `        Get SQL for retrieval stored procedures.` (bare docstring fragment; line 434 is the closing `]` of the live `return` list, line 435 is blank — both must be kept intact). Keep lines 1–435 intact.
- [ ] T008 [US4] Verify fix: run `python3 -c "import ast; ast.parse(open('iris_vector_graph/schema.py').read()); print('OK')"` — must print `OK`
- [ ] T009 [US4] Verify import: run `python3 -c "from iris_vector_graph.schema import GraphSchema; stmts = GraphSchema.get_procedures_sql_list(); print(len(stmts), 'statements')"` — must print `4 statements`
- [ ] T010 [US4] Run `pytest tests/unit/test_schema_procedures.py::test_schema_py_is_importable tests/unit/test_schema_procedures.py::test_get_procedures_sql_list_contains_knn_vec -v` — both must be GREEN

**Checkpoint**: `schema.py` imports cleanly. US4 acceptance scenarios SC-001 and partial SC-002 pass. Remaining stories can now proceed.

---

## Phase 4: User Story 2 — Procedure dimension matches embedding dimension (Priority: P1)

**Goal**: `get_procedures_sql_list` accepts `embedding_dimension` and uses it in `VECTOR(DOUBLE, N)` inside `kg_KNN_VEC` DDL.

**Independent Test**: `GraphSchema.get_procedures_sql_list("Graph_KG", embedding_dimension=384)` returns SQL containing `VECTOR(DOUBLE, 384)`.

- [ ] T011 [US2] Add `embedding_dimension: int = 1000` parameter to `GraphSchema.get_procedures_sql_list` in `iris_vector_graph/schema.py` (signature change only — no default behavior change)
- [ ] T012 [US2] Replace the `DECLARE qvec VECTOR(DOUBLE, 1000)` hardcoded value inside the `kg_KNN_VEC` f-string in `iris_vector_graph/schema.py` with `DECLARE qvec VECTOR(DOUBLE, {embedding_dimension})`
- [ ] T013 [US2] Run `pytest tests/unit/test_schema_procedures.py::test_get_procedures_sql_list_uses_dimension tests/unit/test_schema_procedures.py::test_get_procedures_sql_list_idempotent_default -v` — both must be GREEN

**Checkpoint**: `get_procedures_sql_list(embedding_dimension=384)` produces `VECTOR(DOUBLE, 384)`. SC-005 passes.

---

## Phase 5: User Story 1 — Fresh install uses server-side vector search (Priority: P0)

**Goal**: `initialize_schema()` passes `embedding_dimension` to `get_procedures_sql_list` so the procedure is installed with the correct dimension.

**Independent Test**: Call `initialize_schema()` on a live IRIS container; then call `CALL iris_vector_graph.kg_KNN_VEC(...)` — succeeds, no fallback warning.

- [ ] T014 [US1] Update the procedure-installation loop in `iris_vector_graph/engine.py` (lines ~170–178): change `GraphSchema.get_procedures_sql_list(table_schema="Graph_KG")` to `GraphSchema.get_procedures_sql_list(table_schema="Graph_KG", embedding_dimension=dim)` — `dim` is already in scope from the top of `initialize_schema()`. Note: custom schema prefix support (`set_schema_prefix()`) is out of scope for this fix; the hardcoded `"Graph_KG"` matches the engine's current default. A follow-up task would be needed to thread the active schema prefix through.
- [ ] T015 [US1] Run `pytest tests/unit/test_schema_procedures.py -v` — ALL 6 unit tests must be GREEN
- [ ] T016 [US1] Run integration tests against live IRIS container: `pytest tests/integration/test_stored_procedure_install.py::test_initialize_schema_installs_all_procedures tests/integration/test_stored_procedure_install.py::test_kg_knn_vec_callable_after_init tests/integration/test_stored_procedure_install.py::test_initialize_schema_is_idempotent tests/integration/test_stored_procedure_install.py::test_initialize_schema_dimension_in_procedure -v --use-existing-iris`
- [ ] T017 [US1] Run the manual smoke test from `quickstart.md`: create engine with `embedding_dimension=384`, call `initialize_schema()`, insert test node + embedding, call `kg_KNN_VEC()` — assert no `WARNING: Server-side kg_KNN_VEC failed` in logs

**Checkpoint**: SC-002 and SC-003 pass. Server-side path is reachable on a fresh install.

---

## Phase 6: User Story 3 — Init-time diagnostic for procedure installation failure (Priority: P1)

**Goal**: `initialize_schema()` raises `RuntimeError` when a stored procedure DDL fails unexpectedly, instead of silently swallowing the error.

**Independent Test**: Mock the cursor to raise a non-"already exists" error during procedure DDL; assert `RuntimeError` is raised with message containing the failure count and first error.

- [ ] T018 [US3] Replace the existing procedure-installation loop in `iris_vector_graph/engine.py` with the failure-collection pattern from `plan.md` Phase B:
  - Initialize `procedure_errors = []` before the loop
  - In the `except` block: if `"already exists"` or `"already has"` in error → `continue` (idempotent); otherwise `procedure_errors.append((stmt[:80], e))` and `logger.error("Procedure DDL failed: %s | Error: %s", stmt[:80], e)`
  - After the loop: `if procedure_errors: raise RuntimeError(f"initialize_schema() failed to install {len(procedure_errors)} stored procedure(s). Server-side vector search will be unavailable. First error: {procedure_errors[0][1]}")`
  - Move `self.conn.commit()` to after the guard (only commit if no errors)
- [ ] T019 [US3] Run `pytest tests/unit/test_schema_procedures.py::test_initialize_schema_raises_on_ddl_failure tests/unit/test_schema_procedures.py::test_initialize_schema_ignores_already_exists -v` — both must be GREEN
- [ ] T020 [US3] Run `pytest tests/integration/test_stored_procedure_install.py::test_kg_knn_vec_uses_server_side_path -v --use-existing-iris` — must be GREEN (server-side path confirmed used, fallback not invoked)

**Checkpoint**: SC-004 passes. `initialize_schema()` raises on unexpected DDL failure. SC-006 (no regression) verified by full test run below.

---

## Phase 7: End-to-End Validation (IRIS — Principle IV, Non-Optional)

**Purpose**: Validate all user stories against the live `iris_vector_graph` IRIS container.

- [ ] T021 [P] [US4] e2e: `schema.py` is importable — `python3 -c "from iris_vector_graph import IRISGraphEngine; print('OK')"` in `tests/e2e/test_schema_procedures_e2e.py`
- [ ] T022 [P] [US1] e2e: fresh install → server-side path — acceptance scenario 1 from spec.md US1 in `tests/e2e/test_schema_procedures_e2e.py`
- [ ] T023 [P] [US1] e2e: idempotent re-run — acceptance scenario 3 from spec.md US1 in `tests/e2e/test_schema_procedures_e2e.py`
- [ ] T024 [P] [US2] e2e: dimension in procedure — engine with `embedding_dimension=1536`, `initialize_schema()`, verify procedure contains `VECTOR(DOUBLE, 1536)` in `tests/e2e/test_schema_procedures_e2e.py`
- [ ] T025 [P] [US3] e2e: DDL failure raises `RuntimeError` — mock non-permission error against live conn in `tests/e2e/test_schema_procedures_e2e.py`
- [ ] T026 Run full suite: `pytest tests/ -v --use-existing-iris` — ALL tests GREEN, zero regressions

**Checkpoint**: All acceptance scenarios from spec.md pass against live IRIS. SC-001 through SC-006 all verified.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [ ] T027 [P] Run `ruff check iris_vector_graph/schema.py iris_vector_graph/engine.py` — fix any lint warnings introduced by changes
- [ ] T028 [P] Run `python3 -m build` — verify package builds cleanly with the fixed `schema.py`
- [ ] T029 Verify `quickstart.md` bug-verification section now shows `OK` for all three checks (SyntaxError gone, import works, procedures present after init)
- [ ] T030 Update `CHANGELOG.md` or release notes with fix description: "Fix SyntaxError in schema.py (dead code block removed); fix hardcoded VECTOR dimension in kg_KNN_VEC DDL; initialize_schema() now raises RuntimeError on procedure installation failure"

**Checkpoint**: Package builds, lint clean, release notes updated.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately
- **Phase 2 (Foundational / Tests)**: Depends on Phase 1 — BLOCKS all user story implementation
- **Phase 3 (US4 — SyntaxError fix)**: Depends on Phase 2 RED tests — **MUST come before all other user stories** (everything else depends on an importable `schema.py`)
- **Phase 4 (US2 — dimension param)**: Depends on Phase 3 (needs `schema.py` to be importable)
- **Phase 5 (US1 — initialize_schema wiring)**: Depends on Phase 4 (needs `embedding_dimension` param to exist in `get_procedures_sql_list`)
- **Phase 6 (US3 — RuntimeError)**: Depends on Phase 5 (modifies the same loop in `engine.py`)
- **Phase 7 (e2e)**: Depends on Phases 3–6 all complete
- **Phase 8 (Polish)**: Depends on Phase 7

### User Story Dependencies

```
US4 (schema.py fix) ──→ US2 (dimension param) ──→ US1 (engine wiring) ──→ US3 (error raising)
         ↑
  UNBLOCKS everything
```

US4 is the hard dependency. After US4, the remaining stories are a linear chain in the same two files.

### Within Each Story

- Tests written (Phase 2) and confirmed RED before implementation begins
- Each story's tests go GREEN when its implementation is complete
- `initialize_schema()` in `engine.py` only modified once (Phase 5 + Phase 6 in the same method block to avoid conflict)

### Parallel Opportunities

- T004 and T005 (test file authoring) can run in parallel — different files
- T021–T025 (e2e tests within the same file) can be written in parallel — same file but different test functions, merge at T026
- T027 and T028 (lint + build) can run in parallel

---

## Parallel Example: Phase 2 (Foundational Tests)

```bash
# Launch unit and integration test authoring in parallel:
Task: "Write tests/unit/test_schema_procedures.py (T004)"
Task: "Write tests/integration/test_stored_procedure_install.py (T005)"
# Then merge at T006 (confirm all RED)
```

---

## Implementation Strategy

### MVP Scope (US4 + US1 only — ~45 min)

1. Phase 1: Verify the bug (T001–T003)
2. Phase 2: Write failing tests (T004–T006)
3. Phase 3: Fix `schema.py` dead code (T007–T010)
4. Phase 4: Add `embedding_dimension` param (T011–T013)
5. Phase 5: Wire `dim` into `initialize_schema()` (T014–T017)
6. **STOP and VALIDATE**: `pytest tests/unit/test_schema_procedures.py -v` and smoke test from `quickstart.md`

This delivers the core bug fix: procedures are installed, server-side path is reachable.

### Full Delivery (all 4 stories — ~90 min)

Add Phase 6 (US3 — RuntimeError) after MVP validation, then Phase 7 (e2e) and Phase 8 (polish).

### Single-Developer Sequence

```
T001 → T002 → T003 → T004+T005(parallel) → T006 → T007 → T008 → T009 → T010
→ T011 → T012 → T013 → T014 → T015 → T016 → T017
→ T018 → T019 → T020
→ T021–T025(parallel) → T026
→ T027+T028(parallel) → T029 → T030
```

---

## Notes

- **No new dependencies** — zero changes to `pyproject.toml`
- **2 files changed** — `iris_vector_graph/schema.py` and `iris_vector_graph/engine.py`
- **2 new test files** — `tests/unit/test_schema_procedures.py` and `tests/integration/test_stored_procedure_install.py`
- **Rollback**: `git checkout main -- iris_vector_graph/schema.py iris_vector_graph/engine.py` (no migrations to undo)
- **[P]** tasks can run in parallel with different files; tasks in the same file (schema.py, engine.py) must be sequential
- Commit after each phase checkpoint for clean bisect history
