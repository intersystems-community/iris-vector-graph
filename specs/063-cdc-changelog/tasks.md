# Tasks: CDC Changelog (spec 063)

---

## Phase 1 — Setup

- [ ] T001 Baseline: `pytest tests/unit/ -q` — confirm 531 passed
- [ ] T002 Create `tests/unit/test_cdc_changelog.py` with SKIP_IRIS_TESTS guard, `TestCDCChangelogE2E` class using `iris_connection` fixture, `_run` UUID suffix

---

## Phase 2 — Failing tests (must fail before implementation)

- [ ] T003 [US1] Add `test_cdc_disabled_by_default`: `IRISGraphEngine(conn, cdc=False)`; `create_edge`; assert `iris_obj.get("^IVG.CDC", ...)` returns nothing — **run, confirm FAILS or passes vacuously** (no CDC key exists; this is the baseline)
- [ ] T004 [US1] Add `test_create_edge_writes_cdc`: `IRISGraphEngine(conn, cdc=True)`; `create_edge(A, R, B)`; `changes = engine.get_changes_since(0)`; assert `len(changes) >= 1` and `changes[0]["op"] == "CREATE_EDGE"` — **run, confirm FAILS** (AttributeError: cdc param or get_changes_since missing)
- [ ] T005 [P] [US1] Add `test_delete_edge_writes_cdc`: cdc=True; create then delete edge; `get_changes_since(0)`; assert DELETE_EDGE entry present — **confirm FAILS**
- [ ] T006 [P] [US1] Add `test_get_changes_since_millis`: cdc=True; record `ts_before`; create 3 edges; `get_changes_since(ts_before)`; assert exactly 3 entries with correct ts/seq/op/src/pred/dst — **confirm FAILS**
- [ ] T007 [US2] Add `test_replay_changes_idempotent`: cdc=True; create 2 edges; `changes = get_changes_since(0)`; drop edges; `replay_changes(changes)`; assert edges exist; `replay_changes(changes)` again; assert still 2 edges (no duplicate) — **confirm FAILS**
- [ ] T008 [P] [US2] Add `test_replay_record_flag`: replay with `record_replay=True`; assert entries with op containing "REPLAY_" are written to CDC — **confirm FAILS**
- [ ] T009 [P] [US1] Add `test_clear_changelog`: create entries; `clear_changelog()`; assert `get_changes_since(0)` returns [] — **confirm FAILS**
- [ ] T010 [P] [US1] Add `test_five_creates_five_entries`: SC-001 — 5 create_edge calls; `get_changes_since(0)` returns exactly 5 — **confirm FAILS**

---

## Phase 3 — Engine: __init__ + _write_cdc

- [ ] T011 Add `cdc: bool = False` parameter to `IRISGraphEngine.__init__` in `engine.py`; store as `self._cdc`; add `@property def cdc(self): return self._cdc`
- [ ] T012 Add `_write_cdc(self, op, src, pred, dst, graph_id=None)` private method to `IRISGraphEngine`: if `not self._cdc` return immediately; get `ts_ms = int(time.time() * 1000)`; use native API `iris_obj.increment("^IVG.CDC.seq", str(ts_ms))` for seq; write `iris_obj.set("^IVG.CDC", str(ts_ms), str(seq), val)` where val is `\x1f`-delimited string; wrap all in try/except debug log (non-fatal)

---

## Phase 4 — Wire CDC into write paths

