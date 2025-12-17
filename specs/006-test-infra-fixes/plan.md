# Implementation Plan: Test Infrastructure Standardization

**Branch**: `006-test-infra-fixes` | **Date**: 2025-12-17 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/006-test-infra-fixes/spec.md`

## Summary

Standardize the test infrastructure to use `iris-devtester` for automatic IRIS container discovery, fix Python import conflicts caused by the `iris/` project directory, ensure all required database tables exist, and make the API health endpoint work correctly.

## Technical Context

**Language/Version**: Python 3.11
**Primary Dependencies**: pytest, iris-devtester, intersystems-irispython, FastAPI
**Storage**: InterSystems IRIS with Vector Search
**Testing**: pytest with markers (requires_database, integration, e2e)
**Target Platform**: Linux/macOS development, Docker containers
**Project Type**: Single project with API layer
**Performance Goals**: Test collection <5s, test suite completion <60s
**Constraints**: Must use iris-devtester per constitution, no `import iris` in test files
**Scale/Scope**: ~20 test files to update, 1 API file to fix

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Evidence |
|-----------|--------|----------|
| I. IRIS-Native Development | PASS | Using iris-devtester for connection management |
| II. Test-First with Live Database | PASS | All tests run against live IRIS |
| II. iris-devtester (NON-NEGOTIABLE) | PASS | Primary connection mechanism |
| III. Performance as a Feature | PASS | Test collection <5s target |
| VII. Explicit Error Handling | PASS | Graceful skip when no database |
| IX. Authorship | PASS | No AI attribution in commits |

## Project Structure

### Documentation (this feature)

```text
specs/006-test-infra-fixes/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # N/A (no new data models)
├── quickstart.md        # Testing quick reference
├── contracts/           # N/A (no new APIs)
└── tasks.md             # Phase 2 output (via /speckit.tasks)
```

### Source Code (repository root)

```text
tests/
├── conftest.py          # Shared fixtures (MODIFY: fix import iris fallback)
├── unit/                # Unit tests (may mock IRIS)
├── integration/         # Integration tests (MODIFY: remove local fixtures)
│   ├── gql/             # GraphQL tests
│   └── *.py             # Other integration tests
├── contract/            # Contract tests
├── e2e/                 # E2E tests (MODIFY: use shared fixtures)
└── python/              # Legacy Python tests (MODIFY: use shared fixtures)

api/
└── main.py              # FastAPI app (MODIFY: fix import iris)

sql/
└── schema.sql           # Database schema (includes kg_NodeEmbeddings)
```

**Structure Decision**: Single project structure. No new directories needed. Focus on modifying existing test infrastructure files.

## Complexity Tracking

> No violations requiring justification

## Files to Modify

### High Priority (P1 - Full Test Suite)

| File | Change | Reason |
|------|--------|--------|
| `tests/conftest.py` | Replace `import iris` fallback with `intersystems_irispython` | FR-002 |
| `api/main.py` | Replace `import iris` with `intersystems_irispython` | FR-002 |

### Medium Priority (P2 - Consistent Fixtures)

| File | Change | Reason |
|------|--------|--------|
| `tests/e2e/test_multi_query_engine_platform.py` | Remove local `iris_connection` fixture | FR-001, FR-003 |
| `tests/integration/test_nodepk_*.py` (6 files) | Remove local fixtures, use conftest | FR-001, FR-003 |
| `tests/integration/gql/test_graphql_*.py` (4 files) | Remove local fixtures, use conftest | FR-001, FR-003 |
| `tests/integration/test_pagerank_sql_optimization.py` | Remove local fixture, use conftest | FR-001, FR-003 |
| `tests/python/*.py` (8 files) | Replace `import iris` with proper import | FR-002 |

### Low Priority (P3 - Schema)

| File | Change | Reason |
|------|--------|--------|
| `sql/schema.sql` | Already has kg_NodeEmbeddings - verify nodes table | FR-004 |
| `scripts/setup_schema.py` | Ensure creates all tables | FR-004 |

## Key Technical Decisions

### Import Pattern for IRIS

**Problem**: Project has `iris/` directory containing ObjectScript sources. `import iris` resolves to this directory instead of the `intersystems-irispython` package.

**Solution**: Use explicit import path:
```python
# Instead of:
import iris

# Use:
import importlib
iris_module = importlib.import_module('intersystems_irispython.iris')
# Then: iris_module.connect(...)

# Or use iris-devtester which handles this:
from iris_devtester.utils.dbapi_compat import get_connection
```

### Fixture Hierarchy

**Pattern**: All test files use the shared fixture from `tests/conftest.py`:
```python
# In conftest.py (already exists, needs fix)
@pytest.fixture(scope="module")
def iris_connection():
    # Uses iris-devtester first, falls back to intersystems_irispython
    ...

# In test files (remove local definitions)
def test_something(iris_connection):  # Uses shared fixture
    cursor = iris_connection.cursor()
    ...
```

### Database Skip Pattern

**Pattern**: Tests skip gracefully when no database:
```python
@pytest.fixture(scope="module")
def iris_connection():
    try:
        # Try iris-devtester
        ...
    except Exception as e:
        pytest.skip(f"No IRIS database available: {e}")
```
