# Implementation Plan: 092 — Zero-SQL Multi-Context IVG API (v1.81.0)

**Branch**: `092-zero-sql-multi-context`
**Spec**: `specs/092-zero-sql-multi-context/spec.md`
**Target version**: `1.81.0`

## Technical Context

| Item | Value | Source |
|------|-------|--------|
| Primary test container | `gqs-ivg-test` | `docker-compose.yml` |
| Primary IRIS port | `1972` | `docker-compose.yml` |
| Enterprise container | `iris-enterprise-2026` | `docker ps` |
| Enterprise port | `4972` | verified |
| Schema prefix | `Graph_KG` | `engine.py` |
| Package name | `iris-vector-graph` | `pyproject.toml` |
| Current version | `1.80.5` | `pyproject.toml` |
| Target version | `1.81.0` | spec clarification |
| ObjectScript class | `IVG.CypherEngine` | spec clarification |
| ObjectScript file | `iris_src/src/IVG/CypherEngine.cls` | existing partial |

## Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Library-First | PASS | All changes in iris_vector_graph/ and iris_src/ |
| II. Compatibility-First | PASS | embed_nodes(where=) kept as deprecated alias |
| III. Test-First | PASS | Tests written before implementation in each group |
| IV. IRIS E2E | PASS | E2E tests use gqs-ivg-test via iris-devtester |
| V. Simplicity | PASS | IVG.CypherEngine is a thin %SYS.Python wrapper |
| VI. Grounding | PASS | All container names/ports verified above |

## Phase 0: Research — COMPLETE

All decisions resolved in spec clarifications. No unknowns.

## Phase 1: Design

### Data Model (no schema changes)

All new methods query existing tables:
- get_labels() -> Graph_KG.rdf_labels DISTINCT label
- get_relationship_types() -> Graph_KG.rdf_edges DISTINCT p
- get_node_count(label) -> rdf_labels WHERE label=? or nodes COUNT
- get_edge_count(predicate) -> rdf_edges WHERE p=? or COUNT
- get_label_distribution() -> rdf_labels GROUP BY label
- get_property_keys(label) -> rdf_props JOIN rdf_labels
- node_exists(node_id) -> nodes WHERE node_id=? FETCH FIRST 1

### API Contracts

Python additions to IRISGraphEngine:
  get_labels() -> List[str]
  get_relationship_types() -> List[str]
  get_node_count(label=None) -> int
  get_edge_count(predicate=None) -> int
  get_label_distribution() -> Dict[str, int]
  get_property_keys(label=None) -> List[str]
  node_exists(node_id) -> bool

embed_nodes new params (additive, backward compat):
  label=None, predicate=None, node_ids=None
  where= kept with DeprecationWarning

ObjectScript IVG.CypherEngine:
  ClassMethod Local(dim=768) As IVG.CypherEngine
  ClassMethod Remote(host,port,ns,user,pass,dim=768) As IVG.CypherEngine
  Method Query(cypher, params="") As %DynamicObject  -> {columns, rows, error}
  Method GetLabels() As %DynamicArray
  Method GetNodeCount(label="") As %Integer
  Method NodeExists(nodeId) As %Boolean
  Method InitSchema() As %DynamicObject
  Method RebuildKG() As %Status

## Phase 2: Task Groups

### Group A — Embedded path fixes (in working tree)
A1: _ensure_embedded_iris_first iterates [mgr_path, embedded_path] -> lib/python at index 0
A2: _require_iris_sql wraps full call chain in single try/except ImportError
A3: 26/26 test_embedded.py passing

### Group B — Test collection fixes
B1: tests/e2e/test_gql_traversal.py — pytest.importorskip("strawberry")
B2: tests/python/test_networkx_loader.py — pytest.importorskip("pandas")
B3: tests/python/test_python_sdk.py — pytest.importorskip("pandas")
B4: pytest tests/ -> 0 collection errors

### Group C — Python-first introspection API (Test-First)
C1: Write failing tests in tests/unit/test_introspection_api.py
C2: Write failing e2e tests in tests/e2e/test_introspection_api.py
C3: Implement all 7 methods in engine.py
C4: All tests green

### Group D — embed_nodes typed params (Test-First)
D1: Write failing tests for label=, predicate=, node_ids= params
D2: Write failing test that where= still works with DeprecationWarning
D3: Implement in engine.py
D4: All tests green

### Group E — IVG.CypherEngine ObjectScript class (Test-First)
E1: Write smoke test script iris_src/tests/test_cypher_engine.script
E2: Implement IVG.CypherEngine.cls
E3: Compile on iris-enterprise-2026 (port 4972)
E4: Run smoke test via docker exec — Local().Query() returns correct result

### Group F — Execution context test suite
F1: Write tests/test_execution_contexts.py
    - DBAPI: all FR-004 methods against gqs-ivg-test
    - Embedded unit mock: EmbeddedConnection mock
    - ObjectScript smoke: docker exec into enterprise
F2: All tests green

### Group G — Release
G1: Full pytest suite green
G2: Bump pyproject.toml to 1.81.0
G3: Update README.md changelog
G4: python -m build && twine upload
G5: git commit + push
