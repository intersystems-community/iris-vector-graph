# Tasks: BM25Index — Pure ObjectScript Lexical Search

**Input**: Design documents from `/specs/044-bm25-index/`
**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅, quickstart.md ✅

**Organization**: Tasks grouped by user story. TDD required per Constitution III — tests written before implementation in every phase.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to

---

## Phase 1: Setup

- [X] T001 Create `iris_src/src/Graph/KG/BM25Index.cls` — empty class scaffold: `Class Graph.KG.BM25Index Extends %RegisteredObject` with stub ClassMethods Build, Search, Insert, Drop, Info, SearchProc, Tokenize (each returning "" or 0 — compilable but not functional)
- [X] T002 Create `tests/unit/test_bm25_index.py` — empty file with `SKIP_IRIS_TESTS` guard, imports, two empty test classes: `TestBM25IndexUnit` and `TestBM25IndexE2E`
- [X] T003 Compile `BM25Index.cls` into `iris_vector_graph` container via `IRISContainer.attach("iris_vector_graph")` docker exec pattern and confirm clean compile
- [X] T004 Run `pytest tests/unit/ -q --timeout=20` — confirm 375 existing tests still pass (no regression from empty scaffold)

**Checkpoint**: Scaffold compiles, no existing test regressions.

---

## Phase 2: Foundational (blocking all user stories)

**Purpose**: Tokenizer and `^BM25Idx` global write/read primitives used by Build, Search, Insert.

- [X] T005 Write unit test `test_tokenize_lowercases_and_splits` in `tests/unit/test_bm25_index.py::TestBM25IndexUnit`: mock `Graph.KG.BM25Index.Tokenize("Ankylosing Spondylitis HLA-B27")` response as `["ankylosing","spondylitis","hla","b27"]`; assert tokens are lowercase, punctuation-split — must FAIL before T006
- [X] T006 Implement `Graph.KG.BM25Index.Tokenize(text As %String) As %List` in `iris_src/src/Graph/KG/BM25Index.cls`: lowercase via `$ZCONVERT(text,"L")`; try `##class(%iFind.Utils).Analyze(lower,"en",1)` in Try/Catch; fallback: iterate chars, accumulate alphanumeric runs, build `$ListBuild(tok1,tok2,...)`; compile and verify T005 passes

**Checkpoint**: `pytest tests/unit/test_bm25_index.py -v -k "tokenize"` — T005 passes.

---

## Phase 3: US1 — Build a BM25 index (P1)

**Goal**: `bm25_build("ncit", text_props=["name","definition"])` indexes all nodes and stores `^BM25Idx` globals correctly.

**Independent test**: After build, `bm25_info("ncit")["indexed"]` == node count in graph; `bm25_search("ncit","test",1)` returns a list (not error).

- [X] T007 [US1] Write unit test `test_bm25_build_calls_classmethod` in `TestBM25IndexUnit`: mock engine, assert `bm25_build("ncit", ["name"])` calls `##class(Graph.KG.BM25Index).Build("ncit", ...)` with correct args — must FAIL before T009
- [X] T008 [US1] Write unit test `test_bm25_build_returns_dict` in `TestBM25IndexUnit`: mock classMethodValue returns `'{"indexed":3,"avgdl":5.0,"vocab_size":12}'`; assert `bm25_build` returns dict with keys `indexed`, `avgdl`, `vocab_size` — must FAIL before T009
- [X] T009 [US1] Implement `IRISGraphEngine.bm25_build(name, text_props, k1=1.5, b=0.75) -> dict` in `iris_vector_graph/engine.py`: call `classMethodValue("Graph.KG.BM25Index","Build", name, $ListBuild(text_props), k1, b)`; parse JSON response to dict; return dict
- [X] T010 [US1] Implement `Graph.KG.BM25Index.Build(name, propsList, k1, b)` in `iris_src/src/Graph/KG/BM25Index.cls`: (1) Kill existing `^BM25Idx(name)` to ensure idempotency; (2) iterate `SELECT node_id FROM Graph_KG.nodes` via SQL cursor (schema-prefix-aware); (3) for each node, query `SELECT val FROM Graph_KG.rdf_props WHERE s=? AND key IN (?)` for each prop key; (4) concatenate values, Tokenize; (5) for each term: `$Increment(^BM25Idx(name,"tf",term,nodeId))`, track `df(term)`; (6) store `^BM25Idx(name,"len",nodeId)` = token count; (7) after all nodes: compute IDF for each term via `$Order(^BM25Idx(name,"tf",term,""))` to get df; store `^BM25Idx(name,"idf",term)`; (8) store cfg: N, avgdl, k1, b, vocab_size; (9) return JSON `{"indexed":N,"avgdl":F,"vocab_size":V}`; compile
- [X] T011 [P] [US1] Write E2E test `test_build_indexes_nodes` in `TestBM25IndexE2E`: create 3 nodes with "name" property; `bm25_build("test44a", ["name"])`; assert `bm25_info("test44a")["indexed"] == 3`; `bm25_info("test44a")["vocab_size"] > 0`; `bm25_drop("test44a")` — container: `iris_vector_graph`
- [X] T012 [P] [US1] Write E2E test `test_build_idempotent` in `TestBM25IndexE2E`: build twice on same name; assert second build returns same indexed count, no error; `bm25_drop` after
- [X] T013 [US1] Compile BM25Index.cls in container and run E2E: `pytest tests/unit/test_bm25_index.py::TestBM25IndexE2E -v -k "build"` — T011, T012 pass

