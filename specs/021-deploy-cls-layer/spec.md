# Feature Specification: Deploy ObjectScript .cls Layer

**Feature Branch**: `021-deploy-cls-layer`
**Created**: 2026-02-27
**Status**: Draft
**Depends on**: 020-initialize-schema-stored-procedures (merged)

---

## Problem Statement

`iris_src/src/` contains production-quality ObjectScript classes that are **never deployed** by
`initialize_schema()`. The result: every graph operation runs a slow SQL/Python fallback instead
of the fast `^KG`-global path that the .cls layer was built for.

### The gap

| What exists in iris_src/ | What actually runs today |
|---|---|
| `Graph.KG.GraphIndex` — functional index, auto-populates `^KG` globals on every edge write | Nothing — `^KG` is always empty |
| `Graph.KG.PageRank.Run()` — embedded Python, reads `^KG` directly, ~2-5ms | Inline Python SQL function auto-deployed at runtime over dbapi, ~25ms+, full edge scan |
| `Graph.KG.Traversal.BFSFast()` — pure ObjectScript `$Order` on `^KG`, sub-ms | SQL JOIN traversal |
| `iris.vector.graph.GraphOperators` — `kg_KNN_VEC`, `kg_RRF_FUSE` as SqlProc methods | Direct SQL strings in engine.py |

### Root cause

`initialize_schema()` only executes DDL SQL via the dbapi connection. Loading `.cls` files
requires `$system.OBJ.Load()` — an ObjectScript call. There was never a bridge between
Python startup and ObjectScript class compilation.

---

## Goals

1. **Auto-deploy** `.cls` files into IRIS during `initialize_schema()` — best-effort, opt-out.
2. **Capability flags** — detect at runtime whether ObjectScript classes are compiled; route all
   graph operations to the fast path when available, Python fallback otherwise.
3. **^KG bootstrap** — after first deploy on an existing database, call `Traversal.BuildKG()`
   once to backfill `^KG` from existing `rdf_edges` rows.
4. **Wire engine.py** — PPR, BFS, neighborhood expansion all use `$CLASSMETHOD` calls through
   to the .cls layer.  Remove `_auto_deploy_ppr_sql_function()` and its inline Python blob.
5. **Package iris_src/** — include `.cls` files as package data so they're available on disk
   wherever the library is installed.

---

## Architecture (from oracle review)

### Deployment trigger
`initialize_schema(auto_deploy_objectscript=True)` — best-effort deploy, logs warning on failure.
Explicit `deploy_objectscript(conn)` also available for controlled environments.

### Detection
```sql
SELECT COUNT(*) FROM %Dictionary.ClassDefinition
WHERE Name='Graph.KG.Edge' AND CompilationStatus='c'
```
Fallback: `SELECT $CLASSMETHOD('Graph.KG.Edge','%Exists',1)`

### ^KG bootstrap
After successful deploy: if `SELECT COUNT(*) FROM Graph_KG.rdf_edges > 0` and `^KG` empty,
call `Graph.KG.Traversal.BuildKG()`. Record completion in `Graph_KG.kg_meta(key,value)`.

### PPR wiring
New `Graph.KG.PageRank.RunJson()` wrapper — calls `Run()`, reads `^||PPR.Results`, returns JSON.
Python calls: `SELECT $CLASSMETHOD('Graph.KG.PageRank','RunJson', ?, ?, ?, ?, ?)`.
Remove `_auto_deploy_ppr_sql_function()` entirely.

### BFS wiring
New `Graph.KG.Traversal.BFSFastJson()` wrapper — calls `BFSFast()`, reads `^||BFS.Results`,
returns JSON array. Python calls via `$CLASSMETHOD`.

### Capability flags (instance-level cache)
```python
@dataclass
class IRISCapabilities:
    objectscript_deployed: bool = False    # Graph.KG.Edge compiled
    kg_built: bool = False                 # ^KG populated
    graphoperators_deployed: bool = False  # iris.vector.graph.GraphOperators compiled
```
Checked once per engine instance; cached. `reset_capabilities()` for testing.

---

## User Stories

### US1 — Fresh install: graph queries use fast path (P0)

**As a developer**, `initialize_schema()` deploys `.cls` files and I get sub-5ms PPR without
any extra setup steps.

**Acceptance criteria:**
1. After `initialize_schema()` on a clean IRIS instance, `Graph.KG.Edge` `CompilationStatus='c'` in `%Dictionary.ClassDefinition`.
2. After inserting edges, `^KG("out",...)` globals are populated (functional index fires).
3. `kg_PERSONALIZED_PAGERANK(["seed"])` does NOT invoke `_kg_PERSONALIZED_PAGERANK_python_fallback`.
4. PPR completes in < 50ms on a 1K-node test graph (integration benchmark assertion).

### US2 — Existing database: ^KG backfilled (P0)

**As a developer** deploying to a database that already has 20K edges in `rdf_edges`,
`initialize_schema()` automatically calls `BuildKG()` and the next PPR/BFS call is fast.

**Acceptance criteria:**
1. After deploy with pre-existing edges, `^KG` is not empty.
2. `kg_meta` table has `key='kg_built'` row.
3. Second `initialize_schema()` call does NOT re-run `BuildKG()`.

### US3 — Graceful degradation when .cls deployment blocked (P1)

**As a developer** on a locked-down IRIS instance where class loading is forbidden,
`initialize_schema()` logs a warning and all operations fall back to Python/SQL paths —
no exception raised.

**Acceptance criteria:**
1. When `auto_deploy_objectscript=False`, no `.cls` loading is attempted.
2. When deploy fails, `capabilities.objectscript_deployed = False` and PPR uses Python fallback.
3. All existing tests still pass (no regression).

### US4 — PPR RunJson() wrapper returns correct scores (P1)

**As a developer**, `Graph.KG.PageRank.RunJson()` returns JSON matching the Python fallback
output format: `[{"id": "node_id", "score": 0.123}, ...]` sorted by score descending.

**Acceptance criteria:**
1. On a 5-node test graph with known structure, `RunJson()` top result matches the highest-degree
   node (hub node in a star topology gets highest score when seeded from spokes).
2. Output is valid JSON parseable by `json.loads()`.
3. Returns top-1000 capped results (same as existing Python fallback cap).

### US5 — BFS BFSFastJson() wrapper returns correct paths (P1)

**As a developer**, `Graph.KG.Traversal.BFSFastJson()` returns JSON edges reachable from
a source within N hops.

**Acceptance criteria:**
1. On a 2-hop chain A→B→C, `BFSFastJson("A", null, 2, "")` returns both hops.
2. Output is `[{"s":..,"p":..,"o":..,"w":..,"step":..}, ...]`.
3. `maxHops=1` returns only direct neighbors.

---

## Implementation Plan

### Step 1 — ObjectScript wrappers (new methods in existing .cls)

Add to `Graph.KG.PageRank`:
- `ClassMethod RunJson(seedJson, alpha, maxIter, bidirectional, reverseWeight) As %String`
  Calls `Run()`, reads `^||PPR.Results`, returns JSON array sorted by score desc.

Add to `Graph.KG.Traversal`:
- `ClassMethod BFSFastJson(srcId, preds, maxHops, dstLabel) As %String`
  Calls `BFSFast()`, reads `^||BFS.Results`, converts `$ListBuild` to JSON array.

Add `Graph.KG.Meta` (new class):
- Simple global-backed key/value for tracking bootstrap state.
- `ClassMethod Get(key) As %String`
- `ClassMethod Set(key, value) As %Status`

### Step 2 — `GraphSchema` deployment helpers

In `iris_vector_graph/schema.py`:
- `deploy_objectscript_classes(cursor, iris_src_path) -> Dict[str, bool]`
  Copies `.cls` files into IRIS via `$system.OBJ.Load()` using dbapi exec of embedded Python.
- `check_objectscript_classes(cursor) -> IRISCapabilities`
  Queries `%Dictionary.ClassDefinition`.
- `bootstrap_kg_global(cursor) -> bool`
  Calls `BuildKG()` and records in `kg_meta`.

### Step 3 — `initialize_schema()` changes

Add to the end of `initialize_schema()`:
```python
if auto_deploy_objectscript:
    self._capabilities = GraphSchema.deploy_and_detect(cursor, iris_src_path=...)
```

### Step 4 — `engine.py` wiring

- `kg_PERSONALIZED_PAGERANK()`: use `$CLASSMETHOD('Graph.KG.PageRank','RunJson',...)` when
  `capabilities.objectscript_deployed and capabilities.kg_built`.
- `kg_NEIGHBORHOOD_EXPANSION()`: use `$CLASSMETHOD('Graph.KG.Traversal','BFSFastJson',...)`.
- Remove `_auto_deploy_ppr_sql_function()` and inline Python blob.
- Remove `_PPR_SQL_FUNCTION_NAME`, `_ppr_sql_function_available` class vars.

### Step 5 — pyproject.toml package data

```toml
[tool.hatch.build.targets.wheel]
packages = ["iris_vector_graph"]
include = ["iris_src/src/**/*.cls"]
```

---

## Test Plan

### Unit tests (`tests/unit/test_cls_deployment.py`)
- `test_check_capabilities_returns_false_when_not_deployed` — mock cursor returning 0 rows
- `test_deploy_objectscript_classes_idempotent` — second call doesn't error on already-compiled
- `test_iris_capabilities_dataclass` — default values, repr

### Integration tests (`tests/integration/test_cls_layer.py`)
- `test_objectscript_classes_deployed_after_initialize_schema`
- `test_kg_global_populated_after_insert`
- `test_ppr_uses_cls_fast_path`
- `test_ppr_results_match_python_fallback`
- `test_bfs_fast_json_2hop`
- `test_bfs_fast_json_maxhops_1`
- `test_bootstrap_kg_called_once`
- `test_bootstrap_not_repeated_on_reinit`
- `test_fallback_when_objectscript_not_available`

---

## Files Changed

```
iris_src/src/Graph/KG/PageRank.cls          # + RunJson() ClassMethod
iris_src/src/Graph/KG/Traversal.cls         # + BFSFastJson() ClassMethod
iris_src/src/Graph/KG/Meta.cls              # NEW — bootstrap state tracking
iris_vector_graph/schema.py                 # + deploy_objectscript_classes(), check_objectscript_classes(), bootstrap_kg_global()
iris_vector_graph/engine.py                 # initialize_schema() auto-deploy; PPR/BFS wiring; remove _auto_deploy_ppr_sql_function()
iris_vector_graph/__init__.py               # export IRISCapabilities
pyproject.toml                              # iris_src/ as package data
tests/unit/test_cls_deployment.py           # NEW
tests/integration/test_cls_layer.py         # NEW
```

---

## Non-Goals

- Replacing `Graph.KG.Edge` as the SQL table backing `rdf_edges` — that's a migration, not this feature.
- The REST `Graph.KG.Service` layer — not called by `engine.py`.
- `Graph.KG.Benchmark.cls` — not part of the deployed library surface.
