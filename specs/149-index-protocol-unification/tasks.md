# Tasks: Index Protocol Unification

**Branch**: `149-index-protocol-unification`
**Spec**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md)

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to
- Execution order: A (protocol) → C (PLAID tests, test-first) → B (PLAID impl) → D (registry+engine.index) → E (type keys)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Verify environment and create new file stubs before implementation begins.

- [ ] T001 Verify container name `iris_vector_graph` in `docker-compose.yml` matches all test fixtures
- [ ] T002 Verify `iris-devtester` resolves via `IRISContainer.attach("iris_vector_graph")`
- [ ] T003 [P] Create empty `iris_vector_graph/index_protocol.py`
- [ ] T004 [P] Create empty `tests/e2e/test_plaid.py`
- [ ] T005 [P] Create empty `tests/e2e/test_index_protocol.py`

**Checkpoint**: Files stubbed, environment verified.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: `IVGIndex` Protocol and `IndexHandle` — pure Python, no IRIS dependency.
All user story phases depend on this.

- [ ] T006 [US1] Implement `IVGIndex` `@runtime_checkable` Protocol in `iris_vector_graph/index_protocol.py` with methods: `search(query, k, **kwargs) -> list`, `insert(id, vector) -> None`, `drop() -> None`, `info() -> dict`
- [ ] T007 [US1] Implement `IndexHandle` dataclass in `iris_vector_graph/index_protocol.py` with fields `name: str`, `type: str`, `_engine: Any` and dispatch methods that call the corresponding `*_search`, `*_insert`, `*_drop`, `*_info` engine methods
- [ ] T008 [US1] Export `IVGIndex` and `IndexHandle` from `iris_vector_graph/__init__.py`
- [ ] T009 [US1] Write unit tests for `IndexHandle` dispatch with a mock engine in `tests/unit/test_index_handle.py` — verify each method dispatches to the correct engine method for all 4 index types

**Checkpoint**: `IVGIndex` importable, `IndexHandle` dispatchable without IRIS. Unit tests pass.

---

## Phase 3: User Story 4 — PLAID e2e test coverage (P2, test-first) 🎯

**Goal**: PLAID has passing e2e tests before any PLAID refactor begins (test-first per Principle III).

**Independent Test**: `pytest tests/e2e/test_plaid.py` passes once PLAID refactor lands.

> **WRITE THESE TESTS FIRST — they should FAIL until Phase 4 is complete.**

- [ ] T010 [US4] Write e2e test `test_plaid_build_and_search` in `tests/e2e/test_plaid.py` — build PLAID index from synthetic 32-dim token embeddings (4 docs, 8 tokens each), assert `plaid_info["indexed"] == 4`
- [ ] T011 [US4] Write e2e test `test_plaid_search_returns_ranked_results` in `tests/e2e/test_plaid.py` — search with query tokens, assert top result has highest MaxSim score, k results returned
- [ ] T012 [US4] Write e2e test `test_plaid_insert_appears_in_search` in `tests/e2e/test_plaid.py` — insert new doc, assert `info["indexed"]` increments and doc appears in search
- [ ] T013 [US4] Write e2e test `test_plaid_drop_removes_all_data` in `tests/e2e/test_plaid.py` — drop index, assert `plaid_info` returns `{}`
- [ ] T014 [US4] Confirm all 4 PLAID tests currently FAIL (no `PLAIDSearch.Build` exists yet)

**Checkpoint**: 4 failing PLAID tests committed.

---

## Phase 4: User Story 2 — PLAID method renames (P1)

**Goal**: `PLAIDSearch.cls` has `Build` public method; internal helpers become Private. `plaid_build` calls `PLAIDSearch.Build`.

**Independent Test**: `pytest tests/e2e/test_plaid.py` passes 4/4.

