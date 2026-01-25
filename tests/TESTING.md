# Testing Infrastructure

## Overview

IRIS Vector Graph uses a **managed test container** approach to ensure reliable, reproducible tests. All database tests run against a dedicated IRIS container managed by the test infrastructure using the `iris-devtester` library.

## Quick Start

```bash
# Run all tests (container starts automatically)
pytest

# Run specific test category
pytest -m integration
pytest -m e2e
pytest -m performance

# Run with existing container (faster for development)
# Start container once: python scripts/setup_iris.py
pytest --use-existing-iris
```

## Test Container Lifecycle

The `iris_test_container` fixture (session scope) manages the container lifecycle:

1.  **Start**: Starts a dedicated Docker container named `iris_vector_graph_test`.
2.  **Ready**: Waits for the IRIS SuperServer to be ready (120s timeout).
3.  **Initialize**: Automatically executes `sql/schema.sql` and `sql/operators_fixed.sql` using the **`User`** schema.
4.  **Security**: Creates a dedicated **`test`** user with password **`test`** and ensures password expiry is disabled.
5.  **Execute**: Runs the requested tests.
6.  **Tear down**: Stops and removes the container at the end of the session (unless `--use-existing-iris` is specified).

## Key Fixtures

| Fixture | Scope | Description |
|---------|-------|-------------|
| `iris_test_container` | Session | Managed IRIS container for the entire test session. |
| `iris_connection` | Module | Reusable DBAPI connection from the managed container. |
| `iris_cursor` | Function | Fresh cursor with automatic rollback isolation. |
| `clean_test_data` | Function | Provides a unique prefix (e.g., `TEST_abc123:`) and auto-deletes matching data after the test. |

## Writing New Tests

Always use the `requires_database` marker and the provided fixtures:

```python
import pytest

@pytest.mark.requires_database
def test_my_feature(iris_connection, clean_test_data):
    """Test with managed container and auto-cleanup."""
    prefix = clean_test_data
    cursor = iris_connection.cursor()
    
    # Create test data using the unique prefix
    cursor.execute(
        "INSERT INTO nodes (node_id) VALUES (?)",
        (f"{prefix}node1",)
    )
    iris_connection.commit()
    
    # Assertions
    cursor.execute("SELECT COUNT(*) FROM nodes WHERE node_id LIKE ?", (f"{prefix}%",))
    assert cursor.fetchone()[0] == 1
    
    # No need to manual cleanup - clean_test_data fixture handles it!
```

## Configuration

| Environment Variable | Default | Description |
|----------------------|---------|-------------|
| `IRIS_TEST_USE_EXISTING` | `false` | Set to `true` to reuse a running container. |
| `IRIS_CONTAINER` | `iris_db` | Name of the container to attach to when using existing. |
| `IRIS_TEST_TIMEOUT` | `120` | Startup timeout in seconds. |

## Troubleshooting

### "Password change required" or "Access Denied"
IRIS Community Edition often enforces a password change on first login. The test infrastructure automatically handles this by:
1. Creating a dedicated `test`/`test` user with `%ALL` roles.
2. Forcefully clearing `ChangePassword` and `PasswordNeverExpires` flags via ObjectScript.

If you encounter this when connecting manually:
- Use the **`test`** user instead of `_SYSTEM`.
- Or reset the `_SYSTEM` password: `Set usr = ##class(Security.Users).%OpenId("_SYSTEM"), usr.ChangePassword=0, usr.PasswordNeverExpires=1 Do usr.%Save()`

### Schema Requirements (`User`)
All tables and procedures are created in the **`User`** schema. If you are writing raw SQL queries in tests, ensure you either:
- Use the **`iris_cursor`** fixture (which automatically runs `SET SCHEMA User`).
- Or use fully qualified names: `SELECT * FROM User.nodes`.

### "AttributeError: module 'iris' has no attribute 'connect'"
This used to happen because of a naming conflict with the `iris/` directory. That directory has been renamed to `iris_src/`, so `import iris` should now work correctly.

The `iris_connection` fixture handles connection management for you.

### Resetting the Environment
If the test container gets into a bad state:
```bash
docker rm -f iris_vector_graph_test
pytest
```

## Constitutional Compliance

This infrastructure enforces **Principle II: Test-First with Live Database**:
- ✅ All `@pytest.mark.requires_database` tests hit real IRIS.
- ✅ No mocked database for integration tests.
- ✅ Schema automatically initialized.
- ✅ Full isolation via `clean_test_data` prefixes.
