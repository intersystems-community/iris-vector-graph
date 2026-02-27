# Quickstart: Testing initialize_schema() Stored Procedure Fix

**Feature**: 020-initialize-schema-stored-procedures  
**Audience**: Developer implementing or reviewing this fix

---

## Prerequisites

```bash
# Ensure IRIS container is running
docker ps | grep iris_vector_graph
# If not running:
docker-compose up -d

# Install dev dependencies
pip install -e ".[dev]"
```

---

## Verify the Bug (before fix)

```bash
# Bug 1: schema.py SyntaxError
python3 -c "import ast; ast.parse(open('iris_vector_graph/schema.py').read()); print('OK')"
# Expected (current): SyntaxError: unmatched ')' (line 442)
# Expected (after fix): OK

# Bug 2: module is unimportable
python3 -c "from iris_vector_graph.schema import GraphSchema; print('OK')"
# Expected (current): SyntaxError or ImportError
# Expected (after fix): OK

# Bug 3: server-side procedure missing — requires live IRIS
python3 - <<'EOF'
import irisnative, os
port = int(os.environ.get("IRIS_TEST_PORT", "1972"))
conn = irisnative.createConnection("localhost", port, "USER", "test", "test")
cursor = conn.cursor()
cursor.execute("SELECT ROUTINE_NAME FROM INFORMATION_SCHEMA.ROUTINES WHERE ROUTINE_SCHEMA = 'iris_vector_graph'")
rows = cursor.fetchall()
print("Installed procedures:", rows)
# Expected (current): [] — nothing installed
# Expected (after fix): [('kg_KNN_VEC',), ('kg_TXT',), ('kg_RRF_FUSE',)]
EOF
```

---

## Run Unit Tests

```bash
# All unit tests (no IRIS required)
pytest tests/unit/test_schema_procedures.py -v

# Specific tests
pytest tests/unit/test_schema_procedures.py::test_schema_py_is_importable -v
pytest tests/unit/test_schema_procedures.py::test_get_procedures_sql_list_uses_dimension -v
pytest tests/unit/test_schema_procedures.py::test_initialize_schema_raises_on_ddl_failure -v
```

Expected output (all green):
```
PASSED tests/unit/test_schema_procedures.py::test_schema_py_is_importable
PASSED tests/unit/test_schema_procedures.py::test_get_procedures_sql_list_contains_knn_vec
PASSED tests/unit/test_schema_procedures.py::test_get_procedures_sql_list_uses_dimension
PASSED tests/unit/test_schema_procedures.py::test_get_procedures_sql_list_idempotent_default
PASSED tests/unit/test_schema_procedures.py::test_initialize_schema_raises_on_ddl_failure
```

---

## Run Integration Tests (requires live IRIS)

```bash
# Integration tests against live container
pytest tests/integration/test_stored_procedure_install.py -v --use-existing-iris

# Full integration run
pytest tests/integration/ -v --use-existing-iris -k "procedure"
```

Expected output:
```
PASSED tests/integration/test_stored_procedure_install.py::test_initialize_schema_installs_all_procedures
PASSED tests/integration/test_stored_procedure_install.py::test_kg_knn_vec_callable_after_init
PASSED tests/integration/test_stored_procedure_install.py::test_kg_knn_vec_uses_server_side_path
PASSED tests/integration/test_stored_procedure_install.py::test_initialize_schema_is_idempotent
PASSED tests/integration/test_stored_procedure_install.py::test_initialize_schema_dimension_in_procedure
```

---

## Manual Smoke Test (after fix)

```python
import irisnative
import json, math

# Connect (replace port with actual assigned port)
conn = irisnative.createConnection("localhost", 1972, "USER", "test", "test")

from iris_vector_graph import IRISGraphEngine
engine = IRISGraphEngine(conn, embedding_dimension=384)

# Initialize — should complete without WARNING about procedures
engine.initialize_schema()

# Insert a test node and embedding
engine.add_node("test:node:1", labels=["TestLabel"])
vector_384 = [math.sin(i * 0.01) for i in range(384)]
engine.store_embedding("test:node:1", vector_384)

# Search — should use server-side path (no WARNING in logs)
import logging
logging.basicConfig(level=logging.WARNING)

query = json.dumps(vector_384)
results = engine.kg_KNN_VEC(query, k=1)
print(results)
# Expected: [('test:node:1', 1.0)]  — exact match, score ~1.0
# No WARNING about "Server-side kg_KNN_VEC failed"
```

---

## Key Files Changed

| File | Change |
|------|--------|
| `iris_vector_graph/schema.py` | Remove dead code lines 436–520; add `embedding_dimension` param to `get_procedures_sql_list` |
| `iris_vector_graph/engine.py` | Pass `embedding_dimension=dim` to `get_procedures_sql_list`; raise `RuntimeError` on procedure DDL failures |
| `tests/unit/test_schema_procedures.py` | **New** — unit tests for schema.py fix |
| `tests/integration/test_stored_procedure_install.py` | **New** — integration tests against live IRIS |

---

## Rollback

If the fix introduces a regression:

```bash
git checkout main -- iris_vector_graph/schema.py iris_vector_graph/engine.py
```

The fix is surgical — only `schema.py` (dead code removal + param addition) and `engine.py` (two lines changed in `initialize_schema`). No schema migrations, no new dependencies.
