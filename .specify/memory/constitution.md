<!--
SYNC IMPACT REPORT
==================
Version change: 1.1.0 → 1.1.1 (PATCH — clarification; no principles added or removed)
Bump rationale: Principle IV example container name corrected from "los-iris" (a different
  project's container) to the actual project container name "iris_vector_graph" (from
  docker-compose.yml). Added Principle VI (Grounding Rule) to prevent recurrence: any
  infrastructure detail written into specs, tests, or templates MUST first be verified
  against the authoritative source in the repository (docker-compose.yml, pyproject.toml,
  conftest.py) before use. No placeholder values, no assumed names from other projects.

Modified principles:
  - IV: example container name corrected (iris_vector_graph, not los-iris)
  - VI: new principle added — Grounding Rule (verify before you write)

Templates requiring updates:
  ✅ .specify/templates/plan-template.md — container name corrected
  ✅ .specify/templates/tasks-template.md — container name corrected

Deferred items: none.
-->

# iris-vector-graph Constitution

## Core Principles

### I. Library-First

All features MUST be implemented within the library codebase and remain self-contained,
independently testable, and documented. Application-specific workarounds are not acceptable
as the primary interface.

### II. Compatibility-First Interfaces

Existing public interfaces (library API and any CLI entry points) MUST remain backward
compatible unless a documented breaking change is explicitly approved. New features MUST
extend, not replace, existing usage patterns.

### III. Test-First (Non-Negotiable)

All feature work MUST follow a test-first approach. Tests are written or updated before
implementation and MUST fail before code changes are introduced.

### IV. Integration & End-to-End Testing for IRIS

Any feature that includes IRIS as a backend component MUST include comprehensive end-to-end
(e2e) tests that run against a live IRIS container. The following rules are non-negotiable:

- The IRIS container MUST be named and dedicated to this project (not shared or anonymous).
  The container name for this project is `iris_vector_graph` (defined in `docker-compose.yml`).
  NEVER use a container name from another project.
- Container lifecycle (start, stop, port resolution, credentials) MUST be managed exclusively
  by `iris-devtester` (`IRISContainer.attach("iris_vector_graph")` pattern). IRIS ports MUST
  NOT be hardcoded in test code or fixtures.
- The environment variable `SKIP_IRIS_TESTS` MUST default to `"false"`. Tests always hit the
  live database unless explicitly overridden by the developer.
- Changes that affect database behavior or SQL translation MUST additionally include
  integration tests (in `tests/integration/`) that validate behavior at the SQL layer.
- Unit tests alone are insufficient to satisfy this principle for IRIS-backend features.

**Rationale**: This project is a knowledge graph engine built on InterSystems IRIS. Behavior
that cannot be observed without a live database (vector indexing, SQL translation, schema
migration, Cypher execution) cannot be validated by mocks. Skipping live tests has
historically caused regressions discovered only by downstream consumers (`posos`, `iris-vector-rag`).

### V. Simplicity and Clarity

Prefer the simplest design that meets requirements. Avoid unnecessary abstractions or
over-engineering. Every layer of indirection MUST be justified by a concrete requirement.

### VI. Grounding Rule (Verify Before You Write)

Any infrastructure detail — container names, port numbers, schema names, credentials,
package names, file paths — written into specs, tests, templates, or commit messages MUST
first be verified against the authoritative source in this repository before use.

**Authoritative sources**:
- Container name → `docker-compose.yml` (`container_name:` field)
- IRIS port → `docker-compose.yml` (`ports:` field)
- Package name / version → `pyproject.toml`
- Schema prefix → `iris_vector_graph/engine.py` (`set_schema_prefix(...)` call)
- Test infrastructure → `tests/conftest.py`

**Never assume. Never copy from another project. Always look first.**

Violation of this rule caused the `los-iris` incident (Feb 2026): a container name from
an unrelated project was propagated into the constitution, all spec artifacts, and test
code before being caught. The fix required amending 8+ files. The cost is not acceptable.

## Additional Constraints

- Use the existing RDF schema (`nodes`, `rdf_labels`, `rdf_props`, `rdf_edges`,
  `kg_NodeEmbeddings`) unless a schema change is explicitly approved and documented.
- Numeric comparisons MUST be deterministic and documented when values are stored as strings.
- `iris-vector-rag` MUST NOT be added as a dependency of `iris-vector-graph`. The two
  packages are siblings; shared behavior belongs in `iris-vector-graph` or a new shared
  package, not through cross-dependency.

## Development Workflow

- All work MUST be traceable to a spec and plan.
- Feature changes MUST be grouped by user story to support incremental delivery.
- Every plan for a feature with an IRIS backend component MUST include an explicit e2e test
  task group (per Principle IV) as a non-optional phase, not as a polish/optional item.
- Before writing any infrastructure detail into a spec or test, verify it against the
  authoritative source (Principle VI). This is a blocking prerequisite, not a suggestion.

## Governance

This constitution supersedes all other development guidance for this repository. Any
amendments MUST be documented and explicitly approved before implementation begins.
Version increments follow semantic versioning: MAJOR for backward-incompatible governance
changes, MINOR for new or materially expanded principles, PATCH for clarifications.

**Version**: 1.1.1 | **Ratified**: 2026-01-31 | **Last Amended**: 2026-02-21