---

## Phase 4: US2 — Search a BM25 index (P1)

**Goal**: `bm25_search("ncit", "ankylosing spondylitis HLA-B27", 10)` returns ranked results.

**Independent test**: Node containing all 3 query terms scores higher than node containing 1 term.

- [X] T014 [US2] Write unit test `test_bm25_search_returns_sorted_tuples` in `TestBM25IndexUnit`: mock returns `'[{"id":"A","score":8.4},{"id":"B","score":3.1}]'`; assert `bm25_search` returns `[("A",8.4),("B",3.1)]` sorted DESC — must FAIL before T016
- [X] T015 [US2] Write unit test `test_bm25_search_empty_query_returns_empty` in `TestBM25IndexUnit`: mock returns `'[]'`; assert empty list returned, no error — must FAIL before T016
- [X] T016 [US2] Implement `IRISGraphEngine.bm25_search(name, query, k=10) -> list[tuple[str,float]]` in `iris_vector_graph/engine.py`: call `classMethodValue("Graph.KG.BM25Index","Search",name,query,k)`; parse JSON array; return `[(r["id"], float(r["score"])) for r in results]`
- [X] T017 [US2] Implement `Graph.KG.BM25Index.Search(name, queryText, k)` in `iris_src/src/Graph/KG/BM25Index.cls`: (1) read cfg (N, avgdl, k1, b); return "[]" if N=0 or queryText=""; (2) Tokenize queryText; (3) for each query token: check `$Data(^BM25Idx(name,"idf",token))`; if exists: get IDF; iterate posting list `$Order(^BM25Idx(name,"tf",token,docId),1,tf)`; compute BM25 score contribution; accumulate in `^||scores(docId)`; (4) sort `^||scores` descending, collect top k as JSON array `[{"id":docId,"score":score},...]`; return JSON; compile
- [X] T018 [P] [US2] Write E2E test `test_search_ranks_correctly` in `TestBM25IndexE2E`: create node A with name "ankylosing spondylitis HLA-B27 disease"; node B with name "HLA-B27 antigen"; build "test44b"; search "ankylosing spondylitis HLA-B27"; assert A scores higher than B; `bm25_drop("test44b")`
- [X] T019 [P] [US2] Write E2E test `test_search_empty_returns_empty` in `TestBM25IndexE2E`: build "test44c" with nodes; search ""; assert `[]` returned; `bm25_drop("test44c")`
- [X] T020 [P] [US2] Write E2E test `test_search_no_match_returns_empty` in `TestBM25IndexE2E`: build "test44d"; search "xyzzy quantum flux"; assert `[]` returned; `bm25_drop("test44d")`
- [X] T021 [US2] Compile and run: `pytest tests/unit/test_bm25_index.py::TestBM25IndexE2E -v -k "search"` — T018, T019, T020 pass

---

## Phase 5: US3 — Incremental Insert (P2)

**Goal**: `bm25_insert("ncit","new:001","new text")` adds document to existing index.

**Independent test**: After insert, search for term unique to new doc returns new doc in results.

