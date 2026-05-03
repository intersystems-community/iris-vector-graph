# Spec 092: Zero-SQL Multi-Context IVG API

## Goal

IVG consumers should need **zero SQL** across **all three execution contexts**:

1. **External DBAPI** — Python outside IRIS process (`iris.connect()`)
2. **Embedded irispython** — Python inside IRIS process (`EmbeddedConnection`)
3. **ObjectScript** — `.cls` files calling IVG via `IVG.CypherEngine`

All three must present an **identical API surface** that works without Cypher knowledge or SQL.

## Clarifications

### Session 2026-05-02

- Q: ObjectScript class name → A: `IVG.CypherEngine` (matches `IVG.Percentile` namespace, avoids collision with customer `Graph.*` namespaces)
- Q: Version target for all phases → A: v1.81.0 (all phases in one release, not split across v1.82/83)
- Q: `embed_nodes(where=SQL)` backward compat → A: Keep `where=` as deprecated alias, add `label=`, `predicate=`, `node_ids=` typed params
- Q: Include `node_exists()` → A: Yes, include in Phase 3 Python-first API

### Open Question: EmbeddedConnection naming (deferred)

IRIS has TWO server-side Python contexts, both currently served by `EmbeddedConnection`:
1. `Language=python` ObjectScript method — Python dispatched *inside* an ObjectScript method call (same transaction, same process)
2. `irispython` executable — IRIS-shipped Python interpreter, separate process, native bridge via shared memory

Both have `hasattr(iris, 'sql') == True` (native `iris` module, not pip `intersystems-irispython`).
Both are different from "embedded database" (SQLite/DuckDB model — no server).

Candidate rename: `ServerSideConnection` or `InProcessConnection`.
RULED OUT: `IRISNativeConnection` / `irisnative` — already established as the Native API (globals, gref, classMethodValue) from Aleks Djaković's group. Cannot reuse.
Decision deferred — rename does not affect v1.81.0 correctness, only docs/marketing.

## Market Context

### Neo4j
- **No embedded Python execution context** — all queries go through Bolt protocol
- **No Python-first introspection API** — everything requires `session.run("CALL db.labels()")`
- Consumers must know Cypher even for basic introspection

### Memgraph
- Has `@mgp.read_proc` embedded Python UDFs (equivalent to IRIS irispython)
- But still no Python-first API — all introspection is Cypher procedures

### FalkorDB
- No embedded Python — consistent Cypher-only via Redis protocol
- Still no Python-first API

### IVG Opportunity
IVG is unique: ObjectScript IS the server language, and IRIS has a real embedded Python bridge. **IVG can offer what no competitor has**: a single Python API that works identically in all three contexts AND provides Python-native methods that don't require Cypher.

## Current State

### Working ✅
- External DBAPI: `IRISGraphEngine(conn).execute_cypher(q)` — works
- `CALL db.*` procedures — fixed in v1.80.5 via `_SYSTEM_PROCEDURE_NAMESPACES` pass-through
- `SHOW DATABASES / SHOW PROCEDURES / SHOW FUNCTIONS` — works

### Broken / Missing ❌
1. **Embedded irispython sys.path ordering**: `lib/python` must precede `mgr/python`; `_require_iris_sql` must wrap full call chain — fix in progress
2. **Test collection errors**: `strawberry` / `pandas` not installed → `importorskip` guards needed
3. **Python-first introspection API**: `engine.get_labels()`, `engine.get_node_count()` etc. — don't exist
4. **`IVG.CypherEngine` ObjectScript class** — doesn't exist yet
5. **`embed_nodes(where=SQL)`** exposes SQL fragment — replace with typed `label=`, `predicate=`, `node_ids=` params (keep `where=` as deprecated alias)
6. **Execution context test suite** — `tests/test_execution_contexts.py` incomplete/segfaulting

## Acceptance Criteria

### Context 1: External DBAPI
```python
engine = IRISGraphEngine.from_connect('localhost', 1972, 'USER', '_SYSTEM', 'SYS', 768)
status = engine.initialize_schema()
engine.create_node("n1", labels=["Person"], props={"name": "Alice"})
engine.create_edge("n1", "KNOWS", "n2")
labels = engine.get_labels()               # List[str]
counts = engine.get_label_distribution()   # Dict[str, int]
n = engine.get_node_count("Person")        # int
exists = engine.node_exists("n1")          # bool
keys = engine.get_property_keys("Person")  # List[str]
r = engine.execute_cypher("CALL db.labels() YIELD label RETURN label")
```

### Context 2: Embedded irispython (inside IRIS Language=python method)
```python
from iris_vector_graph.embedded import EmbeddedConnection
from iris_vector_graph.engine import IRISGraphEngine
engine = IRISGraphEngine(EmbeddedConnection(), embedding_dimension=768)
r = engine.execute_cypher("MATCH (n:Person) RETURN n.name AS name")
assert r["columns"] == ["name"]   # never empty even in embedded context
labels = engine.get_labels()      # identical API to external
```

### Context 3: ObjectScript via `IVG.CypherEngine`
```objectscript
Set engine = ##class(IVG.CypherEngine).Local()
Set r = engine.Query("MATCH (n:Person) RETURN n.name LIMIT 5")
// r.columns -> %DynamicArray, r.rows -> %DynamicArray of %DynamicArray
Set labels = engine.GetLabels()           // %DynamicArray of strings
Set count = engine.GetNodeCount("Person") // integer
Set exists = engine.NodeExists("n1")      // 1 or 0

// Remote IRIS:
Set eng2 = ##class(IVG.CypherEngine).Remote("other-host", 1972, "USER", "_SYSTEM", "SYS")
Set r2 = eng2.Query("MATCH (n) RETURN count(n) AS cnt")
```

## Functional Requirements

