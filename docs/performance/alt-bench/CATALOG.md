<!-- markdownlint-disable MD013 MD060 -->

# Benchmark and stress catalog

Every benchmark and stress suite in the repo, what it needs to run, and whether the matrix harness
(`scripts/bench/alt/`) can run it against side A (baseline 6f17be0f, 4.2.3) and side B (the alternative,
default `alt-integration`). Written from a read of the sources on 2026-10-12 plus a smoke run at the smallest dataset
size; runtimes marked "smoke" were measured there, under host load 15 to 30 (other agents were working), so they are
orders of magnitude, not benchmarks. Everything else is an estimate from comments, docs or committed result files and
is marked "est.".

How to use it: `run_matrix.py --list` prints the registered ids. An id in the first table below is registered in
`scripts/bench/alt/suites.py`; a suite in "Not registered" is documented but cannot run, with the reason.

## 1. Cross-cutting facts that decide how a suite can be run

### 1.1 How a suite picks host, port and namespace

There is no single mechanism. This is why the harness has a connection shim.

| family                                                                                             | variables                                                                                               | suites                                                                         |
| -------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| `IRIS_HOST`, `IRIS_PORT` (default 1972), `IRIS_NAMESPACE` (default USER), `IRIS_USERNAME/PASSWORD` | read by the script                                                                                      | `bench.py`, `ldbc_full_loader.py`; `tests/performance/conftest.py` (port 1981) |
| pytest root conftest                                                                               | `IVG_TEST_CONTAINER`, `IVG_NAMESPACE` (default USER), `IVG_PORT` (31972, last resort)                   | every `tests/perf`, `tests/e2e`, `tests/integration` suite using fixtures      |
| pytest root conftest connection order                                                              | OrbStack DNS `<container>.orb.local:1972`, then container IP:1972, then `localhost:$IVG_PORT`           | same; on this Mac `IVG_PORT` is normally unused                                |
| own connection inside the test file                                                                | `IRIS_HOST`, `IVG_PORT`, `IRIS_NAMESPACE`                                                               | `tests/e2e/test_stress_*.py`, `test_lazy_node_resolution.py`                   |
| flags                                                                                              | `--host --port --namespace`                                                                             | `three_engine_benchmark.py`, `load_drkg.py` (host, port only)                  |
| hard-coded                                                                                         | `USER` (`bench_193_full.py`, `ic2_profile.py`, `load_drkg.py`, `_load_and_bench_enterprise.py`, ...)    | see the suite entries                                                          |
| hard-coded scratch namespace                                                                       | `IVGLEGACY` (`bench_migration_4_0_0.py`, wipes `Graph_KG`); `IVG236` (`test_bench_format.py`)           | not pointable                                                                  |
| devtester                                                                                          | `IRISContainer.attach("<fixed name>")` or `attach($IVG_TEST_CONTAINER)`, namespace from devtester (USER) | `bench_index_maintenance_strategy.py`, `bench_temporal_preagg.py`              |

The harness sets all of these for the side (`IRIS_*`, `IVG_*`, `IVG_BENCH_*`) and, for the hard-coded ones, puts a
`sitecustomize` shim (`scripts/bench/alt/shim/`) first on `PYTHONPATH`. It rewrites the host, port and namespace
arguments of `iris.connect` and `iris.dbapi.connect`, and the container name passed to devtester's
`IRISContainer.attach`, from `IVG_BENCH_NS`, `IVG_BENCH_PORT`, `IVG_BENCH_HOST`, `IVG_BENCH_CONTAINER`. It refuses any
namespace other than `IVGBENCHA`, `IVGBENCHB`, `IVGBENCHSMOKE`.

### 1.2 Pointing a suite at a source tree (A or B)

`PYTHONPATH` alone does not work. `pyproject.toml` has `pythonpath = ["."]`, `tests/__init__.py` exists, and most
scripts do `sys.path.insert(0, <repo root of the script>)`, so the library that is imported is the one in the tree the
script or pytest rootdir lives in. The harness therefore builds one tree per side, `/tmp/ivg-src-A` and
`/tmp/ivg-src-B`: `iris_vector_graph/`, `iris_src/`, `sql/`, `config/` from the side's ref (`git archive`), overlaid
with `tests/`, `scripts/`, `benchmarks/`, `pyproject.toml` from the harness commit, so both sides run identical
benchmark code against their own library. Every run uses the side tree as its working directory. `data/` and the SF10
KNOWS directory are symlinks into the main checkout. The runner records which `iris_vector_graph/__init__.py` each side
imports in `manifest.json`.

The pytest conftest also does `docker cp iris_src/src/.` from the working directory and loads every class whose hash
differs from `^IVGDeployed(<ns>, <rel>)`. Setup pre-writes those hashes after its own deploy, so the first pytest
session in a namespace does not recompile all 57 or 59 classes, and A's classes can never be replaced by B's source.

### 1.3 Suites that wipe the namespace

Fixture `iris_master_cleanup` calls `Graph.KG.Eraser.EraseAll` before and after each test that requests it: every row
of `Graph_KG.*`, `^KG`, `^NKG`, `^ArnoKG` and the algorithm cache are gone, so a loaded R-MAT or DRKG dataset does not
survive. `betweenness`, `head-to-head` and `msbfs` delete by hand. The registry marks such suites `wipes`. The runner
schedules data-dependent suites first, wiping suites last, and clears the dataset sentinel (`^IVGBench("dataset")`)
after a wiping run; `ensure_data` also compares the sentinel with the real node count, so a dirty namespace is
reloaded, never trusted.

