# Tasks: PLAID Multi-Vector Retrieval

**Input**: Design documents from `/specs/029-plaid-search/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Required (SC-006: ≥6 unit tests, ≥4 e2e tests; Constitution Principle IV: integration + e2e mandatory)

**Organization**: US1 (build) and US2 (search) are both P1. US2 depends on US1 (can't search without an index). US3 (incremental insert) is P2 and depends on US1.

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

- [X] T001 Verify all existing unit tests pass: `python3 -m pytest tests/unit/ -q`
- [X] T002 Verify IRIS container is running and accessible

**Checkpoint**: Baseline green.

---

## Phase 2: Foundational — PLAIDSearch.cls Skeleton

- [X] T003 Create `iris_src/src/Graph/KG/PLAIDSearch.cls` with class declaration and empty ClassMethod signatures: StoreCentroids, StoreDocTokens, BuildInvertedIndex, Search, Insert, Info, Drop
- [X] T004 Add JsonToVector helper method to PLAIDSearch.cls (reuse pattern from VecIndex.cls — parse %DynamicArray, build $vector)
- [X] T005 Implement PLAIDSearch.Info() — read `^PLAID(name, "meta", *)` and return JSON with nCentroids, nDocs, dim, totalTokens in `iris_src/src/Graph/KG/PLAIDSearch.cls`
- [X] T006 Implement PLAIDSearch.Drop() — `Kill ^PLAID(name)` in `iris_src/src/Graph/KG/PLAIDSearch.cls`
- [X] T007 Deploy PLAIDSearch.cls to test container and verify compilation

**Checkpoint**: PLAIDSearch.cls compiles. Info/Drop work. All existing tests still pass.

---

## Phase 3: User Story 1 — Build PLAID Index (Priority: P1) MVP

**Goal**: Python K-means → store centroids + tokens + inverted index in `^PLAID` globals.

**Independent Test**: Build index from 100 docs × 10 tokens, verify centroid count, doc count, inverted index structure.

### Tests for User Story 1

- [X] T008 [P] [US1] Unit test: plaid_build with mock data (10 docs × 5 tokens) runs K-means and returns correct nCentroids in `tests/unit/test_plaid_search.py`
- [X] T009 [P] [US1] Unit test: centroid count defaults to √N in `tests/unit/test_plaid_search.py`
- [X] T010 [P] [US1] Unit test: empty docs list returns error or empty index in `tests/unit/test_plaid_search.py`

### Implementation for User Story 1

- [X] T011 [US1] Implement PLAIDSearch.StoreCentroids(name, centroidsJSON) — parse JSON array of arrays, store each as $vector in `^PLAID(name, "centroid", k)`, set metadata in `iris_src/src/Graph/KG/PLAIDSearch.cls`
- [X] T012 [US1] Implement PLAIDSearch.StoreDocTokens(name, docId, tokensJSON) — parse JSON, store each token as $vector in `^PLAID(name, "docTokens", docId, tokPos)`, increment metadata in `iris_src/src/Graph/KG/PLAIDSearch.cls`
- [X] T013 [US1] Implement PLAIDSearch.BuildInvertedIndex(name, assignmentsJSON) — parse JSON assignments, set `^PLAID(name, "docCentroid", centroidId, docId)` for each in `iris_src/src/Graph/KG/PLAIDSearch.cls`
- [X] T014 [US1] Add `plaid_build(name, docs, n_clusters, dim)` to IRISGraphEngine in `iris_vector_graph/engine.py`: run sklearn K-means on all token vectors, call StoreCentroids/StoreDocTokens/BuildInvertedIndex, return summary dict
- [X] T015 [US1] Deploy updated PLAIDSearch.cls and verify plaid_build + plaid_info round-trip

**Checkpoint**: `plaid_build()` creates a valid PLAID index. `plaid_info()` returns correct counts.

---

## Phase 4: User Story 2 — Search with Multi-Vector Query (Priority: P1)

**Goal**: Three-stage PLAID search in single classMethodValue call, <15ms on 500 docs.

**Independent Test**: Build index, search with known query, verify top result matches expected document.

### Tests for User Story 2

- [X] T016 [P] [US2] Unit test: plaid_search returns list of dicts with id and score keys in `tests/unit/test_plaid_search.py`
- [X] T017 [P] [US2] Unit test: plaid_search results are sorted by score descending in `tests/unit/test_plaid_search.py`

### Implementation for User Story 2

- [X] T018 [US2] Implement PLAIDSearch.Search Stage 1 — centroid scoring: for each query token, compute dot product against all centroids via $vectorop, accumulate scores in ^||centroidScore, pick top nprobe centroids in `iris_src/src/Graph/KG/PLAIDSearch.cls`
- [X] T019 [US2] Implement PLAIDSearch.Search Stage 1.5 — candidate generation: $Order on ^PLAID(name, "docCentroid", topCentroid, *) to collect candidate docIds into ^||candidates in `iris_src/src/Graph/KG/PLAIDSearch.cls`
- [X] T020 [US2] Implement PLAIDSearch.Search Stage 2 — exact MaxSim: for each candidate doc, for each query token, find max dot product against all doc tokens via $vectorop, sum max dots, store in ^||ranked in `iris_src/src/Graph/KG/PLAIDSearch.cls`
- [X] T021 [US2] Implement PLAIDSearch.Search TopK output — extract top-k from ^||ranked, build JSON result array in `iris_src/src/Graph/KG/PLAIDSearch.cls`
- [X] T022 [US2] Add `plaid_search(name, query_tokens, k, nprobe)` to IRISGraphEngine in `iris_vector_graph/engine.py`: JSON-marshal query tokens, single classMethodValue call, return parsed results

**Checkpoint**: PLAID search works end-to-end. Results ranked by MaxSim score.

---

## Phase 5: User Story 3 — Incremental Insert (Priority: P2)

**Goal**: Insert new document without rebuild, immediately searchable.

### Tests for User Story 3

- [X] T023 [P] [US3] Unit test: plaid_insert adds document that appears in subsequent search in `tests/unit/test_plaid_search.py`

### Implementation for User Story 3

- [X] T024 [US3] Implement PLAIDSearch.Insert(name, docId, tokensJSON) — store tokens, assign each to nearest existing centroid via $vectorop dot product, update inverted index in `iris_src/src/Graph/KG/PLAIDSearch.cls`
- [X] T025 [US3] Add `plaid_insert(name, doc_id, token_embeddings)` to IRISGraphEngine in `iris_vector_graph/engine.py`
- [X] T026 [US3] Add `plaid_drop(name)` to IRISGraphEngine in `iris_vector_graph/engine.py`
- [X] T026a [US3] Add `plaid_info(name)` to IRISGraphEngine in `iris_vector_graph/engine.py`: single classMethodValue call to PLAIDSearch.Info, return parsed JSON dict

**Checkpoint**: Incremental insert works. New document appears in search results.

---

## Phase 5.5: Integration Tests (Principle IV)

- [X] T027 [US1] Integration test: plaid_build stores centroids as $vector in ^PLAID globals, verify via native API get in `tests/integration/test_plaid_search_integration.py`
- [X] T028 [US2] Integration test: PLAIDSearch.Search returns valid JSON from single classMethodValue call in `tests/integration/test_plaid_search_integration.py`

**Checkpoint**: Global structure and classmethod interface verified.

---

## Phase 6: End-to-End Tests (Principle IV, Non-Optional)

- [X] T029 [US1] E2e test: plaid_build with 100 docs × 10 tokens creates valid index with correct centroid count in `tests/e2e/test_plaid_search_e2e.py`
- [X] T030 [US2] E2e test: plaid_search returns top result matching expected document in `tests/e2e/test_plaid_search_e2e.py`
- [X] T031 [US2] E2e test: plaid_search recall@10 ≥ 80% vs brute-force MaxSim on 100 docs in `tests/e2e/test_plaid_search_e2e.py`
- [X] T032 [US2] E2e test: plaid_search latency < 50ms on 100 docs (conservative for test container) in `tests/e2e/test_plaid_search_e2e.py`
- [X] T033 [US3] E2e test: plaid_insert adds document that appears in subsequent plaid_search in `tests/e2e/test_plaid_search_e2e.py`

**Checkpoint**: All acceptance scenarios pass against live IRIS.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [X] T034 Run full regression: `python3 -m pytest tests/unit/ tests/e2e/ -q` — all existing tests pass
- [X] T035 [P] Update `docs/python/PYTHON_SDK.md` with PLAID API reference (plaid_build, plaid_search, plaid_insert, plaid_info, plaid_drop)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies
- **Foundational (Phase 2)**: Depends on Phase 1 — PLAIDSearch.cls skeleton
- **US1 (Phase 3)**: Depends on Phase 2 — needs StoreCentroids/StoreDocTokens/BuildInvertedIndex
- **US2 (Phase 4)**: Depends on US1 — can't search without a built index
- **US3 (Phase 5)**: Depends on Phase 2 — uses StoreCentroids for nearest centroid assignment
- **Integration (Phase 5.5)**: Depends on US1 + US2
- **E2E (Phase 6)**: Depends on all implementation phases
- **Polish (Phase 7)**: Depends on Phase 6

### User Story Dependencies

- **US1 (P1)**: Independent after Phase 2
- **US2 (P1)**: Depends on US1 (needs built index)
- **US3 (P2)**: Depends on Phase 2 (needs centroids to assign to)

### Parallel Opportunities

- T008-T010 (US1 unit tests) can run in parallel
- T016-T017 (US2 unit tests) can run in parallel
- T029-T033 (all e2e tests) are independent and can run in parallel
- T018-T021 (Search stages) are sequential within the same method but logically separable

---

## Implementation Strategy

### MVP First (User Story 1 + 2)

1. Complete Phase 1: Verify baseline
2. Complete Phase 2: PLAIDSearch.cls skeleton with Info/Drop (T003-T007)
3. Complete Phase 3: Build pipeline — Python K-means + ObjectScript storage (T008-T015)
4. Complete Phase 4: Three-stage search (T016-T022)
5. **STOP and VALIDATE**: Build + Search work, recall@10 ≥ 80%

### Incremental Delivery

1. Foundational → PLAIDSearch.cls compiles
2. US1 → Build pipeline works (Python K-means → ^PLAID globals)
3. US2 → Search works (<15ms, 90%+ recall)
4. US3 → Incremental insert
5. Integration + E2E → All stories validated
6. Polish → Docs + regression
