# Test Infrastructure Quick Reference

## Running Tests

### Prerequisites

1. **IRIS Container Running**: Start with `docker-compose up -d`
2. **Dependencies Installed**: `uv sync` or `pip install -e .`

### Run All Tests

```bash
# Recommended: Full test suite
uv run pytest tests/ -v --tb=short

# Quick validation (skips slow tests)
uv run pytest tests/ -v --tb=short -m "not slow"
```

### Run Specific Test Categories

```bash
# Unit tests only (can run without database)
uv run pytest tests/unit/ -v

# Integration tests (requires database)
uv run pytest tests/integration/ -v

# E2E tests (requires database + API)
uv run pytest tests/e2e/ -v

# Contract tests
uv run pytest tests/contract/ -v
```

### Test Markers

```bash
# Tests that require live IRIS database
uv run pytest -m requires_database

# Integration tests
uv run pytest -m integration

# E2E tests
uv run pytest -m e2e

# Skip slow tests
uv run pytest -m "not slow"
```

## Database Connection

### Auto-Discovery (Recommended)

Tests automatically discover running IRIS containers via `iris-devtester`:

```python
# In your test file - just use the fixture
def test_something(iris_connection):
    cursor = iris_connection.cursor()
    cursor.execute("SELECT 1")
    result = cursor.fetchone()
    assert result[0] == 1
```

### Manual Override

If auto-discovery doesn't work, set environment variables in `.env`:

```bash
IRIS_HOST=localhost
IRIS_PORT=1972
IRIS_NAMESPACE=USER
IRIS_USER=_SYSTEM
IRIS_PASSWORD=SYS
```

## Writing New Tests

### Use Shared Fixtures

All database tests should use the shared fixture from `conftest.py`:

```python
# Good: Uses shared fixture
def test_my_feature(iris_connection):
    cursor = iris_connection.cursor()
    # ... test code

# Bad: Defines own fixture (causes shadowing)
@pytest.fixture
def iris_connection():  # DON'T DO THIS
    ...
```

### Clean Up Test Data

Always clean up test data to prevent cross-test contamination:

```python
@pytest.fixture
def test_data_cleanup(iris_connection):
    cursor = iris_connection.cursor()

    # Setup: Clean before test
    cursor.execute("DELETE FROM nodes WHERE node_id LIKE 'TEST:%'")
    iris_connection.commit()

    yield

    # Teardown: Clean after test
    cursor.execute("DELETE FROM nodes WHERE node_id LIKE 'TEST:%'")
    iris_connection.commit()

def test_something(iris_connection, test_data_cleanup):
    # Test runs with clean state
    ...
```

### Skip When No Database

For tests that can run without a database in some modes:

```python
@pytest.mark.requires_database
def test_database_feature(iris_connection):
    # This test is skipped if no database available
    ...
```

## Troubleshooting

### "No IRIS database available"

1. Check container is running: `docker ps | grep iris`
2. Check port mapping: `docker port <container_name> 1972`
3. Try manual connection with `.env` variables

### "Import iris failed" / Wrong module imported

The project has an `iris/` directory for ObjectScript sources. If you see import errors:

```python
# DON'T use:
import iris

# DO use:
import importlib
iris_module = importlib.import_module('intersystems_irispython.iris')

# OR better, use iris-devtester:
from iris_devtester.utils.dbapi_compat import get_connection
```

### Tests fail with "table does not exist"

Run schema setup:

```bash
# Run all migrations
python scripts/setup_schema.py

# Or manually run SQL
docker exec -i <container> iris session IRIS -U USER < sql/schema.sql
docker exec -i <container> iris session IRIS -U USER < sql/migrations/001_add_nodepk_table.sql
```

### Connection pool exhausted

If tests hang waiting for connections:

```bash
# Restart the container
docker-compose restart

# Or increase pool size in api/main.py
connection_pool = ConnectionPool(max_connections=20)
```

## Performance Expectations

| Test Category | Expected Time | Note |
|---------------|---------------|------|
| Unit tests | <5s | No database needed |
| Integration tests | <30s | Database operations |
| E2E tests | <30s | Full API + database |
| Contract tests | <10s | API contract validation |
| **Full suite** | <60s | All tests combined |