### 1.4 The driver hangs on large `executemany`

Found while smoking: in IVGBENCHA and IVGBENCHB a `cursor.executemany` of 1500 rows or more never returns (1000 rows
take 0.02 s), with the client in a socket read and no error. `bench_index_maintenance_strategy.py` inserts every edge
in one `executemany` (20,000 or 100,000 rows), so both of its suites are registered as `blocked`. The library's own
loaders batch at 1000 and are not affected. It is independent of the side (the same on A and B) and was not
investigated further; it should be reported against the IRIS build (2026.3.0AI.113.0) and `intersystems-irispython`.

### 1.5 Arno

The Arno callout is loaded into each namespace by setup (`NKGAccel.Capabilities` shows `rust_callout: true`). Two
caveats from the repo's own notes: `scripts/arno_reader_gate.py` states the Rust readers walk USER's `^KG` whatever the
caller's namespace (measured on callout 1.1.0), so `ivg-arno` rows outside USER are suspect unless proven otherwise;
and `docs/KNOWN_ISSUES.md` records that on build 113 the Arno BFS is 0.7 to 0.9 of the ObjectScript path. The smoke
run showed the same (speedup 0.8x at S). Library path: `ARNO_LIB` defaults to `/usr/irissys/mgr/libarno_callout.so`
and `IVG_ARNO_SO_PATH` to `/tmp/libarno_callout.so`; the container holds `/tmp/libarno_tcp_<sha>.so`, and the runner
sets both variables to it.

### 1.6 Competitors

| engine                     | state on this machine                                                                                                                                                                                                                        |
| -------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Neo4j                      | images `neo4j:5.24-community` and `5.21-community` are local; no container is running; the `neo4j` Python driver is not installed in the main venv. Published runs used 5.24.2 at `bolt://localhost:7688`, container `neo4j-ivg-bench`.        |
| Neo4j GDS                  | not installed (2.12.0 was used for the published head-to-head and betweenness numbers); `graphdatascience` not installed.                                                                                                                   |
| networkx, igraph, leidenalg | installed in the main venv; used in-process by `perf-head-to-head`, `perf-leiden-four-way`, `perf-betweenness-*`.                                                                                                                          |
| GES (Huawei), GraphScope   | published numbers only: `/Users/tdyar/ldbc_snb_ic_latencies_extracted.txt` (from LDBC FDR PDFs) and the figures quoted in `docs/performance/BENCHMARKS.md`.                                                                                  |
| Arno stores (fjall, IRIS)  | `three_engine_benchmark.py` only; needs the `arno_graph` module (`/Users/tdyar/ws/arno-graph` exists, `maturin develop` not done) and `/opt/iris20252cust`, which is absent.                                                                  |

Competitor results for the comparer go in `results/<run-id>/competitor_<engine>_<suite>.jsonl`, one object per line:
`{"engine": "neo4j", "suite": "<suite id>", "metric": "<metric name as in the rep files>", "values_ms": [..]}`.

## 2. Registered suites

"Smoke" is the wall time of one run at the smallest size, side A, on a loaded host. Quick means under about five
minutes at the smallest size. `wipes` means the suite dirties the namespace.

<!-- SUITE-TABLE -->

## 3. Suite details

### 3.1 R-MAT traversal family (`tests/benchmarks/`)

#### bench-q1q6-s, bench-q1q6-m, bench-q1q6-l (`tests/benchmarks/bench.py`, spec 093)

- Measures: Q1 one-hop count through `engine.execute_cypher`; Q2, Q3, Q4 BFS at 2, 3, 4 hops through
  `Traversal.BFSFastJson` (`ivg-os`) and `NKGAccel.BFSJson` (`ivg-arno`); Q5 Cypher `shortestPath`; Q6
  `ivg.shortestPath.weighted`. Per query: hot p50, p90, p99, min, cold, every run in `values_ms`, result counts, and an
  os-versus-arno subset check. Ends with acceptance targets (dataset M: Q2 arno under 5 ms, Q3 under 30, Q4 under 60,
  Q1 under 1 ms). The `PRE_RELEASE_CHECKLIST.md` parity gate is "no p50 regression above 10 percent on Q1 to Q6 at
  dataset M".
- Data: generated, deterministic. `graph_gen.RMATGenerator(seed=42)`: S 1,000 nodes and 5,000 edges, M 10,000 and
  50,000, L 100,000 and 500,000. The loader (`bench_utils.load_graph_to_iris`) deletes and refills `SQLUser.nodes`,
  `rdf_edges`, `rdf_props`, `rdf_labels` (these exist in a namespace built by `initialize_schema`; checked in
  IVGBENCHA/B), one node insert at a time, then `Traversal.BuildKG`, `BuildNKG`, `NKGAccel.Load(ARNO_LIB)`. The seed is
  `NKGAccel.GetFirstNKGNode`.
- Harness: a dataset load run (`--runs 1 --warmup 0`) once per side, then `--skip-load` for the timed reps (that path
  rebuilds `^NKG` and reloads Arno, untimed).
- Command: `python tests/benchmarks/bench.py --datasets S --runs 20 --warmup 5 --skip-load --outdir OUT` from the side
  tree. Namespace honoured (`IRIS_NAMESPACE`); tree selected by running from it.
