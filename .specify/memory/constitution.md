<!--
Sync Impact Report
==================
Version: 1.2.0 → 1.3.0
Modified Principles: Development Standards - Enhanced Package Management section
Added Sections:
  * Package Management (NON-NEGOTIABLE) - Comprehensive rules for multi-environment Python development
  * Multi-Environment Awareness - Explicit handling of multiple Python environments and Docker containers
  * PyPI Package Cache Management (CRITICAL) - 4-step aggressive cache-clearing protocol
Removed Sections: N/A
Templates Status:
  ✅ plan-template.md - No changes needed
  ✅ spec-template.md - No changes needed
  ✅ tasks-template.md - No changes needed
Follow-up TODOs: None
Amendment Rationale: Package caching issues and multi-environment confusion are recurring pain points causing significant development time waste and false bug reports. This amendment codifies aggressive cache management, explicit environment targeting (uv run, iris-devtester), and fresh-process verification as NON-NEGOTIABLE requirements. Addresses perennial issues with pip cache, Python import cache, and ambiguous container/environment selection.

Previous Amendments:
==================
Version: 1.1.0 → 1.2.0
Modified Principles: N/A
Added Sections:
  * Pre-Release Checklist (NON-NEGOTIABLE) - 7-step comprehensive checklist covering documentation organization, root directory hygiene, source code review, README accuracy, CHANGELOG updates, version consistency, and test validation
Removed Sections: N/A
Templates Status:
  ✅ plan-template.md - No changes needed
  ✅ spec-template.md - No changes needed
  ✅ tasks-template.md - No changes needed
Follow-up TODOs: None
Amendment Rationale: Added mandatory pre-release checklist to prevent repository drift, maintain professional presentation, and ensure consistent quality standards across releases. Checklist codifies best practices for documentation organization, cleanup, and version management.

Version: 1.0.0 → 1.1.0
Modified Principles:
  * Test-First Development - Enhanced with live IRIS database validation requirements
  * Added VII. Explicit Error Handling (NON-NEGOTIABLE)
  * Added VIII. Standardized Database Interfaces
Added Sections:
  * IRIS Docker Management Procedures (within Test-First Development)
  * Development Standards (package management with uv)
  * AI Architecting Principles (within Governance)
Amendment Rationale: Merged learnings from rag-templates project including live IRIS testing requirements, explicit error handling, standardized database interfaces, and AI development constraints
-->

# IRIS Vector Graph Constitution

## Core Principles

### I. IRIS-Native Development
All features MUST leverage IRIS database capabilities directly. Python code integrates via embedded Python (`iris.cls`). SQL procedures implement performance-critical operations. REST APIs use ObjectScript classes. External dependencies are minimized except for domain-specific libraries (embeddings, NetworkX).

**Rationale**: IRIS provides multi-model capabilities (SQL, objects, documents, vectors) in a single database. Using IRIS-native features eliminates architectural complexity, reduces latency, and ensures optimal performance for graph and vector operations.

### II. Test-First Development with Live Database Validation (NON-NEGOTIABLE)
Tests MUST be written before implementation. All contract tests and integration tests MUST fail before implementation begins. Red-Green-Refactor cycle is strictly enforced. Performance tests define acceptance criteria (<10ms for vector search with HNSW, <1ms for graph queries).

**IRIS Database Requirement**: All tests that involve data storage, vector operations, schema management, or graph operations MUST execute against a running IRIS database instance. Tests MUST use either:
1. **Framework-managed Docker IRIS** (preferred): Use available licensed IRIS images with dynamic port allocation
2. **External IRIS instance**: When configured via environment variables

**IRIS Docker Management Procedures**:
- **Required Package**: ALL IRIS-based projects MUST use `iris-devtester` PyPI package for container state management and IRIS testing assistance. This package provides standardized utilities for:
  * IRIS container lifecycle management (start, stop, health checks)
  * Database connection pooling and testing
  * Schema migration and validation
  * Performance benchmarking utilities
