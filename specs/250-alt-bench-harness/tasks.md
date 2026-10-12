<!-- markdownlint-disable MD013 MD060 -->

# Tasks: Alternative-vs-baseline benchmark harness

**Input**: [spec.md](spec.md), [plan.md](plan.md). Tests come first in every phase; a phase is done when its tests pass.

## Phase 1: Comparer (US4)

- [x] T001 Write `tests/unit/test_alt_bench_compare.py`: verdict rule and its boundaries, results loading, competitor
      `.jsonl` loading, report layout (watched fail: import error).
- [x] T002 `scripts/bench/alt/compare.py`: `verdict`, `load_results`, `load_competitors`, `render_report`, CLI.
- [x] T003 `tests/unit/test_alt_bench_status.py`: `tests-failed` reps keep their numbers and are listed with the failed
      test ids; hard failures lose them (watched fail).
- [x] T004 Implement `COUNTED`, `load_failure_details` in compare.py.

## Phase 2: Sides (US2)

- [x] T010 `tests/unit/test_alt_bench_setup.py`: side table, namespace guard, namespace script never deletes, tree export
      layout, overlay, stale files dropped, refusal to wipe an unmarked directory, imported package resolves inside the tree.
- [x] T011 `scripts/bench/alt/sides.py`, `setup_sides.py` (export, namespaces, schema, deploy, record, arno, status).
- [x] T012 Live: create `IVGBENCHA`, `IVGBENCHB`; load and compile; Arno; record `^IVGDeployed`.

## Phase 3: Runner (US3)

- [x] T020 `tests/unit/test_alt_bench_runner.py`: ps parsing and the wrapper filter, quiet wait, interleave order, env,
      placeholder expansion, metric extraction, rep and manifest layout, registry well-formedness (watched fail: no module).
- [x] T021 `tests/unit/test_alt_bench_shim.py`, `scripts/bench/alt/shim/sitecustomize.py`: connection rewriting.
- [x] T022 `tests/unit/test_alt_bench_parsers.py` against the real output formats, then fix `parser-bench` (watched fail).
- [x] T023 `scripts/bench/alt/{metrics,suites,run_matrix}.py`.
- [x] T024 `blocked` suites and `select_ids` (watched fail).

## Phase 4: Catalog (US1)

- [x] T030 Research every benchmark and stress suite (two read-only passes over `tests/`, `scripts/`, `benchmarks/`,
      `docs/performance/`, `specs/`).
- [x] T031 `tests/unit/test_alt_bench_catalog.py`: every registered id is in the catalog and the generated table matches
      the registry (watched fail).
- [x] T032 `scripts/bench/alt/catalog_table.py`; `docs/performance/alt-bench/CATALOG.md`.

## Phase 5: Smoke (SC-001, SC-003)

- [x] T040 One rep of the quick suites at dataset S on both sides; record per-suite time in the catalog.
- [x] T041 Leave both namespaces loaded with R-MAT S and compiled (`run_matrix.py --ensure-data rmat-s`).

## Not done in this package

Timed runs for the record (the lead runs them serially), competitor runs (another agent), the LDBC KNOWS loader and
the DRKG analytics leg (listed in the catalog as the gaps that matter for parity).