- Output: `OUT/bench_<UTC ts>.json` with `{meta, results[...], correctness}`.
- Runtime: S smoke 4 s per timed run plus 22 to 30 s load (spot load: up to 4 minutes under heavy server load);
  M est. 4 to 8 minutes; L unknown, est. 30+ minutes, and Q3/Q4 hit `<MAXSTRING>` on high-degree seeds.
- Known quality issue: at S the shortest-path pair came out as `node_0 -> node_0`, so Q5 and Q6 measure a degenerate
  query there. Q1 to Q4 are sound.
- Competitors: none inside. Neo4j counterpart: `benchmark_neo4j.py` (3.3 below), not runnable.

#### bench-193-full-s (`tests/benchmarks/bench_193_full.py`, spec 193)

- Measures: S1 `BFSFastJsonDirect` against `BFSFastJson` at hops 1 to 4; S2 the NKG fast path
  `MATCH (n {node_id:$x})-[*1..N]->(m)` against raw Arno `BFSJson`; S3 the Q1 to Q6 regression; S4 the same on a second
  port; S5 Neo4j `[*1..h]` counts; then four gates (Direct no slower than Fast at hops 1; NKG at least 1.5x Arno at hops
  3; Q1 p50 under 5 ms and Q2 arno under 20 ms on M).
- Data: the same R-MAT loader (the generator object is reused, so M differs from a fresh `bench.py` M). Namespace is
  hard-coded USER (shim redirects); default `IRIS_PORT` is 21972, the Community container, which must not be used.
- Neo4j: its S5 queries `{node_id: $x}` but no loader in the repo creates that shape (`load_neo4j.py` writes
  `Node {id}` with `RELATED`), so S5 cannot give numbers without a new loader. The harness passes `--skip-neo4j`.
- Side effect: overwrites `<tree>/specs/193-bfs-nkg-fast-path/benchmark_results.md` if that directory exists (it does
  not in the side tree).
- Command: `python tests/benchmarks/bench_193_full.py --datasets S --runs 5 --warmup 2 --skip-neo4j --skip-load --outdir OUT`.
- Runtime: est. 5 minutes at S with these flags (S1 Direct at hops 2 to 4 takes 0.6 to 4.3 s per call); S plus M
  with 20 runs est. 20 to 40 minutes. Exit code 1 when a gate fails.

### 3.2 Scripts that load their own data

- **nkg-enterprise-khop** (`benchmarks/_load_and_bench_enterprise.py`): loads a 10K/50K graph into `Graph_KG`,
  builds `^KG` and `^NKG`, times `NKGAccelTraversal.KHopNeighbors` for Q1 (1 hop), Q2a (2 hop distance 2), Q3 (BFS 1 to
  3) with five warm-ups and 20 reps. Constants `ENT_*` (localhost:31972, USER; shim redirects). Prints p50 and p95; no
  JSON. Wipes the namespace's graph. Needs Arno.
- **three-engine** (`benchmarks/three_engine_benchmark.py`): the same Cypher through `IRISGraphEngine` with three stores:
  IVG-SQL, Arno/fjall, Arno over IRIS `^KG`. Data: first `--edges` rows of the SF10 KNOWS CSV (symlinked into the side
  tree), falling back to a 500-node synthetic graph. Only the IVG-SQL leg runs; the Arno legs report SKIP (no `arno_graph`
  module, no `/opt/iris20252cust`). Flags `--host --port --namespace`. Output
  `benchmarks/results/three_engine_latest.json`. Loads data, so it counts as `wipes`.
- **perf-scale-benchmark** (`tests/performance/scale_benchmark.py`): `bulk_create_nodes`, `get_nodes` at batch sizes 1,
  10, 100, 1000, substring search on `rdf_props.val`, single `create_node` latency over 10,000 generated entities
  (`BENCH:NODE:i`). The connection is hard-coded `get_connection("localhost", 1972, "USER", ...)`; the shim redirects
  it. Leaves 10,000 nodes behind. Stdout only.
- **temporal-preagg** (`scripts/bench/bench_temporal_preagg.py`, spec v1.39.0): ingest rate through
  `bulk_create_edges_temporal` (batches of 500, 2 warm-up and 5 rounds) for 200K rows of RE2-TT `traces.tsv`, 200K of
  `rcaeval.tsv` and 50K synthetic; query latency (median of 12) of `get_temporal_aggregate`, `get_bucket_groups`,
  `get_distinct_count` over 1, 12 and 288 buckets; HLL accuracy. Targets: SC-004 at least 80K edges/s, SC-005
  GetAggregate 1 bucket under 0.5 ms, SC-006 GetBucketGroups under 5 ms. Runs everything at import; attaches the
  container named `iris-vector-graph-main` (does not exist; the shim maps it to `ivg-iris-enterprise`) and calls
  `TemporalIndex.Purge`. Data: `~/ws/iris-datasets/re2-tt/{traces,rcaeval}.tsv` (799 MB and 871 MB, present). Est. 10
  to 20 minutes. No stored results.
- **drkg-load-500k, drkg-load-full** (`scripts/load_drkg.py`, specs 184 and 185): loads DRKG (97,238 nodes, 5,874,261
  edges, 13 node types) through `bulk_load_session()` and prints phase times. Namespace hard-coded USER (shim);
  `embedding_dimension=400`. `--edges-limit 500000` is the "scale leg" that fits a parity round (est. 1 to 3 minutes);
  the full load is 9.2 minutes (incremental `^NKG`, spec 185) to 18.6 minutes (spec 184) on the published run. The
  analytics timings in `DRKG_SCALE.md` (degree centrality, closeness) came from ad-hoc runs; no script reproduces them.
  Data at `/Users/tdyar/ws/iris-vector-graph/data/drkg` (776 MB: `drkg.tsv` 384 MB, the tarball, `embed/`), linked
  into each side tree.
