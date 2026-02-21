<!--
SYNC IMPACT REPORT
==================
Version change: 1.0.0 → 1.1.0 (MINOR — Principle IV materially expanded; new tooling constraint added)
Bump rationale: Adding mandatory e2e test discipline with named IRIS container managed by iris-devtester
  is a material expansion of existing Principle IV (Integration Testing for IRIS). No principles removed
  or redefined; no backward-incompatible governance changes.

Modified principles:
  - IV. Integration Testing for IRIS → IV. Integration & End-to-End Testing for IRIS
    (expanded: e2e tests now mandatory for any feature with IRIS as a backend component;
     container MUST be named, dedicated to the project, and managed by iris-devtester)

Added sections: none
Removed sections: none

Templates requiring updates:
  ✅ .specify/templates/tasks-template.md — Phase 2 Foundational section updated to reference
     iris-devtester container setup as a blocking prerequisite for IRIS-backend features.
  ✅ .specify/templates/plan-template.md — Constitution Check section updated to gate on
     Principle IV (e2e test requirement).
  ⚠  .specify/templates/spec-template.md — No structural change required; Principle IV is
     enforced at plan/tasks time, not at spec time. No update needed.
  ⚠  .specify/templates/agent-file-template.md — Review manually if it references testing
     discipline; not read (out of scope for this amendment).

Deferred items: none — all placeholders resolved.
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
- Container lifecycle (start, stop, port resolution, credentials) MUST be managed exclusively
  by `iris-devtester` (`IRISContainer.attach("los-iris")` pattern). IRIS ports MUST NOT be
  hardcoded in test code or fixtures.
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

## Governance

This constitution supersedes all other development guidance for this repository. Any
amendments MUST be documented and explicitly approved before implementation begins.
Version increments follow semantic versioning: MAJOR for backward-incompatible governance
changes, MINOR for new or materially expanded principles, PATCH for clarifications.

**Version**: 1.1.0 | **Ratified**: 2026-01-31 | **Last Amended**: 2026-02-21
