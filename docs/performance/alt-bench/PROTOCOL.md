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

## Call-overhead floor (declared after the first parity measurement, and said so)

A metric whose baseline cost is below about 1 microsecond per call (a single `KHopCount` answer, one keyed delete) is judged by
the absolute difference, not the ratio: parity when B is within 0.25 microseconds per call of A. The reason is measured, not
assumed: the generation-validated degree cache answers in three global reads where the stored counter used one, which is about
0.17 microseconds per call on this machine (0.58 to 0.62 against 0.41 to 0.43 for a 200-entry source; the hub case is level).
That is 1.4 times on a 0.4 microsecond operation and nothing next to the roughly 100 microseconds a call costs through the Native
API. This rule was added after seeing that number, so it is stated here rather than applied silently. Every such metric still
appears in the report with both figures, and a ratio over 1.10 with an absolute difference over 0.25 microseconds is "slower".

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

## Rules added during the run (each from something that went wrong)

1. **Restart the container before a write-heavy block, and time one fast suite first.** After long uptime and heavy journaling
   the Write Daemon stalls and IRIS pauses every session; before the stall a suite can run 6x slower than after a restart. A
   result taken in a container that has been up for days is suspect. Namespaces, classes and data survive `docker restart`.
2. **Reset derived state between repetitions of a suite that deletes rows by SQL.** The baseline leaves `^KG` behind, so
   later repetitions walk a different, smaller graph and look faster. Suites that need it carry `reset_derived` in
   `scripts/bench/alt/suites.py`; the runner kills the derived globals for the side before every repetition.
3. **Counterbalance order for long loads, and report every run.** A single-repetition suite (the full DRKG load) is run
   in both orders from a fresh container; the table shows min, median and max per side, and the verdict is "no systematic
   difference" when the spread inside a side exceeds the difference between sides.
4. **Settle gap for the migration driver.** Alternating the two class versions in one scratch namespace makes `ALTER TABLE`
   wait out lock timeouts; `run_migration.py --settle 20` leaves the same gap before each run on both sides.
5. **Control rows.** The in-process references (networkx) run identical code on every IVG side. Their run-to-run difference
   (20 % to 28 % here) is the noise floor for any competitor ratio, and differences inside about 10 % between IVG sides are
   drift (A against itself moved 0 % to 6 % across passes).
6. **Check units before applying a floor.** The sub-microsecond floor applies to microseconds; a 0.25 millisecond difference
   is not covered by it (that was a real translator regression, found by profiling `translate_to_sql`).
7. **The final code is re-measured.** Fixes made after a confirmation pass are covered by a last pass (`final-1`, `final-2`)
   on a freshly restarted container, with the side-B tree checked equal to the committed branch.
14. **Tune the tables after a bulk load, on both sides, before timing.** The SQL optimizer plans from table statistics; a load
    leaves them as they were on the empty tables. The same translated SQL for SNB IC7 ran in 20 s in one namespace and 0.5 s in
    the other (different join order, the plan warned `Table Graph_KG.rdf_edges is not tuned`), and after `TUNE TABLE` on every
    table both ran in 0.31 s. Any Cypher-to-SQL suite loaded by the harness is tuned first (`GraphSchema.tune_tables`).
15. **A result that carries an engine error is an error.** `execute_cypher` returns an empty result with `.error` set when the
    database rejects the statement. Timing loops must read `.error` (the SNB worker does, the older suites did not), or a failing
    query is recorded as a fast zero-row answer; `scripts/bench/alt/validity.py` classifies the older runs after the fact.
16. **Two sides run the same parameter sets.** A slow leg runs fewer sets; side A's plan is handed to side B so both time the same
    calls, and answers are compared on the sets every engine ran.

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