- **cypher-parser-bench** (`tests/benchmark_parser.py`): parse six Cypher strings 100 times, print average and p95 per
  query. No IRIS. A client-side control: A and B should agree unless the parser changed (smoke: both identical to
  noise).

### 3.3 Suites that exist but are not registered

| suite                                                                                    | why not                                                                                                                                                                                                                                                                                                                                                                                                 |
| ---------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `scripts/bench/bench_full.py`                                                            | needs the 535M-edge `KGBENCH` namespace on `kg-iris:11982` (RE2-TT, RE2-OB, RE1-TT); not present, hours to ingest. Its `--ingest-only` mode still connects to KGBENCH at import.                                                                                                                                                                                                                        |
| `tests/benchmarks/ic2_profile.py` (LDBC IC2 decomposition)                                | needs LDBC SF10 persons and KNOWS as nodes `P_<id>` (seed `P_28587302384882`, 195 neighbours). No loader for that naming is in the repo (`ldbc_full_loader.py` has no Person or KNOWS phase). The KNOWS-only CSV exists (see 4.3), so a small loader (est. half a day, about 1.9M edges) would make it runnable. This is the suite where parity matters most (see 5.1).                                |
| `tests/benchmarks/ldbc_full_loader.py`                                                    | a loader, not a benchmark; data root `/tmp/sf10_out/...` is gone; 54M edges, hours.                                                                                                                                                                                                                                                                                                                     |
| `tests/benchmarks/bfs_benchmark.py`, `iris_baseline_run.py`                                | call `Traversal.BFS_JSON`, which does not exist in `iris_src`; need embedded `iris.cls`. Stale (spec 013).                                                                                                                                                                                                                                                                                             |
| `tests/benchmarks/iris_os_run.py`, `establish_baseline.py`                                | embedded-only (`iris.cls`, `iris.gref`) and write `/tmp` JSON; low value (3 runs, no percentiles). Superseded by `bench-q1q6`.                                                                                                                                                                                                                                                                          |
| `tests/benchmarks/benchmark_neo4j.py` + `load_neo4j.py`                                   | the Neo4j side for `synthetic_baseline.csv` (BFS depth 1 to 6 from `node_0`, 3 runs, average only). Needs a Neo4j on 7687 and the driver; writes into `specs/013-bfs-refactoring/` (may not exist). Competitor job, not an A/B suite.                                                                                                                                                                  |
| `benchmarks/neo4j_comparison.py`                                                          | IVG against Neo4j on Q1, Q2a, Q3 (10K/50K, `generate_edges` seed 42). Constants port 21972, USER, Neo4j `bolt://localhost:7688` password `ivgbenchpw`. Needs the Neo4j container and driver; registered ids exclude it (`needs_neo4j`). The IVG leg equals `nkg-enterprise-khop`.                                                                                                                      |
| `benchmarks/bench_index_maintenance_strategy.py` (`index-maintenance-20k/100k`)           | registered but `blocked`: single `executemany` of all edges hangs the driver (1.4). Published: deferred 46,306 edges/s at 100K against per-row 3,105 edges/s, ratio 14.9; at 20K deferred 20,429 and per-row 3,128, ratio 6.5.                                                                                                                                                                          |
| `scripts/bench_migration_4_0_0.py`                                                        | hard-codes `IVGLEGACY` and wipes `Graph_KG` there. PERF.md already measured its key statement (15.5x faster on B). Not pointable at IVGBENCH namespaces; do not run.                                                                                                                                                                                                                                   |
| `scripts/fhir/bench_235.py`                                                               | already an A/B tool (`--repo TREE`, `--set fixture\|scale`, `compare`); budgets sync 1.15x, group 1.20x, reports under 2000 ms. FHIR-specific, uses the test helper `connect()` (USER) and the IVGFHIR repository state; skipped here, run it as is for the FHIR leg.                                                                                                                                  |
| `scripts/niche/build_qbuckets.py`, `download_drkg.py`                                      | need container `ivg-arno-bench` (port 25972, currently unhealthy) and DRKG embeddings there; spec 168.                                                                                                                                                                                                                                                                                                  |
| `tests/performance/test_ppr_stress.py`, `test_stress_v1_5.py`                              | marker `performance`, both `xfail` (license exhaustion), own conftest with default port 1981, and they `DROP TABLE` seven `Graph_KG` tables then recreate a DDL-only shell. Destructive and unreliable.                                                                                                                                                                                               |
| `tests/integration/test_nodepk_production_scale.py`                                       | needs `RUN_PRODUCTION_SCALE=1`; 100K nodes, 500K edges, xfail ("DBAPI memory/segfault"); setup 2 to 3 minutes and 500 MB disk.                                                                                                                                                                                                                                                                         |
| `tests/e2e/test_lazy_node_resolution.py`                                                  | needs at least 1M `rdf_edges` with `node_` ids (bench.py XL); skips by design below that. R-MAT L is 500K.                                                                                                                                                                                                                                                                                              |
| `tests/integration/test_bench_format.py`, `test_small_helper_classes.py`                   | coverage tests for `Graph.KG.BenchFormat`, bound to scratch namespace `IVG236` by a sentinel check. `BenchFormat.Compare(n)` (adjacency-format microbenchmark) could be called directly in each namespace by a custom runner.                                                                                                                                                                          |
| `tests/python/test_performance_benchmarks.py`                                              | targets container `iris_test_vector_graph_ai`, writes USER, several tests xfail. Stale.                                                                                                                                                                                                                                                                                                                 |
| `tests/e2e/test_231_fhir_concept_pipeline_e2e.py::test_latency_budget`                     | real latency test but needs the IVGFHIR repository, which the session fixture resets.                                                                                                                                                                                                                                                                                                                   |
| spec 185 (incremental NKG) SC-003                                                          | no benchmark test exists; T007, T008, T011 are manual measurements via `load_drkg.py`, unchecked. A stand-in (bulk_load_session with and without incremental `^NKG` on an ER graph, assert 65 percent) would be new work.                                                                                                                                                                              |
| `tests/integration/test_vector_optimizer_live.py`, `test_engine_utils_paths.py`            | functional tests of a helper named `benchmark_vector_search`.                                                                                                                                                                                                                                                                                                                                           |

