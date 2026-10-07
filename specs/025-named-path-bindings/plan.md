<!-- markdownlint-disable MD013 MD024 -->

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

## Amendment 2026-10-07: path functions on variable-length paths with a bound source

Spec: User Story 4 scenarios 3 to 11, FR-010 to FR-015, SC-006 to SC-009. Evidence:
`docs/research/length-path-empty-result-report.md`. No new dependency, no schema change.

**Cause (read from the code and confirmed by running the pieces):**

1. `QueryMixin._route_var_length` resolves the source id from the pattern, then hands any
   query with `return_path_funcs` to `_execute_var_length_cypher`, which discards it and
   takes the first string in the SQL's bound parameters (the label).
2. `_execute_var_length_cypher` has no path-function support; its id-only fast path
   returns one column.
3. `_execute_var_length_labeled_path_funcs` is the executor that does implement path
   functions, but it is reached only with no bound source, and its
   `extract_vlp_source_ids` takes every node with the label (ignoring an id binding),
   through `store.query_nodes` -> `get_nodes`, whose single `IN (...)` list fails past
   about 1,000 parameters. The failure is logged at debug level and answered as `[]`.

**Design:**

- `get_nodes` reads its three `IN` lists in chunks of 500.
- `extract_vlp_source_ids`: an id binding in the statement (`node_id = ?` or literal)
  wins over the label; a label then only narrows it (the node must carry the label).
  A failed lookup raises (FR-013) instead of returning `[]`.
- `_route_var_length`: `return_path_funcs` goes to `_execute_var_length_labeled_path_funcs`
  with the resolved source id; that executor accepts `source_ids` and skips extraction when
  given them.
- `_execute_var_length_cypher` prefers the router's `src_id_param` to the positional guess
  (defence for the remaining callers: `min_hops > 1`, relationship properties).
- Every empty return in the path-function executor carries the RETURN columns (FR-014).

**Files:** `iris_vector_graph/_engine/query.py`, `iris_vector_graph/stores/iris_sql_store.py`.
Tests: `tests/unit/test_025_varlen_path_source.py` (mocked), `tests/integration/
test_025_varlen_path_functions.py` (live, scratch namespace IVG236, more than 1,000 nodes
of the source's label).

**Constitution check:** unchanged from above; the live tests run on `ivg-iris-enterprise`.
**Risk:** raising where `[]` was returned can surface lookup failures that earlier ran as
empty results in TCK-style flows; the Cypher unit and TCK-adjacent suites are the gate.
