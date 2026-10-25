<!-- markdownlint-disable MD013 MD060 -->

# Alternative vs baseline: benchmark harness

Runs the repo's benchmark breadth against side A (baseline 6f17be0f, 4.2.3, namespace `IVGBENCHA`) and side B (the
alternative, default branch `alt-integration`, namespace `IVGBENCHB`) on `ivg-iris-enterprise`, repeatedly and
interleaved, and compares the results. [CATALOG.md](CATALOG.md) lists every suite and what it needs. Design and
decisions: `specs/250-alt-bench-harness/` (tracked with `git add -f`).

[REPORT_TEMPLATE.md](REPORT_TEMPLATE.md) is the source of the report, not the report: its relative links are written
for `docs/performance/ALT_VS_BASELINE.md`, where `python -m scripts.bench.alt.build_report` puts the result. Link to
[ALT_VS_BASELINE.md](../ALT_VS_BASELINE.md), never to the template. `results/` is ignored by version control, so the
report can only be rebuilt on the machine that holds the result files.

## Order of use

```bash
PY=/Users/tdyar/ws/iris-vector-graph/.venv/bin/python

# 1. Build both sides (trees in /tmp/ivg-src-A and -B, namespaces, schema, classes, Arno). Idempotent.
$PY scripts/bench/alt/setup_sides.py all --alt-ref alt-integration
$PY scripts/bench/alt/setup_sides.py status          # classes compiled per side

# 2. Run. Serial; waits for a quiet machine (no other pytest or benchmark process; zsh -c wrappers are ignored).
$PY scripts/bench/alt/run_matrix.py --list
$PY scripts/bench/alt/run_matrix.py --suites bench-q1q6-s bench-q1q6-m perf-head-to-head --reps 3 --run-id 20261013-a
$PY scripts/bench/alt/run_matrix.py --quick --reps 1 --run-id smoke --no-wait   # plumbing check only

# 3. Compare (run_matrix also writes report.md at the end)
$PY scripts/bench/alt/compare.py docs/performance/alt-bench/results/20261013-a
```

After a run that wiped the namespaces, `run_matrix.py --ensure-data rmat-s` reloads the R-MAT S dataset on both sides.
Re-run `setup_sides.py export` (and `deploy` if `iris_src` changed) after the alternative ref moves.

## Result layout

`results/<run-id>/`

- `<suite>_<side>_<rep>.txt`: stdout and stderr of the run.
- `<suite>_<side>_<rep>.json`: `{suite, side, rep, status, metrics{name: ms}, returncode, elapsed_s, failed_tests, argv}`.
  `status` is `ok`, `tests-failed` (pytest exit 1: a timing gate or assertion failed; the numbers are still valid and kept)
  or `failed` (no numbers).
- `<suite>_<side>_<rep>.out/`: the suite's own artefacts (its JSON, `junit.xml`).
- `manifest.json`: refs and resolved commits of both sides, which `iris_vector_graph` file each side imports, the harness
  commit, the environment, and per run the load average before and after and what the quiet-machine wait saw.
- `competitor_<engine>_<suite>.jsonl` (written by whoever runs the competitors): one object per line,
  `{"engine": "neo4j", "suite": "<suite id>", "metric": "<metric name from the rep files>", "values_ms": [..]}`.
- `report.md`: the comparison.

## Verdict rule

B/A is the ratio of the medians over reps. `parity` when B/A is at most 1.10 or B's median lies inside A's rep spread
(min to max); `faster` when B/A is below 0.90 and B's median is below A's spread; otherwise `slower`. With one rep the
spread is a point, so a smoke run is only a plumbing check.

## Things that will surprise you

- A side is a directory tree, not a `PYTHONPATH`: pytest's `pythonpath = ["."]` and the scripts' own `sys.path.insert`
  would otherwise import the wrong library. Runs use the side tree as working directory.
- Many scripts hard-code `USER` or a port. `scripts/bench/alt/shim/sitecustomize.py` rewrites their connections to the
  side's namespace and refuses any other namespace.
- Suites that wipe the namespace (`wipes` in `--list`) run last and invalidate the loaded dataset; the dataset check
  compares a sentinel with the real node count.
- Some suites are `blocked` (they hang or fail on this driver today; the reason is in `suites.py` and the catalog).
  `--include-blocked` runs them anyway.
- The host is shared. Real timings need a quiet machine; the runner waits up to `--max-wait` seconds and records
  whether it got one (`quiet` in the manifest).
