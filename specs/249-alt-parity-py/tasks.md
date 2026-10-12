<!-- markdownlint-disable MD013 -->

# Tasks: parity-py

**Input**: [spec.md](spec.md), [plan.md](plan.md)

## Phase 1: Tests first (unit, fakes)

- [ ] T001 `tests/unit/test_parity_py_lazykg.py`: `iter_nodes` over a tree with `out` only; `degree` and `degree_for_predicate` call `Graph.KG.Adj.Degree` with the derived graph key; int coercion, None to 0, cache.
- [ ] T002 [P] `tests/unit/test_parity_py_snapshot.py`: plan has no `deg`/`degp`, has derived `gen`/`degc`; restore ignores `deg`/`degp`; restore clears `degc` then `gen` for the graphs of `out`/`in` lines, after the writes; old-release archive still restores.
- [ ] T003 [P] `tests/unit/test_parity_py_namespace_probe.py`: the probe SQL names `^KG("out")`.
- [ ] T004 [P] `backfill_degp` is a deprecated no-op that calls no server method.
- [ ] T005 [P] Source scan `tests/unit/test_parity_py_no_counter_strings.py` with a self-test of the scanner and an allow-list that carries reasons.
- [ ] T006 [P] Oracle tests: `deg`/`degp` entries are ignored; a `degc` entry with the current generation and a wrong count is `degc_mismatch`; a `degc` whose generation is behind is not checked.
- [ ] T007 Run the new files and record that they fail on the old code.
- [ ] T008 Edit the existing tests that pin the old behaviour (`test_lazy_kg*.py`, `test_lazykg_graph_scope.py`, `test_engine_mocked.py`, `test_schema_unit.py`, `test_schema_final_v10.py`, `test_derived_oracle.py`, `test_graph_stores_inventory.py`, `test_engine_critical_fixes.py`, `test_host_sites.py`), each with its reason.

## Phase 2: Code

- [ ] T009 `stores/lazy_kg.py`.
- [ ] T010 `stores/iris_sql_store.py` probe.
- [ ] T011 `_engine/snapshot.py`.
- [ ] T012 `_engine/schema.py` `backfill_degp`.
- [ ] T013 `tests/support/derived_oracle.py`.
- [ ] T014 Run the unit files; show output.

## Phase 3: Live tests and docs (the lead runs the live ones)

- [ ] T015 Edit the integration/e2e tests that assert stored counters to assert `Graph.KG.Adj.Degree` or the `out` entries.
- [ ] T016 Docs: USER_GUIDE, ADMIN_GUIDE, README.

## Dependencies

T001-T006 before T007; T007 before T009-T013; T008 with T009.
