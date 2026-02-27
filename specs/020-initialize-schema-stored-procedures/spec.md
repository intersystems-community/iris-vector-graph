# Feature Specification: initialize_schema() Stored Procedure Installation

**Feature Branch**: `020-initialize-schema-stored-procedures`  
**Created**: 2026-02-27  
**Status**: Draft  
**Input**: Bug report — `initialize_schema()` does not install `iris_vector_graph.kg_KNN_VEC` stored procedure; server-side vector search permanently unreachable on fresh installs.

---

## Problem Statement

Two compounding bugs prevent server-side vector search from ever working on a fresh install:

### Bug 1 — `schema.py` is syntactically broken (P0 blocker)

`iris_vector_graph/schema.py` has a **syntax error** that makes the entire module unimportable:

```
SyntaxError: unmatched ')' (line 442)
```

Root cause: `get_procedures_sql_list()` has a complete `return [...]` (lines 356–434) followed by dead code — a stray docstring fragment and a second `return f"""..."""` block (lines 436–520) that belong to an older version of the method that was never cleaned up. Python cannot parse the file.

**Impact**: `from iris_vector_graph import IRISGraphEngine` raises `ImportError`. The entire library is broken on any Python version that validates this file at import time. (That this was not caught suggests tests are running against a cached `.pyc` or the file was recently broken.)

### Bug 2 — Even if `schema.py` were fixed, the stored procedure DDL uses a fixed `VECTOR(DOUBLE, 1000)` declaration

The `CREATE OR REPLACE PROCEDURE iris_vector_graph.kg_KNN_VEC` in `get_procedures_sql_list()` hardcodes:

```sql
DECLARE qvec VECTOR(DOUBLE, 1000);
```

This is `≥` the embedding dimension only by accident. If a user has an embedding model with dimension > 1000 (e.g., 1536 for OpenAI `text-embedding-3-small`), the procedure silently truncates or errors. The dimension must be parameterised to match `embedding_dimension` passed to the engine.

### Bug 3 — The fallback path in `engine.kg_KNN_VEC()` is silent about the root cause

When `CALL iris_vector_graph.kg_KNN_VEC(?)` fails, the engine logs:

```
WARNING: Server-side kg_KNN_VEC failed: Procedure named IRIS_VECTOR_GRAPH.KG_KNN_VEC does not exist
```

But the user already called `initialize_schema()` which claims to install procedures. There is no diagnostic at init time to tell the user "procedure installation failed" — the failure is swallowed with a generic `logger.warning(...)` inside `initialize_schema()`.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Fresh install uses server-side vector search (Priority: P0)

As a developer, I want to call `engine.initialize_schema()` once and have all IRIS stored procedures installed correctly, so that `engine.kg_KNN_VEC()` uses the fast server-side path on the very first query without any additional setup steps.

**Why this priority**: The current state renders server-side vector search **permanently unreachable** on fresh installs. The fallback loads all embeddings into Python memory — a >10× performance regression on real datasets (11K+ nodes).

**Independent Test**: Call `initialize_schema()` on a clean schema; then call `kg_KNN_VEC()` without mocking; verify the server-side `CALL` succeeds and the Python fallback is NOT invoked (assert no `logger.warning` about fallback).

**Acceptance Scenarios**:

1. **Given** a freshly created IRIS connection with no prior schema, **When** `engine.initialize_schema()` is called, **Then** the procedure `iris_vector_graph.kg_KNN_VEC` exists in IRIS and `CALL iris_vector_graph.kg_KNN_VEC(?, ?, ?, ?)` succeeds.
2. **Given** a working `iris_vector_graph.kg_KNN_VEC` procedure, **When** `engine.kg_KNN_VEC(query_vector, k=5)` is called, **Then** `_kg_KNN_VEC_python_optimized` is NOT invoked and no fallback warning is logged.
3. **Given** `initialize_schema()` is called twice on the same database (idempotent re-run), **Then** no error is raised and the procedure is not duplicated or corrupted.

---

### User Story 2 — Procedure dimension matches configured embedding dimension (Priority: P1)

As a developer using a 1536-dimension embedding model, I want the stored procedure to be created with the correct vector dimension, so that `VECTOR_COSINE` comparisons work correctly without silent truncation.

