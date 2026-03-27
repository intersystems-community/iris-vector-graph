# Implementation Plan: Named Path Bindings

**Branch**: `025-named-path-bindings` | **Date**: 2026-03-27 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/025-named-path-bindings/spec.md`

## Summary

Add named path binding support to IVG's Cypher parser and translator: `MATCH p = (a)-[r]->(b) RETURN p, length(p), nodes(p), relationships(p)`. Paths are serialized as JSON objects containing ordered node IDs and predicate strings. Implementation touches 3 files in the Cypher stack (ast.py, parser.py, translator.py) with zero new dependencies.

## Technical Context

**Language/Version**: Python 3.11  
**Primary Dependencies**: `iris_vector_graph.cypher` (ast, lexer, parser, translator) — no new deps  
**Storage**: InterSystems IRIS — existing `Graph_KG` schema (nodes, rdf_edges, rdf_labels, rdf_props)  
**Testing**: pytest — unit tests (mocked) + e2e against live IRIS  
**Target Platform**: IRIS 2023.1+ (JSON_ARRAY support confirmed)  
**Project Type**: Single library  
**Performance Goals**: Named path queries within 10% of equivalent unnamed queries  
**Constraints**: No native PATH type in IRIS SQL — paths serialized as JSON  
**Scale/Scope**: 3 files modified, 1 AST node added, 3 function translations added

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- [x] A dedicated, named IRIS container (`iris-vector-graph-main`) managed by `iris-devtester` (verified: conftest.py:153, conftest.py:348)
- [x] An explicit e2e test phase (non-optional) covering all user stories
- [x] `SKIP_IRIS_TESTS` defaulting to `"false"` in all new test files
- [x] No hardcoded IRIS ports; all resolved via `IRISContainer.attach("iris-vector-graph-main").get_exposed_port(1972)`

**Gate status**: PASS

## Project Structure

### Documentation (this feature)

```text
specs/025-named-path-bindings/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
└── tasks.md             # Phase 2 output (created by /speckit.tasks)
```

### Source Code (repository root)

```text
iris_vector_graph/cypher/
├── ast.py               # ADD: NamedPath dataclass; MODIFY: MatchClause.named_paths field
├── parser.py            # MODIFY: parse_match_clause to detect `var = (pattern)` prefix
├── translator.py        # MODIFY: translate_return_clause for path vars; translate_expression for length/nodes/relationships

tests/
├── unit/
│   └── test_named_paths.py         # NEW: 10+ unit tests (parse, translate, functions, errors)
└── e2e/
    └── test_named_paths_e2e.py     # NEW: 5+ e2e tests against live IRIS
```

**Structure Decision**: Extends existing Cypher stack in-place. No new modules, no new directories. The parser already handles MATCH clauses; we extend the parse_match_clause method with a lookahead for `IDENTIFIER = (`.

## Complexity Tracking

No constitution violations. No complexity justifications needed.
