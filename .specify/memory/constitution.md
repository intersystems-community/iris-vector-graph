# iris-vector-graph Constitution

## Core Principles

### I. Library-First
All features must be implemented within the library codebase and remain self-contained, independently testable, and documented. Application-specific workarounds are not acceptable as the primary interface.

### II. Compatibility-First Interfaces
Existing public interfaces (library API and any CLI entry points) must remain backward compatible unless a documented breaking change is explicitly approved. New features should extend, not replace, existing usage patterns.

### III. Test-First (Non-Negotiable)
All feature work must follow a test-first approach. Tests are written or updated before implementation and must fail before code changes are introduced.

### IV. Integration Testing for IRIS
Changes that affect database behavior or SQL translation must include integration tests that validate behavior against IRIS.

### V. Simplicity and Clarity
Prefer the simplest design that meets requirements. Avoid unnecessary abstractions or over-engineering.

## Additional Constraints

- Use the existing RDF schema (nodes, rdf_labels, rdf_props, rdf_edges, kg_NodeEmbeddings) unless a schema change is explicitly approved.
- Numeric comparisons must be deterministic and documented when values are stored as strings.

## Development Workflow

- All work must be traceable to a spec and plan.
- Feature changes should be grouped by user story to support incremental delivery.

## Governance

This constitution supersedes all other development guidance for this repository. Any amendments must be documented and explicitly approved before implementation begins.

**Version**: 1.0.0 | **Ratified**: 2026-01-31 | **Last Amended**: 2026-01-31