- **Required IRIS Image**: ALL Docker Compose files SHOULD use `docker.iscinternal.com/intersystems/iris:2025.3.0EHAT.127.0-linux-arm64v8` or similar licensed images when available (note: NOT iris-lockeddown)
- **Standardized Port Mapping**: ALL Docker Compose files MUST follow consistent port mapping:
  * **Container ports**: Always use IRIS standard ports (1972 SuperServer, 52773 Management Portal)
  * **Host port ranges**:
    - Default IRIS: `1972:1972` and `52773:52773` (docker-compose.yml)
    - Licensed IRIS: `21972:1972` and `252773:52773` (docker-compose.acorn.yml)
    - Development: `11972:1972` and `152773:52773` (if needed for multiple instances)
  * **Rationale**: Predictable ports avoid conflicts, enable easy configuration, support multiple IRIS instances
- **Health Validation**: Always verify IRIS connectivity before database-dependent testing using `iris-devtester` health check utilities

**Test Categories with IRIS Requirements**:
- `@pytest.mark.requires_database`: MUST connect to live IRIS
- `@pytest.mark.integration`: MUST use IRIS for data operations
- `@pytest.mark.e2e`: MUST use complete IRIS + vector workflow
- Unit tests MAY mock IRIS for isolated component testing

**Database Health Validation**: All test suites MUST verify IRIS health before execution.

**Rationale**: Graph and vector systems are fundamentally dependent on IRIS database for storage, embeddings, and search operations. Testing without live database connections provides false validation and cannot detect real-world integration failures, performance issues, or data consistency problems.

### III. Performance as a Feature
Vector search MUST use HNSW indexing where available (ACORN-1, IRIS 2025.3+). Graph queries MUST be bounded (max hops, confidence thresholds). Performance benchmarks MUST be tracked in `docs/performance/`. Degradation from baseline triggers investigation.

**Rationale**: The primary value of IRIS Vector Graph is performance—combining vector similarity, graph traversal, and text search in milliseconds. Performance is not optional; it defines the system's utility for biomedical research and real-time applications.

### IV. Hybrid Search by Default
Search features MUST combine vector similarity, text search (BM25), and graph constraints using Reciprocal Rank Fusion (RRF). Single-mode searches are acceptable only when requirements explicitly exclude other modalities.