- **FR-001**: `_ensure_embedded_iris_first()` MUST insert `/usr/irissys/lib/python` at index 0 (ahead of `/usr/irissys/mgr/python`)
- **FR-002**: `_require_iris_sql()` MUST wrap the full call chain (including `_ensure_embedded_iris_first`) in a single `try/except ImportError` so any import failure produces the IVG error message
- **FR-003**: Test files with optional deps (`strawberry`, `pandas`) MUST use `pytest.importorskip` so collection never fails in base env
- **FR-004**: `IRISGraphEngine` MUST expose: `get_labels()`, `get_relationship_types()`, `get_node_count(label=None)`, `get_edge_count(predicate=None)`, `get_label_distribution()`, `get_property_keys(label=None)`, `node_exists(node_id)`
- **FR-005**: All FR-004 methods MUST work identically via external DBAPI and `EmbeddedConnection`
- **FR-006**: `embed_nodes()` MUST accept `label=`, `predicate=`, `node_ids=` typed params; `where=` SQL param MUST still work but emit a `DeprecationWarning`
- **FR-007**: `IVG.CypherEngine` ObjectScript class MUST provide `Local()`, `Remote(host, port, ns, user, pass)`, `Query(cypher)`, `InitSchema()`, `RebuildKG()`, `GetLabels()`, `GetNodeCount(label)`, `NodeExists(nodeId)`
- **FR-008**: `IVG.CypherEngine.Query()` MUST return a `%DynamicObject` with `columns` (`%DynamicArray`) and `rows` (`%DynamicArray` of `%DynamicArray`), and `error` (empty string on success)
- **FR-009**: `tests/test_execution_contexts.py` MUST cover: external DBAPI, `EmbeddedConnection` (unit-mocked), and ObjectScript `IVG.CypherEngine` smoke test against live container
- **FR-010**: `IVG.CypherEngine.cls` MUST be deployed to and compile cleanly on the enterprise 2026.1 container (port 4972)

## Success Criteria

- **SC-001**: `pytest tests/unit/test_embedded.py` — 26/26 passing (0 failures)
- **SC-002**: `pytest tests/` (base env, no strawberry/pandas) — 0 collection errors, all previously passing tests still pass
- **SC-003**: All FR-004 methods return correct values against live `gqs-ivg-test` container
- **SC-004**: `IVG.CypherEngine.Local().Query("MATCH (n) RETURN count(n) AS cnt")` executes without error from ObjectScript terminal on enterprise container
- **SC-005**: `embed_nodes(label="Gene")` works; `embed_nodes(where="...")` still works with `DeprecationWarning`
- **SC-006**: v1.81.0 published to PyPI; all tests green; changelog updated

## Implementation Phases (all in v1.81.0)

### Phase 1: Fix embedded path tests (DONE — in working tree)
- `_ensure_embedded_iris_first`: iterate `[mgr_path, embedded_path]` so `lib/python` ends at index 0
- `_require_iris_sql`: wrap full call chain in single `try/except ImportError`
- 26/26 `test_embedded.py` passing

### Phase 2: Fix test collection errors
- `tests/e2e/test_gql_traversal.py`: add `pytest.importorskip("strawberry")`
- `tests/python/test_networkx_loader.py`: add `pytest.importorskip("pandas")`
- `tests/python/test_python_sdk.py`: add `pytest.importorskip("pandas")`

### Phase 3: Python-first introspection API
Add to `IRISGraphEngine` in `engine.py`: `get_labels`, `get_relationship_types`, `get_node_count`, `get_edge_count`, `get_label_distribution`, `get_property_keys`, `node_exists`

### Phase 4: `embed_nodes` typed params
Add `label=`, `predicate=`, `node_ids=` to `embed_nodes()`; keep `where=` with `DeprecationWarning`

### Phase 5: `IVG.CypherEngine` ObjectScript class
File: `iris_src/src/IVG/CypherEngine.cls`
- `Local(dim)` — uses `EmbeddedConnection` path via `%SYS.Python`
- `Remote(host, port, ns, user, pass, dim)` — uses `iris.connect()` via `%SYS.Python`
- `Query(cypher)` — returns `%DynamicObject {columns, rows, error}`
- `GetLabels()`, `GetNodeCount(label)`, `NodeExists(nodeId)` — thin wrappers over FR-004 methods
- `InitSchema()`, `RebuildKG()`

### Phase 6: Execution context test suite
`tests/test_execution_contexts.py`:
- External DBAPI: connect to `gqs-ivg-test:1972`, test all FR-004 methods
- Embedded (unit): mock `EmbeddedConnection`, verify same code path
- ObjectScript smoke: `docker exec` into enterprise container, run `Do ##class(IVG.CypherEngine).Local().Query(...)`

### Phase 7: Release
- Bump `pyproject.toml` to `1.81.0`
- Update `CHANGELOG` / `README`
- `python -m build && twine upload`
- `git push origin 092-zero-sql-multi-context`

## Competitive Positioning

| Feature | Neo4j | Memgraph | FalkorDB | **IVG** |
|---------|-------|----------|----------|---------|
| External DBAPI | ✅ | ✅ | ✅ | ✅ |
| Embedded execution context | ❌ | ✅ (Python UDF) | ✅ (subprocess) | ✅ (irispython) |
| ObjectScript/native language | ❌ | ❌ | ❌ | **✅ (unique)** |
| Python-first introspection API | ❌ | ❌ | ❌ | **✅ (v1.81.0)** |
| Zero SQL for consumers | ❌ | ❌ | ❌ | **✅ (v1.81.0)** |
| `db.*` procedures | ✅ | ✅ | ✅ | **✅** |
| Identical API across all contexts | ❌ | ❌ | ~✅ | **✅ (v1.81.0)** |
