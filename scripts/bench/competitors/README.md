<!-- markdownlint-disable MD013 MD060 -->

# Competitor benchmark environment

Neo4j 5.24 Community and Memgraph, loaded with exactly the graphs the IVG benchmarks use, plus query and algorithm
runners that mirror the repo's suites and write one JSON line per measured query. Nothing here touches an IRIS
container and nothing imports `iris`.

Spec: `specs/251-alt-competitor-env/`. Unit tests (no database): `tests/unit/test_bench_competitors.py`.

## Versions

| Component     | Version                                             | Notes                                                                                               |
| ------------- | --------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| Neo4j         | 5.24.2 Community (`neo4j:5.24-community`, 74ce2eef) | bolt `localhost:7688`, browser `7475`, user `neo4j`, password `ivgbenchpw`                          |
| Neo4j GDS     | 2.12.0 (Community Edition)                          | 64 MB jar from `graphdatascience.ninja`, sha256 `53fb5ba0...c21ebf`, cached in `~/.cache/ivg-bench` |
| Memgraph      | 3.9.0 (`memgraph/memgraph:latest`, fbe0e850)        | bolt `localhost:7689`, no authentication                                                            |
| Python driver | `neo4j` 5.28.6 (talks to both over Bolt)            | Python 3.12                                                                                         |
| References    | networkx 3.7, python-igraph 1.0.0, leidenalg 0.12.0 | numpy 2.5.4, scipy 1.18.1, pandas 3.0.6, scikit-learn 1.9.1                                         |
| Docker        | 29.4.0 (OrbStack VM, 8 CPUs, 64 GB)                 | host has 128 GB; the VM also runs the IVG and other IRIS containers                                 |

No image was pulled: `neo4j:5.24-community`, `neo4j:5.21-community` and `memgraph/memgraph:latest` were already local.
The only download is the GDS jar. Memgraph's native algorithm library (MAGE) is a separate, larger image and was not
pulled; see "What each engine can run".

## Memory settings

- Neo4j: heap 16g (initial = max), page cache 16g (`NEO4J_HEAP`, `NEO4J_PAGECACHE` override). The DRKG store is a few GB,
  so every dataset is fully cached after warmup. 32 GB of the 64 GB VM; GDS projections of DRKG (5.87M relationships, plus
  an undirected copy) fit in the heap. GDS Community caps concurrency at 4.
- Memgraph: `--memory-limit 32768` MiB (`MEMGRAPH_MEMORY_MIB`), `--query-execution-timeout-sec 300`
  (`MEMGRAPH_QUERY_TIMEOUT`), snapshot on exit and recovery on startup.
- Run one engine at a time when timing (`bench_all.sh` does). They share the VM with each other and with IVG.

## Set up

```bash
cd scripts/bench/competitors
uv venv --python 3.12 /tmp/ivg-comp-venv
uv pip install --python /tmp/ivg-comp-venv/bin/python -r requirements.txt
export PY=/tmp/ivg-comp-venv/bin/python
./competitors.sh gds        # once: fetch the GDS jar (64 MB) into ~/.cache/ivg-bench/neo4j-plugins
```

## Start, load, run, stop

Neo4j Community has one user database and the RMAT datasets share node ids, so **each dataset has its own named volume**
(`ivg-neo4j-data-<DS>`, `ivg-memgraph-data-<DS>`) and one dataset is resident per engine. Switching dataset is a restart
onto another volume, not a reload. Stopping keeps every volume.

```bash
./competitors.sh start neo4j S            # or memgraph, or both; second argument is the dataset
$PY load.py --engine both --dataset S     # load, then read back and compare with the generator (exit 1 on mismatch)
$PY run_queries.py --engine both --dataset S --out results/S.jsonl                 # --warmup 3 --runs 20 by default
$PY run_algos.py   --engine neo4j,memgraph,networkx,igraph --dataset S --out results/S_algos.jsonl
./competitors.sh status
./competitors.sh stop both                # graceful (Memgraph writes its snapshot); volumes kept
./competitors.sh reset neo4j S --yes      # the only command that deletes a volume
```

`PY=... WARMUP=3 RUNS=20 ./bench_all.sh OUTDIR [DATASET ...]` does the whole serial pass: for each dataset and each engine
in turn, start, load if the volume is empty, run both suites, stop; then the in-process references.