Judged not benchmarks (matched `grep -rl "perf\b\|benchmark"` but time nothing): the meta tests
`test_230_test_port_defaults`, `test_ci_sees_what_tests_read`, `test_spec_hygiene_gates`; unit tests of skip logic
(`test_230_lazy_resolution_skip`, `test_arno_probe_seed`, `test_bfs_arno`); coverage tests of
`VectorOptimizer.benchmark_vector_search` (`test_module_coverage`, `test_supporting_modules`, `test_utilities_final`,
`test_utils_coverage`, `test_vector_optimizer_sql`, `test_vector_utils_width`); `tests/contract/test_graphql_schema.py`;
files that only mention "latency" as a data field; `scripts/tck/*` (openCypher correctness); `scripts/arno_reader_gate.py`
(correctness evidence, not a timer); `scripts/load_demo_data.py`, `seed_shaarpec_demo.py`, `ingest/*`.

### 3.4 pytest suites (`tests/perf`, `tests/e2e`, `tests/integration`, `tests/unit`)

All run as `python -m pytest -p no:cacheprovider --durations=0 --junitxml=OUT/junit.xml -s [-m perf] <target>` from
the side tree with `IVG_NAMESPACE` and `IRIS_NAMESPACE` set. Metrics are the pytest `call` durations (ms) plus the
suite-specific parser below. A failed timing gate gives status `tests-failed`: the numbers are kept and the report lists
the failed test ids.

- **perf-227-single-graph-latency** (spec 227 SC-010): median latency of `engine.kg_KNN_VEC` (routed) against the raw
  3.2.0 statement on 300 vectors, 3 warm-ups, 15 interleaved rounds; asserts the ratio is at most 1.10. Self-generated
  (seed 227, width 768). Metrics `scoped_median`, `legacy_scan_median`. Smoke: the ratio gate failed on A at 1.17 under
  load (so the first smoke rep is `tests-failed` with valid numbers). Idempotent.
- **perf-betweenness-quick, perf-betweenness-full** (`test_betweenness_vs_gds.py`, spec 170, marker `perf`):
  `betweenness_centrality` wall time on karate (34 nodes, 78 edges), ER(500, 0.01) 1,230 edges, ER(2000, 0.003) 5,936 edges
  (networkx, seed 42), loaded edge by edge. Wipes the namespace. Quick selects karate and ER500; the JSON
  `benchmarks/betweenness_vs_gds_<ts>.json` is written only by the full suite. GDS exact and sampled when
  `IVG_GDS_BENCHMARK_NEO4J_URI` is set (not installed). Est. karate seconds, ER500 1 to 2 minutes of setup, ER2000 several.
- **perf-head-to-head** (`test_head_to_head.py`, needs `IVG_HEADTOHEAD=1`): per fixture (karate, ER(500, 0.02) 2,437
  edges, ER(2000, 0.005) 9,941 edges) `load_ms`, degree, betweenness (sample 0 or 200), harmonic closeness, Leiden, and
  Pearson against networkx. networkx always; Neo4j GDS when `NEO4J_URI` is reachable (not installed). Output
  `<tree>/benchmarks/head_to_head_<ts>.json`. IVG load alone is 9 to 11 s per fixture in the history. Wipes at the start
  of each fixture.
- **perf-leiden-four-way** (`test_leiden_four_way.py`, spec 163, marker `perf`): `leiden_communities` mean and stdev over
  3 runs (1 at 2,000 nodes), the Arno kernel when available, against networkx Louvain, igraph/leidenalg and GDS. The label
  says `IVG (arno+leiden-rs)` or `IVG (LazyKG+leidenalg)`: compare only equal labels across sides. JSON only when the
  third parametrisation runs. `EraseAll` around each. Est. a few minutes.
- **e2e-communities-perf** (spec 163 NFR-001 to 004, marker `perf`): `triangle_count`, `strongly_connected_components`,
  `k_core`, `leiden_communities` on ER(10000, 0.001), about 50,000 edges; gates 30/60/60/60 s. Setup is about 60,000
  Python-driven writes per test, four times: est. 30 to 90 minutes. Stdout lines `NFR-00N <algo> 10000n <E>e: X.XXXs`.
