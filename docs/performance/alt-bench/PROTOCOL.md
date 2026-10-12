<!-- markdownlint-disable MD013 MD060 -->

# Benchmark protocol: baseline vs alternative vs other graph databases

Goal (set 2026-10-12): the alternative path performs at parity with the baseline across the whole breadth of benchmarks this
repo has used, and is compared against Neo4j and other graph databases on the same graphs. This file fixes the rules before any
number is recorded, so a result cannot be chosen after the fact. The catalog of suites is `CATALOG.md`; the machinery is
`scripts/bench/alt/`; competitor setup is `scripts/bench/competitors/`.

## Sides

- **A (baseline)**: commit `6f17be0f` (4.2.3). Classes in `IVGBENCHA`, Python from an export of the same commit.
- **B (alternative)**: the `alt-parity` integration branch. Classes in `IVGBENCHB`, Python from an export of that branch.
- Same container (`ivg-iris-enterprise`, IRIS 2026.3.0AI build 113), same machine, same Arno callout, same datasets loaded the same
  way, same client code path for each suite. Only the source tree differs.

## Parity

For every metric of every suite, with N >= 5 interleaved repetitions (A, B, A, B, ...) after a warm-up:

| Verdict | Rule                                                                     |
| ------- | ------------------------------------------------------------------------ |
| faster  | B median < 0.90 x A median                                               |
| parity  | B median <= 1.10 x A median, or B median inside the range [A min, A max] |
| slower  | otherwise                                                                |

Parity is the goal; "faster" is reported, not required. Any metric whose result (checksum, row count, ranking) differs between A
and B is a correctness failure and is reported before any timing. A "slower" verdict stays in the report with its cause and an
owner; nothing is dropped, rounded away or re-run until it passes without saying so (re-runs are listed with the reason).
Cold-cache cost of the alternative (first read after a write) is measured and reported separately, never hidden inside a warm figure.

## Fairness rules

1. One suite at a time. No other benchmark, test run, build or agent may use the container or the CPU during a timed block. Before
   each block record `uptime`, the process list filtered for pytest/benchmark processes (ignoring `zsh -c` wrappers), and the
   container CPU; refuse to start above a load average of 8, and say what else was running if the machine was not quiet.
   Known background load on this machine that does not belong to this repo (steady CPU): two debug `leia-kiosk` processes (about
   85 % CPU each), the macOS WindowServer, a Firefox plugin process, and the other containers. They are listed in the manifest.
2. Both IRIS sides are measured in the same session, interleaved, so drift hits both. Competitors are measured in blocks of their own
   (they are separate containers) with the same warm-up and repetition counts, and the IRIS sides re-measured at the end of the
   session to show the machine did not drift.
3. Same inputs: same dataset files or generator seed, same query parameters (start nodes, seeds, k), same result-size limits. The
   competitor loaders verify node and edge counts and a degree checksum against the generator.
4. Report every entry point the repo's own suites use (for example `execute_cypher` and `BFSFastJson`/`NKGAccel.BFSJson`), not the best one.
5. State what each engine is doing: Neo4j and Memgraph are queried over Bolt (network + Cypher parse/plan, with their query caches
   warm after the warm-up); IVG's ObjectScript entry points are in-process calls over the Native API, and `execute_cypher` adds
   translation and SQL. Published third-party numbers (GES at SF1000) are quoted as published and labelled as such, not re-run.
6. Memory settings, versions and the exact commands go in the manifest. Nothing here is a claim about hardware other than this one machine.

## Order of the run

1. Correctness cross-check of A against B (checksums) on every suite at the smallest dataset.
2. IRIS sides, suites in catalog order, S then M then L datasets, then DRKG, then LDBC if loadable, then algorithms, then stress/ingest.
3. Competitor blocks: Neo4j, Memgraph, the in-process references (networkx, igraph), same suites and datasets.
4. Repeat suite 1 of step 2 for A and B to show drift. Write `docs/performance/ALT_VS_BASELINE.md` and per-competitor tables from
   `compare.py`, then update `docs/research/alt-impl/ADOPTION.md`.

## What is out of scope or may be unrunnable

Anything the catalog marks unrunnable (for example a dataset that is no longer on disk) is reported as not run, with the reason and
what it would take, not omitted. A competitor or algorithm leg that needs a component that cannot be obtained offline (Neo4j GDS,
for example) falls back to the in-process references the existing tests already use, and says so.
