# Implementation Plan: Unified Edge Store (spec 048)

**Branch**: `048-unified-edge-store` | **Date**: 2026-04-18 | **Spec**: `specs/048-unified-edge-store/spec.md`

## Summary

Two independently mergeable PRs:

- **PR-A (P1)**: Synchronous `^KG` writes in `create_edge` + `Graph.KG.EdgeScan.MatchEdges` proc + translator CTE swap. Fixes temporal edge visibility and stale-after-write immediately.
- **PR-B (P2)**: Shard subscript migration (`^KG("out", 0, s, p, o)`), all traversal code updated, `BuildNKG()` updated, `BuildKG` migration. Establishes partition-ready layout.

PR-A is the urgent fix. PR-B is the architectural prerequisite for future horizontal partitioning.

## Technical Context

**Language/Version**: Python 3.11 + ObjectScript (IRIS 2024.1+)
**Primary Dependencies**: `intersystems-irispython`, `iris-devtester` (test only)
**No new dependencies**

### PR-A files changed

```
iris_vector_graph/
├── engine.py                          # create_edge: add ^KG writes; delete_edge: add ^KG kills
└── cypher/
    └── translator.py                  # translate_relationship_pattern: swap rdf_edges JOIN → EdgeScan CTE

iris_src/src/Graph/KG/
└── EdgeScan.cls                       # NEW — MatchEdges ClassMethod + SqlProc
```

### PR-B files changed

```
iris_src/src/Graph/KG/
├── Traversal.cls                      # BuildKG, BFSFast, ShortestPathJson: ^KG("out",0,...) subscript
├── TemporalIndex.cls                  # InsertEdge: verify/update shard slot
├── NKGAccel.cls / BuildNKG            # FR-010: read ^KG("out",0,...) layout
└── BenchSeeder.cls                    # update to write shard=0 layout

iris_vector_graph/
└── schema.py                          # _call_classmethod("BuildKG") — no change needed; BuildKG is self-contained
```

## Constitution Check

- [x] IRIS container `iris_vector_graph` — `IRISContainer.attach("iris_vector_graph")`
- [x] E2E test phases covering all user stories
- [x] `SKIP_IRIS_TESTS` defaults to `"false"` in new test file
- [x] No hardcoded IRIS ports

## PR-A Design

### `Graph.KG.EdgeScan.MatchEdges` ObjectScript ClassMethod

```
ClassMethod MatchEdges(sourceId As %String, predicate As %String, shard As %Integer = 0) As %String [SqlProc]
```

**Bound source + bound predicate** (`sourceId` non-empty, `predicate` non-empty):
```objectscript
Set o = ""
For { Set o = $Order(^KG("out", 0, sourceId, predicate, o)) Quit:o=""
      // append {s:sourceId, p:predicate, o:o, w:^KG("out",0,sourceId,predicate,o)} to results }
```

**Bound source + unbound predicate** (`sourceId` non-empty, `predicate` empty):
```objectscript
Set p = ""
For { Set p = $Order(^KG("out", 0, sourceId, p)) Quit:p=""
      Set o = ""
      For { Set o = $Order(^KG("out", 0, sourceId, p, o)) Quit:o=""
            // append {s:sourceId, p:p, o:o, w:...} } }
```

**Unbound source** (`sourceId` empty): outer `$Order(^KG("out", 0, s))` scan.

Returns JSON string `[{"s":"...","p":"...","o":"...","w":1.0},...]`

Note: PR-A uses `^KG("out", 0, ...)` anticipating PR-B layout. Single-node shard=0 is the only supported value in PR-A; the shard slot is present but not yet the subject of routing.

### Translator change (`translate_relationship_pattern`)

Current code (line 1299):
```python
context.join_clauses.append(f"{jt} {_table('rdf_edges')} {edge_alias} ON {edge_cond}")
```

PR-A replaces this for simple MATCH patterns (non-variable-length, non-temporal) with:
```python
# Build MatchEdges CTE and swap rdf_edges JOIN for CTE reference
src_id_sql = f"{source_alias}.node_id"  # bound source
pred_sql = context.add_join_param(rel.types[0]) if len(rel.types) == 1 else "''"
cte = (f"EdgeScan_{edge_alias} AS (\n"
       f"  SELECT j.s, j.p, j.o, j.w FROM JSON_TABLE(\n"
       f"    Graph_KG.MatchEdges({src_id_sql}, {pred_sql}, 0),\n"
       f"    '$[*]' COLUMNS(s VARCHAR(256) PATH '$.s', p VARCHAR(256) PATH '$.p',\n"
       f"                    o VARCHAR(256) PATH '$.o', w DOUBLE PATH '$.w') ) j\n)")
context.stages.insert(0, cte)
edge_alias_ref = f"EdgeScan_{edge_alias}"
context.join_clauses.append(f"{jt} {edge_alias_ref} {edge_alias} ON {edge_cond_from_cte}")
```

