# Implementation Plan: Auto-Generating GraphQL Layer for IRIS Graph Stores

**Branch**: `019-ivg-gql-autogen` | **Date**: 2026-02-24 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/019-ivg-gql-autogen/spec.md`

## Summary

Implement a generic `iris_vector_graph.gql` module that auto-generates a GraphQL API schema by introspecting a live IRIS graph store. The system will expose top-level fields for discovered node labels and properties, support semantic search, and provide bi-directional relationship traversal (incoming and outgoing) using connection pooling to manage IRIS Community license limits.

## Technical Context

**Language/Version**: Python 3.11  
**Primary Dependencies**: `fastapi`, `strawberry-graphql[fastapi]`, `intersystems-irispython`, `iris-devtester`  
**Storage**: InterSystems IRIS (RDF Schema: nodes, rdf_labels, rdf_props, rdf_edges, kg_NodeEmbeddings)  
**Testing**: `pytest` with `TestClient` and `iris-devtester` managed containers  
**Target Platform**: Any environment supporting Python 3.11+ and connectivity to InterSystems IRIS 2025.1+  
**Project Type**: Library module with embedded ASGI server  
**Performance Goals**: < 500ms for label-filtered node queries; < 2s for semantic search on 10k embeddings  
**Constraints**: 5-connection concurrency limit (IRIS CE); Property discovery sampling (1,000 nodes per label)  
**Scale/Scope**: Automated schema generation covering 100% of discovered node types at startup.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

**Principle IV gate (IRIS-backend features)**: Confirm the plan includes:
- [x] A dedicated, named IRIS container (`iris_vector_graph`) managed by `iris-devtester`
- [x] An explicit e2e test phase (non-optional, not in "polish") covering all user stories
- [x] `SKIP_IRIS_TESTS` defaulting to `"false"` in all new test files
- [x] No hardcoded IRIS ports; all resolved via `IRISContainer.attach("iris_vector_graph").get_exposed_port(1972)`

> **Principle VI reminder**: The container name `iris_vector_graph` above was verified from
> `docker-compose.yml`.

## Project Structure

### Documentation (this feature)

```text
specs/019-ivg-gql-autogen/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
└── tasks.md             # Phase 2 output (to be created by /speckit.tasks)
```

### Source Code (repository root)

```text
iris_vector_graph/
├── gql/                 # New module
│   ├── __init__.py      # serve() entry point
│   ├── engine.py        # GraphQL to IRIS Engine bridging
│   ├── schema.py        # Dynamic Strawberry schema generation
│   ├── pooling.py       # Connection pooling implementation
│   └── resolvers.py     # Generic resolvers for nodes, edges, search
```

**Structure Decision**: Single project (Library-First). Implementing the `gql` subpackage within the core library per Principle I.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |
