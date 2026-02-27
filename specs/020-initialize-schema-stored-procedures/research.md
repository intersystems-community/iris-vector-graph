# Research: initialize_schema() Stored Procedure Installation

**Feature**: 020-initialize-schema-stored-procedures  
**Date**: 2026-02-27  
**Status**: Complete — all NEEDS CLARIFICATION resolved

---

## 1. Root Cause Confirmation

### Decision
`iris_vector_graph/schema.py` has a **Python `SyntaxError`** at line 442. The module cannot be parsed by CPython. This is the primary blocker.

### Rationale
Verified by running:
```
python3 -c "import ast; ast.parse(open('iris_vector_graph/schema.py').read())"
# SyntaxError: unmatched ')' (line 442)
```

**Mechanism**: `get_procedures_sql_list()` (lines 351–434) closes with `return [...]` at line 434. Line 435 is blank. Lines 436–520 are dead code — an older version of the same method left behind during a refactor. The dead block begins with a bare docstring fragment (`Get SQL for retrieval stored procedures.`) which Python parses as a statement expression, then hits invalid SQL comment syntax (`-- 1) KNN...`) where the `)` triggers the `unmatched ')'` error.

**Alternatives considered**:
- "Is this a runtime failure, not a syntax error?" → No. `ast.parse()` confirms it is a parse-time failure before any execution occurs.
- "Could the `.pyc` cache hide this?" → On a fresh environment or CI, the `.pyc` is regenerated. This is a hard blocker for all new installs.

---

## 2. Why Server-Side Vector Search Was Never Reachable

### Decision
Even if `schema.py` were syntactically valid, the `initialize_schema()` procedure-installation loop at `engine.py:169–178` would silently swallow DDL failures — making it impossible to distinguish "installed" from "failed to install".

### Rationale
The loop (engine.py:173–178):
```python
try:
    cursor.execute(stmt)
except Exception as e:
    err = str(e).lower()
    if "already exists" not in err and "already has" not in err:
        logger.warning("Procedure setup warning: %s | Statement: %.100s", e, stmt)
```
Logs a `WARNING` but does not raise. In production, this means:
1. Schema created successfully → ✓
2. Procedure DDL fails (e.g., bad SQL, permission denied) → `WARNING` logged, execution continues
3. `conn.commit()` called → transaction committed with no procedures
4. First call to `kg_KNN_VEC()` → falls back to Python with the `WARNING: Server-side kg_KNN_VEC failed` message

The user only sees the problem at query time, not at setup time.

**Alternatives considered**:
- Raise immediately on first DDL failure → chosen, with a "collect all failures then raise" pattern to surface all errors at once
- Add `install_stored_procedures()` as a separate public method → rejected (adds API surface; `initialize_schema()` should be the one-stop setup call per the library-first principle)

---

## 3. Vector Dimension Hardcoding

### Decision
Replace `VECTOR(DOUBLE, 1000)` in the `kg_KNN_VEC` procedure DDL with `VECTOR(DOUBLE, {embedding_dimension})`, parameterized from `get_procedures_sql_list(table_schema, embedding_dimension)`.

### Rationale
The current hardcoded value of 1000:
- Silently **truncates** vectors with dimension > 1000 on assignment in IRIS
- Is arbitrarily large for models with dimension 384 (wastes memory/index space)
- Is insufficient for OpenAI `text-embedding-3-small` (1536), `text-embedding-3-large` (3072), and others

The engine already validates and stores `self.embedding_dimension`. It's passed to `get_base_schema_sql(embedding_dimension=dim)` for table DDL — the same value must flow to procedure DDL.

IRIS `VECTOR(DOUBLE, N)` requires a fixed compile-time dimension. The procedure must be created with the correct `N`. `CREATE OR REPLACE PROCEDURE` updates an existing procedure on re-run, so changing the dimension after initial creation is handled automatically.