- [ ] T015 [US2] Add `Build(name, docs_json, n_clusters, dim)` ClassMethod to `iris_src/src/Graph/KG/PLAIDSearch.cls` — calls `StoreCentroids`, `StoreDocTokensBatch`, `BuildInvertedIndex` in sequence, returns `Info(name)` JSON
- [ ] T016 [US2] Mark `StoreCentroids`, `StoreDocTokens`, `StoreDocTokensBatch`, `BuildInvertedIndex`, `JsonToVector` as `[ Private ]` in `PLAIDSearch.cls`
- [ ] T017 [US2] Compile `PLAIDSearch.cls` on containers `gqs-ivg-test`, `iris-community-2026`, `iris-enterprise-2026` — confirm no compile errors
- [ ] T018 [US2] Update `plaid_build` in `iris_vector_graph/engine.py` to call `PLAIDSearch.Build` (single call) instead of `StoreCentroids` + `BuildInvertedIndex` sequence
- [ ] T019 [US2] Verify `pytest tests/e2e/test_plaid.py` now passes 4/4

**Checkpoint**: PLAID lifecycle works end-to-end, `StoreCentroids`/`BuildInvertedIndex` no longer in public error messages.

---

## Phase 5: User Story 5 — `*_info` methods include `"type"` key (P2)

**Goal**: All `*_info` methods return `{"type": "...", ...}` — enables `IndexHandle.info()` to identify type.

**Independent Test**: Each `*_info` call returns dict with `"type"` key.

- [ ] T020 [P] [US3] Update `ivf_info` in `iris_vector_graph/engine.py` to include `"type": "ivf"` in returned dict
- [ ] T021 [P] [US3] Update `bm25_info` in `iris_vector_graph/engine.py` to include `"type": "bm25"` in returned dict
- [ ] T022 [P] [US3] Update `vec_info` in `iris_vector_graph/engine.py` to include `"type": "vec"` in returned dict
- [ ] T023 [US3] Update `plaid_info` in `iris_vector_graph/engine.py` to include `"type": "plaid"`, `"indexed"`, `"dim"`, `"nlist"` in returned dict
- [ ] T024 [US3] Verify existing tests that call `*_info` still pass (additive change only)

**Checkpoint**: All 4 index types return `"type"` key from `info()`.

---

## Phase 6: User Story 1 — `engine.index()` + IndexRegistry (P1)

**Goal**: `engine.index(name)` works for all 4 index types, registry auto-populated on init.

**Independent Test**: `pytest tests/e2e/test_index_protocol.py` passes.

> **WRITE TESTS FIRST — they should FAIL until implementation is complete.**

- [ ] T025 [US1] Write e2e test `test_engine_index_ivf_dispatch` in `tests/e2e/test_index_protocol.py` — build IVF index, call `engine.index(name).search(vec, k=3)`, assert results match `ivf_search`
- [ ] T026 [US1] Write e2e test `test_engine_index_bm25_dispatch` in `tests/e2e/test_index_protocol.py` — build BM25 index, call `engine.index(name).search("query", k=3)`, assert results match `bm25_search`
- [ ] T027 [US1] Write e2e test `test_engine_index_registry_persists_across_reconnect` in `tests/e2e/test_index_protocol.py` — build IVF index, create new `IRISGraphEngine(conn)`, call `engine.index(name)` without rebuilding, assert works
- [ ] T028 [US1] Write e2e test `test_engine_index_raises_for_unknown_name` in `tests/e2e/test_index_protocol.py` — call `engine.index("nonexistent")`, assert `ValueError` raised
- [ ] T029 [US1] Implement `_build_index_registry()` in `iris_vector_graph/engine.py` — probe `^IVF`, `^VecIdx`, `^BM25Idx`, `^PLAID` globals via `$Order` ObjectScript calls, return `{name: type_str}` dict
- [ ] T030 [US1] Call `_build_index_registry()` at end of `IRISGraphEngine.__init__`, store result in `self._index_registry`
- [ ] T031 [US1] Update `ivf_build`, `bm25_build`, `vec_create_index`, `plaid_build` in `iris_vector_graph/engine.py` to register name in `self._index_registry` after successful build
- [ ] T032 [US1] Implement `engine.index(name) -> IndexHandle` in `iris_vector_graph/engine.py` — raises `ValueError` if name not in `_index_registry`, returns `IndexHandle(name, type, self)`
- [ ] T033 [US1] Verify `pytest tests/e2e/test_index_protocol.py` passes all 4 tests