**Why this priority**: Hardcoded `VECTOR(DOUBLE, 1000)` is a silent correctness bug for any model with dimension > 1000.

**Independent Test**: Create engine with `embedding_dimension=1536`; call `initialize_schema()`; inspect the procedure body from `INFORMATION_SCHEMA` or execute a test query with a 1536-dim vector.

**Acceptance Scenarios**:

1. **Given** `IRISGraphEngine(conn, embedding_dimension=384)`, **When** `initialize_schema()` is called, **Then** the procedure is created with `VECTOR(DOUBLE, 384)`.
2. **Given** `IRISGraphEngine(conn, embedding_dimension=1536)`, **When** `initialize_schema()` is called, **Then** the procedure is created with `VECTOR(DOUBLE, 1536)`.
3. **Given** `embedding_dimension=None` (not set), **When** `initialize_schema()` is called, **Then** a `ValueError` is raised before any DDL is executed (existing behaviour preserved).

---

### User Story 3 — Init-time diagnostic for procedure installation failure (Priority: P1)

As a developer, I want `initialize_schema()` to raise or warn clearly when a stored procedure fails to install, so that I can distinguish "schema installed but procedures failed" from "everything worked".

**Why this priority**: Silent swallowing of DDL failures means operators only discover the problem at query time under production load.

**Independent Test**: Mock the cursor to raise a non-"already exists" error during procedure DDL; verify `initialize_schema()` raises `RuntimeError` (or logs at `ERROR` level, per chosen option).

**Acceptance Scenarios**:

1. **Given** the `iris_vector_graph` schema cannot be created (e.g., permission denied), **When** `initialize_schema()` is called, **Then** a `RuntimeError` is raised with a message that includes which procedure failed and the underlying DB error.
2. **Given** the schema already exists (second call), **When** `initialize_schema()` is called, **Then** `CREATE SCHEMA iris_vector_graph` failure is silently ignored (it's expected on re-runs).
3. **Given** a partial failure (schema created, `kg_KNN_VEC` DDL rejected, `kg_TXT` succeeds), **When** the call completes, **Then** the error message lists which procedures failed.

---

### User Story 4 — `schema.py` is a valid Python module (Priority: P0)

As a contributor, I want `iris_vector_graph/schema.py` to parse without `SyntaxError`, so that the library can be imported and tests can run.

**Why this priority**: The file currently has a syntax error at line 442 that makes the module unimportable.

**Independent Test**: `python3 -c "from iris_vector_graph.schema import GraphSchema; print('OK')"` must exit 0.

**Acceptance Scenarios**:

1. **Given** the fixed `schema.py`, **When** `python3 -c "import ast; ast.parse(open('iris_vector_graph/schema.py').read())"` is run, **Then** it exits 0 with no error.
2. **Given** the fixed `schema.py`, **When** `from iris_vector_graph.schema import GraphSchema` is called, **Then** `GraphSchema.get_procedures_sql_list("Graph_KG")` returns a non-empty list of SQL strings.

---

### Edge Cases

- **Re-entrant calls**: `initialize_schema()` called multiple times must be idempotent. `CREATE OR REPLACE PROCEDURE` handles updates; `CREATE SCHEMA` is wrapped in a try/except for "already exists".
- **Partial schema**: If base tables exist but procedures do not (e.g., after a library upgrade), `initialize_schema()` must install the procedures without erroring on the existing tables.
- **Custom schema prefix**: When `set_schema_prefix("MyGraph")` has been called, the procedures must reference `MyGraph.kg_NodeEmbeddings`, not the default `Graph_KG`.
- **Large dimension**: `embedding_dimension=3072` (OpenAI `text-embedding-3-large`) — procedure must be created with the correct dimension and not hit an IRIS VECTOR size limit silently.
- **Permission denied on `iris_vector_graph` schema**: If the IRIS user cannot create the `iris_vector_graph` SQL schema, `initialize_schema()` must raise with a clear message rather than silently falling back to the Python path.

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `iris_vector_graph/schema.py` MUST be a syntactically valid Python module. The dead code block (lines 436–520 in the current broken file) MUST be removed. The module MUST import without `SyntaxError`.
- **FR-002**: `GraphSchema.get_procedures_sql_list(table_schema, embedding_dimension)` MUST accept an `embedding_dimension: int` parameter (default `1000` for backward compatibility with any existing callers that do not pass the dimension). The `VECTOR(DOUBLE, ...)` declaration inside the `kg_KNN_VEC` procedure body MUST use this dimension value, not a hardcoded `1000`. Internal callers (i.e., `initialize_schema()`) MUST always pass the real dimension.
- **FR-003**: `engine.initialize_schema()` MUST call `GraphSchema.get_procedures_sql_list(table_schema="Graph_KG", embedding_dimension=self.embedding_dimension)` with the engine's actual configured dimension.
- **FR-004**: If any procedure DDL fails with an error other than "already exists", `initialize_schema()` MUST both log at `ERROR` level and raise `RuntimeError` identifying how many procedures failed and the first underlying database error. Silent swallowing of DDL failures is not acceptable.
- **FR-005**: The `CREATE SCHEMA iris_vector_graph` statement MUST remain wrapped in a try/except for "already exists" to preserve idempotency.
- **FR-006**: The list returned by `get_procedures_sql_list` MUST include, at minimum: `CREATE SCHEMA iris_vector_graph`, `iris_vector_graph.kg_KNN_VEC`, `iris_vector_graph.kg_TXT`, `iris_vector_graph.kg_RRF_FUSE`.
- **FR-007**: After `initialize_schema()` completes without error, `CALL iris_vector_graph.kg_KNN_VEC(?, ?, ?, ?)` MUST succeed against a live IRIS instance (integration test gate).

### Non-Functional Requirements

- **NFR-001**: No regression in `initialize_schema()` performance — procedure installation is a one-time cost at setup, not on the query path.
- **NFR-002**: The fix must be backward-compatible: existing databases where the procedure was manually installed via `sql/operators.sql` must continue to work (the `CREATE OR REPLACE` DDL handles this).

### Key Entities *(include if feature involves data)*

- **`GraphSchema.get_procedures_sql_list(table_schema, embedding_dimension)`**: Returns `List[str]` of SQL DDL statements. Signature change: adds `embedding_dimension: int` as required parameter.
- **`IRISGraphEngine.initialize_schema()`**: Orchestrates schema + procedure installation. Calls `get_procedures_sql_list` with `self.embedding_dimension`. Raises on unrecoverable DDL failures.

---

## Implementation Plan *(required before coding)*

### Phase 1 — Fix `schema.py` syntax error (P0, ~15 min)

Remove the dead code block at lines 436–520 in `schema.py`. These lines are an older version of `get_procedures_sql_list` that was not deleted when the method was rewritten. The canonical implementation is the `return [...]` at lines 356–434.

  **Surgical change**: Delete lines 436–520 from `schema.py` (everything from the orphaned `Get SQL for retrieval stored procedures.` docstring fragment through the end of file). Line 434 is the closing `]` of the live `return` list; line 435 is blank — both must be kept.

### Phase 2 — Parameterise the vector dimension in DDL (P1, ~20 min)

Update `get_procedures_sql_list(table_schema, embedding_dimension)`:
- Add `embedding_dimension: int = 1000` parameter (defaulting to 1000 preserves the existing worst-case-safe behavior for callers that don't pass it, but all internal callers MUST pass the real dimension).
- Replace `VECTOR(DOUBLE, 1000)` inside `kg_KNN_VEC` body with `VECTOR(DOUBLE, {embedding_dimension})`.

Update the call site in `engine.initialize_schema()`:
```python
for stmt in GraphSchema.get_procedures_sql_list(
    table_schema="Graph_KG",
    embedding_dimension=dim,   # ← was missing
):
```

### Phase 3 — Surface procedure installation failures (P1, ~20 min)

In `initialize_schema()`, change the procedure installation loop:

```python
procedure_errors = []
for stmt in GraphSchema.get_procedures_sql_list(...):
    if not stmt.strip():
        continue
    try:
        cursor.execute(stmt)
    except Exception as e:
        err = str(e).lower()
        if "already exists" in err or "already has" in err:
            continue  # idempotent re-run, ignore
        procedure_errors.append((stmt[:80], e))
        logger.error("Procedure DDL failed: %s | Error: %s", stmt[:80], e)

if procedure_errors:
    raise RuntimeError(
        f"initialize_schema() failed to install {len(procedure_errors)} stored procedure(s). "
        "Server-side vector search will be unavailable. "
        f"First error: {procedure_errors[0][1]}"
    )
```

### Phase 4 — Tests (P1, ~30 min)

**New unit test** (`tests/unit/test_schema_procedures.py`):
- `test_schema_py_is_importable()` — parse + import check.
- `test_get_procedures_sql_list_contains_knn_vec()` — assert `kg_KNN_VEC` is in the returned list.
- `test_get_procedures_sql_list_uses_dimension()` — assert `VECTOR(DOUBLE, 384)` appears when `embedding_dimension=384`.
- `test_initialize_schema_raises_on_ddl_failure()` — mock cursor raises on procedure DDL; assert `RuntimeError`.

**New integration test** (`tests/integration/test_stored_procedure_install.py`):
- `test_initialize_schema_installs_knn_vec_procedure()` — call `initialize_schema()` on live IRIS, then `CALL iris_vector_graph.kg_KNN_VEC(...)`, assert no fallback warning logged.
- `test_kg_knn_vec_uses_server_side_path_after_init()` — monkey-patch `_kg_KNN_VEC_python_optimized` to raise; assert `kg_KNN_VEC()` succeeds (i.e., server-side path was used).

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `python3 -c "from iris_vector_graph.schema import GraphSchema"` exits 0.
- **SC-002**: After `initialize_schema()` on a fresh IRIS container, `CALL iris_vector_graph.kg_KNN_VEC(?, ?, ?, ?)` returns results without triggering the Python fallback path.
- **SC-003**: `initialize_schema()` called twice on the same DB does not raise an error.
- **SC-004**: `initialize_schema()` raises `RuntimeError` when a non-"already-exists" DDL error occurs during procedure installation.
- **SC-005**: `get_procedures_sql_list(table_schema="Graph_KG", embedding_dimension=384)` returns SQL containing `VECTOR(DOUBLE, 384)`.
- **SC-006**: All existing tests continue to pass (`pytest` green).

---

## Out of Scope

- Replacing the Python fallback entirely — the fallback remains as a last-resort safety net; this fix makes the server-side path actually reachable.
- Adding `kg_GRAPH_PATH`, `kg_RERANK`, or other procedures from `sql/operators_fixed.sql` — those are out of scope for this bug fix.
- Schema migration for users who have `VECTOR(DOUBLE, 1000)` procedures already installed — `CREATE OR REPLACE` will update them on next `initialize_schema()` call.

---

## Clarifications

### Session 2026-02-27

- **Root cause of silent failure confirmed**: `schema.py` has a `SyntaxError` at line 442 (`unmatched ')'`). The dead code starting at line 436 prevents the module from being parsed. This is the primary blocker.
- **Chosen fix option**: Combination of options 1 + 3 from the bug report — `initialize_schema()` installs the procedure (already attempted but broken by the syntax error), AND raises a clear error if DDL fails (option 3).
- **Dimension parameterisation**: Required. `VECTOR(DOUBLE, 1000)` is incorrect for models with > 1000 dimensions. The engine already has `self.embedding_dimension` — it must be threaded through to DDL generation.
- **Backward compatibility**: `CREATE OR REPLACE PROCEDURE` handles upgrades to existing installs. No manual migration needed.

## Assumptions

- The dead code block (lines 436–520 of `schema.py`) is an old version of the method that was accidentally left in during a refactor. The canonical implementation is the `return [...]` list at lines 356–434.
- The `iris_vector_graph` SQL schema (distinct from the `Graph_KG` data schema) is a shared namespace for all retrieval procedures — this design is intentional and should be preserved.
- IRIS Community 2025.1 supports `CREATE OR REPLACE PROCEDURE` with `RETURNS TABLE` syntax. No version-gating is required.
- The `embedding_dimension` is always known by the time `initialize_schema()` is called (enforced by the existing `ValueError` guard on `dim is None`).
