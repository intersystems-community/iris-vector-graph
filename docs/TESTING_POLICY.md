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

| Fixture               | Scope    | Required For                                |
| --------------------- | -------- | ------------------------------------------- |
| `iris_connection`     | Module   | Any test requiring a database connection    |
| `iris_cursor`         | Function | Any test executing SQL statements           |
| `iris_test_container` | Session  | Managed container lifecycle (auto-injected) |
| `clean_test_data`     | Function | Tests that create data requiring cleanup    |

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

| If Test Uses...       | MUST Have Marker                 |
| --------------------- | -------------------------------- |
| `iris_connection`     | `@pytest.mark.requires_database` |
| `iris_cursor`         | `@pytest.mark.requires_database` |
| `iris_test_container` | `@pytest.mark.requires_database` |

### Marker Definitions

| Marker                           | Meaning                 | IRIS Required          |
| -------------------------------- | ----------------------- | ---------------------- |
| `@pytest.mark.requires_database` | Test requires live IRIS | **YES**                |
| `@pytest.mark.integration`       | Integration test        | **YES**                |
| `@pytest.mark.e2e`               | End-to-end test         | **YES**                |
| `@pytest.mark.performance`       | Performance benchmark   | **YES**                |
| (no marker)                      | Unit test               | May mock for isolation |

---

## Runtime Environment

### Supported Runtime: `iris-devtester` Containers ONLY

The **only supported runtime** for database tests is a dedicated container managed by `iris-devtester`:

```python
# From tests/conftest.py: attach to this project's container by name
from iris_devtester import IRISContainer

name = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")
container = IRISContainer.attach(name)
```

The user path runs on a stock container in `scripts/quickstart_e2e.py`: the
repo's `docker-compose.yml`, the built wheel in a clean venv, the README and
`docs/setup/QUICKSTART.md` code as published, `examples/demo_*.py`, and an
upgrade from the last PyPI release. CI runs it on every push and weekly with a
fresh image pull. Locally: `python scripts/quickstart_e2e.py --isolated`.

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

## Coverage That Counts

4.1.0 shipped five bugs (DEBT entry 10) that the suite had every chance to
catch. Each rule below closes the gap one of them went through. Line coverage
was above 89% the whole time: coverage says a line ran, not that the test would
notice it doing the wrong thing.

### CI must see what the tests see

- **Main's CI is green before anything merges to it.** Main was red for eight
  pushes; a red run that everyone ignores tests nothing. Check with `gh run
list --branch main --limit 3` before a merge.
- **Run `scripts/ci-parity.sh` before pushing.** It copies exactly what a
  commit would carry into a scratch directory, builds fresh venvs from
  `pyproject.toml` on 3.12 and 3.13, runs `tests/unit` with the live container
  hidden, then builds the wheel, installs it into a clean venv and imports it
  with `-W error`.
- **No test reads a gitignored file.** `specs/` is ignored; spec files a test
  reads are force-added (`git add -f`).
  `tests/unit/test_ci_sees_what_tests_read.py` fails locally on any file
  present but ignored.
- **An optional package a test needs goes in the `dev` extra.** A bare
  `importorskip` turns a missing dependency into a silent skip in CI
  (jsonschema did that to the ledger schema check). CI prints `-rs`; read the
  skips.
- **`SKIP_IRIS_TESTS=true` does not hide a running container.** conftest skips
  IRIS fixtures only when the container is absent, so a local "unit" run with
  `ivg-iris-enterprise` up runs live tests CI never does. ci-parity hides the
  Docker daemon (`DOCKER_HOST` at a socket that does not exist), as CI's runner
  has none.
- **The local `.venv` is not a clean install.** Three packages write
  `iris/__init__.py` (intersystems-irispython, iris-embedded-python-wrapper,
  sqlalchemy-iris, which iris-devtester pulled in before 1.20) and the last
  installed wins. The `.venv` had the driver's copy; a uv-built venv got
  sqlalchemy-iris 0.18.1's, whose driver loader looks for a file the driver
  renamed in 5.2.0, so `iris.createIRIS` and `iris.IRISConnection` became the
  wrapper's placeholder MagicMocks. pip resolved a newer sqlalchemy-iris, so CI
  never saw it: a venv from a different resolver is a different install. Connections
  "succeeded", `import iris.dbapi` failed on 3.12+, and tests that fed a
  MagicMock connection into code expecting `createIRIS` to raise took other
  branches. `iris_vector_graph/_iris_compat.py` repairs the namespace at
  import; `tests/unit/test_iris_dbapi_import.py` checks it in a fresh
  interpreter.
- **A mock connection pins the branch it tests.** If the code under test has
  a fallback, patch the step before it to fail or return nothing; do not rely
  on a MagicMock making the driver raise. Patch what the code calls
  (`eng._iris_obj`, `iris.dbapi` as an attribute), not a helper it does not.
