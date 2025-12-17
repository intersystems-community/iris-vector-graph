# Feature Specification: Test Infrastructure Standardization

**Feature Branch**: `006-test-infra-fixes`
**Created**: 2025-12-17
**Status**: Draft
**Input**: User description: "Standardize test infrastructure to use iris-devtester for automatic IRIS container discovery, fix conflicting import statements, create missing database tables, and ensure API health endpoints work correctly"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Run Full Test Suite Successfully (Priority: P1)

As a developer, I want to run the complete test suite (unit, integration, contract, e2e) with a single command and have all tests pass against any running IRIS container, so I can validate changes before committing.

**Why this priority**: This is the core value proposition. If tests cannot run reliably, CI/CD pipelines fail and development velocity suffers. The constitution mandates test-first development with live database.

**Independent Test**: Can be fully tested by running `pytest tests/` and verifying all tests pass with proper IRIS connection auto-discovery.

**Acceptance Scenarios**:

1. **Given** an IRIS container is running on any port, **When** I run `pytest tests/`, **Then** all tests should auto-discover the container and connect successfully
2. **Given** no IRIS container is running, **When** I run tests marked `@pytest.mark.requires_database`, **Then** they should be skipped with a clear message, not fail with connection errors
3. **Given** multiple IRIS containers are running, **When** I run tests, **Then** the system should connect to the first available container or use `.env` configuration as override

---

### User Story 2 - E2E Tests Validate Multi-Query Engine Platform (Priority: P2)

As a QA engineer, I want end-to-end tests that validate the complete multi-query-engine platform (GraphQL, openCypher, SQL) works correctly, so I can ensure cross-engine data consistency.

**Why this priority**: E2E tests validate the full system integration. Without working e2e tests, we cannot verify the platform operates correctly in production-like scenarios.

**Independent Test**: Can be tested by running `pytest tests/e2e/` and verifying all three query engines return consistent results for the same data.

**Acceptance Scenarios**:

1. **Given** test data exists in the database, **When** I query via SQL, GraphQL, and openCypher, **Then** all three engines return consistent results
2. **Given** the API server is running, **When** I check the health endpoint, **Then** it reports all engines as available and database as connected
3. **Given** test data is created via one engine, **When** I query via another engine, **Then** the data is visible (cross-engine consistency)

---

### User Story 3 - Consistent Test Fixtures Across All Test Files (Priority: P2)

As a developer, I want all test files to use the same connection management pattern (iris-devtester), so I don't encounter import conflicts or inconsistent database connections.

**Why this priority**: The current codebase has test files with local fixtures that shadow the shared `conftest.py`, causing import conflicts with the `iris/` project directory.

**Independent Test**: Can be tested by verifying no test file imports the `iris` module directly (which conflicts with the `iris/` directory).

**Acceptance Scenarios**:

1. **Given** a test file exists, **When** it needs database access, **Then** it uses the shared fixture from `conftest.py`
2. **Given** the project has an `iris/` directory (ObjectScript sources), **When** tests import database connectivity, **Then** they use `intersystems_irispython` or `iris_devtester`, not `import iris`
3. **Given** a new test is created, **When** I follow the testing pattern, **Then** I can copy the fixture pattern from `conftest.py`

---

### User Story 4 - Database Schema Ready for All Tests (Priority: P3)

As a test runner, I want all required database tables to exist before tests run, so tests don't fail due to missing schema.

**Why this priority**: Some tests (particularly e2e) require tables like `kg_NodeEmbeddings` that may not exist in a fresh database.

**Independent Test**: Can be tested by running schema setup and verifying all required tables exist.

**Acceptance Scenarios**:

1. **Given** a fresh IRIS database, **When** I run schema setup, **Then** all tables required by tests are created (nodes, rdf_edges, rdf_labels, rdf_props, kg_NodeEmbeddings)
2. **Given** tables already exist, **When** I run schema setup, **Then** existing tables are preserved (idempotent operation)
3. **Given** the `kg_NodeEmbeddings` table exists, **When** e2e tests insert embeddings, **Then** the inserts succeed

---

### Edge Cases

- What happens when IRIS container port changes between test runs?
  - iris-devtester auto-discovers the new port
- How does system handle IRIS container restart during test execution?
  - Tests should fail gracefully with connection error, not hang
- What happens if `.env` specifies a port but container is on different port?
  - `.env` takes precedence if set, otherwise auto-discovery

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: All test fixtures MUST use `iris-devtester` for IRIS connection management per constitution
- **FR-002**: Test files MUST NOT use `import iris` directly due to conflict with `iris/` project directory
- **FR-003**: The shared `conftest.py` MUST provide database connection fixtures usable by all test types
- **FR-004**: Schema setup MUST create the `kg_NodeEmbeddings` table with vector column for e2e tests
- **FR-005**: API health endpoint MUST return accurate status for all query engines (GraphQL, openCypher, SQL)
- **FR-006**: Tests marked `@pytest.mark.requires_database` MUST skip gracefully when no database is available
- **FR-007**: E2E tests MUST clean up test data after execution to prevent cross-test contamination
- **FR-008**: All test fixtures MUST close database connections properly to prevent resource leaks

### Key Entities

- **Test Fixture**: Reusable test setup/teardown component that provides database connections and test data
- **IRIS Container**: Docker container running InterSystems IRIS with vector search capabilities
- **Schema Tables**: Database tables required for tests (nodes, rdf_edges, rdf_labels, rdf_props, kg_NodeEmbeddings)
- **Query Engine**: API interface for data access (GraphQL at /graphql, openCypher at /api/cypher, SQL via iris.connect)

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of test files use shared fixtures from `conftest.py` instead of local connection management
- **SC-002**: Full test suite (`pytest tests/`) passes with zero connection-related failures when IRIS container is running
- **SC-003**: E2E tests complete successfully and validate all three query engines (GraphQL, openCypher, SQL)
- **SC-004**: Health endpoint returns "healthy" status when all components are operational
- **SC-005**: Test discovery time (pytest collection) completes in under 5 seconds
- **SC-006**: No test file contains `import iris` (verified by grep)

## Assumptions

- IRIS Community Edition or higher is available for testing
- Docker is installed and can run IRIS containers
- The `iris-devtester` package is installed as a development dependency
- The existing schema.sql file defines the base tables correctly
- The API server (FastAPI) is used for GraphQL and openCypher endpoints

## Out of Scope

- Performance optimization of tests (focus is on correctness)
- Adding new test coverage (focus is on fixing existing infrastructure)
- Changes to the core application code (only test infrastructure)
- CI/CD pipeline configuration (focus is on local development)
