<!-- markdownlint-disable MD013 MD060 -->

# Performance: alt-integration (113ccea7) vs baseline 6f17be0f (4.2.3)

A = baseline classes in IVGPERFA. B = alternative classes in IVGPERFB. Both on `ivg-iris-enterprise`
(IRIS 2026.3.0AI build 113, ARM64 container). Timed with `$ZHorolog` inside IRIS, so client round trips are
excluded.

## How it was run

- Method: one warm-up run per measurement, then 5 reps, interleaved A,B,A,B. Each rep is a fresh `iris session`
  process. Tables give median and min over the 5 reps. B/A is the ratio of medians.
- Generator, identical on both sides (written straight into the globals, baseline layout including
  `deg`/`degp`, graph key 0):
  - 50,000-node power-law-ish graph with 3 predicates (p0, p1, p2).
  - One `hub` with 100,000 out-edges.
  - Nodes `d200` and `d1000` with exactly 200 and 1,000 out-edges, added so the Degree measurement has
    those sizes.
  - Totals: 499,984 edges, 48,889 source nodes, 8 nodes with out-degree above 1,000, max degree 100,000.
- Setup detail: `Graph.KG.Subgraph` and `TraversalBuild` do not compile without the `Graph_KG` tables.
  I ran each side's own `initialize_schema(auto_deploy_objectscript=False)` (from a `git archive` export of that
  commit, connected only to IVGPERFA or IVGPERFB), then `LoadDir(...,"ck")`. Both sides loaded with no errors.
- Load: the host was busy throughout (other work in the lab). The 1-minute load average at the start of each
  run is below. Reps 1 and 2 were run under heavy load (17 to 54), reps 3 to 5 under 11 to 23.

| rep | W (A / B)   | K (A / B)   | R (A / B)   |
| --- | ----------- | ----------- | ----------- |
| 1   | 44.0 / 42.7 | 44.9 / 53.6 | 47.2 / 43.4 |
| 2   | 33.9 / 32.1 | 32.1 / 24.6 | 20.0 / 17.8 |
| 3   | 11.2 / 11.0 | 11.0 / 10.8 | 13.9 / 12.8 |
| 4   | 12.8 / 12.8 | 12.4 / 12.0 | 12.2 / 11.8 |
| 5   | 11.3 / 11.1 | 11.1 / 12.7 | 22.6 / 21.3 |

The Degree runs (B only) were at about 19 to 20. The migration run was at about 28 to 31.

