# Feature Specification: Competitor benchmark environment

**Feature Branch**: `alt/competitor-env`
**Created**: 2026-10-12
**Status**: Draft
**Input**: Parity round work package "competitor-env" (`docs/research/alt-impl/BRIEF-PARITY.md`): stand up the comparison databases, the loaders and the query runners that mirror the repo's own benchmarks, so the lead can run them head-to-head with IVG (baseline 6f17be0f and the alternative) in one serial pass.

## User Scenarios & Testing

### User Story 1 - The same graph in every engine (Priority: P1)

The lead loads RMAT datasets S, M and L (seed 42), a DRKG graph and the LDBC SNB KNOWS graph into Neo4j and Memgraph, and
needs proof that those graphs are exactly the graphs IVG benchmarks use.

**Why this priority**: a latency comparison on different graphs is meaningless.

**Independent Test**: build the dataset in memory with the same generator IVG uses, load it, read the node count, edge
count and a degree checksum back from the database, and compare them with the in-memory values.

**Acceptance Scenarios**:

1. **Given** dataset S, **When** it is loaded into Neo4j, **Then** the database holds 1,000 nodes and the same de-duplicated
   edge list `tests/benchmarks/bench_utils.load_graph_to_iris` writes (4,363 edges), and the out-degree checksum equals the
   generator's.
2. **Given** a loaded database, **When** `load.py verify` runs, **Then** it exits non-zero on any mismatch.

---

### User Story 2 - Query suites that mirror the repo's (Priority: P1)

Q1 to Q6 of `tests/benchmarks/bench.py`, the Neo4j section of `bench_193_full.py`, the IC2/IC3/IC13-style queries, and the
algorithm legs of `tests/perf/test_head_to_head.py`, `test_betweenness_vs_gds.py` and `test_leiden_four_way.py` run on each
engine with the seeds and start nodes the IVG suites use, and write one JSON object per line.

**Why this priority**: this is the deliverable the lead runs.

**Independent Test**: run each runner on dataset S with one rep and check the .jsonl against the schema.

**Acceptance Scenarios**:

1. **Given** dataset S in Neo4j, **When** the traversal suite runs with 1 rep, **Then** every record carries engine, suite,
   query, metric `latency_ms`, `values_ms` and `params`, and the result counts equal the IVG recorded counts for S.
2. **Given** no GDS procedures on an engine, **When** an algorithm leg needs them, **Then** the runner falls back to the
   networkx or igraph reference and says so in `params.implementation`.

---

### User Story 3 - Start, stop, status without surprises (Priority: P2)

One helper script starts, stops and reports the databases, keeping a persistent named volume per dataset so a dataset is
loaded once.

**Acceptance Scenarios**:

1. **Given** the compose file, **When** `competitors.sh start neo4j S` runs, **Then** Neo4j answers on bolt 7688 with user
   `neo4j` and password `ivgbenchpw`, and Memgraph answers on 7689.
2. **Given** a stop, **When** the engine starts again on the same dataset, **Then** the data is still there.

### Edge Cases

- Duplicate RMAT edges (5,000 generated, 4,363 distinct in S): IVG stores each (s, p, o) once, so the loaders do too.
- Self loops are kept (IVG keeps them).
- Neo4j Community has one user database: datasets live in separate volumes, one resident at a time.
- Variable-length expansion can exceed any sane time at depth 4: a per-query timeout turns it into a recorded error, not a hang.
- Memgraph is in-memory: it must be stopped gracefully so the snapshot is written.

## Requirements

### Functional Requirements

- **FR-001**: `docker-compose.yml` defines Neo4j 5.24 community (bolt 7688, `neo4j` / `ivgbenchpw`) and Memgraph (bolt 7689),
  each with a persistent named volume per dataset, memory settings stated.
- **FR-002**: Neo4j carries the GDS plugin when the jar is obtainable; otherwise the algorithm legs fall back to networkx
  or igraph and say so.
- **FR-003**: `datasets.py` reproduces the IVG graphs: RMAT S/M/L with `RMATGenerator(seed=42)` from
  `tests/benchmarks/graph_gen.py`, de-duplicated; node ids `node_<i>`; predicate `R`; the `neo4j_comparison.py` M graph; the
  LDBC SNB KNOWS file; DRKG.
- **FR-004**: The seed node and the shortest-path pair are derived from the dataset by the rule the IVG suites use
  (`get_highest_degree_seed`, `pick_shortest_path_pair`), documented, and unit-tested against the counts IVG recorded.
- **FR-005**: `load.py` loads Neo4j and Memgraph, then verifies node count, edge count and a degree checksum.
- **FR-006**: `run_queries.py` runs the traversal suites on Neo4j and Memgraph; `run_algos.py` runs the algorithm suites on
  Neo4j (GDS), Memgraph (MAGE) and the networkx/igraph references; all write `.jsonl`.
- **FR-007**: Warmup (default 3) and runs (default 20) are options. The schema is
  `{"engine","suite","query","metric":"latency_ms","values_ms":[...],"params":{...}}`.
- **FR-008**: A README states start, load, run, stop, versions, memory settings, and the fairness caveats.

### Key Entities

- **Dataset**: name, node list, de-duplicated edge list, seed node, SP pair, degree checksum.
- **Record**: one measured query on one engine.

## Success Criteria

- **SC-001**: For S, M and L the node count, edge count and out-degree checksum read back from each engine equal the generator's.
- **SC-002**: Seed node and result counts for S and M equal those in `tests/benchmarks/results/bench_20261010_153012.json`
  (Q1 186 and 749; BFS reach 550/615/621 and 4398/5440/5488).
- **SC-003**: Every runner produces a valid `.jsonl` on S with 1 rep, and both containers are stopped afterwards with volumes kept.
