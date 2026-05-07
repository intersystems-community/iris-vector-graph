# Tasks: IVGResult for execute_cypher

**Branch**: `151-ivgresult-execute-cypher`
**Spec**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md)

---

## Phase 1: Setup

- [ ] T001 Verify container `iris_vector_graph` and port `1972` in `docker-compose.yml`
- [ ] T002 [P] Create empty `iris_vector_graph/result.py`
- [ ] T003 [P] Create empty `tests/unit/test_ivgresult.py`

**Checkpoint**: Stubs created.

---

## Phase 2: IVGResult Unit Tests (test-first — write BEFORE implementation)

> **These tests MUST FAIL until Phase 3 is complete.**

- [ ] T004 [US1] Write unit tests in `tests/unit/test_ivgresult.py`:
  - `test_success_result_has_columns_rows` — columns/rows accessible via dot and subscript
  - `test_error_result_is_falsy` — `bool(result)` = False when error set
  - `test_success_result_is_truthy` — `bool(result)` = True even with empty rows
  - `test_dict_compat_getitem` — `result["columns"]` works
  - `test_dict_compat_get` — `result.get("error")` returns None on success
  - `test_dict_compat_contains` — `"error" in result` = False on success, True on error
  - `test_missing_key_raises` — `result["nonexistent"]` raises KeyError
  - `test_metadata_always_present` — `result.metadata` is QueryMetadata, never None
  - `test_empty_result_still_truthy` — 0 rows but no error = truthy
  - `test_isinstance_check` — `isinstance(result, IVGResult)` = True
- [ ] T005 [US1] Confirm T004 tests currently FAIL (IVGResult doesn't exist yet)

**Checkpoint**: 10 failing unit tests committed.

---

## Phase 3: IVGResult Implementation

- [ ] T006 [US1] Implement `IVGResult` Pydantic BaseModel in `iris_vector_graph/result.py`:
  - Fields: `columns: list[str]`, `rows: list`, `error: Optional[str]`, `metadata: QueryMetadata`, `sql: Optional[str]`, `params: Optional[list]`
  - `__bool__`: `return self.error is None`
  - `__getitem__`: maps keys to fields; raises `KeyError` for absent optional keys; `"error"` raises when `error is None`
  - `__contains__`: returns `False` for `"error"` when `self.error is None`; `True` for structural keys
  - `.get(key, default=None)`: delegates to `__getitem__`, catches `KeyError`
  - `model_config = {"arbitrary_types_allowed": True}`
- [ ] T007 [US1] Export `IVGResult` from `iris_vector_graph/__init__.py` and `__all__`
- [ ] T008 [US1] Verify T004 unit tests now pass 10/10

**Checkpoint**: IVGResult works standalone.

---

## Phase 4: engine.py Migration

- [ ] T009 [US1] Update `execute_cypher` return type annotation from `Dict[str, Any]` to `IVGResult` in `iris_vector_graph/engine.py`
- [ ] T010 [US1] Add `from iris_vector_graph.result import IVGResult` import to `iris_vector_graph/engine.py`
- [ ] T011 [US1] Update all `return {"columns": ..., "rows": ...}` dicts in `_execute_parsed` and `execute_cypher` to `return IVGResult(columns=..., rows=...)` — use sed/ast-grep for bulk replacement, verify each
- [ ] T012 [US1] Update all `return {"columns": [], "rows": [], ..., "error": ...}` error paths similarly
- [ ] T013 [US1] Update `_execute_var_length_cypher`, `_execute_shortest_path_cypher`, `_execute_weighted_shortest_path`, `_try_khop_fast_path` return paths to return `IVGResult`

**Checkpoint**: All engine return paths use `IVGResult`.

---

## Phase 5: bolt_server.py Adaptation

- [ ] T014 [US2] In `iris_vector_graph/bolt_server.py`, find where `result["_bolt_column_types"]` is set after `execute_cypher` call, update to: `d = result.model_dump(); d["_bolt_column_types"] = ...; use d downstream`
- [ ] T015 [US2] Verify bolt_server still handles `result["columns"]` and `result["rows"]` correctly (already works via `__getitem__`)

**Checkpoint**: bolt_server adapted.

---

## Phase 6: Validation (Constitution Principle IV — Non-Optional)

- [ ] T016 [US2] Run `pytest tests/unit/test_ivgresult.py` — 10/10 pass
- [ ] T017 [US2] Run `pytest tests/unit/test_cypher_parser.py tests/unit/test_cypher_translator.py tests/unit/test_index_handle.py -q` — zero regressions
- [ ] T018 [US2] Run `pytest tests/e2e/test_streaming_bfs.py tests/e2e/test_plaid.py tests/e2e/test_ivf_insert.py -q` — zero regressions
- [ ] T019 [US3] Run `pytest tests/e2e/test_cypher_vl_path_bfs.py -q` — 8/8 pass
- [ ] T020 [P] Verify `from iris_vector_graph import IVGResult; isinstance(engine.execute_cypher('MATCH (n) RETURN n.node_id LIMIT 1', {}), IVGResult)` = True
- [ ] T021 [P] Verify `result.columns`, `result.rows`, `result["columns"]`, `result.get("error")`, `"error" in result`, `bool(result)` all work correctly on a live query result

**Checkpoint**: All acceptance scenarios pass.

---

## Phase 7: Polish

- [ ] T022 Update `ENGINEERING_DEBT.md` — mark `IVGResult for execute_cypher` resolved; note as second Pydantic increment after `SQLQuery`/`QueryMetadata`/`IndexHandle`
- [ ] T023 Update `README.md` changelog with v1.86.0 entry
- [ ] T024 Bump version to `1.86.0` in `pyproject.toml` and publish

---

## Dependencies & Execution Order

- Phase 1 → Phase 2 (tests-first, MUST FAIL before Phase 3) → Phase 3 → Phase 4 → Phase 5 → Phase 6 → Phase 7
- T011 is the largest task — ~200 return sites in engine.py; use ast-grep or sed for bulk replacement
- T014 is small and safe — bolt_server change is localized

## Notes

- Constitution Principle III: T005 must confirm tests fail before Phase 3 begins.
- Constitution Principle VI: Container `iris_vector_graph` from `docker-compose.yml:4`, port `1972` from `docker-compose.yml:5`.
- 189 test sites use `result["columns"]`, `result.get("error")`, etc. — ALL must pass without modification.
- `_bolt_column_types` is the only mutation site that needs adaptation.
- VL path fast-path methods (`_try_khop_fast_path`) return early with their own dicts — these also need updating.
