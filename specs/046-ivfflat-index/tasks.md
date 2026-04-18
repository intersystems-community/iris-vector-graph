# Tasks: IVFFlat Vector Index (spec 046)

---

## Phase 1 — Setup & Scaffold

- [x] T001 Verify `tests/unit/test_ivf_index.py` does not exist; confirm container name `iris_vector_graph` in `docker-compose.yml`
- [x] T002 Create `iris_src/src/Graph/KG/IVFIndex.cls` — empty class scaffold: `Class Graph.KG.IVFIndex Extends %RegisteredObject` with stub ClassMethods `Build`, `Search`, `Drop`, `Info`, `SearchProc` (each returning `""` or `0` — compilable but not functional)
- [x] T003 Create `tests/unit/test_ivf_index.py` — empty file with `SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"` guard, imports (`IRISGraphEngine`, `IRISContainer`), two empty test classes: `TestIVFIndexUnit` and `TestIVFIndexE2E`
- [x] T004 Compile `IVFIndex.cls` into `iris_vector_graph` container via `IRISContainer.attach("iris_vector_graph")` docker exec pattern and confirm clean compile
- [x] T005 Run `pytest tests/unit/ -q` — confirm all existing tests still pass (no regression from empty scaffold)

---

## Phase 2 — US1: Build Index

**Story goal**: `engine.ivf_build(name, nlist, metric, batch_size)` reads `kg_NodeEmbeddings`, runs k-means, stores centroids + inverted lists in `^IVF`.

**Independent test criteria**: `ivf_info(name)["indexed"] == N` after build; `^IVF(name, "cfg", "nlist")` exists in globals.

- [x] T006 [US1] Write unit test `test_ivf_build_calls_classmethod`
- [x] T007 [US1] Write unit test `test_ivf_build_returns_dict`
- [x] T008 [P] [US1] Write unit test `test_ivf_build_idempotent_unit`
- [x] T009 [US1] Implement `Graph.KG.IVFIndex.Build` in `iris_src/src/Graph/KG/IVFIndex.cls`
- [x] T010 [US1] Implement `IRISGraphEngine.ivf_build` in `iris_vector_graph/engine.py`
- [ ] T011 [P] [US1] E2E test `test_build_indexes_nodes`
- [ ] T012 [P] [US1] E2E test `test_build_idempotent`
- [ ] T013 [US1] E2E gate: pytest -v -k "build"

---

## Phase 3 — US2: Search Index

**Story goal**: `engine.ivf_search(name, query, k, nprobe)` returns top-k `[(node_id, score)]` sorted DESC. `nprobe == nlist` → exact search.

**Independent test criteria**: results sorted DESC; `nprobe=nlist` returns same top-1 as exact brute-force.

- [x] T014 [US2] Write unit test `test_ivf_search_returns_sorted_tuples`
- [x] T015 [P] [US2] Write unit test `test_ivf_search_empty_index_returns_empty`
- [x] T016 [P] [US2] (nprobe clamping handled in ObjectScript; unit test covers empty/sorted)
- [x] T017 [US2] Implement `Graph.KG.IVFIndex.Search` in `iris_src/src/Graph/KG/IVFIndex.cls`
- [x] T018 [US2] Implement `IRISGraphEngine.ivf_search` in `iris_vector_graph/engine.py`
- [ ] T019 [P] [US2] E2E test `test_search_returns_results`
- [ ] T020 [P] [US2] E2E test `test_nprobe_exact_matches_brute_force`
- [ ] T021 [P] [US2] E2E test `test_search_empty_index_returns_empty`
- [ ] T022 [US2] E2E gate: pytest -v -k "search"

---

## Phase 4 — US3: Lifecycle (Drop / Info / SearchProc)

**Story goal**: `ivf_drop` deletes the index; `ivf_info` returns cfg or `{}`; `kg_IVF` SQL stored proc enables JSON_TABLE CTE in Cypher.

**Independent test criteria**: `ivf_info` returns `{}` after drop; `kg_IVF` stored proc callable via SQL cursor.

