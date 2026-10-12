<!-- markdownlint-disable MD013 MD060 -->

# Implementation Plan: Competitor benchmark environment

**Branch**: `alt/competitor-env` | **Date**: 2026-10-12 | **Spec**: [spec.md](spec.md)
**Input**: `specs/251-alt-competitor-env/spec.md`

## Summary

Compose file plus helper script for Neo4j 5.24 community and Memgraph, a dataset module that reproduces the IVG benchmark
graphs, a loader with read-back verification, and runners that emit `.jsonl`. No IRIS container is touched; nothing here
imports `iris`.

## Technical Context

**Language/Version**: Python 3.12 in a throwaway venv (`requirements.txt`); the repo's `.venv` is not used.
**Primary Dependencies**: `neo4j` driver 5.x (speaks to Memgraph too), `networkx`, `python-igraph`, `leidenalg`, `numpy`,
`scikit-learn` (ARI), `graphdatascience` is not needed (plain Cypher `CALL gds.*`).
**Storage**: Docker named volumes `ivg-neo4j-data-<DS>`, `ivg-memgraph-data-<DS>`; GDS jar cached in `~/.cache/ivg-bench`.
**Testing**: pytest unit tests that need no database (`tests/unit/test_bench_competitors.py`), plus live checks marked `e2e`.
**Target Platform**: macOS, OrbStack (docker VM: 8 CPU, 64 GB).
**Project Type**: single; tooling under `scripts/bench/competitors/`.
**Constraints**: no timing for the record; smoke timing only. Neo4j and Memgraph run one at a time during measured runs.

## Constitution Check

Principle IV (IRIS backend) does not apply: no IRIS component. The only IRIS-adjacent rule that applies is BRIEF rule 4 and
its parity-round exception: this package does not use any IRIS container.

## Decisions

- **One dataset resident per engine.** Community Neo4j has a single user database, and S, M, L share node ids. Each dataset
  gets its own volume; `competitors.sh start <engine> <ds>` selects it. Switching costs a restart, not a reload.
- **Graph model.** `(:Node {node_id})-[:R]->(:Node)` with a uniqueness constraint (Neo4j) or index (Memgraph) on `node_id`,
  matching the `node_id` property `bench_193_full.py` queries. IVG's unlabeled `{node_id: $x}` lookups hit an index in IVG,
  so labelling the nodes is the fair equivalent, not a favour.
- **Dedupe.** `rdf_edges` keeps one row per (s, p, o); the loaders write the distinct edges in first-seen order.
- **Seed rule.** `get_highest_degree_seed` returns `^NKG("$ND",0)`, the first interned node; on RMAT it is `node_0`, which is
  also the highest out-degree node. The module computes the highest out-degree node (ties by node id) and a unit test pins
  it to node_0 for S, M and L.
- **SP pair rule.** `pick_shortest_path_pair(seed, 4)` runs `BFSFastJson` to 3 hops and takes the first result of maximum
  step. The module replays that order exactly: frontier visited in ASCII order of id, successors in ASCII order, every edge
  recorded (including edges into already seen nodes), first edge with the maximum step.
- **BFS queries.** IVG Q2 to Q4 return the edge list of a breadth-first search. Neo4j has no equal operator, so the
  runner reports `[:R*1..d]` with `count(DISTINCT m)` (the bench_193_full query), and on Memgraph both `*1..d` and the
  `*BFS` operator.
- **Algorithms.** GDS jar 2.12.0 (the release for Neo4j 5.24) is fetched into the cache and mounted. Memgraph algorithms use
  the MAGE modules present in the image; absent ones fall back to the references.
- **DRKG load.** Batched `UNWIND` per relation type (relation names are quoted with backticks); the same code path for both
  engines. `neo4j-admin import` was not used: it needs the database offline and a second CSV export, and one path that runs
  against both engines is easier to trust.

## Project Structure

```text
scripts/bench/competitors/
├── docker-compose.yml
├── competitors.sh          # start | stop | status | reset | gds
├── bench_all.sh            # one serial pass
├── requirements.txt
├── datasets.py             # graphs, seed rule, SP-pair rule, checksums
├── engines.py              # Neo4j and Memgraph drivers behind one small interface
├── load.py                 # load and verify
├── run_queries.py          # traversal suites -> jsonl
├── run_algos.py            # algorithm suites -> jsonl
├── jsonl.py                # record writer, timing helper
└── README.md
tests/unit/test_bench_competitors.py
specs/251-alt-competitor-env/{spec,plan,tasks}.md
```
