<!--
Sync Impact Report
==================
Version: 0.1.0 (Initial ratification)
Modified Principles: N/A (initial version)
Added Sections: All (initial creation)
Removed Sections: N/A
Templates Status:
  ✅ plan-template.md - Reviewed, compatible with IRIS-Native Development and Test-First principles
  ✅ spec-template.md - Reviewed, compatible with requirements-driven approach
  ✅ tasks-template.md - Reviewed, aligned with TDD and IRIS-native workflow
Follow-up TODOs: None
-->

# IRIS Vector Graph Constitution

## Core Principles

### I. IRIS-Native Development
All features MUST leverage IRIS database capabilities directly. Python code integrates via embedded Python (`iris.cls`). SQL procedures implement performance-critical operations. REST APIs use ObjectScript classes. External dependencies are minimized except for domain-specific libraries (embeddings, NetworkX).

**Rationale**: IRIS provides multi-model capabilities (SQL, objects, documents, vectors) in a single database. Using IRIS-native features eliminates architectural complexity, reduces latency, and ensures optimal performance for graph and vector operations.

### II. Test-First Development (NON-NEGOTIABLE)
Tests MUST be written before implementation. All contract tests and integration tests MUST fail before implementation begins. Red-Green-Refactor cycle is strictly enforced. Performance tests define acceptance criteria (<10ms for vector search with HNSW, <1ms for graph queries).

**Rationale**: TDD ensures requirements are testable and implementation is verifiable. In a multi-model database system with embedded Python and SQL procedures, tests prevent integration issues and performance regressions.

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

## Development Workflow

### Setup Validation
New contributors MUST run:
1. `uv sync` - Install Python dependencies
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

### Testing Requirements
- **Contract Tests**: Validate REST API request/response schemas
- **Integration Tests**: Verify SQL procedures + Python + IRIS storage
- **Performance Tests**: Ensure vector search <10ms, graph queries <1ms (with HNSW)
- **Benchmark Scripts**: Track performance over time in `docs/performance/`

## Governance

### Constitution Authority
This constitution supersedes informal practices and ad-hoc decisions. All design choices MUST align with core principles or document justification in `Complexity Tracking` section of implementation plans.

### Amendment Process
1. Propose change with rationale (GitHub issue or pull request)
2. Increment version according to semantic versioning rules
3. Update all dependent templates (plan-template.md, spec-template.md, tasks-template.md)
4. Document change in Sync Impact Report (prepended as HTML comment)

### Compliance Review
Each implementation plan MUST include a "Constitution Check" section evaluating alignment with principles. Violations require explicit justification in "Complexity Tracking" table.

**Version**: 1.0.0 | **Ratified**: 2025-09-30 | **Last Amended**: 2025-09-30