**Alternatives considered**:
- Use `VECTOR(DOUBLE, 3072)` as the new universal maximum → rejected (IRIS indexes dimension-specific; oversized declaration affects index efficiency and is a correctness risk)
- Accept dimension via a separate schema-level constant → rejected (unnecessary complexity)

---

## 4. Test Infrastructure Patterns (Grounding Rule compliance)

All infrastructure details verified from authoritative sources before inclusion:

| Detail | Authoritative Source | Value |
|--------|---------------------|-------|
| Container name | `docker-compose.yml` (`container_name:`) | `iris_vector_graph` |
| Port resolution | `tests/conftest.py:255` | `iris_test_container.get_exposed_port(1972)` |
| Connection module | `tests/conftest.py:263–274` | `irisnative.createConnection` (primary), `iris.connect` (fallback) |
| Schema | `tests/conftest.py:288` | `Graph_KG` (SET SCHEMA at connection time) |
| `SKIP_IRIS_TESTS` default | `tests/integration/test_schema_migration.py:43` pattern | `os.environ.get("SKIP_IRIS_TESTS", "false") == "true"` |
| Fixture for connection | `tests/conftest.py:252` | `iris_connection` (module-scoped) |
| Clean fixture pattern | `tests/integration/test_schema_migration.py:7–41` | Drop tables explicitly, then yield, then drop again |

### Decision: Test structure
- **Unit tests** (`tests/unit/test_schema_procedures.py`): Mock the cursor; test `GraphSchema` API in isolation; no IRIS required.
- **Integration tests** (`tests/integration/test_stored_procedure_install.py`): Use `iris_connection` fixture; test against live IRIS via `iris_test_container`.

No e2e test directory needed specifically for this feature (it's a schema/DDL fix, not a user-facing Cypher/GraphQL feature). Integration tests against live IRIS satisfy Principle IV.

---

## 5. IRIS `CREATE OR REPLACE PROCEDURE` idempotency

### Decision
`CREATE OR REPLACE PROCEDURE` is used throughout (already in the existing DDL). This is idempotent — calling `initialize_schema()` twice replaces the procedure body without error.

### Rationale
Verified from existing SQL files (`sql/operators.sql`, `sql/operators_fixed.sql`) — all use `CREATE OR REPLACE PROCEDURE`. IRIS supports this syntax in 2025.1. The `CREATE SCHEMA iris_vector_graph` statement is NOT `CREATE OR REPLACE` and must remain wrapped in a try/except for "already exists", consistent with the existing `CREATE SCHEMA Graph_KG` guard at engine.py:139–141.

---

## 6. Procedure schema: `iris_vector_graph` vs `Graph_KG`

### Decision
Procedures live in the `iris_vector_graph` SQL schema (a separate namespace from the `Graph_KG` data schema). This design is intentional and correct.

### Rationale
- The `Graph_KG` schema holds **data tables** (nodes, edges, embeddings).
- The `iris_vector_graph` schema holds **retrieval procedures** shared across potentially multiple data schemas.
- The procedure bodies reference `{table_schema}.kg_NodeEmbeddings` via f-string substitution — `table_schema` is the data schema, `iris_vector_graph` is the procedure namespace.
- This separation is already in `sql/operators.sql` and the existing `get_procedures_sql_list()` implementation.

---

## 7. The `conftest.py` does NOT call `initialize_schema()`

### Observation
`tests/conftest.py` sets up the schema via raw ObjectScript DDL (hardcoded SQL in `_setup_iris_container`), not via `engine.initialize_schema()`. This means the existing integration test suite does **not** exercise `initialize_schema()` at all — it creates the schema manually.

### Impact
The new integration tests for this feature must call `engine.initialize_schema()` explicitly to test the actual code path. They cannot rely on the conftest fixture to have already set up the schema — in fact, a `clean_schema` fixture that drops tables first (pattern from `test_schema_migration.py`) is needed to ensure a clean starting state.

### Decision
Add a `clean_procedures` fixture that drops the `iris_vector_graph` SQL schema procedures (or the whole `iris_vector_graph` schema) before each test, so `initialize_schema()` is tested from a true blank slate.
