<!-- markdownlint-disable MD013 MD060 -->

# Feature Specification: Alternative-vs-baseline benchmark harness

**Feature Branch**: `alt/bench-harness`
**Created**: 2026-10-12
**Status**: Draft
**Input**: Parity round, package bench-harness. Run the repo's whole benchmark breadth against the baseline
(commit 6f17be0f, 4.2.3, side A) and the alternative (default ref `alt-integration`, side B), interleaved and
repeated, and compare the results.

## User Scenarios & Testing

### User Story 1 - Know what benchmarks exist and what they need (Priority: P1)

The lead wants one list of every benchmark and stress suite in the repo, with the dataset, command, environment,
runtime and competitors of each, and an honest note on those that cannot run today.

**Why this priority**: nothing can be scheduled or compared until the breadth is known.

**Independent Test**: `docs/performance/alt-bench/CATALOG.md` names every file matched by the discovery
globs in the task list, and every suite id in `scripts/bench/alt/suites.py` appears in the catalog.

**Acceptance Scenarios**:

1. **Given** the repo, **When** the catalog is read, **Then** each suite has what it measures, data, prerequisites,
   command, environment, output, runtime, competitors and "can it be pointed at a namespace and a source tree".
2. **Given** a suite that cannot run (missing data, missing competitor), **Then** the catalog says so and why.

### User Story 2 - Two sides, each in its own namespace with its own sources (Priority: P1)

**Independent Test**: after `setup_sides.py all`, `IVGBENCHA` holds the classes of 6f17be0f, `IVGBENCHB` holds the
classes of the alternative ref, `/tmp/ivg-src-A` and `/tmp/ivg-src-B` hold the matching Python trees, and a
`python -c "import iris_vector_graph"` with the side's `PYTHONPATH` resolves inside that side's tree.

**Acceptance Scenarios**:

1. **Given** a running `ivg-iris-enterprise`, **When** setup runs twice, **Then** the second run changes nothing
   (namespaces exist, classes recompiled without error).
2. **Given** both namespaces, **Then** `Graph.KG.*` class text differs between the two exactly as the two refs differ.

### User Story 3 - Run a matrix of suites on both sides, interleaved (Priority: P1)

**Independent Test**: `run_matrix.py --suites ... --sides A B --reps 3 --run-id X` writes
`docs/performance/alt-bench/results/X/<suite>_<side>_<rep>.{txt,json}` in the order A1,B1,A2,B2,A3,B3 per suite and a
`manifest.json`, after waiting for a quiet machine.

**Acceptance Scenarios**:

1. **Given** another pytest or benchmark process, **When** the runner starts, **Then** it waits and records load
   before and after; `zsh -c` wrapper command lines do not count.
2. **Given** a suite that needs data, **When** the side has no sentinel, **Then** the data is loaded once and the
   sentinel recorded; a second run skips the load.
3. **Given** a suite fails, **Then** its stdout and exit code are kept and the matrix continues.

### User Story 4 - Compare the sides (Priority: P1)

**Independent Test**: `compare.py <results dir>` emits a markdown report grouped by suite with A median/min,
B median/min, B/A and a verdict; unit tests pin the verdict rule.

**Acceptance Scenarios**:

1. **Given** B/A <= 1.10 or B's median inside A's rep spread, **Then** the verdict is `parity`.
2. **Given** B/A < 0.90 (and not parity), **Then** `faster`; otherwise `slower`.
3. **Given** `competitor_<engine>_<suite>.jsonl` files, **Then** a competitor column shows each engine's median.

## Requirements

- **FR-001**: The catalog lists every benchmark/stress suite, with the fields in User Story 1.
- **FR-002**: Setup creates `IVGBENCHA`, `IVGBENCHB` (and optionally `IVGBENCHSMOKE`) with their own databases,
  never deleting a database file, and never touching another namespace.
- **FR-003**: Setup exports `iris_src` and `iris_vector_graph` of each ref with `git archive`, loads and compiles
  into the side's namespace over TCP, and loads the Arno callout there.
- **FR-004**: A run's process uses `IRIS_NAMESPACE`, `IRIS_PORT=31972`, `IVG_TEST_CONTAINER`, and a `PYTHONPATH`
  and working directory of its own side; the manifest records which `iris_vector_graph` file was imported.
- **FR-005**: The runner is serial, interleaved A,B,A,B per suite, and waits for a quiet machine.
- **FR-006**: Results and a manifest are written under `docs/performance/alt-bench/results/<run-id>/`.
- **FR-007**: The compare report follows the verdict rule above and tolerates missing sides and failed reps.
- **FR-008**: Competitor results use `.jsonl` lines `{"engine", "suite", "metric", "values_ms": [...]}`.
- **FR-009**: Timed numbers are not produced by this package for the record; the smoke run only proves plumbing.

### Key Entities

- **Suite**: id, command template, data requirement, output kind (json file, pytest stdout, stdout metrics),
  quick flag, needs Neo4j, needs Arno.
- **Side**: letter, ref, namespace, Python tree path.
- **Run**: run id, manifest, one result file pair per (suite, side, rep).
- **Metric**: (suite, name) to a list of millisecond values, one per rep.

## Success Criteria

- **SC-001**: a smoke run of the quick suites at dataset S completes on both sides with exit codes recorded and a
  comparison report generated.
- **SC-002**: unit tests for verdicts, quiet-machine filtering, metric extraction and manifest layout pass without
  IRIS.
- **SC-003**: both namespaces are left loaded and compiled with the S dataset.
