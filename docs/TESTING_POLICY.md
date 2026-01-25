# Mandatory IRIS Integration Policy

> **Version**: 1.0.0 | **Effective**: 2026-01-25 | **Supersedes**: None

This document establishes the **non-negotiable** testing policy for IRIS Vector Graph. All contributors MUST comply with these requirements. Violations will cause test failures at the pytest collection phase.

---

## Core Mandate

**NO MOCKING of InterSystems IRIS is permitted for any test marked `integration` or `e2e`.**

This policy derives directly from [Constitution Principle II](../.specify/memory/constitution.md):

> "TDD with running IRIS instance. No mocked database for integration tests. All tests involving data storage, vector operations, or graph operations MUST use live IRIS."

---

## Fixture Requirements

### Mandatory Fixtures for Database Tests

All tests that interact with the database MUST use the official fixtures defined in `tests/conftest.py`:

| Fixture | Scope | Required For |
|---------|-------|--------------|
| `iris_connection` | Module | Any test requiring a database connection |
| `iris_cursor` | Function | Any test executing SQL statements |
| `iris_test_container` | Session | Managed container lifecycle (auto-injected) |
| `clean_test_data` | Function | Tests that create data requiring cleanup |

### Prohibited Patterns

The following patterns are **FORBIDDEN** in `integration` and `e2e` tests:

```python
# ❌ FORBIDDEN: Mocking IRIS connection
@patch('iris.connect')
def test_something(mock_connect):
    mock_connect.return_value = MagicMock()
    ...

# ❌ FORBIDDEN: Mocking cursor/connection objects
def test_something():
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [...]
    ...

# ❌ FORBIDDEN: Fake database fixtures
@pytest.fixture
def fake_db():
    return {"nodes": [...]}  # In-memory fake
```

### Required Patterns

```python
# ✅ CORRECT: Use official fixtures with proper markers
@pytest.mark.requires_database
@pytest.mark.integration
def test_vector_search(iris_connection, clean_test_data):
    cursor = iris_connection.cursor()
    # Real IRIS operations
    ...

# ✅ CORRECT: E2E tests with full stack
@pytest.mark.requires_database
@pytest.mark.e2e
def test_full_workflow(iris_cursor, clean_test_data):
    # Real IRIS operations through the entire stack
    ...
```

---

## Marker Requirements

### Marker-Fixture Consistency (ENFORCED)

Any test using `iris_connection` or `iris_cursor` fixtures **MUST** have the `@pytest.mark.requires_database` marker. This is enforced by a pytest hook that will **fail tests** violating this policy.

| If Test Uses... | MUST Have Marker |
|-----------------|------------------|
| `iris_connection` | `@pytest.mark.requires_database` |
| `iris_cursor` | `@pytest.mark.requires_database` |
| `iris_test_container` | `@pytest.mark.requires_database` |

### Marker Definitions

| Marker | Meaning | IRIS Required |
|--------|---------|---------------|
| `@pytest.mark.requires_database` | Test requires live IRIS | **YES** |
| `@pytest.mark.integration` | Integration test | **YES** |
| `@pytest.mark.e2e` | End-to-end test | **YES** |
| `@pytest.mark.performance` | Performance benchmark | **YES** |
| (no marker) | Unit test | May mock for isolation |

---

## Runtime Environment

### Supported Runtime: `iris-devtester` Containers ONLY

The **only supported runtime** for database tests is a dedicated container managed by `iris-devtester`:

```python
# From tests/conftest.py - the ONLY supported container source
from iris_devtester.containers.iris_container import IRISContainer
from iris_devtester.ports import PortRegistry

container = IRISContainer(
    image="intersystemsdc/iris-community:latest-em",
    port_registry=PortRegistry(),
    project_path=os.getcwd()
)
```

### Prohibited Runtimes

- ❌ Shared development IRIS instances
- ❌ Production IRIS instances
- ❌ Hardcoded port connections (e.g., `iris.connect(port=1972)`)
- ❌ SQLite or other mock databases

---

## Policy Enforcement

### Automated Enforcement

A `pytest_runtest_setup` hook in `tests/conftest.py` enforces marker-fixture consistency:

1. Before each test runs, the hook inspects the test's fixtures
2. If `iris_connection` or `iris_cursor` is used without `@pytest.mark.requires_database`, the test **fails immediately**
3. The failure message includes this policy document reference

---

## Rationale

### Why No Mocking?

1. **IRIS-Specific Behavior**: IRIS SQL has unique behaviors (HNSW indexes, `%ID` columns, stored procedures) that mocks cannot replicate
2. **Vector Operations**: Embedding similarity calculations require real HNSW index traversal
3. **Graph Queries**: Multi-hop traversals and RRF fusion depend on actual data distribution
4. **Regression Prevention**: Mocked tests pass while production fails—we've learned this the hard way

### Why Dedicated Containers?

1. **Isolation**: Each test session gets a clean database state
2. **Reproducibility**: CI/CD produces identical results to local development
3. **Port Safety**: Dynamic port allocation prevents conflicts
4. **Password Handling**: Automatic `test`/`test` user creation avoids auth issues

---

**Author**: Thomas Dyar (thomas.dyar@intersystems.com)

---

## Unified Test Runner

The project provides a single, authoritative command for running all test categories.

> **DEPRECATED**: `tests/python/run_all_tests.py` is deprecated. Use `run-tests` instead.

### Usage

```bash
# Entry point (after uv sync)
run-tests                    # Run all tests
run-tests unit               # Fast unit tests (no database)
run-tests integration        # Database integration tests
run-tests e2e                # Full end-to-end tests
run-tests ux                 # UI tests (auto-starts demo server)
run-tests --quick            # Run unit + integration only

# Pytest passthrough
run-tests unit -- -x --pdb   # Pass arguments directly to pytest
```

### Test Categories

| Category | Markers | Demo Server | Database |
|----------|---------|-------------|----------|
| `unit` | `not (requires_database or e2e or integration)` | No | No |
| `integration` | `integration` or `requires_database` | No | Required |
| `e2e` | `e2e` | Optional | Required |
| `ux` | `e2e` + `*_ui.py` | **Auto** | Required |
| `contract` | `tests/contract/` | No | Required |

### Demo Server Lifecycle

For `ux` tests, the runner automatically:
1. Starts the demo server on port 8200
2. Waits for health check (up to 30s)
3. Executes requested tests
4. Gracefully shuts down the server

Override with `--no-demo-server` if the server is already running.

### Migration from Legacy Runner

```bash
# Old (deprecated)
python tests/python/run_all_tests.py --category api
python tests/python/run_all_tests.py --quick

# New
run-tests integration
run-tests --quick
```