- [ ] T013 [US1] In `create_edge` (line ~2016): after `self.conn.commit()` succeeds, call `self._write_cdc("CREATE_EDGE", source_id, predicate, target_id, graph)` — T004 now PASSES
- [ ] T014 [US1] In `delete_edge` (line ~2060): after commit succeeds, call `self._write_cdc("DELETE_EDGE", source_id, predicate, target_id)` — T005 now PASSES
- [ ] T015 [P] In `create_edge_temporal`: after ObjectScript InsertEdge + rdf_edges insert, call `self._write_cdc("CREATE_EDGE_TEMPORAL", source, predicate, target, graph)`
- [ ] T016 [P] In `bulk_create_edges`: after the executemany commit, loop edges and call `self._write_cdc("BULK_EDGE", s, p, o, effective_graph)` for each — one entry per edge (cap at 10K entries; beyond that write one BULK_BATCH op with count instead of per-edge entries to preserve performance)
- [ ] T017 [P] In `import_rdf._flush()`: accumulate CDC entries in a list during the edge loop; after the flush commit, call `_write_cdc_batch` once per flush cycle — avoids per-edge native API overhead on large RDF imports
- [ ] T018 [US1] Run `pytest test_cdc_changelog.py -k "create or delete or five"` — T004, T005, T010 PASS

---

## Phase 5 — get_changes_since + clear_changelog

- [ ] T019 [US1] Implement `get_changes_since(self, ts_ms: int) -> List[dict]`: FIRST verify if `iris_obj.order()` method exists on intersystems_iris IRIS object; if yes use it; if no, add ObjectScript helper `Graph.KG.CDC.GetChangesSince(ts_ms)` that returns JSON array and call via classMethodValue — iterate subscripts; for each (ts, seq) pair, get value, split on `\x1f`, build dict with keys ts/seq/op/src/pred/dst/graph_id — T006, T009 now PASS (add T009 after T020)
- [ ] T020 [US1] Implement `clear_changelog(self, before_ts: Optional[int] = None)`: if before_ts is None, `iris_obj.kill("^IVG.CDC")`; else iterate and kill subscripts < before_ts — T009 PASSES
- [ ] T021 [US1] Run `pytest test_cdc_changelog.py -k "since or clear or five"` — T006, T009, T010 PASS

---

## Phase 6 — replay_changes

- [ ] T022 [US2] Implement `replay_changes(self, entries: List[dict], record_replay: bool = False) -> dict`: iterate entries; for CREATE_EDGE/BULK_EDGE/IMPORT_RDF/CREATE_EDGE_TEMPORAL call appropriate create method; for DELETE_EDGE call delete_edge (non-fatal); if record_replay=True prefix op with "REPLAY_" when calling _write_cdc on this engine; return `{"applied": N, "skipped": N, "new_nodes_without_embeddings": [...]}` — T007, T008 PASS
- [ ] T023 [US2] Run `pytest test_cdc_changelog.py -v` — all 8 tests PASS

---

## Phase 7 — Gate + Polish

- [ ] T024 [P] Run `pytest tests/unit/ -q` — 531+ passed, zero regressions (cdc=False default preserves existing behavior)
- [ ] T025 Bump version to 1.57.0 in pyproject.toml
- [ ] T026 Commit and publish: `feat: v1.57.0 — CDC changelog via ^IVG.CDC, get_changes_since, replay_changes (spec 063)`

**Dependencies**: T001-T002 → T003-T010 (failing tests) → T011-T012 (scaffold) → T013-T017 (wire) → T018 → T019-T020 → T021 → T022-T023 → T024-T026

---

## Council Conditions (required before implement)

- [ ] TC-001 Add `cdc_errors` counter property to `IRISGraphEngine`: incremented (non-fatally) each time `_write_cdc` fails. Caller can inspect `engine.cdc_errors` to detect silent gaps in the changelog. Reset on `clear_changelog()`.
- [ ] TC-002 Promote snapshot+CDC E2E test to P1: add `test_snapshot_cdc_replay_roundtrip` in `test_cdc_changelog.py` — this is the primary combined use case. Covered by TC-004 in 064 spec but should also have an entry here referencing spec 064's fixture.
- [ ] TC-003 Implementation note for T019: verify `iris_obj.order()` exists in the installed `intersystems_iris` version before using it. If missing, use `Graph.KG.CDC.GetChangesSince(ts_ms)` ObjectScript helper returning JSON array. Both are correct approaches; the helper is more reliable across irispython versions.
