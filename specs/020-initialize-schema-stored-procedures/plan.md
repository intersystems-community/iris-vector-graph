# Implementation Plan: initialize_schema() Stored Procedure Installation

**Branch**: `020-initialize-schema-stored-procedures` | **Date**: 2026-02-27 | **Spec**: [spec.md](spec.md)  
**Input**: Feature specification from `/specs/020-initialize-schema-stored-procedures/spec.md`

---

## Summary

Fix three compounding bugs that prevent server-side vector search from being reachable on a fresh install:

1. **P0 — `schema.py` `SyntaxError`**: Dead code (lines 436–520, an orphaned second version of `get_procedures_sql_list`) makes the entire module unimportable. Fix: delete the dead block.
2. **P1 — Hardcoded `VECTOR(DOUBLE, 1000)`**: The `kg_KNN_VEC` procedure DDL uses a fixed dimension instead of the engine's configured `embedding_dimension`. Fix: add `embedding_dimension` parameter to `get_procedures_sql_list` and thread it through.
3. **P1 — Silent DDL failure**: `initialize_schema()` swallows procedure installation errors with `logger.warning`, so users only discover the failure at query time. Fix: collect failures and raise `RuntimeError`.

Technical approach: pure Python + SQL changes in 2 files (`schema.py`, `engine.py`) + 2 new test files. No new dependencies. No schema migrations.

---

## Technical Context

**Language/Version**: Python 3.11 (project target per AGENTS.md)  
**Primary Dependencies**: `intersystems-irispython`, `iris-devtester` (test only)  
**Storage**: InterSystems IRIS — SQL schema `Graph_KG` (data), `iris_vector_graph` (procedures)  
**Testing**: `pytest`; unit tests with `unittest.mock`; integration tests via `iris-devtester` container  
**Target Platform**: IRIS Community 2025.1+ (Docker)  
**Project Type**: Single library (`iris_vector_graph/`)  
**Performance Goals**: No regression — procedure installation is one-time setup, not on query path  
**Constraints**: Backward-compatible public API; `CREATE OR REPLACE` handles upgrades; no new public methods  
**Scale/Scope**: 2 source files changed (~30 lines total); 2 new test files (~80 lines each)

---

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

**Principle I (Library-First)**: ✅ All changes are within `iris_vector_graph/` library code.

**Principle II (Compatibility-First)**: ✅ `get_procedures_sql_list` gains a new optional parameter with a backward-compatible default (`embedding_dimension=1000`). `initialize_schema()` signature is unchanged. No public API breakage.

**Principle III (Test-First)**: ✅ Tests are specified before implementation. Unit tests cover the `GraphSchema` API in isolation; integration tests validate live IRIS behavior.

**Principle IV gate (IRIS-backend features)**:
- [x] IRIS container `iris_vector_graph` used (verified from `docker-compose.yml`)
- [x] Integration tests in `tests/integration/` covering all user stories (live IRIS via `iris-devtester`)
- [x] `SKIP_IRIS_TESTS` defaulting to `"false"` in all new test files (pattern from `tests/integration/test_schema_migration.py:43`)
- [x] No hardcoded IRIS ports — all resolved via `iris_test_container.get_exposed_port(1972)` (from `tests/conftest.py:255`)

**Principle V (Simplicity)**: ✅ Fix is surgical — no new abstractions, no new public methods, minimum lines changed.

**Principle VI (Grounding Rule)**: ✅ All infrastructure details verified before writing:
- Container name: `iris_vector_graph` ← `docker-compose.yml`
- Port resolution: `iris_test_container.get_exposed_port(1972)` ← `tests/conftest.py:255`
- Schema: `Graph_KG` ← `tests/conftest.py:288`
- Connection pattern: `iris_connection` fixture ← `tests/conftest.py:252`
- Clean fixture pattern: ← `tests/integration/test_schema_migration.py:7–41`

**No violations requiring justification.**

---

## Project Structure

### Documentation (this feature)

```text
specs/020-initialize-schema-stored-procedures/
├── spec.md          ✅ written
├── plan.md          ✅ this file
├── research.md      ✅ written
├── data-model.md    ✅ written
├── quickstart.md    ✅ written
├── contracts/
│   ├── get_procedures_sql_list.md  ✅ written
│   └── initialize_schema.md        ✅ written
└── tasks.md         (Phase 2 — /speckit.tasks)
```

### Source Code

```text
iris_vector_graph/          # Library (single project)
├── schema.py               # CHANGED: remove dead code; add embedding_dimension param
└── engine.py               # CHANGED: pass dim to get_procedures_sql_list; raise on DDL failure

tests/
├── unit/
│   └── test_schema_procedures.py       # NEW: unit tests for schema.py fix
└── integration/
    └── test_stored_procedure_install.py  # NEW: live IRIS integration tests
```

**Structure Decision**: Single project (Option 1). All changes within `iris_vector_graph/` library and `tests/`. No frontend, no mobile, no new packages.

---

## Complexity Tracking

> No constitution violations requiring justification.

---

## Phase 0: Research Summary

Research complete. See [research.md](research.md) for full findings.

| Question | Resolution |
|----------|-----------|
| Is `schema.py` truly broken at parse time? | ✅ Confirmed: `SyntaxError` at line 442. Dead code block lines 436–520. |
| Why was the procedure never installed? | ✅ Module unimportable → `initialize_schema()` could never call `get_procedures_sql_list`. Additionally, DDL failures silently swallowed. |
| Does `conftest.py` call `initialize_schema()`? | ✅ No — uses raw ObjectScript DDL. New tests must call it explicitly. |
| What test infrastructure patterns apply? | ✅ `iris_connection` fixture; `get_exposed_port(1972)`; `SKIP_IRIS_TESTS` default `"false"`. |
| Can we use `CREATE OR REPLACE` for idempotency? | ✅ Yes — IRIS 2025.1 supports it; already used in `sql/operators.sql`. |