**Checkpoint**: `engine.index(name)` works for all registered index types, registry survives reconnect.

---

## Phase 7: End-to-End Validation (Constitution Principle IV — Non-Optional)

**Purpose**: Full acceptance criteria pass against live `iris_vector_graph` container.

- [ ] T034 [US1] Run `pytest tests/e2e/test_index_protocol.py` — all 4 tests pass
- [ ] T035 [US2] Run `pytest tests/e2e/test_plaid.py` — all 4 tests pass
- [ ] T036 Run `pytest tests/unit/test_index_handle.py` — all unit tests pass
- [ ] T037 [P] Run full regression: `pytest tests/unit/ tests/e2e/ -q --tb=short` — zero regressions vs pre-feature baseline
- [ ] T038 Verify `from iris_vector_graph import IVGIndex, IndexHandle` works and `isinstance(handle, IVGIndex)` returns `True`

**Checkpoint**: All acceptance scenarios from spec.md pass. Zero regressions.

---

## Phase 8: Polish

- [ ] T039 Update `ENGINEERING_DEBT.md` — mark Spec 105 Index Protocol Unification complete
- [ ] T040 [P] Update `docs/python/PYTHON_SDK.md` deprecation notice to list `engine.index()` as new unified API
- [ ] T041 Bump version to `1.84.0` in `pyproject.toml` and publish

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately
- **Phase 2 (Foundational)**: Depends on Phase 1 — blocks all user story phases
- **Phase 3 (PLAID tests, test-first)**: Depends on Phase 2 — tests written before Phase 4 implementation
- **Phase 4 (PLAID impl)**: Depends on Phase 3 (failing tests must exist first)
- **Phase 5 (`type` keys)**: Depends on Phase 2, can run in parallel with Phase 3/4
- **Phase 6 (`engine.index()`)**: Depends on Phase 2 + Phase 5 (needs `type` keys for dispatch)
- **Phase 7 (e2e validation)**: Depends on all implementation phases
- **Phase 8 (Polish)**: Depends on Phase 7

### Parallel Opportunities

```
Phase 1 (T001–T005): T003, T004, T005 in parallel
Phase 2 (T006–T009): T006+T007 sequential, T008 parallel
Phase 5 (T020–T024): T020, T021, T022 in parallel
Phase 7 (T034–T038): T034, T035, T036 in parallel; T037, T038 after
```

---

## Implementation Strategy

### MVP (User Story 1 + Foundational only)

1. Phase 1 → Phase 2 → Phase 5 → Phase 6
2. Stop and validate: `engine.index(name).search()` works for IVF + BM25
3. Ship as incremental improvement

### Full Delivery

1. Phase 1 → Phase 2 → Phase 3 (PLAID tests) → Phase 4 (PLAID impl) in sequence
2. Phase 5 in parallel with 3/4
3. Phase 6 after Phase 5
4. Phase 7 → Phase 8

---

## Notes

- Constitution Principle III (Test-First): Phase 3 tests MUST be committed and failing before Phase 4 implementation begins. Phase 6 tests MUST be committed before Phase 6 implementation.
- Constitution Principle IV: Phase 7 is non-optional. All e2e tests must hit live `iris_vector_graph` container.
- Constitution Principle VI: Container name `iris_vector_graph` verified from `docker-compose.yml:4`. Port `1972` verified from `docker-compose.yml:5`.
- No hardcoded ports anywhere in new test files — all via `IRISContainer.attach("iris_vector_graph").get_exposed_port(1972)`.
