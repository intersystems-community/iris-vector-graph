# Implementation Plan: LOS Cypher & API Integration Gaps

**Branch**: `015-los-cypher-api-gaps` | **Date**: 2026-01-31 | **Spec**: [specs/015-los-cypher-api-gaps/spec.md](spec.md)
**Input**: Feature specification from `specs/015-los-cypher-api-gaps/spec.md`

## Summary

This feature addresses several integration gaps identified during the implementation of the LOS knowledge graph. The primary goal is to eliminate direct SQL workarounds in application code by enhancing the Cypher query language support and providing high-level APIs for node properties and embeddings. Key technical improvements include support for `RETURN n` (returning whole node objects), `ORDER BY`/`LIMIT` in Cypher, comparison operators, and string pattern matching.

## Technical Context

**Language/Version**: Python 3.11  
**Primary Dependencies**: `intersystems-irispython`, `fastapi`, `strawberry-graphql`, `lark` (for Cypher parsing)  
**Storage**: InterSystems IRIS (RDF Schema: nodes, rdf_labels, rdf_props, rdf_edges, kg_NodeEmbeddings)  
**Testing**: `pytest`  
**Target Platform**: Linux server (Docker)  
**Project Type**: Single project (Library)  
**Performance Goals**: < 10% overhead for Cypher query translation; efficient SQL generation for `ORDER BY`/`LIMIT` using database-level filtering.  
**Constraints**: Properties are stored as strings in `rdf_props.val`; numeric comparisons require SQL type coercion (CAST).  
**Scale/Scope**: LOS knowledge graph integration; handling complex Cypher queries with properties and labels.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Check | Status |
|-----------|-------|--------|
| I. Library-First | Cypher translation logic remains self-contained in `iris_vector_graph.cypher`. | PASS |
| II. CLI Interface | Enhanced Cypher features available via existing CLI/API endpoints. | PASS |
| III. Test-First | Unit tests for parser/translator and engine APIs will be written before implementation. | PASS |
| IV. Integration Testing | Integration tests with IRIS for SQL generation and embedding storage are included. | PASS |
| V. Simplicity | Enhancements prioritize clean API over complex workarounds. | PASS |

## Project Structure

### Documentation (this feature)

```text
specs/015-los-cypher-api-gaps/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
└── tasks.md             # Phase 2 output
```

### Source Code (repository root)

```text
iris_vector_graph/
├── cypher/
│   ├── parser.py        # Update for ORDER BY, LIMIT, comparison, functions
│   ├── translator.py    # Update for SQL generation, labels(), properties(), type()
│   └── ast.py           # Update AST nodes for new clauses
├── engine.py            # Add get_node, store_embedding, store_embeddings
└── schema.py            # Schema constants and queries
tests/
├── integration/
│   ├── test_nodepk_constraints.py
│   ├── test_nodepk_graph_analytics.py
│   └── test_cypher_enhancements.py  # NEW: Test ORDER BY, LIMIT, etc.
└── unit/
    ├── test_cypher_parser.py        # NEW: Test parser updates
    └── test_cypher_translator.py    # NEW: Test SQL generation
```

**Structure Decision**: Single project structure follows existing repository layout. New integration and unit tests will be added to verify enhancements.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| N/A | | |
