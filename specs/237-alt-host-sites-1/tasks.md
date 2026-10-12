<!-- markdownlint-disable MD013 -->

# Tasks: Host sites (package host-sites-1)

Test tasks come first. A task is ticked when its test has been seen failing (for test tasks) or passing (for
implementation tasks).

## Phase 1: Tests first (must fail before implementation)

- [ ] T001 [US4] `tests/unit/test_host.py`: `test_a_native_object_already_on_the_connection_is_used` (host_for honours `conn.iris`)
- [ ] T002 [US1] `tests/unit/test_host_sites.py`: `test_count_entries_reads_through_the_host_and_never_createiris`
- [ ] T003 [US1] `tests/unit/test_host_sites.py`: `test_recompile_asks_existence_in_sql_and_still_compiles_the_shipped_classes`
- [ ] T004 [US2] `tests/unit/test_host_sites.py`: `test_lazy_kg_reads_neighbours_through_the_host`
- [ ] T005 [US2] `tests/unit/test_host_sites.py`: `test_build_kg_adjacency_json_walks_through_the_host`
- [ ] T006 [US2] `tests/unit/test_host_sites.py`: `test_build_kg_adjacency_chunked_walks_through_the_host_before_refusing_without_arno`
- [ ] T007 [US3] `tests/unit/test_host_sites.py`: `test_call_classmethod_runs_through_host_for`
- [ ] T008 [US3] `tests/unit/test_host_sites.py`: `test_call_classmethod_large_delegates_to_host_call_large`
- [ ] T009 [US3] `tests/unit/test_host_sites.py`: `test_call_classmethod_large_accepts_a_host`

Phase gate: run T001-T009 on the parent commit state (`git stash`-free: run before editing sources), all must fail.

## Phase 2: Host (host.py)

- [ ] T010 [US4] `host_for`: honour `conn.iris` first (makes T001 pass)
- [ ] T011 `ClientHost.__init__(conn, native=None)` (prebuilt native; used by T008/T009 wrappers)

## Phase 3: Sites

- [ ] T012 [US1] `migrations/kg_node_stores.py`: remove `_native`; `host_for`; `_value_at` via `is_defined`+`get`; `class_exists`; `host.call` for rebuild steps and CompileList (T002, T003)
- [ ] T013 [US2] `stores/lazy_kg.py`: `host_for(conn)` and host operations (T004)
- [ ] T014 [US2] `stores/arno_bridge.py`: two sites, `host_for(conn)` (T005, T006)
- [ ] T015 [US3] `schema.py`: `_call_classmethod` via `host_for`; `_call_classmethod_large` via `call_large`; delete `_read_large_chunks`, `_read_sorted_marker`, reader constants (T007, T008, T009)

## Phase 4: Verification

- [ ] T016 Run `tests/unit/test_host.py`, `tests/unit/test_host_sites.py` and every unit test that touches the four modules (list in the report); all pass
- [ ] T017 Commit with plain-words message; no attribution lines
