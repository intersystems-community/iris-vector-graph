<!-- markdownlint-disable MD013 MD060 -->

# Tasks: Native-handle sites go through the Host

**Input**: spec.md, plan.md. Test tasks come first; each is written and seen failing before its implementation task.

## Phase 1: Tests first

- [x] T001 Copy host.py and tests/unit/test_host.py from native-handle-module (done, commit 58ade9c3)
- [x] T002 [P] tests/unit/test_host_sites_2.py: engine `_host()` factory cases (US2 acceptance 1 to 5, FR-001, FR-004)
- [x] T003 [P] tests/unit/test_host_sites_2.py: store `_host()` factory cases (embedded and ordinary connection)
- [x] T004 [P] tests/unit/test_host_sites_2.py: static check, no raw Native method name in migrated files (SC-003)
- [x] T005 [P] tests/unit/test_host_sites_2.py: representative migrated sites (engine `_arno_call` chunk read, store
  `_iris_obj` untouched, `_engine/vector.py` kill of `^NKG`, `_engine/admin.py` nextSubscript argument order)
- [x] T006 Run the new tests; confirm they fail for the expected reason (no `_host`, raw names present)

## Phase 2: Implementation

- [x] T007 engine.py: add `_native_source()` (connection selection, no name check) and `_host()`; `_iris_obj()` keeps
  returning the raw handle built from the same selection
- [x] T008 iris_sql_store.py: add `_host()`; migrate the 9 `_iris_obj()` sites and the `iris_inst.set` site
- [x] T009 _engine/admin.py, algorithms.py, prov.py, nodes_edges.py, fhir_graph.py: migrate sites to `_host()` and Host
  operations (isDefined, nextSubscript argument order)
- [x] T010 _engine/query.py, snapshot.py, temporal.py, vector.py: migrate sites
- [x] T011 Helpers that take a handle (`_reload_arno_if_needed`, `_arno_call`, algorithms reader) take a Host
- [x] T012 Re-run T002 to T005 until they pass
- [x] T013 Move unit tests that patch `engine._iris_obj` / `store._iris_obj` to patch `iris.createIRIS` (list in the
  report); assertions unchanged
- [~] T014 Run the unit tests touching these files and tests/unit/test_host.py; compare failures with base branch via
  a WIP commit checkout (no stash)
- [x] T015 Commit (no attribution); report

## Not run (lead's serial pass with IRIS)

- tests/e2e and tests/integration tests that call `engine._iris_obj()` (still valid: it returns the raw handle)
- tests/integration tests that patch `_iris_obj` (updated patch target, not run)

## Status at commit

- Done: T001 to T013, T015. T006 red state confirmed before implementation (factory tests failed with no `_host`).
- Partly done: T014. Offline unit set (190 files) and unit-named tests in the live-fixture files run; the live-fixture
  files were not run in full (they need IRIS, not run here).

## Deviations (see the report)

- Void calls (`classMethodVoid`, 14 sites) stay on the raw handle: the Host has no void operation and `call` maps to
  `classMethodValue`, which errors on IRIS for a void method.
- `algorithms.py` uses `Host.call_large` in place of `schema._call_classmethod_large(raw)`.
- `admin.py` `orderAll(list)` maps to `next_subscript("^KG", ...)`; not verified on IRIS.
- `snapshot.py` raise helpers keep the raw handle (they end in a void BumpVersion call).