Docker note: the compose services use `network_mode: bridge`. On this host `docker compose up` could not create a
project network ("all predefined address pools have been fully subnetted").

### Datasets

All loaded and verified in both engines on 2026-10-12 (node count, edge count, out/in-degree checksum, and for graphs up
to 1M edges the whole edge multiset).

| Dataset                     | Nodes            | Edges              | What it is                                                                                                 | Load, Neo4j / Memgraph |
| --------------------------- | ---------------- | ------------------ | ---------------------------------------------------------------------------------------------------------- | ---------------------- |
| `S`                         | 1,000            | 4,363              | RMAT seed 42, 5,000 generated, 4,363 distinct                                                              | 0.3 s / 0.1 s          |
| `M`                         | 10,000           | 47,641             | RMAT seed 42, 50,000 generated                                                                             | 7.5 s / 0.6 s          |
| `L`                         | 100,000          | 485,854            | RMAT seed 42, 500,000 generated                                                                            | 18.7 s / 5.9 s         |
| `M193`                      | 10,000           | 47,691             | M as `bench_193_full.py` builds it (one generator for S then M, so M continues the random stream)          | 2.7 s / 0.5 s          |
| `NCM`                       | 10,000           | 50,000             | the graph of `benchmarks/neo4j_comparison.py` (label `N`, property `id`)                                   | 2.7 s / 0.7 s          |
| `LDBC10`                    | 62,371           | 1,938,516          | LDBC SNB SF10 `person_knows_person` (all rows); see below                                                  | 60.6 s / 25.6 s        |
| `DRKG`                      | 97,238           | 5,874,258          | `data/drkg/drkg.tsv` (5,874,261 rows, 3 exact duplicates dropped), entity-type labels, relation-name types | 206.9 s / 65.1 s       |
| `karate`, `er500`, `er2000` | 34 / 500 / 2,000 | 78 / 2,437 / 9,941 | the `test_head_to_head.py` fixtures                                                                        | under 5 s              |
| `btw500`, `btw2000`         | 500 / 2,000      | 1,230 / 5,936      | the `test_betweenness_vs_gds.py` fixtures                                                                  | under 5 s              |

Defined but not loaded (load on demand): `L193`, `LDBC10_10k` (the first 10,000 SF10 rows, what
`three_engine_benchmark.py` reads by default), `LDBC100` (SF100, 19.9M rows, 808 MB file).

DRKG load: batched `UNWIND` per relation type, 20,000 rows per batch, the same code path for both engines.
`neo4j-admin database import` was not used: it needs the database offline and a second CSV export, and one path that
runs against both engines is easier to trust. Neo4j took 206.9 s (28,000 edges/s), Memgraph 65.1 s.

**LDBC.** The only SNB data on this machine is the `person_knows_person` file for SF10 and SF100
(`social_network-sf{10,100}-CsvBasic-LongDateFormatter/dynamic/` in the main checkout). The SF10 directory holds that one
file; `/tmp/sf10_out`, which `ldbc_full_loader.py` reads, does not exist, and no datagen is installed. So only the
KNOWS graph can be loaded; the other IC queries (those touching posts, comments, tags) cannot run. IC2, IC3, IC13 and
the friend count run as `benchmarks/three_engine_benchmark.py` writes them: `Person:<id>` node ids, label `Person`,
property `id`, relationship `KNOWS`, rows as directed edges in file order. Persons that appear in no KNOWS row are not
loaded (62,371 persons appear in the SF10 file), as in that script. IC13 uses the first two of its `SEEDS`.

## What the runners do

`run_queries.py` writes one record per query:

```json
{
  "engine": "neo4j",
  "suite": "bench_q",
  "query": "Q2",
  "metric": "latency_ms",
  "values_ms": [3.1, 2.9],
  "params": {
    "dataset": "S",
    "cypher": "...",
    "bindings": { "x": "node_0" },
    "result": 550,
    "expected": 550,
    "match": true
  }
}
```

`values_ms` are the timed runs (warmup runs are not recorded); `params.expected` is the count the IVG benchmark recorded
or that the dataset module computes, and `params.match` says whether the engine agreed. A failed leg has empty
`values_ms` and `params.error`; a leg the engine cannot run has empty `values_ms` and `params.skipped`. A
`<out>.meta.json` beside each file holds versions, the dataset checksum, the seed, warmup, runs and timeout. Pass
`--only Q1,Q2`, `--suite bench_q`, `--warmup N`, `--runs N`, `--timeout-s S`.

