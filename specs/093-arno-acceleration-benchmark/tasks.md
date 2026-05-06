# Tasks: Spec 093 — Arno Acceleration Benchmark Suite

## Phase 1: Setup

- [ ] T001 Create `tests/benchmarks/results/.gitkeep` and add `tests/benchmarks/results/*.json` to `.gitignore`
- [ ] T002 Add `seed` parameter to `RMATGenerator` in `tests/benchmarks/graph_gen.py` so runs are reproducible
- [ ] T003 Create `tests/benchmarks/results/` directory

## Phase 2: Foundational — Graph Load + Arno Detection

- [ ] T004 Add `load_graph_to_iris(conn, iris_obj, nodes, edges, dataset_label)` helper in `tests/benchmarks/bench_utils.py` — inserts nodes+edges into `^KG` directly via `BuildKG()`, skips SQL for speed
- [ ] T005 Add `detect_arno(iris_obj)` in `tests/benchmarks/bench_utils.py` — uses `engine.status()` or direct `Graph.KG.NKGAccel.Capabilities` call; returns `{"bfs": bool, "ppr": bool}`
- [ ] T006 Add `get_highest_degree_seed(iris_obj)` in `tests/benchmarks/bench_utils.py` — walks `^KG("deg",...)` to find max-degree node ID; used as BFS seed
- [ ] T007 Add `pick_shortest_path_pair(iris_obj, seed, target_distance=4)` in `tests/benchmarks/bench_utils.py` — runs 1-hop BFS from seed, picks a node at step 4 as the SP target

## Phase 3: Core Benchmark Harness (US1 — Single Command Run)

- [ ] T008 [US1] Create `tests/benchmarks/bench.py` — main entry point with argparse: `--datasets`, `--runs`, `--seed`, `--skip-load`, `--neo4j-uri`, `--neo4j-password`, `--memgraph-uri`, `--compare`
- [ ] T009 [US1] Implement Q1 runner in `bench.py` — 1-hop expand via `engine.execute_cypher("MATCH (s {id:$id})-[:R]->(n) RETURN count(n)", {"id": seed})`; times 10 runs; records min/p50/p90/p99/max/result_count
- [ ] T010 [US1] Implement Q2–Q4 runner (`ivg-os` path) in `bench.py` — calls `Graph.KG.Traversal.BFSFastJson` directly via `iris_obj.cls(...).BFSFastJson(seed, preds, depth)` for depths 2/3/4
- [ ] T011 [US1] Implement Q2–Q4 runner (`ivg-arno` path) in `bench.py` — calls `Graph.KG.NKGAccel.BFSJson` if `detect_arno()["bfs"]` is True; falls back to `ivg-os` with warning if not available
- [ ] T012 [US1] Implement Q5 runner (shortestPath) in `bench.py` — uses `engine.execute_cypher("MATCH p=shortestPath((a {id:$a})-[*..8]-(b {id:$b})) RETURN length(p)", {"a": src, "b": dst})`
- [ ] T013 [US1] Implement Q6 runner (weighted SP) in `bench.py` — uses `engine.execute_cypher("CALL ivg.shortestPath.weighted($a,$b,'weight',9999,10) YIELD totalCost RETURN totalCost", {"a": src, "b": dst})`
- [ ] T014 [US1] Implement timing harness in `bench.py` — `run_timed(fn, warmup=3, runs=10)` returns `{"min", "p50", "p90", "p99", "max", "cold_p50", "hot_p50", "values": [...]}`
- [ ] T015 [US1] Implement correctness check in `bench.py` — after Q2/Q3/Q4, assert `set(ivg_os_node_ids) == set(ivg_arno_node_ids)`; print PASS/FAIL per query; fail loud on mismatch

## Phase 4: Output (US2 — JSON + Table)

- [ ] T016 [P] [US2] Implement JSON result writer in `bench.py` — writes `tests/benchmarks/results/bench_{timestamp}.json` with full `meta` + `results` + `correctness` structure per spec
- [ ] T017 [P] [US2] Implement console table printer in `bench.py` — prints comparison table to stdout: rows = queries, columns = ivg-os / ivg-arno / neo4j / memgraph / arno-speedup
- [ ] T018 [US2] Implement `--compare` mode in `bench.py` — loads two JSON result files, prints delta table showing p50 change per (dataset, query, path)

## Phase 5: External Systems (US3 — Neo4j + Memgraph)

- [ ] T019 [P] [US3] Create `tests/benchmarks/load_memgraph.py` — mirrors existing `load_neo4j.py`, uses same RMAT edge list; connects via bolt to `MEMGRAPH_URI`
- [ ] T020 [P] [US3] Integrate Neo4j runner into `bench.py` — calls `benchmark_neo4j.benchmark_neo4j()` for Q2/Q3/Q4 depths; skips with warning if `--neo4j-uri` not provided or connection fails
- [ ] T021 [US3] Integrate Memgraph runner into `bench.py` — same query as Neo4j (`MATCH (n)-[:R*1..D]->(m) RETURN count(DISTINCT m)`); skips gracefully if not available

## Phase 6: Datasets + Repeatability (US4)

- [ ] T022 [US4] Implement multi-dataset loop in `bench.py` — iterates over `--datasets S M L`; generates each graph from `RMATGenerator(seed=args.seed)` with params `(nodes, edges)` per dataset label
- [ ] T023 [US4] Implement `--skip-load` in `bench.py` — skips graph generation and `BuildKG/BuildNKG` calls; asserts `^KG` is non-empty before proceeding
- [ ] T024 [US4] Add `tests/benchmarks/README.md` — documents: single command usage, env vars, output format, how to reproduce results, what each query tests, arno prerequisite (enterprise IRIS + libarno_callout.so)

## Phase 7: Validation Run

- [ ] T025 Run `python tests/benchmarks/bench.py --datasets S M --runs 10` against `gqs-ivg-test` container
- [ ] T026 Verify correctness checks all PASS for Q2/Q3/Q4
- [ ] T027 Record actual p50 numbers in `specs/093-arno-acceleration-benchmark/results.md` — fill in the comparison table with real measured values
- [ ] T028 Verify SC-008 through SC-014 from spec.md pass or document which targets need arno enabled

## Dependencies

```
T001-T003  (setup, parallel)
    ↓
T004-T007  (foundational utils, parallel)
    ↓
T008-T015  (core harness, sequential within phase)
    ↓
T016-T018  (output, T016+T017 parallel)
T019-T021  (external, T019+T020 parallel)
T022-T024  (datasets, parallel after T008)
    ↓
T025-T028  (validation run, sequential)
```

## MVP Scope

To get real numbers with minimum effort: **T001–T003, T004–T007, T008–T015, T016–T017, T022–T023, T025–T028**.
Skip external systems (T019–T021) on first pass — IVG vs arno numbers are the primary goal.
