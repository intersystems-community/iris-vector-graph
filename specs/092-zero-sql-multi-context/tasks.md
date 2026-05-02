# Tasks: 092 — Zero-SQL Multi-Context IVG API (v1.81.0)

**Branch**: `092-zero-sql-multi-context`
**Plan**: `specs/092-zero-sql-multi-context/plan.md`
**Spec**: `specs/092-zero-sql-multi-context/spec.md`

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel with other [P] tasks in same phase
- **[Story]**: User story group (A=embedded fixes, B=collection errors, C=introspection API, D=embed_nodes, E=CypherEngine, F=exec context tests, G=release)

---

## Phase 1: Setup (verify working tree state)

- [ ] T001 Verify embedded.py changes are in working tree (git status confirms modified files)
- [ ] T002 Confirm 26/26 tests/unit/test_embedded.py pass with current working tree changes

---

## Phase 2: Foundational — Embedded Path Fixes

**Goal**: Clean baseline — embedded tests all green, no import order bugs.

- [ ] T003 [A] Fix _ensure_embedded_iris_first in iris_vector_graph/embedded.py — iterate [mgr_path, embedded_path] so lib/python lands at sys.path[0]
- [ ] T004 [A] Fix _require_iris_sql in iris_vector_graph/embedded.py — wrap _ensure_embedded_iris_first() call inside the existing try/except ImportError (single try block covers full chain)
- [ ] T005 [A] Run pytest tests/unit/test_embedded.py — verify 26/26 passing

---

## Phase 3: Test Collection Fixes [Group B]

**Goal**: `pytest tests/` runs with 0 collection errors in base py312 env (no strawberry, no pandas).
**Independent test**: `pytest tests/ --collect-only 2>&1 | grep -c ERROR` returns 0.

- [ ] T006 [P] [B] Add `pytest.importorskip("strawberry")` guard to tests/e2e/test_gql_traversal.py (line 2, before other imports)
- [ ] T007 [P] [B] Add `pytest.importorskip("pandas")` guard to tests/python/test_networkx_loader.py (line after shebang/docstring, before imports)
- [ ] T008 [P] [B] Add `pytest.importorskip("pandas")` guard to tests/python/test_python_sdk.py (line after shebang/docstring, before imports)
- [ ] T009 [B] Run `pytest tests/ --collect-only 2>&1 | grep ERROR` — confirm 0 collection errors

---

## Phase 4: Python-First Introspection API [Group C]

**Goal**: 7 new methods on IRISGraphEngine work identically via DBAPI and EmbeddedConnection.
**Independent test**: `pytest tests/unit/test_introspection_api.py tests/e2e/test_introspection_api.py` all green.

- [ ] T010 [C] Write failing unit tests in tests/unit/test_introspection_api.py covering: get_labels, get_relationship_types, get_node_count (with/without label), get_edge_count (with/without predicate), get_label_distribution, get_property_keys (with/without label), node_exists (true/false)
- [ ] T011 [C] Write failing e2e tests in tests/e2e/test_introspection_api.py against gqs-ivg-test container using iris-devtester
- [ ] T012 [C] Implement get_labels(self) -> List[str] in iris_vector_graph/engine.py
- [ ] T013 [C] Implement get_relationship_types(self) -> List[str] in iris_vector_graph/engine.py
- [ ] T014 [C] Implement get_node_count(self, label=None) -> int in iris_vector_graph/engine.py
- [ ] T015 [C] Implement get_edge_count(self, predicate=None) -> int in iris_vector_graph/engine.py
- [ ] T016 [C] Implement get_label_distribution(self) -> Dict[str, int] in iris_vector_graph/engine.py
- [ ] T017 [C] Implement get_property_keys(self, label=None) -> List[str] in iris_vector_graph/engine.py
- [ ] T018 [C] Implement node_exists(self, node_id: str) -> bool in iris_vector_graph/engine.py
- [ ] T019 [C] Export all 7 new methods from iris_vector_graph/__init__.py (or confirm auto-exported via IRISGraphEngine)
- [ ] T020 [C] Run pytest tests/unit/test_introspection_api.py tests/e2e/test_introspection_api.py — all green

---

## Phase 5: embed_nodes Typed Params [Group D]

**Goal**: `embed_nodes(label="Gene")` works; `embed_nodes(where="...")` still works with DeprecationWarning.
**Independent test**: `pytest tests/unit/test_embed_nodes_params.py` all green.

- [ ] T021 [D] Write failing tests in tests/unit/test_embed_nodes_params.py: label= filters to matching nodes, predicate= filters by edge predicate, node_ids= embeds specific nodes, where= still works + emits DeprecationWarning
- [ ] T022 [D] Add label=, predicate=, node_ids= params to embed_nodes() in iris_vector_graph/engine.py; translate to WHERE clause internally; keep where= as deprecated alias with warnings.warn(DeprecationWarning)
- [ ] T023 [D] Run pytest tests/unit/test_embed_nodes_params.py — all green

