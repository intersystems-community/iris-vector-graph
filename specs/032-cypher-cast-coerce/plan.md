# Implementation Plan: Cypher CAST Functions + COUNT(DISTINCT)

**Branch**: `032-cypher-cast-coerce` | **Date**: 2026-03-31 | **Spec**: [spec.md](./spec.md)

## Summary

Fix 4 broken CAST function emissions in the Cypher translator. The `_CYPHER_FN_MAP` maps `tostring/tointeger/tofloat/toboolean` but the generic emit path emits `CAST(expr)` without a target type. Fix is ~15 lines. Verify `COUNT(DISTINCT)` and add tests.

## Technical Context

**Language/Version**: Python 3.11
**Files to change**: `iris_vector_graph/cypher/translator.py` — single file, no schema changes
**Tests**: `tests/unit/test_cypher_functions.py` + `tests/e2e/test_cypher_coerce_e2e.py`

## Constitution Check

- [x] Container `iris-vector-graph-main` (conftest.py:153/348)
- [x] E2e tests (non-optional)
- [x] `SKIP_IRIS_TESTS` defaults `"false"`
- [x] No hardcoded ports / No schema changes

**Gate status**: PASS

## Root Cause

`translate_expression()` line 988-989: `_CYPHER_FN_MAP` maps to `"CAST"` but the generic emit ignores type:
```python
return f"{sql_fn}({', '.join(args)})"  # emits CAST(expr) — missing AS TYPE
```

## Fix (4 lines in translator.py, before generic emit)

```python
if fn == "tointeger":  return f"CAST({args[0]} AS INTEGER)"
if fn == "tofloat":    return f"CAST({args[0]} AS DOUBLE)"
if fn == "tostring":   return f"CAST({args[0]} AS VARCHAR(4096))"
if fn == "toboolean":  return f"CASE WHEN LOWER({args[0]}) IN ('true','1','yes','y') THEN 1 ELSE 0 END"
```

## Project Structure

```text
iris_vector_graph/cypher/
└── translator.py   # MODIFY: 4 lines before generic sql_fn emit (~line 988)

tests/
├── unit/test_cypher_functions.py    # ADD: 6+ unit tests
└── e2e/test_cypher_coerce_e2e.py    # ADD: 2+ e2e tests
```

## Complexity Tracking

No constitution violations. No schema changes. Pure translator fix.
**Input**: Feature specification from `/specs/[###-feature-name]/spec.md`

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
