# Implementation Plan: Alternative-vs-baseline benchmark harness

**Branch**: `alt/bench-harness` | **Date**: 2026-10-12 | **Spec**: [spec.md](spec.md)

## Summary

Four scripts and one catalog under `scripts/bench/alt/` and `docs/performance/alt-bench/`, no change to library
code. Setup builds the two sides; the runner executes registry suites against a side with that side's sources;
compare turns a results directory into a markdown verdict table.

## Technical Context

- Python 3.10+, standard library only for the harness (json, subprocess, statistics, argparse); the suites bring
  their own dependencies (`intersystems-irispython`, `neo4j`, `networkx`).
- Target: `ivg-iris-enterprise` (port 31972, user `_SYSTEM`/`SYS`), namespaces `IVGBENCHA` / `IVGBENCHB`.
- Tests: `tests/unit/test_alt_bench_*.py` (no IRIS); live smoke is documented in the report, not in pytest.

## Design decisions

1. **A side is a directory tree, not just a PYTHONPATH.** `pyproject.toml` has `pythonpath = ["."]` and
   `tests/__init__.py` exists, so pytest puts the repo root first on `sys.path` and beats `PYTHONPATH`. Each side's
   tree `/tmp/ivg-src-<X>` therefore holds `iris_vector_graph/` and `iris_src/` from the side's ref, plus
   `tests/`, `scripts/`, `benchmarks/`, `pyproject.toml` from the harness checkout, so both sides run identical
   benchmark code against their own library. `data` is a symlink to the main checkout's data.
2. **Suites are data.** `suites.py` holds a list of `Suite` records; the catalog documents the same ids.
3. **Metrics come from three sources**: a JSON file the suite writes (path pattern in the suite), pytest durations
   (`--durations`/junit), or a `BENCH_METRIC name=value_ms` stdout line convention for wrappers. Each rep yields a
   `{metric: ms}` dict saved in `<suite>_<side>_<rep>.json`; compare takes medians across reps.
4. **Quiet machine**: poll `ps -axo pid,command`, ignore wrapper shells (`zsh -c`, `bash -c`, `/bin/sh -c`) and the
   runner itself; wait up to a limit; record `os.getloadavg()` before and after.
5. **Data sentinel**: `^IVGBench("loaded", <dataset>)` in the side's namespace, written by the loader on success.
6. **No resets**: suites whose fixtures reset a namespace are marked in the catalog and not registered.

## Files

- `scripts/bench/alt/{sides,suites,setup_sides,run_matrix,compare,quiet}.py`
- `tests/unit/test_alt_bench_{compare,runner,setup}.py`
- `docs/performance/alt-bench/CATALOG.md`, `README.md`
