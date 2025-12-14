<!--
================================================================================
SYNC IMPACT REPORT
================================================================================
Version Change: 0.0.0 → 1.0.0 (MAJOR: Initial constitution ratification)

Modified Principles:
- N/A (initial version - all principles are new)

Added Sections:
- Core Principles (8 principles extracted from CLAUDE.md)
- Development Standards
- Authorship and Attribution
- AI Development Constraints
- Governance

Removed Sections:
- N/A (initial version)

Templates Status:
- .specify/templates/plan-template.md - ✅ Compatible (Constitution Check section exists)
- .specify/templates/spec-template.md - ✅ Compatible (no constitutional references needed)
- .specify/templates/tasks-template.md - ✅ Compatible (testing requirements aligned)

Follow-up TODOs:
- None
================================================================================
-->

# IRIS Vector Graph Constitution

## Core Principles

### I. IRIS-Native Development

All features MUST leverage InterSystems IRIS capabilities directly rather than
working around them. This includes:

- Embedded Python for in-process execution (10-50x faster than client-side)
- SQL stored procedures for performance-critical operations
- ObjectScript REST API classes for HTTP endpoints
- Native VECTOR functions and HNSW indexing where available
- Direct `iris.connect()` for optimal database performance

**Rationale**: IRIS provides unique multi-model capabilities. Bypassing them
with external tools negates the architectural advantages and degrades performance.

### II. Test-First with Live Database (NON-NEGOTIABLE)

All tests involving data storage, vector operations, or graph operations MUST
use a live IRIS instance. No mocked databases for integration tests.

Test categories and requirements:
- `@pytest.mark.requires_database` - MUST connect to live IRIS
- `@pytest.mark.integration` - MUST use IRIS for data operations
- `@pytest.mark.e2e` - MUST use complete IRIS + vector workflow
- Unit tests MAY mock IRIS only for isolated component testing

Test discipline:
- TDD mandatory: Tests written → Tests fail → Then implement
- Red-Green-Refactor cycle strictly enforced
- All tests MUST pass before merge

**Rationale**: IRIS-specific behaviors (FK constraints, HNSW indexing, embedded
Python) cannot be accurately mocked. Live testing catches integration issues early.

### III. Performance as a Feature

Performance MUST be tracked, measured, and maintained as a first-class concern:

- HNSW indexing required for vector search (<10ms target)
- Graph queries MUST complete in <1ms with HNSW optimization
- Bounded queries required (max hops, confidence filtering)
- All performance claims MUST have reproducible benchmarks

Performance gates:
- Vector search: <10ms with HNSW index
- Node lookup: <1ms with PRIMARY KEY index
- Graph traversal: <0.1ms per hop

**Rationale**: Knowledge graph queries compound quickly. Unbounded or slow
queries make the system unusable for interactive applications.

### IV. Hybrid Search by Default

Multi-modal search MUST be the default approach, combining:

- Vector similarity search (semantic matching)
- Text/keyword search (lexical matching)
- Graph constraints (structural filtering)
- RRF (Reciprocal Rank Fusion) for result combination

Single-mode search is acceptable only when explicitly justified for specific
use cases.

**Rationale**: No single retrieval method captures all relevance signals.
RRF fusion (Cormack & Clarke SIGIR'09) provides robust result combination.

### V. Observability and Debuggability

Every layer MUST support debugging and operational visibility:

- Structured logging at database, API, and application layers
- Query execution plans available for performance debugging
- Error messages MUST be actionable (include context, suggest fixes)
- All API responses MUST include timing/metadata when requested

**Rationale**: Distributed graph + vector + text systems are complex. Without
observability, debugging becomes guesswork.

### VI. Modular Core Library

The `iris_vector_graph_core` module MUST remain:

- Database-agnostic in its interfaces
- Self-contained with clear boundaries
- Independently testable
- Suitable for integration with external RAG systems

Implementation-specific code (IRIS SQL syntax, ObjectScript classes) belongs
in adapter layers, not the core module.

**Rationale**: The core algorithms (RRF fusion, graph traversal patterns) are
reusable. Tight coupling to IRIS prevents use in other contexts.

### VII. Explicit Error Handling

No silent failures are permitted:

- All errors MUST propagate with meaningful context
- Database constraint violations MUST produce actionable messages
- API errors MUST include status codes and resolution guidance
- Logging MUST capture error chains for debugging

Anti-patterns prohibited:
- Empty catch blocks
- Generic "something went wrong" messages
- Swallowing exceptions without logging

**Rationale**: Silent failures lead to data corruption and debugging nightmares.
Explicit errors enable faster resolution.

### VIII. Standardized Database Interfaces

Database operations MUST use proven patterns:

- Use established utilities from `iris_vector_graph_core`
- New patterns MUST be documented and added to core if reusable
- Connection management through centralized configuration
- Schema changes MUST use versioned migrations

**Rationale**: Inconsistent database access leads to connection leaks, SQL
injection, and maintenance burden.

## Development Standards

### Package Management

- Use `uv` for all Python dependency management
- Dependencies declared in `pyproject.toml`
- Lock files committed to repository

### Code Quality

All code MUST pass before merge:
- `black` formatting
- `isort` import ordering
- `flake8` linting
- `mypy` type checking

### Documentation

- Comprehensive docstrings for all public APIs
- Architecture decisions documented in `docs/architecture/`
- Performance benchmarks documented in `docs/performance/`

### Versioning

- Semantic versioning for schema and API changes
- Breaking changes require migration scripts
- API deprecations announced one minor version before removal

### Docker Port Conventions

Standardized port ranges prevent conflicts:
- Default IRIS: `1972:1972` and `52773:52773`
- Licensed IRIS (ACORN-1): `21972:1972` and `252773:52773`
- Development instances: `11972:1972` and `152773:52773`

## Authorship and Attribution

**Project Owner**: Thomas Dyar <thomas.dyar@intersystems.com>

All code, documentation, and artifacts in this project are authored by and
attributed to the project owner. AI assistants (Claude Code, GitHub Copilot,
ChatGPT, or any other AI tools) MUST NOT be attributed as authors or co-authors
in any form, including:

- Git commit messages (no "Co-Authored-By: Claude" or similar)
- Code comments or documentation
- Pull request descriptions
- License headers or copyright notices
- README acknowledgments

AI tools are development aids, not contributors. Attribution belongs solely
to human authors.

## AI Development Constraints

When using AI assistance for development:

- Follow constraint-based architecture, not "vibecoding"
- Constitutional validation gates prevent repeating known bugs
- Every bug fix MUST be captured as new validation rule or enhanced guideline
- Work within established frameworks, patterns, and validation loops
- NEVER attribute AI assistants as authors or co-authors

**Constraint Philosophy**: Less freedom = less chaos. Constraints prevent
regression and maintain architectural integrity.

## Governance

### Amendment Process

1. Propose amendment with rationale in pull request
2. Document impact on existing code and templates
3. Require approval from project maintainers
4. Update version according to semantic versioning rules
5. Create migration plan if breaking changes

### Version Semantics

- MAJOR: Backward incompatible principle removals or redefinitions
- MINOR: New principle/section added or materially expanded guidance
- PATCH: Clarifications, wording, typo fixes, non-semantic refinements

### Compliance

- All PRs MUST verify compliance with these principles
- Complexity violations MUST be justified in PR description
- Constitution supersedes ad-hoc practices
- Use CLAUDE.md for runtime development guidance

**Version**: 1.0.0 | **Ratified**: 2025-12-14 | **Last Amended**: 2025-12-14
