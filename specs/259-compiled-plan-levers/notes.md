<!-- markdownlint-disable MD013 -->

# Spec 259 notes: what the queries furthest behind Neo4j spend their time on, after spec 257

**Date**: 2026-10-24 (US Eastern). **Status**: findings; no product code changed. **Data**: LDBC SNB SF1 in `SNB1B` on
`ivg-iris-enterprise` (8 CPUs), read only. **Tools**: `scripts/bench/alt/prof259/` (scratch classes `ZIVGP.*`, deleted
afterwards; `mkprof.py` regenerates the timed copy of `RunA`).

## Read this first: how soft the numbers are

Other agents used the container and the host while this ran (host load average 25 to 44; the container at 0 to 300% CPU
before the series; and the container was restarted by someone else mid-session, so the first runs after it were cold).
So:

- Every figure below is a **ratio, or a minimum over 5 to 11 alternating runs in one process** (A, B, A, B, ...). None
  is an absolute time to quote. A ratio that is a median of per-round ratios is given with its range.
- Parallel (worker) timings are the softest: workers compete with the other agents' processes. The serial simulation (the
  8 parts run one after another in one process) gives total work that contention cannot blur, and is used for
  attribution. Real parallel latency is quoted with the container's CPU before the series where I had it.
- Noise floor: the same code in two arms differed by up to 8% in most series and by 21% once (BI04, early). Anything
  under about 10% is **not** a finding.

## What the generated walks cost, per query (one process)

In-IRIS phase times come from a timed copy of `RunA`/`EmitGroups`/`Parallel`/`Part` (`ZIVGP.Prof`). The walk's inner
split is by ablation: each variant removes one kind of work from the generated class, and the paired difference is that
work's cost. (The ablated variants return other rows by design. The unmodified copy returned the same rows as the
generated class in every paired run.)

| Query (set)             |  Walk | EmitGroups: bound + sort filing + emit | Setup, anchors, Props begin/end, kill, Head | Client: Python + Native API round trip |
| ----------------------- | ----: | -------------------------------------: | ------------------------------------------: | -------------------------------------: |
| BI04 (Greece)           |  ~92% |                           ~5% (4.6 ms) |                                       ~0.5% |                      0.5 ms (about 1%) |
| BI14 (Brazil)           |  ~99% |                            ~0.4% (0.3) |                                       ~0.5% |                           about 0.5 ms |
| BI18 (personId set 0)   |  ~91% |                              ~8% (3.4) |                                       ~0.5% |                           about 0.5 ms |
| BI11 (49-anchor sample) | ~100% |                                      0 |                                           0 |                             negligible |

The client path is not a lever. `execute_cypher` end to end minus the `Exec.Run` call was 0.0 to 0.5 ms; the call minus
the in-IRIS time 0.3 to 0.7 ms (BI04 Tajikistan, 8 ms in IRIS, showed the most). `plan_query` is 0.04 ms, the plan's
`json.dumps` 0.03 ms. Rows come back in `Head`'s single page (10 or 20 rows), so there is no paging round trip.

Walk, split by ablation (minimum over 11 alternating rounds; percentages of the walk):

| Walk part                                           | BI04 (Greece) |  BI18 (set 0) | BI14 (Brazil) |
| --------------------------------------------------- | ------------: | ------------: | ------------: |
| Traversal: loops, label checks, node checks         | 14.4 ms (19%) | 17.3 ms (46%) | 68.0 ms (92%) |
| Group key: the property read and the key list       | 24.4 ms (32%) |  4.3 ms (11%) |             - |
| Group lookup and creation (process-private globals) | 12.4 ms (16%) |  4.7 ms (13%) |             - |
| count(DISTINCT) members and the counts              | 26.0 ms (34%) | 11.2 ms (30%) |             - |
| BI14's count (`$Increment` per match)               |             - |             - |   5.6 ms (8%) |
| Walk total                                          |       77.2 ms |       37.4 ms |       73.6 ms |