The outer SQL JOINs on `nodes`, `rdf_labels`, `rdf_props` for node metadata remain unchanged — they JOIN off the CTE's `o` column exactly as they did off `rdf_edges.o_id`.

**Important constraint**: The translator MUST detect when the source node IS bound (has an `id` property filter or a variable already in scope) to decide whether to pass the source ID or empty string to `MatchEdges`. When unbound, `MatchEdges("")` performs the full scan.

### `engine.py` — `create_edge` change

After the existing `cursor.execute(INSERT INTO rdf_edges ...)` + `self.conn.commit()`:

```python
# Synchronous ^KG write via Native API
try:
    iris_obj = self._iris_obj()
    iris_obj.classMethodVoid(
        "Graph.KG.TemporalIndex", "WriteAdjacency",
        source_id, predicate, target_id, str(weight or 1.0)
    )
except Exception as e:
    logger.warning(f"^KG adjacency write failed (will be recovered by BuildKG): {e}")
```

This calls a new lightweight ObjectScript method `WriteAdjacency(s, p, o, w)` that only does:
```objectscript
Set ^KG("out", 0, s, p, o) = +w
Set ^KG("in",  0, o, p, s) = +w
```

Alternatively, the two `Set` calls can live in `EdgeScan.cls` as `InsertAdjacency`. Either way: no transaction involvement, no `LOCK` needed for single-node (IRIS globals are process-safe for non-conflicting subscripts).

### `engine.py` — `delete_edge` change

After the existing DELETE from `rdf_edges`:
```python
iris_obj.classMethodVoid("Graph.KG.EdgeScan", "DeleteAdjacency", source_id, predicate, target_id)
```

## PR-B Design

### Global subscript migration

Every `$Order(^KG("out", s, ...))` reference in ObjectScript becomes `$Order(^KG("out", 0, s, ...))`. Mechanical find-and-replace across:
- `Traversal.cls`: `BFSFast`, `BFSFastJson`, `ShortestPathJson`, `BuildNKG`, `BuildKG`
- `TemporalIndex.cls`: `InsertEdge` — already writes correct slot if implemented in PR-A; verify
- `NKGAccel.cls`: `BuildNKG` reads
- `BenchSeeder.cls`: seeding writes

### `BuildKG` migration

Add migration step at top of `BuildKG()`:
```objectscript
// Migrate old layout ^KG("out", s, p, o) → ^KG("out", 0, s, p, o)
Set s = ""
For { Set s = $Order(^KG("out", s)) Quit:s=""
      If $Data(^KG("out", 0, s)) Continue  // already migrated
      Merge ^KG("out", 0, s) = ^KG("out", s)
      Kill ^KG("out", s) }
// Same for ^KG("in", ...)
```

This is idempotent — if `^KG("out", 0, s)` already exists, skip.

## Reuse Map

| Component | Reused from |
|-----------|-------------|
| `JSON_TABLE` CTE pattern | `_translate_bm25_search`, `_translate_ivf_search` (identical pattern) |
| `SqlProc` ClassMethod | `Graph.KG.IVFIndex.SearchProc`, `BM25Index.SearchProc` |
| `classMethodVoid` write pattern | `create_edge_temporal` → `TemporalIndex.InsertEdge` |
| Process-private global cleanup | `ShortestPathJson` `^||SP.*` pattern |

## Complexity Tracking

| Risk | Mitigation |
|------|-----------|
| Translator CTE injection breaks complex MATCH (WITH clauses, subqueries) | Gate: run all 492 unit tests; add explicit tests for multi-hop MATCH + temporal MATCH |
| Unbound-source `MatchEdges` full scan at 535M edges | `MatchEdges` passes sourceId from bound aliases; only truly unbound (schema exploration) queries pay full cost — same as current SQL full scan |
| PR-B shard subscript migration on live data | `BuildKG` migration is idempotent; can run in background without downtime |
| `^KG` write failure in `create_edge` swallows error silently | Warning logged; `BuildKG` is always the recovery path |

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
