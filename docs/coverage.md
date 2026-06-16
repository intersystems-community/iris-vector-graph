# Test Coverage Policy

## Unit vs Integration Coverage Split

This project distinguishes between two test tiers with different coverage expectations.

### Unit tests (`tests/unit/`)

Run without a live IRIS container. Fast, always-on in CI.

| Module | Unit coverage target | Notes |
|--------|---------------------|-------|
| `iris_vector_graph/sdk.py` | ≥95% | HTTP client, mocked via `unittest.mock` |
| `iris_vector_graph/cypher/` | ≥85% | Lexer, parser, translator — pure Python |
| `iris_vector_graph/api_auth.py` | ≥98% | FastAPI middleware — testable with `TestClient` |
| `iris_vector_graph/_engine/` | ≥75% | Mixed: logic mocked, IRIS callin skipped |
| `iris_vector_graph/result.py` | ≥90% | Pure dataclass |

### Integration tests (`tests/integration/`, `tests/unit/` with IRIS fixtures)

Require a live IRIS container (`ivg-iris` or `ivg-iris-enterprise`). These provide the meaningful
coverage for IRIS-native modules.

| Module | Coverage tier | Reason |
|--------|--------------|--------|
| `iris_vector_graph/stores/iris_sql_store.py` | Integration only | ~95% of lines execute IRIS SQL/globals directly |
| `iris_vector_graph/stores/arno_bridge.py` | Integration only | Calls `^%SYS.zf` IRIS callin — no mock equivalent |
| `iris_vector_graph/bolt_server.py` | Integration only | Bolt network I/O |

These modules are excluded from unit coverage reporting in `pyproject.toml` under
`[tool.coverage.run].omit`. Low unit-only numbers for these files are **expected and correct**.

## Running coverage

```bash
# Unit coverage (no IRIS container needed)
coverage run -m pytest tests/unit/
coverage report --include="iris_vector_graph/**"

# Full coverage (requires ivg-iris container)
IVG_PORT=21972 coverage run -m pytest tests/
coverage report --include="iris_vector_graph/**"

# Enterprise container (for arno/BFS tests)
IVG_PORT=31972 IVG_TEST_CONTAINER=ivg-iris-enterprise coverage run -m pytest tests/
```

## CI baseline

GitHub Actions CI (`ci.yml`) runs unit tests only and enforces `--fail-under=70` on the
`iris_vector_graph/**` scope (after excluding IRIS-native modules). The coverage gate is
intentionally set at unit-testable code only.

## Known: Python 3.13 segfault with live IRIS

`intersystems_irispython 5.3.2` segfaults in its C extension when running IRIS E2E tests
sequentially under Python 3.13 (macOS, arm64). The crash occurs at
`iris_devtester/connections/cursor_wrapper.py:15` during `execute()` in a session that
has already run several IRIS SQL queries.

**Workaround**: run the full suite with Python 3.11, or run individual E2E test files
in isolation (`pytest tests/unit/test_cypher_case_when.py`).

CI runs on Python 3.11 via GitHub Actions and is not affected.