- **BI04 is a grouping query, not a traversal.** 14 ms of 77 is the walk; spec 258's figure (14 ms with the grouping
  removed) is reproduced. The property read is a third of it (24 ms: at spec 258's 13.7k distinct forums, 1.8 us each).
- **BI14 is a pure traversal.** Its 12,472 leaf paths (a to b to b's city to its country) cost about 5 us each: roughly
  14 global references a path at 0.3 to 0.4 us (an estimate from counting the generated code's references). Emission is
  under 10%.
- **BI18** is half traversal, half grouping, and then the parallel split adds its own cost (below).
- **BI11**: about two thirds of the time is `Exec.NodeOK`, which the compiled walk calls for the two cross filters
  (`a.sid < b.sid AND b.sid < c.sid`), once per (a, b) and once per (b, c) pair. Each call re-reads two properties through
  `Props.Get`, looks up two sort keys in `^||IVG.CypherRun("sk")` and calls `CmpKeys`. With those two calls written inline
  the walk takes 0.35x the time (below).

### What does not matter (at or under the noise floor)

Paired against the unmodified class in the same run (so each ratio below is one pair's; the unmodified class itself moved
between 94% and 102% of the generated one):

| Removed from the walk                                                     | BI04      | BI14       | BI18       |
| ------------------------------------------------------------------------- | --------- | ---------- | ---------- |
| The `For` around the relationship-identity test (`noidloop`)              | -15%, -1% | +1%, +12%  | +3%, -5%   |
| `a/b/e/z` and the identity compares altogether (`noident`)                | -         | -12%, -8%  | -          |
| The `$Data` guard before each neighbour loop (`noguard`)                  | -         | -6%, -7%   | -          |
| `Set pB(k)` / `Kill pB(k)` where the emission does not read them (`nopb`) | -8%       | -3%, -1.5% | -5%, +1.5% |

Each is at the noise floor; together they might be 15% on BI14. A micro-optimisation, not a lever. Spec 258's result (a
node-local property layout gains 3 to 10%) stands: reading the property costs for the random access, not for the second
reference.

## The parallel split (BI18, BI04, BI14)

The default gate (`parallel_min` 1500) estimates work as first-hop neighbours x the second hop's fan-out, whatever the
chain's depth. Measured as the largest `parallel_min` at which each run still splits (`p12.py`, 8 parameter sets):

| Query | Estimate at SF1 | One-process time | Splits by default? |
| ----- | --------------: | ---------------- | ------------------ |
| BI14  |        8 to 736 | 2.4 to 71 ms     | never              |
| BI04  |        8 to 736 | 9 to 357 ms      | never              |
| BI18  | 4,632 to 28,636 | 30 to 58 ms      | always             |

Work per estimate unit is 0.1 to 0.23 ms for BI14 and 0.0045 ms for BI18: the estimate ignores hops 3 and later. So on
AWS BI04 and BI14 run in one process and only BI18 is split.

**BI14 forced to split** (`parallel_min` 0, paired against one process, answers equal in every round; median of the
per-round ratios one process / parallel, range in brackets):

| Set (one-process median)  | Ratio                                                 | Container CPU before the series |
| ------------------------- | ----------------------------------------------------- | ------------------------------- |
| Brazil (60 ms)            | 2.4 (2.0 to 3.2); 3.0 (2.3 to 3.2) in a second series | 31%; 1.5%                       |
| Philippines (72 to 86 ms) | 2.5 (2.3 to 3.1); 2.0 (0.9 to 3.0)                    | 0.3%; 285%                      |
| South_Africa (18 ms)      | 1.85 (1.4 to 2.4)                                     | 31%                             |
| Madagascar (19 to 22 ms)  | 1.45 (0.25 to 2.3); 1.4 (0.9 to 2.4)                  | 25%; 150%                       |
| Greece (23 ms)            | 1.35 (0.75 to 2.6)                                    | 258%                            |
| Jordan (8 ms)             | 0.83 (0.4 to 5.1)                                     | 1%                              |
| Tajikistan (3 ms)         | 0.65 (0.1 to 0.9)                                     | 175 to 198%                     |

Start-up costs about 1.5 ms in all (Tajikistan: 4.1 to 4.3 ms split against 2.5 to 2.9 in one process). Queueing the 8
jobs takes 0.25 to 0.55 ms, and the pool's workers start at once: `WaitForComplete` returned within 1 ms of the slowest
worker's own time (BI14, BI18). For BI14's 10 groups the export is 0.2 ms a worker and the merge 0.1 ms. The break-even
is a one-process time of about 10 to 12 ms on this host.

**BI18 and BI04 under the shipped positional split**: each worker's **export** is as large as its walk.

- BI18, serial simulation, sum over the 8 parts (ms): walk 56 to 58, export 33 to 34, spill 8 (`SpillDistinct`); then the
  coordinator's merge 2.7 to 3.0 and `EmitGroups` 2.2 to 4.3. One process takes 42 to 44. So the 8 parts do 100 to 103
  ms of work for a 44 ms query. In real parallel runs one process / parallel was 0.52 to 1.22 (medians 0.85 to 0.95 on a
  loaded host): the split is not paying for BI18 here.
- BI04 forced to split: worker export 18 to 140 ms each against walks of 7 to 98 ms; one process / parallel 0.5 to 1.3.
  Each worker `$Increment`s every count(DISTINCT) member it holds into one shared global (`Exec.Export`).

The export exists because two workers may hold the same count(DISTINCT) member. If the walk is split on a hash of the node
the distinct variable binds, they cannot, and `Export` can add counts as it does for the split variable today. A
prototype (`p10.py`: a hash of hop 1's node for BI18, of hop 2's for BI04; no spill; `splitvar` set; answers equal in all
three arms):

| Query / set          | Hash split against the shipped split (median, range per round) | Hash split against one process |
| -------------------- | -------------------------------------------------------------- | ------------------------------ |
| BI18 set 0           | 1.15 (0.38 to 1.57)                                            | 1.16 (0.49 to 1.34)            |
| BI18 set 5           | 1.26 (1.14 to 1.47)                                            | 1.62 (1.37 to 2.01)            |
| BI04 Greece          | 1.37 (1.31 to 1.62)                                            | 0.97 (0.79 to 1.22)            |
| BI04 South_Africa    | 1.32 (1.05 to 1.72)                                            | 0.97 (0.78 to 1.13)            |
| BI04 Brazil (292 ms) | 1.59 (1.31 to 1.65)                                            | 1.52 (1.23 to 1.81)            |

The hash split is 1.2 to 1.6x better than today's. Against one process BI18 and the large BI04 set profit, but BI04's
small sets do not: its forums are read and grouped in every worker that holds one of their members, so the property read
(32% of the walk) is done again per worker. A weak lever, measured on a loaded host with wide spreads.

## The levers, ranked

"Gain" is measured unless marked _est._ The last column is what the answer must keep identical.

| #   | Lever                                                                                                                                                                                                          | Queries                                                                | Gain                                                                                                                                                                                                                                                                                        | Must stay identical                                                                                                                                                                                                                                                 |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| L1  | Write the cross filters inline in `Gen.Check` instead of calling `NodeOK`: each node's sort key in a local cache (`pQ("xs", prop, node)`), the other node's looked up in the same cache, compared by `CmpKeys` | BI11 (the only SNB query with a cross filter)                          | **2.8x and 2.95x** in one process on two stride samples (98 and 49 anchors), rows equal. Whole label: _est._ 230 to 320 s of one-process work down to 80 to 110 s                                                                                                                           | `NodeOK`'s order of checks (id, labels, properties, comparisons, cross filters); a missing property filters the row out; range operators go through `CmpKeys` (kinds that do not compare give null, so the row goes); `=` and `<>` compare the raw values (`Holds`) |
| L2  | A work estimate for the split gate that includes hops 3 and later (sample each hop's fan-out, as it already does for hops 1 and 2)                                                                             | BI14, and any chain of 3 or more hops that is under the gate; not BI04 | BI14: **1.4 to 3x** when one process takes about 15 ms or more; 0.4 to 0.8x if a query under 8 ms splits                                                                                                                                                                                    | The merge's own guarantees (already tested). The gate must not split a many-group count(DISTINCT) plan until L4 is in (BI04 gets slower)                                                                                                                            |
| L3  | Group state in locals, flushed once (prototype `lg.py`): ids, key map, counts                                                                                                                                  | BI04, BI18                                                             | BI04 walk **-18 to -23%**, but the flush into the run's process-private globals costs 15 to 17 ms for the groups and cancels it: whole run **0.96 to 1.02x**. About 1.25x only if `EmitGroups` and `Export` read the locals (_est._ from the ablation `localgroups`: 76 to 87% of the walk) | Group ids and their order of creation (ties at an ORDER BY's LIMIT depend on them); the long-key fallback to `GroupOf`; `Export` and `Stage2` read the group globals                                                                                                |
| L4  | Hash split on the count(DISTINCT) variable's hop; `Export` adds counts, no spill                                                                                                                               | BI18, BI04 (large sets)                                                | 1.2 to 1.6x over today's split; 1.0 to 1.6x over one process (above)                                                                                                                                                                                                                        | The distinct variable's disjointness across workers (true for a hash of its node, whatever the anchor, which also makes the "two anchors" guard unnecessary here); the ranged scan splits are unchanged                                                             |
| L5  | Read the group key's property after the walk, in node order (258: 0.8 us ordered against 1.5 us in the walk), merging groups that share a key                                                                  | BI04                                                                   | _est._ 10 ms of 77 (1.12x); with L3 about 1.4 to 1.6x                                                                                                                                                                                                                                       | Groups with equal keys from different nodes must merge, and their count(DISTINCT) sets be united, not added                                                                                                                                                         |
| L6  | A per-node property layout (spec 258)                                                                                                                                                                          | BI04, BI18, BI03                                                       | 3 to 10% (258's prototype; the `gprop`/`gnoprop` pair here agrees)                                                                                                                                                                                                                          | -                                                                                                                                                                                                                                                                   |
| L7  | Micro changes: the `$Data` guard, the identity loop, `pB`                                                                                                                                                      | all                                                                    | at the noise floor (above)                                                                                                                                                                                                                                                                  | -                                                                                                                                                                                                                                                                   |
| L8  | The walk in a native kernel (Arno/Rust over `^KG`)                                                                                                                                                             | BI14, BI04, BI18                                                       | not measured. ObjectScript's floor is 0.3 to 0.4 us a global reference; BI14 makes about 14 a leaf path and is 3.8x behind Neo4j                                                                                                                                                            | the same oracle (compiled = interpreted = SQL)                                                                                                                                                                                                                      |

### Predicted effect on the AWS SF1 ratios (`aws-sf1-6`), soft

L1 alone should take BI11 from 1.36x behind Neo4j (80 s against 59 s) to ahead of it: one-process CPU falls to about a
third, and the worker split already shares it. L2 should take BI14 from 3.75x to about 1.6 to 2x behind (a 2.4x gain on
the sets that run 15 ms or more on this host, which are about 40 to 70 ms on AWS). L3 to L5 together might take BI04 from
3.8x to about 2.5x and BI18 from 3.2x to about 2.3x. Nothing here brings BI04 or BI14 to parity: that needs L8.

## What was built, and why nothing in the product changed

The gate was one contained, safe lever worth at least 1.3x on at least two of BI04, BI14, BI18 and BI11. There is none.
L1 is 2.9x on one query. L2 is 1.4 to 3x on one query. L3, L4 and L5 are at most 1.2 to 1.6x on two, and none is contained
(each reaches into the generator, `Export`, `EmitGroups` or the split). So no product class was changed and no test
added. The prototypes are scratch classes (`ZIVGP.*` in `SNB1B`, since deleted) driven from `scripts/bench/alt/prof259/`:

- `p7.py`: L1 (the generated walk's text with the two `NodeOK` calls replaced; answers equal to the generated walk's).
- `p8.py` with `lg.py`: L3. `p10.py`: L4. `p12.py`: the gate's estimates. `abl.py`: the ablations. `p9.py`: the client
  path. `p1.py`, `p3.py`, `p4.py`, `p5.py`: surveys, parallel against one process, the serial simulation, the BI11 sample.

## Recommendation

Build **spec 259 as two small parts, L1 and L2**, and leave L3 to L5 for later. Both are contained, measured at 2.8x
(BI11) and 1.4 to 3x (BI14), and provable against the existing oracle: `tests/e2e/test_257_compiled_plans_e2e.py`
(compiled = interpreted = SQL) with new cases for a cross filter on `<`, `<=`, `>`, `>=`, `=` and `<>`, a missing
property, a string against a number, two chained filters, and a cross filter on a node that also has property checks;
for L2, the estimate's order against measured one-process time on the SNB and DRKG plans, and a BI14-shaped plan that must
split while a small one must not.

Do not build a storage change (258 is right: 3 to 10%), and do not build L3 as prototyped (its flush cancels the gain).
The gap that remains on BI04, BI18 and BI14 (2 to 4x) is the cost of each global reference in a walk that touches many
nodes once. Closing it needs the walk to leave ObjectScript (L8), which is a different spec (236's callout) and not a
cheap one.

Open questions this run could not settle: (1) L2's gate on AWS Graviton, where a one-process BI14 is about 3x slower than
here, so the break-even is lower; (2) whether L4's gain holds on 16 quiet vCPUs; (3) BI11's whole-label time after L1,
extrapolated from stride samples (the full query was not re-run: one process takes minutes on this host).