| Suite              | Source                                       | Queries (id)                                                                                                                                |
| ------------------ | -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `bench_q`          | `tests/benchmarks/bench.py` (RMAT datasets)  | Q1 1-hop count; Q2, Q3, Q4 distinct nodes within 2, 3, 4 hops; Q5 shortestPath; Q6 weighted path                                            |
| `bench193`         | `bench_193_full.py` section 5                | B193_h2, B193_h3, B193_h4: `MATCH (n {node_id:$x})-[*1..N]->(m) RETURN count(distinct m)` verbatim                                          |
| `neo4j_comparison` | `benchmarks/neo4j_comparison.py` (NCM)       | NC_Q1, NC_Q2a, NC_Q3                                                                                                                        |
| `ldbc`             | `benchmarks/three_engine_benchmark.py`       | IC2, IC3, IC13, COUNT_friends                                                                                                               |
| `drkg`             | new (no DRKG query suite exists in the repo) | D_Q1, D_Q2 (`--drkg-max-depth 3` adds D_Q3), D_SP; Memgraph adds `_bfs` variants                                                            |
| `algos`            | the three `tests/perf` files                 | pagerank, degree_out, degree_both, wcc, scc, betweenness_exact, betweenness_sampled200, closeness_harmonic, louvain, leiden, triangle_count |

On Memgraph each `Q2`..`Q4` also has a `_bfs` variant using its BFS expansion operator (set semantics, like IVG's
`BFSFastJson`); both forms are reported.

### Seed and start nodes (same as the IVG suites)

- **Seed.** `bench_utils.get_highest_degree_seed` returns `^NKG("$ND", 0)`, the first node `BuildNKG` interned. On the
  RMAT graphs that is `node_0`, which is also the node with the highest out-degree (186 distinct out-edges in S, 749
  in M). `datasets.highest_out_degree_seed` computes the highest out-degree node (ties by node id) and a unit test pins
  it to `node_0` for S, M and L. DRKG and LDBC have no recorded IVG seed: DRKG uses the highest out-degree entity
  (`Anatomy::UBERON:0000473`, 23,866 out-edges); LDBC uses `Person:933`. `NCM` uses the highest total degree as
  `neo4j_comparison.py` does (`node_8156`).
- **Shortest-path pair.** `pick_shortest_path_pair(seed, 4)` runs `BFSFastJson` for 3 hops and takes the first row of
  maximum step. `datasets.ivg_bfs_edges` replays that order exactly: each hop visits the frontier in ASCII order of node
  id, each node's out entries in order, every edge is recorded (including edges into nodes already seen), and the
  first maximum-step row wins. **On S and M the pair is `(node_0, node_0)`**: the first 3-hop row is an edge back into the
  seed, so the recorded IVG Q5 asks for a path from a node to itself (length 0). Q5 and Q6 are therefore reported twice:
  `Q5`/`Q6` with that pair, `Q5_distinct`/`Q6_distinct` with the first maximum-step target that is not the seed
  (S: `node_11`, one hop away). Neo4j `shortestPath()` refuses identical endpoints, so `Q5` records an error there;
  Memgraph returns no row.
- **Unit weights.** The IVG Q6 call `ivg.shortestPath.weighted($a, $b, 'weight', ...)` returned nothing in the recorded
  runs (`result_count` 0): these edges have no `weight` property. The competitor Q6 runs with unit weights (GDS
  Dijkstra on a projection built once, not timed; Memgraph `*WSHORTEST`). It measures the intended operation, not
  the empty answer IVG gave.
- **RMAT generation.** `tests/benchmarks/graph_gen.RMATGenerator(seed=42)` itself is imported (not copied). Edges are
  distinct on `(s, o)`, first-seen order, because `rdf_edges` keeps one row per `(s, p, o)`. Node ids `node_<i>`,
  predicate `R`. `bench.py` makes a fresh generator per dataset (`S`, `M`, `L`); `bench_193_full.py` makes one for the
  run, so with `--datasets S M` its M is `M193`.

Counts were checked against `tests/benchmarks/results/bench_20261010_153012.json`: Q1 186 / 749, distinct reach 550,
615, 621 (S) and 4398, 5440, 5488 (M) come out the same in Neo4j and Memgraph (smoke runs on S and M).

