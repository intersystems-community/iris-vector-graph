# Implementation Plan: Recursive-Descent Cypher Parser

**Branch**: `001-cypher-rd-parser` | **Date**: 2026-01-25 | **Spec**: [specs/001-cypher-rd-parser/spec.md](spec.md)
**Input**: Feature specification from `/specs/001-cypher-rd-parser/spec.md`

## Summary

Implement a hand-written recursive-descent parser for Cypher using a dedicated `Lexer` and `Parser` class pattern. This implementation will replace the current regex-based parser, enabling multi-stage queries via `WITH` clauses (mapped to IRIS Common Table Expressions), standard aggregation functions (mapped to explicit `GROUP BY` SQL), and built-in graph functions.

## Technical Context

**Language/Version**: Python 3.11  
**Primary Dependencies**: `intersystems-irispython`, `fastapi`, `strawberry-graphql`. Hand-written `Lexer`/`Parser` pattern with Python 3.11 `match/case`.  
**Storage**: InterSystems IRIS (NodePK schema)  
**Testing**: `pytest` with live IRIS instance (per constitution)  
**Target Platform**: Linux (Docker)  
**Project Type**: Core Library (`iris_vector_graph`) + API (`api/`)  
**Performance Goals**: Parser overhead <10ms for standard queries; leverage IRIS 2025.1+ CTE optimization.  
**Constraints**: Must strictly enforce openCypher variable scoping in `WITH` clauses.  
**Scale/Scope**: Support multi-stage queries with aggregations.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

1. **Test-First (NON-NEGOTIABLE)**: Integration tests will be written before implementation. **YES**
2. **Library-First**: The parser logic will reside in the `iris_vector_graph` core library. **YES**
3. **CLI Interface**: Functionality will be accessible via existing Cypher API endpoints. **YES**
4. **Integration Testing**: Focus on Cypher-to-SQL translation correctness against live IRIS. **YES**
5. **Observability**: Error messages will include line/column position. **YES**
6. **IRIS-Native Performance**: Uses CTEs and explicit GROUP BY for optimal IRIS execution. **YES**

## Project Structure

### Documentation (this feature)

```text
specs/001-cypher-rd-parser/
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
└── cypher/
    ├── lexer.py         # New: Tokenizer
    ├── parser.py        # Updated: RD Parser implementation
    ├── ast.py           # Updated: Enriched AST nodes
    └── translator.py    # Updated: Multi-stage SQL generator

api/
└── routers/
    └── cypher.py        # Updated: Use new parser/translator

tests/
├── unit/
│   └── cypher/
│       ├── test_lexer.py
│       └── test_parser.py
└── integration/
    └── test_cypher_rd.py
```

**Structure Decision**: Single project structure with updates to the `cypher` module and corresponding tests.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |
