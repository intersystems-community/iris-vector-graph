# Implementation Plan: shortestPath() openCypher Syntax (spec 047)

**Branch**: `047-shortest-path` | **Date**: 2026-04-18 | **Spec**: `specs/047-shortest-path/spec.md`

## Summary

Fix the `shortestPath()`/`allShortestPaths()` parse error and implement proper path
reconstruction via a new `Graph.KG.Traversal.ShortestPathJson` ObjectScript ClassMethod.
All query-time work is pure ObjectScript + `^KG` globals — consistent with every other
search feature in this project (BM25, IVFFlat, VecIndex, PLAID). Python is wiring only.

## Technical Context

**Language/Version**: Python 3.11 (wiring/tests) + ObjectScript (IRIS 2024.1+, BFS engine)  
**Primary Dependencies**: `intersystems-irispython`, `iris-devtester` (test only)  
**Storage**: `^KG("out")` / `^KG("in")` globals — no new schema, no new globals  
**Testing**: pytest (unit + E2E against `iris_vector_graph` container)  
**Performance Goals**: < 100ms on 10K-node/50K-edge graph, path ≤ 8 hops  
**Constraints**: Both endpoints must be bound node IDs in v1.

## Constitution Check

- [x] IRIS container `iris_vector_graph` — `IRISContainer.attach("iris_vector_graph")`
- [x] E2E test phase (non-optional) covering all user stories
- [x] `SKIP_IRIS_TESTS` defaults to `"false"` in new test file
- [x] No hardcoded IRIS ports — all via `IRISContainer.attach(...).get_exposed_port(1972)`

## Current State — What Already Exists

| Component | Status | Location |
|-----------|--------|----------|
| `BFSFast` + `TraverseAllPredicatesFast` | Complete | `Traversal.cls:154` |
| `BFSFastJson` (reachability, no paths) | Complete — NOT used for shortestPath | `Traversal.cls:289` |
| Python BFS fallback | Exists but wrong architecture | `algorithms/paths.py` |
| Translator stub `shortestpath` | Broken — calls non-existent function | `translator.py:1488` |
| Parser recognition of `shortestPath` | **Missing — root cause of bug** | `parser.py` |
| AST shortest flags on `VariableLength` | Missing | `ast.py` |
| `ShortestPathJson` ObjectScript method | **Missing — must build** | `Traversal.cls` |

## Root Cause

`MATCH p = shortestPath((a {id:$from})-[*..8]-(b {id:$to}))` fails at parse time:
1. Parser sees `p =` → named path variable → calls `parse_graph_pattern()`
2. `parse_graph_pattern()` expects `(` to start a node — hits `shortestPath` IDENTIFIER
3. Throws `Expected (, got IDENTIFIER`

## Project Structure

### Files to modify / create

```text
iris_src/src/Graph/KG/
└── Traversal.cls            # ADD ShortestPathJson ClassMethod (BFS + parent pointers)

iris_vector_graph/cypher/
├── ast.py                   # ADD shortest/all_shortest flags to VariableLength
├── lexer.py                 # Treat shortestPath/allShortestPaths as keyword identifiers
├── parser.py                # ADD path_function_expr rule in parse_match_clause
└── translator.py            # FIX broken stub; route var_length_paths to shortest exec path

iris_vector_graph/
└── engine.py                # ADD _execute_shortest_path_cypher (calls ShortestPathJson)

tests/unit/
└── test_shortest_path.py    # NEW — unit + E2E tests
```

## ShortestPathJson — ObjectScript Design

```
ClassMethod ShortestPathJson(srcId, dstId, maxHops, predsJson, findAll) As %String
```

Uses process-private globals (same pattern as `BFSFast`) for performance:
- `^||SP.parent(nodeId)` = `$ListBuild(parentNodeId, relType)` — parent pointer per visited node
- `^||SP.frontier(nodeId)` = "" — current BFS frontier
- `^||SP.seen(nodeId)` = "" — visited set

Algorithm:
1. If `srcId == dstId` → return `[{"nodes":[$srcId], "rels":[], "length":0}]`
2. BFS loop (hop 1..maxHops): expand frontier → record parent pointer on first visit
3. When `dstId` reached: backtrack `^||SP.parent` chain → reconstruct ordered node/rel lists
4. If `findAll=1`: continue BFS at same depth level, collect all paths of minimum length
5. Return JSON string `[{"nodes":[...], "rels":[...], "length":N}, ...]`

Direction handling:
- Directed (`outgoing`): follow `^KG("out", s, p, o)` only
- Undirected (`both`): follow `^KG("out", s, p, o)` AND `^KG("in", s, p, o)`

Predicate filtering: parse `predsJson` as `%DynamicArray` — if non-empty, only traverse edges where `p` is in the list.

## Python Wiring Design

`engine.py:_execute_shortest_path_cypher(sql_query, parameters)`:
1. Detect `var_length_paths[0]["shortest"] == True`
2. Extract `source_id` and `target_id` from SQL parameters (both `{id: $x}` bindings)
3. Extract `direction` from `var_length_paths[0]["direction"]` (`"both"` for undirected `--`, `"outgoing"` for `-->`)
4. Call `classMethodValue("Graph.KG.Traversal", "ShortestPathJson", srcId, dstId, maxHops, predsJson, findAll)`
5. Parse JSON response → format result columns based on RETURN clause:
   - `RETURN p` → single column `p` with JSON string value
   - `nodes(p)` → parse nodes list from JSON
   - `relationships(p)` → parse rels list from JSON
   - `length(p)` → extract length integer

## Parser / AST Changes

**`ast.py`** — `VariableLength`:
```python
shortest: bool = False
all_shortest: bool = False
```
Relax `max_hops ≤ 10` to `max_hops ≤ 15` when `shortest=True`.

**`parser.py`** — `parse_match_clause`: before calling `parse_graph_pattern()`, check:
```
if peek is IDENTIFIER and peek.value in ("shortestPath", "allShortestPaths"):
    consume function name + "("
    parse inner node-rel-node pattern
    consume ")"
    set variable_length.shortest / all_shortest = True
    wrap in NamedPath if path_var bound
```

**`translator.py`** — remove broken stub at line 1488 (the `if fn in ("shortestpath", ...)` block). The `RETURN p` / `nodes(p)` / `length(p)` handling for **static** named paths stays. For shortestPath results, a new `shortest_path_result` marker in `context` routes RETURN column building to the engine output.

## Implementation Phases

**Phase 1 (P1 — bug fix)**: AST flags → lexer/parser → `ShortestPathJson` ObjectScript → engine wiring → `RETURN p` works  
**Phase 2 (P2)**: `nodes(p)`, `relationships(p)`, `length(p)` in RETURN / WHERE  
**Phase 3 (P3)**: `allShortestPaths` (`findAll=1`)

## Reuse Map

| Component | Reused from |
|-----------|-------------|
| `^KG("out")` traversal | `BFSFast.TraverseAllPredicatesFast` pattern |
| `^KG("in")` traversal | `TemporalIndex` inbound pattern |
| Process-private globals `^||` | `BFSFast` (`^||BFS.Results`) |
| `classMethodValue` call pattern | `ivf_search`, `bm25_search` |
| `_execute_var_length_cypher` | Extended to detect shortest mode |
| `VariableLength` AST node | Extended with two new bool flags |

## Complexity Tracking

| Item | Justification |
|------|---------------|
| `max_hops` raised 10→15 for shortestPath | Real biomedical graphs need more hops |
| ObjectScript not Python for BFS | Consistent with all other search features; keeps query-time code server-side |

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/commands/plan.md` for the execution workflow.

## Summary

[Extract from feature spec: primary requirement + technical approach from research]

## Technical Context

<!--
  ACTION REQUIRED: Replace the content in this section with the technical details
  for the project. The structure here is presented in advisory capacity to guide
  the iteration process.
-->

**Language/Version**: [e.g., Python 3.11, Swift 5.9, Rust 1.75 or NEEDS CLARIFICATION]  
**Primary Dependencies**: [e.g., FastAPI, UIKit, LLVM or NEEDS CLARIFICATION]  
**Storage**: [if applicable, e.g., PostgreSQL, CoreData, files or N/A]  
**Testing**: [e.g., pytest, XCTest, cargo test or NEEDS CLARIFICATION]  
**Target Platform**: [e.g., Linux server, iOS 15+, WASM or NEEDS CLARIFICATION]
**Project Type**: [single/web/mobile - determines source structure]  
**Performance Goals**: [domain-specific, e.g., 1000 req/s, 10k lines/sec, 60 fps or NEEDS CLARIFICATION]  
**Constraints**: [domain-specific, e.g., <200ms p95, <100MB memory, offline-capable or NEEDS CLARIFICATION]  
**Scale/Scope**: [domain-specific, e.g., 10k users, 1M LOC, 50 screens or NEEDS CLARIFICATION]

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

[Gates determined based on constitution file]

**Principle IV gate (IRIS-backend features)**: If this feature has IRIS as a backend
component, confirm the plan includes:
- [ ] A dedicated, named IRIS container (`iris_vector_graph`) managed by `iris-devtester`
- [ ] An explicit e2e test phase (non-optional, not in "polish") covering all user stories
- [ ] `SKIP_IRIS_TESTS` defaulting to `"false"` in all new test files
- [ ] No hardcoded IRIS ports; all resolved via `IRISContainer.attach("iris_vector_graph").get_exposed_port(1972)`

> **Principle VI reminder**: The container name `iris_vector_graph` above was verified from
> `docker-compose.yml`. If you are using this template for a different project, re-verify
> ALL infrastructure details (container name, port, schema) against that project's
> authoritative sources before proceeding. NEVER assume or copy from another project.

## Project Structure

### Documentation (this feature)

```text
specs/[###-feature]/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)
<!--
  ACTION REQUIRED: Replace the placeholder tree below with the concrete layout
  for this feature. Delete unused options and expand the chosen structure with
  real paths (e.g., apps/admin, packages/something). The delivered plan must
  not include Option labels.
-->

```text
# [REMOVE IF UNUSED] Option 1: Single project (DEFAULT)
src/
├── models/
├── services/
├── cli/
└── lib/

tests/
├── contract/
├── integration/
└── unit/

# [REMOVE IF UNUSED] Option 2: Web application (when "frontend" + "backend" detected)
backend/
├── src/
│   ├── models/
│   ├── services/
│   └── api/
└── tests/

frontend/
├── src/
│   ├── components/
│   ├── pages/
│   └── services/
└── tests/

# [REMOVE IF UNUSED] Option 3: Mobile + API (when "iOS/Android" detected)
api/
└── [same as backend above]

ios/ or android/
└── [platform-specific structure: feature modules, UI flows, platform tests]
```

**Structure Decision**: [Document the selected structure and reference the real
directories captured above]

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| [e.g., 4th project] | [current need] | [why 3 projects insufficient] |
| [e.g., Repository pattern] | [specific problem] | [why direct DB access insufficient] |