- [X] T022 [US3] Write unit test `test_bm25_insert_calls_classmethod` in `TestBM25IndexUnit`: mock; assert `bm25_insert("idx","doc1","text")` calls `Insert("idx","doc1","text")` — must FAIL before T024
- [X] T023 [US3] Implement `IRISGraphEngine.bm25_insert(name, doc_id, text) -> bool` in `iris_vector_graph/engine.py`: call `classMethodValue("Graph.KG.BM25Index","Insert",name,doc_id,text)`; return True on success
- [X] T024 [US3] Implement `Graph.KG.BM25Index.Insert(name, docId, text)` in `iris_src/src/Graph/KG/BM25Index.cls`: (1) if existing docId: subtract old tf from df tracking (kill old `^BM25Idx(name,"tf",*,docId)` entries, adjust N and avgdl); (2) Tokenize text; (3) store new `^BM25Idx(name,"tf",term,docId)`; (4) update `^BM25Idx(name,"len",docId)`; (5) `$Increment(^BM25Idx(name,"cfg","N"))`; recompute avgdl; (6) update IDF only for terms in new doc; return 1; compile
- [X] T025 [P] [US3] Write E2E test `test_insert_new_doc_findable` in `TestBM25IndexE2E`: build "test44e" with 2 nodes; insert "test44e","new:001","xylophone unique rare term"; search "xylophone"; assert "new:001" in results; `bm25_drop("test44e")`
- [X] T026 [P] [US3] Write E2E test `test_insert_replaces_existing` in `TestBM25IndexE2E`: build "test44f"; insert same docId twice with different text; search for term only in second text, assert found; `bm25_drop("test44f")`
- [X] T027 [US3] Run: `pytest tests/unit/test_bm25_index.py::TestBM25IndexE2E -v -k "insert"` — T025, T026 pass

---

## Phase 6: US4 — Drop (P2)

**Goal**: `bm25_drop("ncit")` removes all `^BM25Idx("ncit",...)` data.

- [X] T028 [US4] Write unit test `test_bm25_drop_calls_classmethod` in `TestBM25IndexUnit`: mock engine; assert `bm25_drop("idx")` calls `classMethodVoid("Graph.KG.BM25Index","Drop","idx")` — must FAIL before implementation below
- [X] T028b [US4] Implement `IRISGraphEngine.bm25_drop(name) -> None` in `iris_vector_graph/engine.py`: call `classMethodVoid("Graph.KG.BM25Index","Drop",name)`
- [X] T029 [US4] Write unit test `test_bm25_info_returns_dict` in `TestBM25IndexUnit`: mock returns `'{"N":5,"avgdl":4.0,"vocab_size":20}'`; assert `bm25_info("idx")` returns dict with keys `N`, `avgdl`, `vocab_size` — must FAIL before implementation below
- [X] T029b [US4] Implement `IRISGraphEngine.bm25_info(name) -> dict` in `iris_vector_graph/engine.py`: call `classMethodValue("Graph.KG.BM25Index","Info",name)`; parse JSON; return dict (empty dict `{}` if index not found)
- [X] T030 [P] [US4] Write E2E test `test_drop_removes_all_data` in `TestBM25IndexE2E`: build "test44g"; `bm25_drop("test44g")`; assert `bm25_info("test44g")` returns `{}`; assert `bm25_search("test44g","query",3)` returns `[]`
- [X] T031 [US4] Run: `pytest tests/unit/test_bm25_index.py::TestBM25IndexE2E -v -k "drop"` — T030 passes

---

## Phase 7: US5 — Automatic kg_TXT upgrade (P2)

**Goal**: `kg_TXT("diabetes")` uses BM25 (not LIKE) when `"default"` index exists.

**Independent test**: Scores from BM25 path are non-trivial floats; LIKE path returns only 0.0 and 1.0.

- [X] T032 [US5] Write unit test `test_kgtxt_uses_bm25_when_default_exists` in `TestBM25IndexUnit`: mock `Graph.KG.BM25Index.Info("default")` returning `'{"N":5,"avgdl":4.0,"vocab_size":20}'`; assert `_kg_TXT_fallback("diabetes",5)` calls BM25 path — must FAIL before T034
- [X] T033 [US5] Write unit test `test_kgtxt_uses_like_when_no_default` in `TestBM25IndexUnit`: mock `Info("default")` returning `'{}'`; assert `_kg_TXT_fallback("diabetes",5)` calls LIKE fallback — must FAIL before T034
- [X] T034 [US5] Update `_kg_TXT_fallback` in `iris_vector_graph/operators.py`: add check at top of method — call `self.graph_engine.bm25_info("default")`; if result dict has N > 0: call `self.graph_engine.bm25_search("default", query_text, k)` and return results; else: continue with LIKE fallback. Cache result as `self._bm25_default_cached` on the `IRISGraphOperators` instance (set to None to force re-check when `bm25_build` or `bm25_drop` is called through operators)
- [X] T035 [P] [US5] Write E2E test `test_kgtxt_returns_bm25_scores_not_like_scores` in `TestBM25IndexE2E`: build "default" with graph nodes having "name" property; call `engine.kg_TXT("diabetes",5)`; assert scores are floats not equal to 0.0 or 1.0; `bm25_drop("default")`
- [X] T036 [US5] Run: `pytest tests/unit/test_bm25_index.py -v -k "kgtxt"` — T032, T033, T035 pass