- **integration-ledger-perf-quick, -full** (spec 213, `test_ledger_perf.py`, markers `perf` and `slow`): commit latency
  (20 x 100-op, 5 x 5,000-op changesets, against an equivalent transaction, ratio at most 2), `ledger.head()` p95 at most
  10 ms, reconstruct, adjacent and full diff, verify, genesis on a 10K/50K graph at most 60 s, and a soak of 8 writer
  processes x 50 commits x 10 reps. Quick is `-k "sc004 or sc005"` (est. 2 to 5 minutes); full est. 20 to 60 minutes.
  JSON `<tree>/tests/benchmarks/results/ledger_<ts>.json`. Wipes. The spec thresholds are the earlier "parity".
- **unit-cypher-benchmark** (`tests/unit/test_cypher_benchmark.py`, marker `perf`, needs IRIS): p50 and p95 over 10
  timed runs after 3 warm-ups for 12 Cypher shapes (point lookup, label filter, IN list, 1-hop, 2-hop, aggregation, degree
  count, variable-length BFS, ...); gates 50 to 500 ms. 200 nodes and 400 edges, written with SQL plus
  `EdgeScan.WriteAdjacency` per edge, so it exercises the adjacency write path and `^KG` reads. Leaves stale `^KG` rows
  (small). Smoke 31 s.
- **e2e-stress-ingest, -traversal, -search** (`tests/e2e/test_stress_*.py`): functional tests with timing gates:
  `bulk_create_nodes` 1K under 30 s and 10K under 120 s, `bulk_create_edges` 1K under 30 s, `BulkIngestEdgesSQL`, a
  throughput gate of 10,000 edges/s at 500-edge chunks, variable-length with LIMIT under 5 s, IVF search p50 under 500
  ms. No printed numbers: durations only. They open their own connection (`IRIS_HOST`, `IVG_PORT`, `IRIS_NAMESPACE`) and
  leave about 11,000 prefixed nodes per ingest run, so they are not idempotent. `test_stress_setup`, `_api` and
  `_multi_context` are functional only and are not registered.
- **e2e-227-scale** (spec 227 SC-012): 100 routed embedding tables (50 graphs x 2 models), search, inventory, erase; gates
  120 / 90 / 30 s. Stdout `created in X s ..., inventory read in Y s, erased in Z s`. Self-cleaning. Smoke 45 s.
- **e2e-gates-kernels**: `test_ic3_exact_count` (`KHop2CountExact` p50 under 5 ms, `rebuild_nkg()` at most 30 s),
  `test_ic13_shortest_path` (`ShortestPathNKG`), `test_approx_count_distinct`, `test_cypher_vl_path_bfs`,
  `test_230_bulk_ingest_upsert` (rate over 500/s), `test_plaid_search_e2e`. Pass/fail gates plus durations. The first
  two are the paths the alternative changes (PERF.md).
- **e2e-gates-analytics**: `test_analytics_dispatch`, `test_msbfs_closeness`, `test_betweenness_neighborhood_e2e`
  (betweenness, closeness, MSBFS, Leiden, degree timing gates; Arno state manipulated on purpose). Wipes.
- **integration-nodepk-graph-analytics, -performance, -advanced** (marker `performance`): SQL-level timing of the
  NodePK feature (PageRank in Python under 500 ms on 1,000 nodes, components, label propagation, 10,000-node bulk insert at
  least 1,000/s, vector KNN under 5.8 s, 50 qps). They barely touch `Graph.KG`, so they are a noise floor: SQL speed
  should be identical on both sides. `-advanced` has loose gates and reads `IRIS_NAMESPACE`.
- **integration-bidirectional-ppr-perf**: `TestPerformance` and fallback timing in `test_bidirectional_ppr.py` (1,000-node
  graph, bidirectional PPR under 1 s). Single-shot timings with large slack: a smoke-level PPR number.
- **Other timing asserts, not registered**: `test_cls_layer.py::test_ppr_uses_cls_fast_path` (5 s hang guard),
  `tests/python/test_python_sdk.py::TestPerformanceBenchmarks` and `test_python_operators.py::test_performance_benchmarks`
  (small timing asserts, not read in detail).

## 4. Data

### 4.1 R-MAT S/M/L

Generated on the fly, deterministic, nothing to fetch. `tests/benchmarks/synthetic_baseline.csv` (50,001 lines, 1.5 MB,
tracked) is an older R-MAT 10K/50K for the legacy BFS scripts.

### 4.2 DRKG

Present at `/Users/tdyar/ws/iris-vector-graph/data/drkg` (gitignored, so absent from worktrees; linked into the side
trees). `load_drkg.py` finds it as `<repo of the script>/data/drkg`.

### 4.3 LDBC SNB

`/tmp/sf10_out` is gone. It held the full SF10 `CsvBasic-LongDateFormatter` output that `ldbc_full_loader.py` expects. No
LDBC datagen image or archive exists locally (docker images: nothing matching `ldbc`, `datagen`, `snb` or `spark`; a
search under the home directory found only an unrelated `hermes-agent/datagen-config-examples`).

Still present, untracked in the main checkout: `social_network-sf10-CsvBasic-LongDateFormatter/dynamic/person_knows_person_0_0.csv`
(82 MB, 1,938,516 rows, header `Person.id|Person.id|creationDate`) and the SF100 equivalent (847 MB, 19,941,198 rows).
Both are KNOWS only, each edge once (SF10 doubles to the published 3.87M). That is enough for the KNOWS-only workloads
(IC2 1-hop, IC3-style 2-hop, IC13 shortest path, the `KHopCount` fast paths) but not for `ldbc_full_loader.py`, which
needs persons, posts, comments, forums, tags, places, organisations and about 15 more files. The `P_<id>` person loader
that `ic2_profile.py` expects is not in the repo.