## What each engine can run (algorithms)

| Algorithm              | Neo4j                                | Memgraph                                              | networkx / igraph                                |
| ---------------------- | ------------------------------------ | ----------------------------------------------------- | ------------------------------------------------ |
| pagerank               | GDS `pageRank.stream`, 20 iterations | `nxalg.pagerank` (networkx inside the server)         | networkx; igraph PRPACK                          |
| degree_out/degree_both | GDS `degree.stream`                  | Cypher aggregation                                    | yes                                              |
| wcc, scc               | GDS `wcc`, `scc`                     | `wcc.get_components` (Python module), `nxalg` for scc | yes                                              |
| betweenness            | GDS exact and sampled 200, seed 42   | `nxalg.betweenness_centrality` (directed graph)       | networkx exact + k=200; igraph exact             |
| closeness (harmonic)   | GDS `closeness.harmonic.stream`      | skipped                                               | yes                                              |
| louvain, leiden        | GDS `louvain`, `leiden` (seed 42)    | skipped                                               | networkx Louvain; igraph Louvain and `leidenalg` |
| triangle_count         | GDS `triangleCount`                  | skipped                                               | yes                                              |

- **GDS installed.** The jar is public and small (64 MB), so it is mounted and works: `gds.version()` is 2.12.0 and the
  streaming procedures above run on Community Edition. The betweenness-vs-GDS and Leiden four-way legs run on GDS;
  networkx and igraph are the references the existing tests already use.
- **Memgraph has no native algorithm library in this image.** MAGE ships as `memgraph/memgraph-mage`, a much larger
  image, not pulled. What the image has (`CALL mg.procedures()`): the `nxalg` module, which runs networkx inside the
  server, and `wcc.get_components`. Those legs are labelled with their implementation and capped by
  `--memgraph-nx-max-edges` (default 1,000,000): `wcc.get_components` timed out at 300 s on DRKG. Closeness, community detection
  and triangle counting have no procedure there and are recorded as skipped; the networkx/igraph records stand in.
  A Memgraph run with MAGE would need that image.