- Pytest check: the literal check `ps aux | grep -c "[p]ytest"` never returned 0 on this machine. It matches
  `zsh -c` wrapper command lines (the lead's and my own) that contain the word "pytest". It read 0 to 3.
  I used a filtered check that ignores shell wrappers. It found no real pytest process before any timed run, so
  I never had to wait. Treat the numbers as measured on a loaded but not pytest-contended machine.

## Correctness: no difference found

Every method returned the same result on both sides, in all 5 reps plus the warm-up. I compared CRC-32 of the
JSON output or the numeric sums.

| item                                     | A = B                                         |
| ---------------------------------------- | --------------------------------------------- |
| PageRankGlobalJson (top 10,000 JSON)     | CRC 3529544345                                |
| RunJson, PPR from 20 seeds               | CRC 2574116132                                |
| DegreeCentralityJson out, all predicates | CRC 1776898174                                |
| DegreeCentralityJson out, p1             | CRC 809372367                                 |
| KHopCount sums                           | 607490 / 535635 / 20,000,000 / 4,000,000      |
| BFSFast count sums                       | 320007 / 97569                                |
| BFS (paths) count                        | 85513                                         |
| WCCJson                                  | CRC 3349030421                                |
| BuildNKG                                 | edgeCount 499984, sum of `^NKG(-3,*)` 499984  |
| Build2HopStats                           | cnt 109293, sum 10,803,524                    |
| Degree on d200 / d1000 / hub             | 200 / 1000 / 100000 (equals the stored `deg`) |

Caveat: the generator keeps `deg`/`degp` exactly consistent with `out`, so these agree by construction. The
alternative's reason for being (counters that drift after a rollback) was not exercised here.

## 1. Edge writes (200,000 distinct edges, `EdgeScan.WriteAdjacency`, graph key 987654, `^NKG` absent)

Microseconds per edge. B delegates to `Graph.KG.Adjacency.Put`. `Put` still writes `deg`, `degp` and
`__version`, so the work is the same plus one method call.

| metric                                               | A median | A min | B median | B min | B/A  |
| ---------------------------------------------------- | -------- | ----- | -------- | ----- | ---- |
| WriteAdjacency, us/edge                              | 1.7      | 1.7   | 1.9      | 1.9   | 1.12 |
| DeleteAdjacency, us/edge                             | 2.1      | 2.0   | 2.3      | 2.2   | 1.12 |
| WriteAdjacencyKeyed, all 50k calls, us/edge          | 646.3    | 586.7 | 638.2    | 618.8 | 0.99 |
| keyed ekey=1 phase (bare leaf, goes through Put), us | 2.4      | 2.2   | 2.5      | 2.5   | 1.04 |
| keyed ekey=2 phase (runs the SQL `OtherEdgeKey`), us | 2572     | 2337  | 2541     | 2464  | 0.99 |
| keyed ekey=3 phase (pure globals), us                | 4.7      | 3.9   | 4.4      | 4.2   | 0.95 |
| keyed ekey=4 phase (pure globals), us                | 4.3      | 3.8   | 4.4      | 4.2   | 1.03 |
| DeleteAdjacencyKeyed, us/edge                        | 3.9      | 3.5   | 4.0      | 3.8   | 1.03 |

The keyed run is 12,500 triples times 4 edges. The ekey=2 SQL lookup (about 2.5 ms per call) dominates the
average, so I also broke it out per phase.

Per-rep paired B/A for plain write: 0.90, 0.76, 1.15, 1.12, 1.20. The first two reps were under load 33 to 44.
In the quieter reps 3 to 5 it is a steady 1.12 to 1.20.

Verdict:

- Plain write and delete: slightly slower, about 0.2 us per edge (roughly 12 to 15% on a 1.7 us operation).
  This is the extra call frame and status return. It is invisible next to any real caller (SQL insert, network).
- Keyed write and delete: the same.
- Caveat: `^NKG` was not built during this block, so the interning branch (same code in both) was not timed.

## 2. Readers (ms unless noted)

| metric                                     | A median | A min  | B median | B min  | B/A                       |
| ------------------------------------------ | -------- | ------ | -------- | ------ | ------------------------- |
| PageRankGlobalJson, 20 iterations          | 722.0    | 575.4  | 834.9    | 748.1  | 1.16                      |
| PageRank RunJson (PPR), 20 seeds incl. hub | 1280.4   | 1099.7 | 1385.4   | 1328.4 | 1.08 (paired median 1.14) |
| DegreeCentrality out, all predicates       | 109.3    | 84.0   | 291.5    | 265.3  | 2.67                      |
| DegreeCentrality out, p1                   | 100.2    | 81.3   | 174.4    | 164.2  | 1.74 (paired median 2.02) |
| KHopCount, 20 seeds x5, all preds          | 0.185    | 0.103  | 49.8     | 48.8   | about 270x                |
| KHopCount, 20 seeds x5, p0                 | 0.122    | 0.079  | 41.7     | 41.3   | about 340x                |
| KHopCount hub x200                         | 0.085    | 0.070  | 1555.9   | 1483.5 | about 18,000x             |
| KHopCount d200 x20,000                     | 7.0      | 5.3    | 359.4    | 341.8  | about 51x                 |
| BFSFast p0,p1, 2 hops, 20 seeds            | 237.2    | 196.7  | 224.7    | 202.3  | 0.95                      |
| BFSFast p1,p2, 2 hops, 20 seeds            | 90.1     | 74.2   | 83.4     | 78.2   | 0.93                      |
| BFS (paths) p0,p1, 19 non-hub seeds        | 300.1    | 246.9  | 288.0    | 267.5  | 0.96                      |
| WCCJson                                    | 5313     | 5230   | 5405     | 5192   | 1.02                      |
| BuildNKG                                   | 7655     | 6597   | 7367     | 6902   | 0.96                      |
| Build2HopStats                             | 397.8    | 365.0  | 537.0    | 471.7  | 1.35                      |

KHopCount per call, derived: hub is 0.43 us in A and 7.8 ms in B. d200 is 0.35 us in A and 18 us in B.

Verdicts:

- KHopCount is slower, by orders of magnitude. The baseline answers from the stored `deg`/`degp` counter in
  O(1). The alternative removed that fast path, so every call walks the adjacency. That is O(degree): 7.8 ms
  for the 100,000-edge hub.
- DegreeCentrality out is 1.7 to 2.7x slower (it calls `Adj.Degree` per node). PageRank is 8 to 16% slower:
  it counts each source's degree once up front instead of reading `deg`. Build2HopStats is about 1.35x slower
  (it counts each middle node's degree, then walks `in`).
- BFS (the `degp` to `out` lookahead swap), WCC and BuildNKG are the same, within noise. The 4 to 7% BFS edge
  is smaller than the rep-to-rep spread.
- Nothing in the readers got faster.

## 3. `Adj.Degree` vs the stored counter (single call, B only; stored read is `$Get(^KG("deg",0,n))`)

| node                   | Degree median (us) | Degree min (us) | stored median (us) | stored min (us) | ratio          |
| ---------------------- | ------------------ | --------------- | ------------------ | --------------- | -------------- |
| d200 (200 entries)     | 30.7               | 30.1            | 0.0575             | 0.0525          | about 530x     |
| d1000 (1,000 entries)  | 154.0              | 150.6           | 0.0565             | 0.0555          | about 2,700x   |
| hub (100,000 entries)  | 15,058             | 13,497          | 0.0536             | 0.0495          | about 280,000x |
| hub, predicate p0 only | 14,239             | 13,172          | not measured       |                 |                |

`Degree` costs about 0.15 us per out-edge. The stored read is flat at about 0.05 us. Values agreed
(200 / 1000 / 100000). The stored counter was not measured in A separately because it is the same global read.

## 4. Python client side (no container involved; load about 30 during this block)

`import iris_vector_graph`, whole package, 9 fresh interpreters each, interleaved:

|                                    | median | min    |
| ---------------------------------- | ------ | ------ |
| A (baseline export)                | 251 ms | 245 ms |
| B (alt export, includes `host.py`) | 249 ms | 240 ms |

B/A is 0.99, no measurable difference. `host.py` executed on its own with its dependencies already loaded takes
0.18 ms (median).

Indirection through `ClientHost`, ns per call (median/min of 7 batches), against the fake native object:

| call                              | fake                                      | direct (ns) | via Host (ns) | added (ns) |
| --------------------------------- | ----------------------------------------- | ----------- | ------------- | ---------- |
| classMethodValue vs `call`        | trivial constant-time fake                | 49/48       | 283/273       | +234       |
| get                               | trivial constant-time fake                | 70/67       | 197/184       | +126       |
| nextSubscript vs `next_subscript` | trivial constant-time fake                | 56/55       | 167/162       | +111       |
| classMethodValue vs `call`        | `FakeNative` from tests/unit/test_host.py | 312/209     | 661/489       | +349       |
| get                               | `FakeNative` from tests/unit/test_host.py | 86/86       | 200/199       | +113       |
| nextSubscript vs `next_subscript` | `FakeNative` from tests/unit/test_host.py | 411/386     | 515/494       | +104       |

Verdict: the indirection costs about 0.1 to 0.35 us per call (the `%`-method check, the call frame and the
`native` property). Against a real Native API call that crosses a socket or a gref, this is negligible; a real
call costs tens of microseconds or more. I did not measure a real Native API call here, so "negligible" is an
inference from the fake. Import cost is unchanged.

## 5. Migration shortcut (statement-level, scratch namespace IVGPERFC)

`scripts/bench_migration_4_0_0.py` hard-codes `IVGLEGACY` and wipes `Graph_KG` there. I did not run it. Instead
I isolated the one statement in IVGPERFC: 100,000 `nodes` rows (primary key `node_id`, all in graph `g1`) and
100,000 `rdf_labels` rows with `graph_id` NULL. 5 timed reps after a warm-up, with the table reset to NULL
before each.

|                                                                                              | median  | min     |
| -------------------------------------------------------------------------------------------- | ------- | ------- |
| A: baseline correlated `UPDATE ... SET c.graph_id = (SELECT MIN(...))`                       | 6.355 s | 6.001 s |
| B: `_single_graph` probes + constant `UPDATE ... SET graph_id = 'g1' WHERE graph_id IS NULL` | 0.409 s | 0.343 s |
| B, constant UPDATE alone                                                                     | 0.372 s | 0.315 s |

B/A is 0.064, about 15.5x faster. The probes are the `SELECT DISTINCT graph_id` and the `NOT IN` orphan count.
Every row was verified placed after each run. Caveats:

- Only the statement for one child table, not the whole migration. `rdf_props` gets the same treatment.
- Synthetic tables: a `node_id` PK on `nodes`, no other indexes.
- Fires only when `nodes` holds exactly one graph and no orphans, as the code intends.
- Load was about 30.

## Overall

- The alternative makes things slower, not faster, in the ObjectScript runtime:
  - KHopCount, which was a counter read, now walks the adjacency: 50x to 18,000x slower depending on degree.
  - Degree centrality is 1.7 to 2.7x slower.
  - PageRank is 8 to 16% slower.
  - Build2HopStats is about 1.35x slower.
  - Edge write and delete are about 12% slower (0.2 us per edge).
- Neutral: BFS, WCC, BuildNKG, keyed writes, package import time.
- Faster: only the single-graph migration backfill statement, about 15x on 100,000 rows. This is a one-time upgrade
  cost, not a steady-state path.
- Python host indirection: about 0.1 to 0.35 us per call, no import-time effect.
- The cost is the trade the alternative chose: it drops the stored counters from the readers so they cannot
  drift. Where a caller needs a degree for a high-degree node repeatedly (KHopCount on hubs, per-node degree in
  centrality), the stored counter was far cheaper.

## Caveats

- The machine was loaded (host load 11 to 54, 75 users). The ratios that matter (KHopCount, Degree, centrality,
  migration) are far larger than the noise. The 1.08 to 1.16 PageRank and the 0.93 to 0.96 BFS figures are within
  the rep-to-rep spread and should not be over-read.
- One synthetic graph, one namespace per side, a single process and a single graph key. The generator's counters
  are consistent by construction. No concurrent writers. `^NKG` was absent for the write block.
- BFS times were taken with 2 hops only. A 3-hop run from the hub was not timed because it would explode.
- WCC and PageRank return capped or very large JSON; checksums are of the strings returned.

## State left behind

- Namespaces `IVGPERFA`, `IVGPERFB`, `IVGPERFC` and their databases (`/usr/irissys/mgr/ivgperf{a,b,c}/`) are left
  in place, with the compiled `Graph.KG.*` and `Perf.*` classes in A and B and empty `Graph_KG` tables in A and
  B. I killed `^KG`, `^NKG` and `^ArnoKG` in A and B, deleted the 12,500 scratch `rdf_edges` rows, and dropped
  the two scratch tables in C. No database files or directories were deleted. No other namespace was written.
- The repo checkout was not modified (read-only, `git archive` exports only). Nothing was committed, pushed or
  filed. Working files are in `/tmp/perfdrv`, `/tmp/perfsrcA`, `/tmp/perfsrcB`, `/tmp/perf_py` and
  `/tmp/perf_pyA`.
