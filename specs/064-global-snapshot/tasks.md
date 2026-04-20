# Tasks: Graph Snapshot and Restore (spec 064)

---

## Phase 1 — Setup

- [ ] T001 Baseline: `pytest tests/unit/ -q` — confirm 531 passed
- [ ] T002 Create `tests/unit/test_snapshot.py` with SKIP_IRIS_TESTS guard, `TestSnapshotE2E` class using `iris_connection` fixture

---

## Phase 2 — Failing E2E tests (must fail before implementation)

- [ ] T003 [US1] Add `test_save_restore_roundtrip`: create 10 nodes + 10 edges + 2 labels; `engine.save_snapshot("/tmp/test_{run}.ivg")`; drop all tables; `engine.restore_snapshot("/tmp/test_{run}.ivg")`; assert node count, edge count, label count all match — **run, confirm FAILS** (AttributeError: save_snapshot missing)
- [ ] T004 [P] [US1] Add `test_snapshot_info_staticmethod`: `IRISGraphEngine.snapshot_info(path)` — confirm it's callable without an engine instance; confirm returns dict with "metadata" key — **confirm FAILS**
- [ ] T005 [P] [US1] Add `test_restore_is_destructive_by_default`: save; add 1 extra edge; restore; assert extra edge gone — **confirm FAILS**
- [ ] T006 [P] [US1] Add `test_restore_merge_preserves_local`: save; add 1 extra edge local; `restore_snapshot(path, merge=True)`; assert both snapshot edges AND extra edge present — **confirm FAILS**
- [ ] T007 [P] [US2] Add `test_snapshot_includes_globals`: after save+restore, BFS `shortestPath` query works (proves ^KG globals restored) — **confirm FAILS**

---

## Phase 3 — ObjectScript Snapshot class

- [ ] T008 Create `iris_src/src/Graph/KG/Snapshot.cls` with ClassMethods: `ExportGlobals(outputDir, globalListJson)`, `ImportGlobals(inputDir)`, `ReadFile(filePath)`, `WriteFile(filePath, content)`, `DeleteDir(dirPath)` — compile into container
- [ ] T009 Verify `ExportGlobals` writes valid GOF files: call via classMethodVoid, check file exists via `ReadFile` returns non-empty string for a known global (e.g. `^KG`)

---

## Phase 4 — save_snapshot

- [ ] T010 Add `embed_fn=None` and `use_iris_embedding=False` parameters to `IRISGraphEngine.__init__`; store as `self._embed_fn` and `self._use_iris_embedding`
- [ ] T011 Implement `save_snapshot(self, path: str, layers=None)` in `engine.py`:
  - Default `layers = ['sql', 'globals']`
  - SQL dump: `SELECT` each table → NDJSON strings; handle `emb` VECTOR column as float CSV
  - Globals: call `Graph.KG.Snapshot.ExportGlobals("/tmp/ivg_snap_{run}/", globalListJson)`; read each GOF via `ReadFile`; call `DeleteDir` after reading
  - Write ZIP: `zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED)` containing `metadata.json` + `sql/*.ndjson` + `globals/*.gof`
  - `metadata.json`: `{"version": "1.0", "created_ts": millis, "has_vector_sql": bool, "embedding_dim": int, "tables": {name: row_count}, "globals": [names]}`

---

## Phase 5 — restore_snapshot + snapshot_info

- [ ] T012 Implement `@staticmethod def snapshot_info(path: str) -> dict` on `IRISGraphEngine`: open ZIP, read `metadata.json`, return dict — no connection needed — T004 PASSES
- [ ] T013 Implement `restore_snapshot(self, path: str, merge: bool = False)` in `engine.py`:
  - Read ZIP
  - If not merge: TRUNCATE all SQL tables; `iris_obj.kill("^KG"); iris_obj.kill("^BM25Idx")` etc for each global in metadata
  - Insert SQL NDJSON rows (nodes first → rdf_edges → rdf_labels → rdf_props → rdf_reifications → kg_NodeEmbeddings with TO_VECTOR)
  - If merge: use INSERT ... WHERE NOT EXISTS (UPSERT) — snapshot rows overwrite conflicts
  - Write GOF files to `/tmp/ivg_restore_{run}/` via `WriteFile`; call `ImportGlobals`; call `DeleteDir`
  - Return `{"restored_tables": {name: count}, "restored_globals": [names], "snapshot_ts": ts}`
- [ ] T014 Run `pytest test_snapshot.py -v` — T003-T007 all PASS

---

## Phase 6 — embed_fn / auto-embedding after restore

- [ ] T015 [P] Add `get_unembedded_nodes(self) -> List[str]` method: `SELECT n.node_id FROM nodes n LEFT JOIN kg_NodeEmbeddings e ON e.id = n.node_id WHERE e.id IS NULL` — returns node IDs with no embedding
- [ ] T016 [P] In `restore_snapshot`, after all data is restored: if `self._embed_fn` or `self._use_iris_embedding`: call `get_unembedded_nodes()`; for each, get text from `rdf_labels` + `rdf_props`; embed via `_embed_fn(text)` or `SELECT EMBEDDING(text)` SQL; insert into `kg_NodeEmbeddings`; add count to return value as `embedded_new_nodes`

---

## Phase 7 — Gate + Polish

- [ ] T017 [P] Run `pytest tests/unit/ -q` — 531+ passed, zero regressions
- [ ] T018 Bump version to 1.58.0 (after 063 ships as 1.57.0)
- [ ] T019 Add README section: "Graph Snapshots" with save/restore/conftest.py examples
- [ ] T020 Commit and publish: `feat: v1.58.0 — save_snapshot/restore_snapshot, snapshot_info @staticmethod, embed_fn (spec 064)`

**Dependencies**: T001-T002 → T003-T007 (failing) → T008-T009 (ObjectScript) → T010-T011 (save) → T012-T013 (restore) → T014 → T015-T016 (embed) → T017-T020
