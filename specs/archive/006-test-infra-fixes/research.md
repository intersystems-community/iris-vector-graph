# Research: Test Infrastructure Standardization

**Date**: 2025-12-17
**Feature**: 006-test-infra-fixes

## Architecture Decision Record

### Context

The project has accumulated inconsistent test infrastructure patterns:
1. Some test files define local `iris_connection` fixtures that shadow the shared fixture in `conftest.py`
2. The project has an `iris/` directory containing ObjectScript sources, which causes `import iris` to resolve to the wrong module
3. Some tests use hardcoded ports instead of iris-devtester auto-discovery
4. The e2e tests reference a `kg_NodeEmbeddings` table that may not exist in fresh databases

### Current State Analysis

| Component | Location | Issue |
|-----------|----------|-------|
| Shared fixture | `tests/conftest.py` | Fallback uses `import iris` (conflicts with `iris/` directory) |
| E2E tests | `tests/e2e/test_multi_query_engine_platform.py` | Has local fixture (partial fix applied) |
| Integration tests | `tests/integration/test_nodepk_*.py` | 6+ files have local fixtures |
| GraphQL tests | `tests/integration/gql/test_graphql_*.py` | 4 files have local fixtures |
| Legacy tests | `tests/python/*.py` | 8 files use `import iris` |
| API main | `api/main.py` | Uses `import iris` |

### Decision

**Selected**: Standardize all imports to use `intersystems_irispython` module explicitly, and consolidate all fixtures to `conftest.py`

### Rationale

1. **Constitution Compliance**: iris-devtester is NON-NEGOTIABLE per constitution
2. **Import Conflict Resolution**: The `iris/` directory is essential (ObjectScript sources), so we must work around it
3. **Maintainability**: Single fixture definition prevents drift and inconsistency
4. **Reliability**: Auto-discovery works regardless of which port the container uses

### Alternatives Considered

| Option | Approach | Rejected Because |
|--------|----------|------------------|
| A | Rename `iris/` directory | Would break ObjectScript deployment patterns |
| B | Add `sys.path` manipulation | Fragile and ordering-dependent |
| C | Use `import intersystems_irispython.iris as iris` | Works but long import line |
| D | Use `importlib.import_module()` | **SELECTED** - explicit and reliable |

## Technical Research

### Import Module Pattern

The standard pattern to import a package when there's a naming conflict:

```python
import importlib
iris_module = importlib.import_module('intersystems_irispython.iris')

# Usage:
conn = iris_module.connect(host, port, namespace, user, password)
```

This works because `importlib` uses the full package path, bypassing Python's relative import resolution that would find the local `iris/` directory.

### iris-devtester API

The `iris-devtester` package provides:

```python
# Auto-discovery
from iris_devtester.connections import auto_detect_iris_host_and_port
host, port = auto_detect_iris_host_and_port()  # Finds first running IRIS container

# Connection (handles import internally)
from iris_devtester.utils.dbapi_compat import get_connection
conn = get_connection(host, port, 'USER', '_SYSTEM', 'SYS')
```

### pytest Fixture Scoping

For database connections, `scope="module"` is appropriate:
- Creates one connection per test module (file)
- Avoids connection overhead per test function
- `scope="session"` could cause staleness issues across many tests

### Graceful Skip Pattern

When no database is available:

```python
@pytest.fixture(scope="module")
def iris_connection():
    try:
        from iris_devtester.connections import auto_detect_iris_host_and_port
        from iris_devtester.utils.dbapi_compat import get_connection
        host, port = auto_detect_iris_host_and_port()
        conn = get_connection(host, port, 'USER', '_SYSTEM', 'SYS')
        yield conn
        conn.close()
    except Exception as e:
        pytest.skip(f"No IRIS database available: {e}")
```

## Files Requiring Changes

### Category 1: Fix Import Pattern

Files that use `import iris` and need to use `importlib`:

1. `tests/conftest.py` (fallback path)
2. `api/main.py`
3. `tests/python/test_vector_functions.py`
4. `tests/python/test_sql_queries.py`
5. `tests/python/test_schema_validation.py`
6. `tests/python/test_python_sdk.py`
7. `tests/python/test_python_operators.py`
8. `tests/python/test_performance_benchmarks.py`
9. `tests/python/test_networkx_loader.py`
10. `tests/python/run_all_tests.py`
11. `tests/test_working_system.py`
12. `tests/unit/test_graphql_dataloader.py`
13. `tests/integration/test_nodepk_migration.py`
14. `tests/integration/test_nodepk_constraints.py`
15. `tests/integration/gql/test_graphql_vector_search.py`
16. `tests/integration/gql/test_graphql_queries.py`
17. `tests/integration/gql/test_graphql_nested_queries.py`
18. `tests/integration/gql/test_graphql_mutations.py`

### Category 2: Remove Local Fixtures

Files that define their own `iris_connection` fixture:

1. `tests/e2e/test_multi_query_engine_platform.py`
2. `tests/integration/test_nodepk_production_scale.py`
3. `tests/integration/test_nodepk_performance.py`
4. `tests/integration/test_nodepk_migration.py`
5. `tests/integration/test_nodepk_graph_analytics.py`
6. `tests/integration/test_nodepk_constraints.py`
7. `tests/integration/test_nodepk_advanced_benchmarks.py`
8. `tests/integration/test_pagerank_sql_optimization.py`
9. `tests/integration/gql/test_graphql_vector_search.py`
10. `tests/integration/gql/test_graphql_queries.py`
11. `tests/integration/gql/test_graphql_nested_queries.py`
12. `tests/integration/gql/test_graphql_mutations.py`

### Category 3: Schema Verification

Files to verify/update:
1. `sql/schema.sql` - Already includes `nodes` table? (CHECK)
2. `scripts/setup_schema.py` - Creates all required tables?

## Open Questions (Resolved)

| Question | Resolution |
|----------|------------|
| How to import iris when directory exists? | Use `importlib.import_module('intersystems_irispython.iris')` |
| Which fixture scope to use? | `scope="module"` for balance of efficiency and isolation |
| How to skip tests gracefully? | `pytest.skip()` in fixture when no database |

## References

- Constitution: `.specify/memory/constitution.md` (Section II - iris-devtester NON-NEGOTIABLE)
- iris-devtester docs: Package auto-discovers IRIS containers
- pytest fixtures: https://docs.pytest.org/en/stable/how-to/fixtures.html