---

## Phase 8: US6 — Cypher `CALL ivg.bm25.search` (P3)

**Goal**: `CALL ivg.bm25.search('ncit', $query, 10) YIELD node, score` executes BM25 search from Cypher.

**Independent test**: Cypher query produces same result set as `bm25_search()`.

- [X] T037 [US6] Add `SqlProc, SqlName = kg_BM25` to `BM25Index.SearchProc(name, queryText, k)` in `iris_src/src/Graph/KG/BM25Index.cls`: thin wrapper calling `Search(name, queryText, k)` — compile
- [X] T038 [US6] Write unit test `test_ivg_bm25_search_procedure_registered` in `TestBM25IndexUnit`: parse Cypher `CALL ivg.bm25.search('idx','query',5) YIELD node, score`; assert `translate_procedure_call` handles it without ValueError — must FAIL before T039
- [X] T039 [US6] Add `elif name == "ivg.bm25.search": _translate_bm25_search(proc, context)` to `translate_procedure_call` in `iris_vector_graph/cypher/translator.py`; implement `_translate_bm25_search(proc, context)`: resolve args (name str, query str/param, k int/param); build Stage CTE: `SELECT j.node, j.score FROM JSON_TABLE(Graph_KG.kg_BM25(?,?,?),'$[*]' COLUMNS(node VARCHAR(256) PATH '$.id', score DOUBLE PATH '$.score')) j`; `context.all_stage_params.extend([name, query, k])`; `context.stages.insert(0, "BM25 AS (\n{cte}\n)")`; register aliases: `context.variable_aliases["node"] = "BM25"`, `context.variable_aliases["score"] = "BM25"`; `context.scalar_variables.add("score")`
- [X] T040 [P] [US6] Write E2E test `test_cypher_bm25_search_executes` in `TestBM25IndexE2E`: build "test44h"; `execute_cypher("CALL ivg.bm25.search('test44h',$q,3) YIELD node,score RETURN node,score", {"q":"test query"})`; assert result has rows with node and score columns; `bm25_drop("test44h")`
- [X] T041 [P] [US6] Write unit test `test_ivg_bm25_search_yields_node_score` in `TestBM25IndexUnit`: parse and translate `CALL ivg.bm25.search(...) YIELD node, score`; assert `context.variable_aliases["node"] == "BM25"` and `context.variable_aliases["score"] == "BM25"`
- [X] T042 [US6] Run: `pytest tests/unit/test_bm25_index.py -v -k "bm25_search_procedure or cypher_bm25 or ivg_bm25"` — T038, T039, T040, T041 pass

---

## Phase 9: Polish & Cross-Cutting

- [X] T043 [P] Run full unit regression: `pytest tests/unit/ -q --timeout=30` — all 375 + new tests pass (SC-006)
- [X] T044 [P] Run SC-002 latency benchmark from `specs/044-bm25-index/quickstart.md`: build index over NCIT-scale data; assert `bm25_search` median < 50ms on 3-term query; document measured value in spec.md §Clarifications
- [X] T044b [P] Run NFR-002 benchmark: measure `bm25_build` wall-clock time on NCIT 200K nodes; assert < 5 minutes; document measured value in spec.md §Clarifications (SC-003)
- [X] T044c [P] Run NFR-003 benchmark: after `bm25_build` on NCIT data, check `^BM25Idx` global size via `$STORAGE` or container disk usage; assert < 500MB; document measured value in spec.md §Clarifications
- [X] T045 [P] Run SC-005 Community Edition check: confirm `BM25Index.cls` compiles and `bm25_search` returns results on `iris_vector_graph` (Community container, no iFind available) — verify no `%iFind.Index.*` calls in compiled .INT
- [X] T046 Bump version in `pyproject.toml` to `1.46.0`
- [X] T047 Update `README.md`: add BM25Index section under "What It Does" table and under Vector Search section, alongside VecIndex
- [X] T048 Commit: `feat: v1.46.0 — BM25Index pure ObjectScript lexical search (^BM25Idx globals, kg_TXT upgrade, ivg.bm25.search Cypher)`
- [X] T049 Tag `v1.46.0`, build with `python3 -m build`, publish with `twine upload dist/iris_vector_graph-1.46.0*`