- **Timed region.** The procedure call and fetching every result row (as `test_head_to_head.py` does, which also
  looks up each node's id). Building a GDS projection (`gds_project_natural`, `gds_project_undirected`) and building the
  networkx or igraph graph (`build_graph`) are separate records, not part of an algorithm's latency.
- **Cross-check.** Score-producing legs carry `pearson_vs_igraph` (PageRank, degrees, betweenness, closeness). S: GDS
  0.9999 (PageRank), 1.0 (degrees), 0.998 (exact betweenness). Component counts agree across all four engines (S: 262
  weak, 511 strong; DRKG: 303 and 66,009 in GDS and igraph) and so do triangle counts (S: 10,479).
- **Guards.** Exact betweenness and closeness run only up to `--ref-max-nodes` for the references (networkx 3,000,
  igraph 20,000) and 20,000 nodes for GDS; the references skip triangle counting and community detection above
  `--ref-heavy-max-edges` (1M) and networkx above `--ref-max-edges` (2M). Every skip is a record with `params.skipped`.
- **Fixtures** (`karate`, `er500`, `er2000`, `btw500`, `btw2000`) store one direction per edge and are undirected
  graphs: GDS uses its undirected projection for every algorithm (as `test_head_to_head.py` does), Memgraph's degree is
  both directions. Memgraph's `nxalg` PageRank and betweenness treat the stored direction as real, so their Pearson
  against the undirected references is low there; the record says so in `params.note`.

## Fairness caveats

IVG entry points the repo's benchmarks use, and what each competitor query is compared with. Both columns are reported;
nothing is dropped.

| Competitor query               | IVG entry point in the repo's benchmark                                                                                                               |
| ------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| Q1 (`bench.py`)                | `engine.execute_cypher("MATCH (s)-[:R]->(n) WHERE s.node_id = $id RETURN count(n)")`: Python parse and translate, SQL or `KHopCount`                  |
| Q2 to Q4                       | `Graph.KG.Traversal.BFSFastJson` (label `ivg-os`) and `Graph.KG.NKGAccel.BFSJson` (label `ivg-arno`), both via `call_classmethod_large`               |
| Q5, Q6                         | `engine.execute_cypher` with `shortestPath` and `CALL ivg.shortestPath.weighted`                                                                      |
| B193_h2..h4 (`bench_193_full`) | section 2: `engine.execute_cypher("MATCH (n {node_id:$x})-[*1..N]->(m) RETURN m.node_id")`; section 1 compares `BFSFastJson` with `BFSFastJsonDirect` |
| NC_Q1, NC_Q2a, NC_Q3           | `neo4j_comparison.py`: `BFSFastJson` at depth 1, 2, 3 through the Native API                                                                          |
| IC2, IC3, IC13 (LDBC)          | `three_engine_benchmark.py`: `engine.execute_cypher`; `docs/performance/BENCHMARKS.md` quotes `KHopNeighborIds`, `KHop2NeighborIds`                   |

Things that are not equal, so a ratio from these files is not a clean engine-versus-engine number:

1. **Transport.** Neo4j and Memgraph are queried over Bolt on loopback into the Docker VM, through the Python driver.
   IVG's Python benchmark reaches its container over TCP through the IRIS Native API (`classMethodValue`) or the DB-API.
   Both include a round trip and a client library; the server-side `$ZHorolog` timings in `PERF.md` exclude it.
   `execute_cypher` adds Python parse and translate time that a Bolt query does not have.
2. **Result shape.** `BFSFastJson` returns every traversed edge as JSON (a 3.6 MB string limit forces chunking at depth
   4 on M); the Cypher queries return one count. The edge list is more work to build and ship. Memgraph's `*BFS` variants
   are the closest set-semantics equivalent.
3. **Trail semantics.** Neo4j and Memgraph `[*1..N]` enumerate relationship-unique trails and then de-duplicate; IVG's BFS
   keeps a visited set. Counts agree, work does not. Depth 4 on M took seconds in Memgraph's plain `*1..4` and
   milliseconds in its BFS operator. Each query has a server-side timeout (`--timeout-s`, 120 s) so a runaway becomes a
   recorded error.
4. **Labels and indexes.** The competitor nodes carry a label with a unique constraint (Neo4j) or an index (Memgraph) on
   `node_id`, and the `bench_q` queries name the label. IVG's queries are unlabeled and use its own index. The
   `B193_*` queries are the verbatim bench_193 text, unlabeled: Neo4j and Memgraph have no label to seek, so those pay
   a node scan to find the start. Both forms are reported.
5. **Warmup.** Three untimed warmup runs (the brief's default) fill page caches and the plan cache but may not finish JIT
   warmup for the heavier algorithm legs (a first GDS Louvain on S took 3.3 s, then about 1.7 s). Raise `--warmup` for
   algorithms if a run looks bimodal.
6. **References are lower bounds.** networkx and igraph run in-process on an in-memory graph with no wire and no
   storage, and their graph build is excluded. They show what the algorithm costs, not what a database client sees.
7. **Same machine.** The Docker VM has 8 CPUs; GDS Community uses at most 4. The IVG container shares that VM. Do not
   run an IVG benchmark and a competitor benchmark at the same time.
8. **Leiden.** GDS Leiden optimises modularity; IVG's is CPM with `gamma` (the four-way test says each engine runs in
   its natural mode). Community counts differ by design.
9. **Memgraph quirks found while building this.** `*WSHORTEST` with a unit lambda returns `total_weight = hops + 1`
   (the lambda also prices the start vertex), so the query returns the hop count from the path. `*BFS` never returns its start
   node, so on a seed that lies on a cycle it is one short of the trail count (the expected count is adjusted).
10. **Data.** Duplicate RMAT edges are collapsed, self loops are kept, parallel edges of different relation types are
    kept (DRKG). `rdf_edges` and the loaders agree on that.

## Files

| File                             | Purpose                                                                 |
| -------------------------------- | ----------------------------------------------------------------------- |
| `docker-compose.yml`             | Neo4j and Memgraph services, per-dataset named volumes, memory settings |
| `competitors.sh`                 | `start`, `stop`, `status`, `gds`, `reset`                               |
| `bench_all.sh`                   | one serial pass over datasets and engines                               |
| `datasets.py`                    | the graphs, the seed and shortest-path rules, the degree checksum       |
| `engines.py`                     | Neo4j and Memgraph behind one interface                                 |
| `load.py`                        | load and verify                                                         |
| `run_queries.py`, `run_algos.py` | traversal and algorithm runners                                         |
| `refalgos.py`, `jsonl.py`        | networkx/igraph references; record writer and timing loop               |