**Rationale**: Biomedical research requires semantic understanding (vectors), keyword precision (text), and relationship context (graph). RRF fusion (Cormack & Clarke SIGIR'09) provides better results than any single method.

### V. Observability & Debuggability
SQL queries MUST log execution time. Python functions MUST log input/output for graph operations. REST endpoints MUST return structured errors with trace IDs. Performance scripts MUST output results to `docs/performance/` with timestamps.

**Rationale**: Multi-layer architecture (REST → SQL → Python → IRIS storage) requires visibility at each layer. Debugging performance issues or data inconsistencies demands structured logging and reproducible benchmarks.

### VI. Modular Core Library
The `iris_vector_graph_core` module MUST remain independent of IRIS-specific code. Core graph operations (fusion, traversal) MUST work with any database backend providing the required data. IRIS integration lives in separate layers (SQL procedures, ObjectScript classes).

**Rationale**: Modular design enables integration with other RAG systems, testing without IRIS, and reuse across projects. Separation of concerns improves maintainability and allows performance optimization at each layer.

### VII. Explicit Error Handling (NON-NEGOTIABLE)
Domain errors only: no silent failures, every bug MUST be explicit. All error conditions MUST surface as clear exceptions with actionable messages. No swallowed exceptions or undefined behavior. Failed operations MUST provide specific context about what failed and why.

**Rationale**: Graph and vector systems process critical knowledge; silent failures can lead to incorrect or missing information being returned to users, which is unacceptable in research and enterprise environments.

### VIII. Standardized Database Interfaces
All database interactions MUST use proven, standardized utilities from the framework's SQL and vector helper modules. No ad-hoc database queries or direct IRIS API calls outside established patterns. New database patterns MUST be contributed back to shared utilities after validation.

**Rationale**: IRIS database interactions have complex edge cases and performance considerations. Hard-won fixes and optimizations must be systematized to prevent teams from rediscovering the same issues.

## Additional Constraints

### Versioning & Breaking Changes
- **MAJOR**: Schema changes breaking existing SQL procedures or REST endpoints
- **MINOR**: New operators, endpoints, or vector dimensions added
- **PATCH**: Bug fixes, documentation, performance optimizations without API changes

IRIS schema changes MUST include migration scripts in `sql/migrations/`. Breaking changes require deprecation notices for at least one minor version.

### Security Requirements
- Database credentials MUST use environment variables (`.env` file, never committed)
- REST endpoints MUST validate input (SQL injection prevention, vector dimension checks)
- Embedded Python MUST not execute arbitrary user code (sanitize inputs to SQL procedures)

### Documentation Standards
- All SQL procedures MUST document parameters, return types, and performance characteristics
- Performance benchmarks MUST include system specs (IRIS version, ACORN-1 vs Community, dataset size)
- README MUST provide working examples for common use cases (vector search, graph traversal, hybrid search)

## Development Standards

### Package Management (NON-NEGOTIABLE)

**uv-Only Workflow**: All Python projects MUST use `uv` for dependency management, virtual environment creation, and package installation. Traditional pip/virtualenv workflows are deprecated in favor of uv's superior performance and reliability.

**Multi-Environment Awareness**: This development machine hosts MULTIPLE Python environments (conda base, project .venv directories, system Python) and MULTIPLE IRIS Docker containers (default, ACORN-1, test instances). ALL Python and Docker operations MUST be explicit about target environment/container:
- **Python**: ALWAYS use `uv run python` or explicit virtual environment activation
- **Docker**: ALWAYS use `iris-devtester` with NAMED containers (no ambiguous container references)
- **Verification**: NEVER assume which environment is active - always verify with `uv run python -c "import sys; print(sys.executable)"`

**PyPI Package Cache Management (CRITICAL)**: When updating PyPI packages (iris-vector-graph, iris-vector-rag, etc.), MUST follow aggressive cache-clearing protocol:
1. **Force uninstall**: `uv pip uninstall <package-name>` (uv has no -y flag)
2. **No-cache install**: `uv pip install --no-cache <package-name>==<version>`
3. **Fresh process verification**: `uv run python -c "import <module>; print(<module>.__version__)"` (NOT reusing existing Python processes)
4. **Import cache**: Python caches imports - verification MUST use fresh `uv run python` process, NOT interactive sessions

**Rationale**: Package caching issues are a recurring pain point. Multiple Python environments cause version confusion. pip cache can serve stale packages even after "upgrade". Python's import cache persists old versions in running processes. These issues waste development time and create false bug reports. Aggressive cache management is NON-NEGOTIABLE.

**Code Quality**: Code MUST pass linting (black, isort, flake8, mypy) before commits. All public APIs MUST include comprehensive docstrings. Breaking changes MUST follow semantic versioning. Dependencies MUST be pinned and regularly updated for security.

**Documentation**: Documentation MUST include quickstart guides, API references, and integration examples. Agent-specific guidance files (CLAUDE.md) MUST be maintained for AI development assistance.

## Development Workflow

### Setup Validation
New contributors MUST run:
1. `uv sync` - Install Python dependencies using uv
2. `docker-compose up -d` - Start IRIS (ACORN-1 or Community)
3. `\i sql/schema.sql` - Load schema
4. `\i sql/operators.sql` - Load procedures (or `operators_fixed.sql` for older IRIS)
5. `uv run python tests/python/run_all_tests.py --quick` - Verify setup

### Code Review Gates
- [ ] Tests written first and failing (for new features)
- [ ] Performance benchmarks included (for performance-sensitive code)
- [ ] IRIS-native approach used (no unnecessary external dependencies)
- [ ] Logging added for debugging (SQL execution time, Python inputs/outputs)
- [ ] Documentation updated (README examples, API reference, performance docs)
- [ ] Error handling explicit with actionable messages
- [ ] Database interactions use standardized utilities

### Testing Requirements
- **Contract Tests**: Validate REST API request/response schemas
- **Integration Tests**: Verify SQL procedures + Python + IRIS storage (live database)
- **Performance Tests**: Ensure vector search <10ms, graph queries <1ms (with HNSW)
- **Benchmark Scripts**: Track performance over time in `docs/performance/`

### Pre-Release Checklist (NON-NEGOTIABLE)
Before any version bump, PyPI publish, or release tag, the following MUST be completed:

1. **Documentation Organization**:
   - [ ] Move temporary/investigation files to `docs/archive/` or feature-specific subdirectories
   - [ ] Move loose scripts to `scripts/archive/` if not production-ready
   - [ ] Organize related documentation into subdirectories (e.g., `docs/ppr-optimization/`)
   - [ ] Ensure root directory contains only production files

2. **Root Directory Hygiene**:
   - [ ] Remove temporary files (`.sesskey`, `*.log`, test JSON outputs)
   - [ ] Archive or delete one-off Python scripts not in `scripts/`
   - [ ] Verify `.gitignore` covers all temporary/generated files
   - [ ] Clean `.DS_Store` and OS-specific artifacts

3. **Source Code Review**:
   - [ ] Remove commented-out code blocks
   - [ ] Remove debug print statements
   - [ ] Verify all public functions have docstrings
   - [ ] Check for hardcoded credentials or sensitive data
   - [ ] Ensure consistent code formatting (black, isort)

4. **README Accuracy**:
   - [ ] Update version badge/number
   - [ ] Verify installation instructions are current
   - [ ] Update performance benchmarks with latest results
   - [ ] Add new features to feature list
   - [ ] Check all command examples work as documented

5. **CHANGELOG Update**:
   - [ ] Document all new features
   - [ ] Document all bug fixes
   - [ ] Document all breaking changes
   - [ ] Document all performance improvements
   - [ ] Follow Keep a Changelog format

6. **Version Consistency**:
   - [ ] Bump version in `pyproject.toml`
   - [ ] Ensure version follows semantic versioning
   - [ ] Update CHANGELOG version header
   - [ ] Create git tag matching version

7. **Test Validation**:
   - [ ] Run full test suite and verify all passing
   - [ ] Run performance benchmarks and update docs
   - [ ] Verify Docker Compose configurations work
   - [ ] Test PyPI package installation in clean environment

**Rationale**: Maintaining high standards for releases prevents technical debt accumulation, ensures professional presentation, and makes the project accessible to new users and contributors. A clean repository reflects software quality and engineering discipline.

## Governance

### Constitution Authority
This constitution supersedes informal practices and ad-hoc decisions. All design choices MUST align with core principles or document justification in `Complexity Tracking` section of implementation plans.

### AI Architecting Principles
Development with AI tools MUST follow constraint-based architecture, not "vibecoding". Constitutional validation gates serve as constraint checklists that prevent repeating known bugs and design mistakes. Every bug fix MUST be captured as a new validation rule or enhanced guideline. AI development MUST work within established frameworks, patterns, and validation loops.

**Constraint Philosophy**: Less freedom = less chaos. Constraints are superpowers that prevent regression and ensure consistency.

### Amendment Process
1. Propose change with rationale (GitHub issue or pull request)
2. Increment version according to semantic versioning rules
3. Update all dependent templates (plan-template.md, spec-template.md, tasks-template.md)
4. Document change in Sync Impact Report (prepended as HTML comment)

### Compliance Review
Each implementation plan MUST include a "Constitution Check" section evaluating alignment with principles. Violations require explicit justification in "Complexity Tracking" table.

**Version**: 1.3.0 | **Ratified**: 2025-09-30 | **Last Amended**: 2025-11-09