Regenerating (from knowledge of the LDBC project, not verified here; nothing was downloaded): the output format
`CsvBasic-LongDateFormatter` comes from the legacy Hadoop datagen (`ldbc_snb_datagen_hadoop`, v0.4/0.5: `params.ini` with
`ldbc.snb.datagen.generator.scaleFactor:snb.interactive.10`, serializer CsvBasic, formatter LongDateFormatter, the
`ldbc/datagen` / `ldbc/datagen-standalone:0.4.0` image with a mounted `params.ini`). The newer Spark datagen writes a
different layout (`graphs/csv/bi/composite-merged-fk`, the path `BENCHMARKS.md` mentions) that does not match the
`*_0_0.csv` names the loader reads. The image is about 1 to 2 GB, so it needs a go-ahead before pulling. Output about 1 GB
for SF1 and 10 GB for SF10; est. 10 to 20 minutes and 1 to 2 hours on this Mac. Recommendation: skip the full regeneration
and use the existing KNOWS-only SF10 file with a small loader as the LDBC leg.

### 4.4 Temporal and FHIR

`~/ws/iris-datasets/re2-tt/traces.tsv` (799 MB, 12,638,043 rows, `ts`, `source`, `predicate`, `target`, `weight`) and
`rcaeval.tsv` (871 MB) are present. `~/.cache/ivg-235/synthea-100` and `synthea-10` are present (FHIR bench).

## 5. What "parity" meant before: published numbers

### 5.1 `docs/performance/BENCHMARKS.md` (v1.83.0, LDBC SF10, M3 Ultra, IRIS 2025.1 Enterprise build 230)

| query                                                 | IVG                               | GES (published)                  |
| ----------------------------------------------------- | --------------------------------- | -------------------------------- |
| IC2 1-hop, COUNT (`KHopCount`)                         | 0.29 ms (was 2.8 ms before the fast path) | 0.14 ms                    |
| IC2 1-hop, IDs                                         | 0.9 ms                            |                                  |
| IC3 2-hop, LIMIT 1000                                  | 1.2 ms (was 14 to 22 ms)          | 4.19 ms                          |
| IC3 2-hop, exact COUNT (`KHop2CountExact`)             | 0.1 ms (was 70 ms)                |                                  |
| 2-hop COUNT DISTINCT fallback / approximate (HLL)      | 70 ms / 5.3 ms                    |                                  |
| IC13 shortest path, SF1 / SF10                         | 0.22 ms / 2.1 to 3.2 ms           | 2.69 ms                          |

Ingestion: `bulk_ingest_edges` (direct `^KG`) 190 to 312K edges/s; `bulk_create_edges` (SQL batch) about 50K edges/s;
`BuildKG` SF10 71 s, `BuildNKG` 422 s. The IC2 and IC3 fast paths are exactly what PERF.md shows the alternative
slowing by 270x to 18,000x (`KHopCount`), so this is where parity matters most and where no registered suite measures it
yet (needs the LDBC KNOWS loader, 3.3). `KHopCount` is exercised by `e2e-gates-kernels` (`test_ic3_exact_count`) only as a
gate.

### 5.2 `tests/benchmarks/results/` (main checkout, gitignored)

`bench.py`, hot p50 in ms, `ivg-os` / `ivg-arno`. The 2026-10-10 run (build 113, 20 runs) was at host load 110 to 200.

| dataset, query | 2026-05-04, build 161, 10 runs | 2026-10-10, build 113, 20 runs |
| -------------- | ------------------------------ | ------------------------------ |
| S Q1           | 0.48                           | 0.53                           |
| S Q2           | 7.95 / 1.54                    | 7.75 / 9.57                    |
| S Q3           | 18.3 / 1.74                    | 11.3 / 13.4                    |
| S Q4           | 30.3 / 1.8                     | 17.0 / 19.0                    |
| S Q5 / Q6      | 0.40 / 0.32                    | 0.40 / 0.67                    |
| M Q1           | 1.16                           | 1.96                           |
| M Q2           | 61.9 / 13.0                    | 91.1 / 102.3                   |
| M Q3           | 153.8 (10 errors) / 14.7       | 179.9 / 256.2                  |
| M Q4           | 259.2 (10 errors) / 14.6       | 196.3 / 264.2                  |
| M Q5 / Q6      | n/a                            | 0.49 / 0.46                    |

`bench_193_20260606_000718.md` (2026-06-05, Community 2026.1 build 234 and Enterprise 2026.2.0AI build 161): S1 hops 1
`BFSFastJson` 2.04 ms against Direct 1.92 ms; hops 2 56.9 against 614.8 ms; hops 3 151.0 against 4,325.8; hops 4 248.4
against 4,344.6. S2 NKG (S / M): hops 2 10.75 / 12.4 ms, hops 3 23.6 / 23.7, hops 4 25.1 / 25.3, hops 5 22.8 / 24.9. S3
(Community): Q1 0.60 ms, Q2 57.9, Q3 151.4, Q4 249.6.

### 5.3 `benchmarks/*.json` histories (checked in)

- `betweenness_vs_gds_*.json` (29 files, 2026-05-29 to 06-07, spec 170). Newest with GDS (06-02 19:59): karate IVG 17.19
  ms, GDS exact 2.89, GDS sampled(34) 2.54; ER(500) 155.09 / 19.28 / 14.57 (sampled 200); ER(2000) 512.91 / 143.06 / 34.62.
  Newest overall (06-07, IVG only): 16.02 (sd 0.53), 168.13 (41.5), 493.2 (12.1).
