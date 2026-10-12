<!-- markdownlint-disable MD013 MD060 -->

# Tasks: Competitor benchmark environment

**Input**: `specs/251-alt-competitor-env/spec.md`, `plan.md`. Tests come first in each phase.

## Phase 1: Tests that fail first

- [x] T001 [US1] `tests/unit/test_bench_competitors.py`: RMAT S/M/L reproduce the generator, distinct-edge counts (S 4363,
  M 47641), seed `node_0`, out-degree of the seed 186 / 749, reach counts 550/615/621 and 4398/5440/5488.
- [x] T002 [US1] Degree checksum is stable and changes when one edge changes.
- [x] T003 [US2] SP-pair rule: replays the BFSFastJson order on a hand-made graph (an edge into an already seen node wins
  when it is first in order).
- [x] T004 [US2] Record writer: schema keys, one JSON object per line, values_ms numeric.
- [x] T005 [US2] Reference algorithms (networkx/igraph) return the same community count on a fixed graph; the runner's
  fallback label says `networkx` or `igraph`.
- [x] T006 [US3] `competitors.sh` has start, stop, status; compose file declares ports 7688 and 7689 and the password.

## Phase 2: Environment

- [x] T007 `docker-compose.yml`, `competitors.sh`, `requirements.txt`; fetch the GDS jar into the cache.
- [x] T008 Start both engines, confirm bolt on 7688 and 7689, confirm `gds.version()` and list the Memgraph procedures (no MAGE in the image).

## Phase 3: Datasets and loaders

- [x] T009 `datasets.py` (RMAT, neo4j_comparison graph, LDBC KNOWS, DRKG, seed rule, SP rule, checksum).
- [x] T010 `engines.py`, `load.py` (load + verify) for Neo4j and Memgraph.
- [x] T011 Live: load S, M, L into both; verify; load LDBC SF10 KNOWS; load DRKG into Neo4j and report time.

## Phase 4: Runners

- [x] T012 `jsonl.py`, `run_queries.py`: Q1-Q6, bench_193 `[*1..d]`, IC2/IC3/IC13.
- [x] T013 `run_algos.py`: PageRank, degree, WCC, betweenness, Leiden/Louvain; GDS, Memgraph procedures, networkx, igraph.
- [x] T014 Live smoke on S, 1 rep, each runner; then stop the containers.

## Phase 5: Documentation

- [x] T015 README (start, load, run, stop, versions, memory, fairness caveats); markdownlint and prettier.
