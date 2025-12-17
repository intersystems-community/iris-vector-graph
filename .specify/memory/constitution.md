# IRIS Vector Graph Constitution

## Core Principles

### I. IRIS-Native Development
Leverage IRIS capabilities directly. All data operations MUST use IRIS SQL, embedded Python, or ObjectScript. External Python is for API wrappers only. Performance-critical code MUST use embedded Python (10-50ms) not external Python (200ms+).

### II. Test-First with Live Database (NON-NEGOTIABLE)
TDD with running IRIS instance. No mocked database for integration tests. All tests involving data storage, vector operations, or graph operations MUST use live IRIS.

**Connection Management via iris-devtester (NON-NEGOTIABLE)**:
- Use `iris-devtester` package for automatic IRIS container discovery and connection management
- NEVER hardcode port numbers - iris-devtester auto-discovers running containers
- Connection fixture MUST use `from iris_devtester import get_connection` as primary path
- Fallback to .env configuration ONLY when iris-devtester fails
- This prevents port conflicts and enables reliable CI/CD testing

Test categories:
- `@pytest.mark.requires_database` - MUST connect to live IRIS
- `@pytest.mark.integration` - MUST use IRIS for data operations
- `@pytest.mark.e2e` - MUST use complete IRIS + vector workflow
- Unit tests MAY mock IRIS for isolated component testing

### III. Performance as a Feature
Performance targets MUST be defined and tracked. HNSW indexing for vectors, proper SQL indexes for graph traversal, bounded queries to prevent runaway costs.

Benchmarks:
- Vector search: <10ms (with HNSW)
- Graph queries: <1ms per hop
- PageRank 10K nodes: <15ms (with index), <300ms (fallback)

### IV. Hybrid Search by Default
Combine vector + text + graph using RRF fusion. Single-mode search is acceptable only when explicitly requested or when other modes don't apply.

### V. Observability and Debuggability
Structured logging required at each layer. Parameters MUST be logged for query debugging. Text I/O ensures debuggability.

### VI. Modular Core Library
The `iris_vector_graph` module MUST remain database-agnostic at its interface. IRIS-specific code isolated to implementation, not API contracts.

### VII. Explicit Error Handling
No silent failures. All errors MUST produce actionable error messages. Validation MUST reject invalid inputs with clear messages, not silently accept.

### VIII. Standardized Database Interfaces
Use proven utilities and patterns. Follow existing `kg_*` operator conventions. Contribute patterns back to shared infrastructure.

## Testing Requirements

Performance tests MUST verify:
- Vector search <10ms
- Graph queries <1ms (with HNSW)
- New features meet defined targets before merge

## Development Standards

- **Package Management**: Use `uv` for all Python dependency management
- **Code Quality**: Pass black, isort, flake8, mypy before commits
- **Documentation**: Comprehensive docstrings for all public APIs
- **Versioning**: Follow semantic versioning for schema/API changes

## AI Development Constraints

- Follow constraint-based architecture, not "vibecoding"
- Constitutional validation gates prevent repeating known bugs
- Every bug fix MUST be captured as new validation rule or enhanced guideline
- Work within established frameworks, patterns, and validation loops
- **Constraint Philosophy**: Less freedom = less chaos. Constraints prevent regression.

## Authorship and Attribution (NON-NEGOTIABLE)

All code, documentation, and artifacts in this repository are authored by and attributed exclusively to **Thomas Dyar** (thomas.dyar@intersystems.com).

**Prohibited Attribution**:
- NEVER credit AI assistants (Claude Code, GitHub Copilot, Cursor, or similar) as authors or co-authors
- NEVER include "Generated with Claude Code" or similar AI attribution in commits
- NEVER add AI co-author lines (e.g., `Co-Authored-By: Claude`) to commit messages
- NEVER mention AI assistance in pull request descriptions or documentation

**Required Attribution**:
- All commits MUST be attributed to Thomas Dyar
- All pull requests MUST list Thomas Dyar as sole author
- Copyright notices MUST reference Thomas Dyar / InterSystems

This policy applies regardless of how the code was developed or what tools were used in the development process.

## Governance

- Constitution supersedes all other development practices
- Amendments require documentation, approval, and migration plan
- All PRs/reviews must verify compliance
- Complexity must be justified against these principles

**Version**: 1.1.0 | **Ratified**: 2025-12-15 | **Last Amended**: 2025-12-17