- **Never `--disable-warnings`, never a blanket `filterwarnings = ignore`.**
  They hid the `SyntaxWarning` in `schema.py`. `SyntaxWarning` and IVG's own
  `DeprecationWarning` are errors (`pyproject.toml` `filterwarnings`); other
  warnings print in the summary, and a new filter targets one source and says
  which in a comment.
- **Anything that compares source text is normalized per interpreter.** 3.12's
  f-string grammar (PEP 701) changed `ast.unparse` output, and an exact-text
  allowlist failed on 3.12 only. Normalize both sides with
  `ast.unparse(ast.parse(x))`. The CI matrix is `fail-fast: false` so a
  one-version failure is not cancelled away.

### Test what ships, where it runs

- **Test the installed wheel, not the checkout.** The wheel shipped no
  ObjectScript classes because every test ran from the source tree, where
  `iris_src/` exists. CI's `wheel` job and ci-parity install the wheel and
  assert the classes are in site-packages.
- **Deploy into a fresh namespace at least once per release**, and on a
  container without embedded Python or `%AI.*` where the change touches class
  deployment. The enterprise image has both, which hid a compile set that
  fails without them.
- **Legacy installs come from frozen DDL, not hand-written tables.** Every
  release in `tests/e2e/fixtures/old_releases.py` freezes its own
  `initialize_schema` statements (`generate_old_snapshot.py --ddl`). The 2.x
  fixtures once had none, so no test could build a 2.x schema and the dry run
  crashed on the first real one. `tests/e2e/test_upgrade_dry_run_2x.py` is the
  model: replay the DDL, rows and globals into `IVGLEGACY`, run the operation,
  check the result.

### Assert the state, not the call

- **Integration tests assert what the database holds afterwards,** not the SQL
  that was sent. `delete_node` issued the right `DELETE`s, and a named-graph
  node's vectors stayed in `kg_emb_<hash>` because none of them targeted that
  table.
- **A dry run is proven read-only by a before/after snapshot** of columns,
  constraints, indexes, routines, compiled classes, row counts and globals.
  Every step of a multi-step operation gets a whole dry run, not a per-helper
  one.
- **Cover the cross product the feature introduces.** Named graphs x vectors x
  delete was never exercised together. When a spec adds a dimension (graph
  scope, a new store), list the existing write/delete paths and add a case per
  path.
- **No `except Exception: return False` on a path that changes data.** It
  turned a failed delete into a quiet `False`. Let it raise, or catch the
  specific error and say what was not done.
- **Every message that names a call which writes says it writes.** An operator
  running a dry run reads the hint as safe.
  `test_upgrade_dry_run_read_only.py` scans the migrations' `RuntimeError`
  messages for this.

### Environment

- Enterprise container only (`ivg-iris-enterprise`, 31972). Scratch
  namespaces: `IVGTEST`, `IVGREKEY`, `IVGSEC`
  (`IVG_SECONDARY_NAMESPACE=IVGSEC`), `IVGLEGACY`, `IVGFIX`, `IVGFIX20`.
- Timing assertions do not run under load. A full-suite run alongside other
  work is not a benchmark; rerun a timing failure alone before believing it.

---

## Rationale

### Why No Mocking?

1. **IRIS-Specific Behavior**: IRIS SQL has unique behaviors (`VECTOR` column width enforcement, `%ID` columns, stored procedures) that mocks cannot replicate
2. **Vector Operations**: Embedding similarity calculations require a real `VECTOR_COSINE` scan over declared-width columns
3. **Graph Queries**: Multi-hop traversals and RRF fusion depend on actual data distribution
4. **Regression Prevention**: Mocked tests pass while production fails—we've learned this the hard way

### Why Dedicated Containers?

1. **Isolation**: Each test session gets a clean database state
2. **Reproducibility**: CI/CD produces identical results to local development
3. **Port Safety**: Dynamic port allocation prevents conflicts
4. **Password Handling**: fixtures log in as `_SYSTEM`; iris-devtester 1.20 no longer creates a `test`/`test` user

---

**Author**: Thomas Dyar (<thomas.dyar@intersystems.com>)

---

## Unified Test Runner

The project's standard test command is `pytest`. The `run-tests` entry point described below is a project-specific CLI wrapper that may or may not be present depending on how the package was installed.

> **Current recommended commands:**
>
> ```bash
> pytest tests/unit/ -q                  # unit tests (no IRIS required)
> pytest tests/e2e/ -q                   # e2e tests (requires IRIS container)
> pytest tests/ -q -m "not e2e"          # all non-e2e tests
> ```

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

| Category      | Markers                                         | Demo Server | Database |
| ------------- | ----------------------------------------------- | ----------- | -------- |
| `unit`        | `not (requires_database or e2e or integration)` | No          | No       |
| `integration` | `integration` or `requires_database`            | No          | Required |
| `e2e`         | `e2e`                                           | Optional    | Required |
| `ux`          | `e2e` + `*_ui.py`                               | **Auto**    | Required |
| `contract`    | `tests/contract/`                               | No          | Required |

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