---

## Phase 6: IVG.CypherEngine ObjectScript Class [Group E]

**Goal**: ObjectScript developers can instantiate IVG.CypherEngine.Local() and run Cypher queries.
**Independent test**: `docker exec iris-enterprise-2026 iris session IRIS -U USER "Do ##class(IVG.CypherEngine).SmokeTest()"` exits 0.

- [ ] T024 [E] Write ObjectScript smoke test method IVG.CypherEngine.SmokeTest() that exercises Local().Query(), Local().GetLabels(), Local().NodeExists() and writes/reads results
- [ ] T025 [E] Implement IVG.CypherEngine.cls in iris_src/src/IVG/CypherEngine.cls:
      - Properties: _ConnMode, _ConnHost, _ConnPort, _ConnNS, _ConnUser, _ConnPass, _EmbedDim, _PyEngine
      - ClassMethod Local(dim=768) — sets _ConnMode="embedded"
      - ClassMethod Remote(host,port,ns,user,pass,dim=768) — sets _ConnMode="remote" + connection params
      - Method _EnsureEngine() — lazy-init _PyEngine via %SYS.Python; Local uses EmbeddedConnection, Remote uses iris.connect()
      - Method Query(cypher, params="") As %DynamicObject — calls _PyEngine.execute_cypher(); returns {columns, rows, error}
      - Method GetLabels() As %DynamicArray — calls _PyEngine.get_labels()
      - Method GetNodeCount(label="") As %Integer — calls _PyEngine.get_node_count(label)
      - Method NodeExists(nodeId) As %Boolean — calls _PyEngine.node_exists(nodeId)
      - Method InitSchema() As %DynamicObject — calls _PyEngine.initialize_schema()
      - Method RebuildKG() As %Status — calls _PyEngine.build_graph_globals()
      - Method SmokeTest() [for T024]
- [ ] T026 [E] Compile IVG.CypherEngine.cls on iris-enterprise-2026 container (port 4972) via iris-dev MCP or docker exec
- [ ] T027 [E] Run smoke test via docker exec — verify Local().Query("MATCH (n) RETURN count(n) AS cnt") returns {columns:["cnt"], rows:[[0]]}

---

## Phase 7: Execution Context Test Suite [Group F]

**Goal**: Single test file proves all 3 execution contexts work identically.
**Independent test**: `pytest tests/test_execution_contexts.py` all green.

- [ ] T028 [F] Write tests/test_execution_contexts.py with 3 test classes:
      - TestExternalDBAPI: connect to gqs-ivg-test:1972, test get_labels/get_node_count/node_exists/execute_cypher
      - TestEmbeddedConnection: unit-mock iris.sql, verify EmbeddedConnection produces correct cursor behavior, columns never empty
      - TestObjectScriptCypherEngine: docker exec smoke test of IVG.CypherEngine.Local() on enterprise container (skipped if container not running)
- [ ] T029 [F] Run pytest tests/test_execution_contexts.py — all green

---

## Phase 8: Full Regression + Release [Group G]

**Goal**: v1.81.0 published to PyPI, all tests green, GitHub updated.

- [ ] T030 [G] Run full test suite: `pytest tests/ --ignore=tests/python` — 0 failures
- [ ] T031 [G] Bump version in pyproject.toml from 1.80.5 to 1.81.0
- [ ] T032 [G] Add v1.81.0 changelog entry to README.md covering: embedded path fix, test collection fix, 7 introspection methods, embed_nodes typed params, IVG.CypherEngine ObjectScript class, execution context test suite
- [ ] T033 [G] Run `python -m build` — dist/ contains .whl and .tar.gz
- [ ] T034 [G] Run `twine upload dist/*` — v1.81.0 live on PyPI
- [ ] T035 [G] Run `git add -A && git commit -m "feat: v1.81.0 — IVG.CypherEngine, zero-SQL introspection API, embedded fixes (#092)"` then `git push origin 092-zero-sql-multi-context`

---

## Dependencies

```
Phase 1 (T001-T002)
  → Phase 2 (T003-T005): embedded fixes
    → Phase 3 (T006-T009) [P with Phase 2]: collection fixes
      → Phase 4 (T010-T020): introspection API (needs baseline green)
        → Phase 5 (T021-T023) [P with Phase 4]: embed_nodes params
        → Phase 6 (T024-T027) [P with Phase 4]: CypherEngine (needs Phase 4 methods)
          → Phase 7 (T028-T029): execution context suite (needs Phase 6)
            → Phase 8 (T030-T035): release
```

## Parallel Opportunities

- T006, T007, T008 — can all be edited simultaneously (different files)
- T012-T018 — can be implemented in parallel (different methods, same file section)
- T021-T022 (Group D) can run in parallel with T024-T025 (Group E) after Phase 3 is done
