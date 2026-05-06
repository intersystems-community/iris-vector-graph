# IVG Benchmark Suite (Spec 093)

Repeatable benchmark harness measuring IVG traversal performance:
- `ivg-os`: ObjectScript `BFSFastJson` over `^KG` globals
- `ivg-arno`: Rust `NKGAccel.BFSJson` over `^NKG` integer adjacency (enterprise IRIS only)

## Quick start

```bash
cd tests/benchmarks
conda run -n py312 python bench.py
```

Default: dataset M (10K nodes / 50K edges), 10 runs, warmup 3, seed 42.

## Options

```
--datasets S M L        Graph sizes (S=1K/5K, M=10K/50K, L=100K/500K)
--runs N                Timed runs per query (default 10)
--warmup N              Warmup runs discarded (default 3)
--seed N                RMAT random seed (default 42, reproducible)
--skip-load             Skip graph load, reuse existing ^KG/^NKG
--outdir PATH           Output directory (default tests/benchmarks/results/)
--compare A.json B.json Delta table between two result files
```

## Environment variables

```
IRIS_HOST        localhost
IRIS_PORT        1972
IRIS_NAMESPACE   USER
IRIS_USERNAME    _SYSTEM
IRIS_PASSWORD    SYS
```

## Arno prerequisite

Arno (Rust BFS via `NKGAccel.BFSJson`) requires enterprise IRIS with `libarno_callout.so`
deployed to the IRIS manager directory. Community IRIS reports `arno BFS not available`.

To run with arno:
```bash
IRIS_PORT=64780 conda run -n py312 python bench.py --datasets M --skip-load
```
(assumes IVG classes and graph data already loaded in enterprise container)

## Output

Results written to `tests/benchmarks/results/bench_YYYYMMDD_HHMMSS.json`.
The file includes: metadata, per-query latency percentiles, correctness checks.
JSON result files are gitignored.

## Query catalog

| ID | Pattern | Engine path |
|----|---------|-------------|
| Q1 | 1-hop count (SQL MATCH) | IRISGraphEngine.execute_cypher |
| Q2 | 2-hop BFS, DISTINCT nodes | BFSFastJson / NKGAccel.BFSJson |
| Q3 | 3-hop BFS | same |
| Q4 | 4-hop BFS | same (MAXSTRING risk on M+) |
| Q5 | shortestPath (unweighted BFS) | ShortestPathJson |
| Q6 | weighted shortestPath (Dijkstra) | ivg.shortestPath.weighted |

## Known limitations

- Q4 on dataset M hits IRIS `<MAXSTRING>` (3.6MB string limit) for high-degree seeds.
  Use `--datasets S` or wait for arno (chunked output, no string limit).
- Arno columns show `n/a` on community IRIS — enterprise required.
- SP pair selection uses BFS depth=3 max to avoid MAXSTRING during setup.