- `head_to_head_*.json` (12 files, 05-31 to 06-03). Newest 06-03 13:36, networkx / IVG ms. Karate: degree 0.1 / 0.9,
  betweenness 0.9 / 10.2, closeness 0.4 / 0.5, Leiden 0.9 / 58.9, IVG load 9,793.8. ER(500, 2437e): 0.1 / 2.4, 264.9 /
  38.1, 63.0 / 18.8, 24.5 / 27.5, load 9,084. ER(2000, 9941e): 0.2 / 8.5, 505.5 / 56.5 (sample 200), 1,140.2 / 165.6, 178.5 /
  137.1, load 10,612.6. Pearson against networkx 1.0 throughout.
- `leiden_4way_*.json` (17 files, 05-28 to 06-02). Newest 06-02 16:00, mean ms, IVG (LazyKG+leidenalg) / networkx / igraph / GDS:
  karate 1.5 / 0.6 / 3.5 / 155.1; ER(500) 25.5 / 24.4 / 79.8 / 254.0; ER(2000) 136.7 / 152.5 / 362.8 / 155.1.
- `index_maintenance_strategy_{20000,100000}.json`: see 3.3.

### 5.4 `docs/performance/GRAPH_ALGORITHMS.md` (2026-06-02, M3 Ultra; Community 2026.1 and Enterprise 2026.2.0AI)

- Betweenness sampled=200 (IVG Rust / IVG OS-parallel / networkx): karate 0.3 / 5 / about 2 ms; ER(500, 1230e) 2.3 / 167 /
  about 80; ER(2000, 5936e) 8 / 500 / about 8,000. Exact: karate 0.9 / 4.5 / about 2; ER(500) 3.7 / 341 / about 80; ER(2000)
  43 / 4,700 / about 8,000.
- Leiden: karate 14 ms (networkx Louvain 2); ER(500, 2437e) about 50 (networkx 200); ER(2000, 9941e) about 265 (networkx 2,000).
- Closeness (harmonic), Enterprise 2026.2.0AI: ER(500) fast 19 ms against 840 OS fallback (44x); ER(2000) 119 against
  22,131 (186x).
- Traversal and query latency (v1.97.0, 8.9K nodes, 31K edges): 1-hop 570 us; 2-hop BFS 17.6 ms; 3-hop 33.7; 1-hop Arno
  0.3; temporal window 0.1; GetAggregate 1 bucket 0.085, 288 buckets 0.160; VecIndex 4 ms (1K, 128-dim); HNSW 1.7 ms (143K,
  768-dim); PLAID 14 ms; BM25 0.3 ms; PPR 62 ms (10K nodes).

### 5.5 `docs/performance/DRKG_SCALE.md` (Community 2025.1, M3 Ultra, validated 2026-05-31)

Spec 184 (rebuild): edge ingest 511 s (about 11.5K edges/s), index rebuild 50 s, `sync()` 550 s (BuildKG about 46 s,
BuildNKG about 180 s, 2-hop stats), total about 1,115 s. Spec 185 (incremental `^NKG`): ingest 510 s, index rebuild
37 s, sync 2.85 s, total 554.5 s. Spec 185 sync breakdown at 5.87M edges: BuildKG 213 s, BuildNKG 187 s, Build2HopStats
2.9 s, Build2HopExactStats over 120 s. Algorithms at 5.5 to 5.87M edges: `degree_centrality` 0.89 s (0.93 s incremental),
`ClosenessGlobal` (harmonic, maxHops=2) 6.13 s (3.9 s incremental); EigenvectorGlobal `<MAXSTRING>`; TriangleCountJson
over 15 minutes (aborted). Head-to-head ER(2000, 9941e), ms, networkx / IVG / GDS: degree 0.2 / 8.6 / 26.7; betweenness
(k=200) 441 / 55.7 / 184.4; closeness 1,012 / 112.8 / 26.9; Leiden 141 / 132.8 / 281; load 0 / 12,065 / 3,126 (Neo4j 5.24.2 and GDS
2.12.0).

### 5.6 Spec thresholds that serve as parity gates

Spec 213 (ledger): SC-004 p95 at most 500 ms for 100-op commits and 10 s for 5,000-op, overhead at most 2x the plain
transaction, SC-005 head p95 at most 10 ms, SC-006 reconstruct at most 30 s, SC-007 adjacent diff p95 at most 100 ms and full
diff 10 s, SC-008 verify 60 s, SC-011 genesis 60 s. Spec 227: SC-010 scoped search at most 1.10x the 3.2.0 scan, SC-012 100
routes created in under 120 s. Spec 235 (`bench_235.py`): sync at most 1.15x baseline, group PPR 1.20x.

## 6. Commands

```bash
# one-time (idempotent): trees, namespaces, schema, classes, deploy record, Arno
.venv/bin/python scripts/bench/alt/setup_sides.py all [--alt-ref REF]

# run a matrix (serial, interleaved A,B,A,B per suite, waits for a quiet machine)
.venv/bin/python scripts/bench/alt/run_matrix.py --suites bench-q1q6-s cypher-parser-bench --reps 3 --run-id 20261013-a
.venv/bin/python scripts/bench/alt/run_matrix.py --list

# compare
.venv/bin/python scripts/bench/alt/compare.py docs/performance/alt-bench/results/20261013-a
```