---

## Phase 1: Design Summary

Design complete. See [data-model.md](data-model.md) and [contracts/](contracts/).

**Key design decisions**:
- `get_procedures_sql_list(table_schema, embedding_dimension=1000)` — new optional param, backward-compatible default
- `initialize_schema()` raises `RuntimeError` after collecting all procedure DDL failures (fail-all or succeed-all)
- No new public methods — `initialize_schema()` remains the single setup entrypoint
- Dead code removal is a pure deletion — no logic change to the surviving implementation

**Constitution Check (post-design)**:
- All decisions consistent with Principles I–VI ✅
- No new abstractions introduced ✅
- Backward compatibility preserved ✅

---

## Implementation Phases

### Phase A — Fix `schema.py` (P0)

**Files**: `iris_vector_graph/schema.py`

**Change**: Delete lines 436–520 (dead code block). The surviving `get_procedures_sql_list` implementation (lines 351–434) is the canonical version; line 435 is a blank line that must be preserved. Add `embedding_dimension: int = 1000` parameter; replace `VECTOR(DOUBLE, 1000)` with `VECTOR(DOUBLE, {embedding_dimension})`.

**Verification**: `python3 -c "import ast; ast.parse(open('iris_vector_graph/schema.py').read()); print('OK')"` exits 0.

---

### Phase B — Fix `engine.py` (P1)

**Files**: `iris_vector_graph/engine.py`

**Changes** (in `initialize_schema()`, lines 169–178):

```python
# Before:
for stmt in GraphSchema.get_procedures_sql_list(table_schema="Graph_KG"):
    if not stmt.strip():
        continue
    try:
        cursor.execute(stmt)
    except Exception as e:
        err = str(e).lower()
        if "already exists" not in err and "already has" not in err:
            logger.warning("Procedure setup warning: %s | Statement: %.100s", e, stmt)

# After:
procedure_errors = []
for stmt in GraphSchema.get_procedures_sql_list(
    table_schema="Graph_KG",
    embedding_dimension=dim,
):
    if not stmt.strip():
        continue
    try:
        cursor.execute(stmt)
    except Exception as e:
        err = str(e).lower()
        if "already exists" in err or "already has" in err:
            continue  # idempotent re-run
        procedure_errors.append(e)
        logger.error(
            "Procedure DDL failed: %s | Error: %s",
            stmt[:80],
            e,
        )

if procedure_errors:
    raise RuntimeError(
        f"initialize_schema() failed to install {len(procedure_errors)} "
        f"stored procedure(s). Server-side vector search will be unavailable. "
        f"First error: {procedure_errors[0]}"
    )
```

**Verification**: `pytest tests/unit/test_schema_procedures.py -v` all green.

---

### Phase C — Unit Tests (P1, test-first)

**File**: `tests/unit/test_schema_procedures.py` (new)

Tests (written before implementation):
1. `test_schema_py_is_importable` — `ast.parse` on `schema.py` succeeds
2. `test_get_procedures_sql_list_contains_knn_vec` — `"kg_KNN_VEC"` in list
3. `test_get_procedures_sql_list_uses_dimension` — `"VECTOR(DOUBLE, 384)"` when `embedding_dimension=384`
4. `test_get_procedures_sql_list_idempotent_default` — `"VECTOR(DOUBLE, 1000)"` with no dimension arg
5. `test_initialize_schema_raises_on_ddl_failure` — mock cursor raises non-"already exists" on procedure DDL → `RuntimeError`
6. `test_initialize_schema_ignores_already_exists` — mock cursor raises "already exists" → no error

---

### Phase D — Integration Tests (P1, Constitution IV)

**File**: `tests/integration/test_stored_procedure_install.py` (new)

Tests:
1. `test_initialize_schema_installs_all_procedures` — after `initialize_schema()`, query `INFORMATION_SCHEMA.ROUTINES` for `iris_vector_graph` schema; assert all 3 procedures present
2. `test_kg_knn_vec_callable_after_init` — call `CALL iris_vector_graph.kg_KNN_VEC(?, ?, ?, ?)` directly; assert no exception
3. `test_kg_knn_vec_uses_server_side_path` — monkey-patch `_kg_KNN_VEC_python_optimized` to raise `AssertionError("fallback invoked")`; call `engine.kg_KNN_VEC()`; assert no assertion error (server path used)
4. `test_initialize_schema_is_idempotent` — call `initialize_schema()` twice; assert no error on second call
5. `test_initialize_schema_dimension_in_procedure` — create engine with `embedding_dimension=384`; call `initialize_schema()`; assert `VECTOR(DOUBLE, 384)` in procedure DDL (via `INFORMATION_SCHEMA` or direct procedure text query)

All tests use:
- `iris_connection` fixture (from `tests/conftest.py`)
- `@pytest.mark.skipif(os.environ.get("SKIP_IRIS_TESTS", "false") == "true", reason="IRIS not available")`
- `clean_procedures` fixture that drops `iris_vector_graph` schema before each test for clean state

---

## Risks and Mitigations

| Risk | Likelihood | Mitigation |
|------|-----------|-----------|
| IRIS doesn't support querying procedure text from `INFORMATION_SCHEMA` | Low | Fall back to asserting `CALL` succeeds without exception |
| `DROP SCHEMA iris_vector_graph CASCADE` not supported in IRIS | Medium | Drop each procedure individually; fall back to `CREATE OR REPLACE` being idempotent |
| Other code in the project imports `schema.py` in a way that cached `.pyc` hid the bug | High (confirmed) | Run `python -B` in CI; invalidate `__pycache__` in test setup |