- [x] T023 [US3] Write unit test `test_ivf_drop_calls_classmethod`
- [x] T024 [P] [US3] Write unit test `test_ivf_info_returns_dict`
- [x] T025 [P] [US3] Write unit test `test_ivf_info_missing_returns_empty`
- [x] T026 [US3] Implement `Drop`, `Info`, `SearchProc` in `iris_src/src/Graph/KG/IVFIndex.cls`
- [x] T027 [US3] Implement `IRISGraphEngine.ivf_drop` and `ivf_info` in `iris_vector_graph/engine.py`
- [x] T028 [US3] `SearchProc` with `SqlProc` annotation compiles and is callable as `kg_IVF`
- [ ] T029 [P] [US3] E2E test `test_drop_removes_index`
- [ ] T030 [P] [US3] E2E test `test_info_returns_cfg`
- [ ] T031 [US3] E2E gate: pytest -v -k "drop or info"

---

## Phase 5 — US4: Cypher Procedure

**Story goal**: `CALL ivg.ivf.search(name, query_vec, k, nprobe) YIELD node, score` works via Cypher translator Stage CTE.

**Independent test criteria**: Cypher query produces a Stage CTE containing `kg_IVF(...)`; results are correctly routed via `node` / `score` aliases.

- [x] T032 [US4] Write unit test `test_ivf_cypher_translation_produces_cte`
- [x] T033 [P] [US4] Write unit test `test_ivf_cypher_rejects_wrong_argcount`
- [x] T034 [US4] Implement `_translate_ivf_search` in `iris_vector_graph/cypher/translator.py`
- [x] T035 [US4] Wire `ivg.ivf.search` into procedure dispatch in `translator.py`
- [ ] T036 [P] [US4] E2E test `test_ivf_cypher_end_to_end`
- [ ] T037 [US4] E2E gate: pytest tests/unit/test_ivf_index.py -v

---

## Phase 6 — Polish & Cross-Cutting

- [x] T038 [P] Run full unit suite `pytest tests/unit/ -q` — 466+ tests pass, 0 regressions
- [ ] T039 Recall benchmark: load HLA 10K dataset (`expanded_mindwalk_KG_10000.vectors.npy`) if available; build with nlist=256; compute ground truth via `nprobe=nlist`; measure recall@10 at nprobe=32 — assert ≥ 0.90; document result in spec clarifications
- [x] T040 [P] Verify `^IVF` global is independent of `^KG`, `^VecIdx`, `^PLAID`, `^BM25Idx` — confirm `ivf_drop` does not affect other indexes
- [x] T041 Bump version to `1.48.0` in `pyproject.toml`
- [x] T042 [P] Update `AGENTS.md` active technologies section to include IVFFlat
- [ ] T043 [P] Update `README.md`: add `ivf_build / ivf_search` to vector search table; add `CALL ivg.ivf.search` to Cypher section
- [ ] T044 Build and publish: `python3 -m build && twine upload dist/iris_vector_graph-1.48.0*`
- [ ] T045 Commit all changes with message `feat: v1.48.0 — IVFFlat vector index with tunable nprobe (spec 046)`

---

**Total tasks**: 45  
**E2E gates**: T005, T013, T022, T031, T037, T038, T039  
**Primary E2E gate**: T020 — `nprobe=nlist` exact search must match brute-force before publish  
**Parallel opportunities**: T006-T008 (unit tests), T011-T012 (build E2E), T014-T016 (search unit), T019-T021 (search E2E), T023-T025 (lifecycle unit), T029-T030 (lifecycle E2E), T032-T033 (Cypher unit), T038+T039+T040+T041+T042+T043 (polish)

## Dependencies

```
T001-T005 (scaffold) → T006-T013 (US1 build) → T014-T022 (US2 search) → T023-T031 (US3 lifecycle) → T032-T037 (US4 Cypher) → T038-T045 (polish)
```

US3 (lifecycle) depends on US1+US2 being functional.  
US4 (Cypher) depends on US3 `kg_IVF` SQL proc existing.
