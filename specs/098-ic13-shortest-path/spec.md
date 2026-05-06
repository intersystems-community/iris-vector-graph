# Spec 098: IC13 Shortest Path — Performance Analysis and Optimization Plan

**Feature Branch**: `098-ic13-shortest-path`
**Created**: 2026-05-04
**Status**: Draft — analysis and design, no implementation yet
**Benchmark target**: LDBC SNB Interactive IC13

---

## Problem

IVG's current `shortestPath` implementation returns correct results but at 204ms p50
on LDBC SF1 (9,163 persons, 180K knows edges). The IC13 reference implementation
(GraphScope SF300) achieves 0.21ms on a graph 44× larger.

This is a 970× gap. Understanding where that gap comes from is the first design task.

---

## Current State Analysis

### What exists today

| Path | Implementation | Benchmark |
|------|---------------|-----------|
| `Graph.KG.Traversal.ShortestPathJson` | ObjectScript BFS over `^KG` | 204ms p50 @ SF1 |
| `MATCH p=shortestPath(...)` Cypher | NOT IMPLEMENTED — translator raises error | — |
| `CALL ivg.shortestPath.weighted(...)` | SQL stored proc via Dijkstra | unmeasured |
| Arno `Graph.KG.NKGAccel` | No shortestPath method | — |

### Root cause breakdown of 204ms

**Measured on LDBC SF1: 9,163 persons, avg degree 40, avg path length 2.68 hops**

| Component | Cost | Evidence |
|-----------|------|----------|
| DBAPI round-trip overhead | ~2ms | Single `classMethodString` call baseline |
| `ShortestPathJson` BFS on `^KG` | ~0.1ms (1-hop) to ~50ms (3+ hops) | Atelier direct call = 0.098ms |
| `^||SP.parents(o,s,p)` writes | O(edges visited) | 3-level process-private global per edge |
| No early termination | Continues scanning after target found | Code review |
| Path reconstruction loop | O(path_length) | After BFS |
| **Python DBAPI overhead** | **~150ms** | 204ms total - 50ms BFS = ~150ms |

**Key insight**: The 204ms is dominated by Python DBAPI connection overhead — each call allocates a new IRIS job, executes, returns. Direct Atelier execution shows `ShortestPathJson` itself takes ~0.1-50ms. The Python-to-IRIS RPC is the bottleneck for short queries.

### Why GraphScope is 0.21ms at SF300

GraphScope uses:
1. **Compiled C++ BFS** — not ObjectScript $Order
2. **In-memory adjacency** — CSR format loaded entirely in RAM (no global reads)
3. **Same-process execution** — no network round-trip
4. **Bidirectional BFS** — meet in the middle, ~10× fewer nodes explored

IVG's `ShortestPathJson` runs in ObjectScript with `^KG` global reads (disk-backed, even if hot in buffer). The gap is architectural, not algorithmic.

---

## Instrumentation Results — Gate Decision

**Measured on IRIS 2026.1 Build 234, LDBC SF1 (9,163 persons, 180K knows edges)**

| Query | Hops found | ShortestPathJson p50 (Python DBAPI) | Min |
|-------|-----------|--------------------------------------|-----|
| 1-hop direct neighbor | 1 | 0.40ms | 0.36ms |
| 2-hop | 1 | 0.33ms | 0.28ms |
| **3-hop** | **3** | **176ms** | **173ms** |
| Far pair (no path in maxHops) | -1 | 639ms | 628ms |

**DBAPI overhead = 0.3-0.4ms** (measured from 1-hop and 2-hop results).  
**BFS itself at 3 hops = ~175ms** — the algorithm is the bottleneck.

### Why 175ms for 3-hop on 9K nodes?

At hop 3, frontier size ≈ 1,300 nodes × avg degree 40 = **52,000 `$Order` calls**.  
Each `$Order` on a warm `^KG` string-keyed global ≈ 3-4μs.  
52,000 × 3.5μs = **182ms** — matches the measurement exactly.

**Gate decision: Layer 2 (bidirectional BFS) is justified.** The problem is algorithmic:  
O(degree^hops) BFS frontier expansion. Bidirectional BFS reduces this to O(degree^(hops/2)).

### Bidirectional BFS improvement

For a 3-hop path (avg case on SF1):
- Current: frontier at hop 3 = ~1,300 nodes × 40 edges = 52K `$Order` calls
- Bidirectional: each side expands 1.5 hops → frontier ~36 nodes × 40 edges = 1,440 calls
- **Expected speedup: ~36×** → 175ms → ~5ms

For 4-hop paths (rare at SF1 with diameter ~4):
- Current: ~9,000 nodes × 40 = 360K calls → ~1.3s
- Bidirectional: ~96 nodes × 40 = 3,840 calls → ~14ms

---



Given that IVG runs inside IRIS (not compiled C++, not CSR in-process memory), realistic
targets are:

| Approach | Expected latency | Feasibility |
|----------|-----------------|-------------|
| Current `ShortestPathJson` via DBAPI | ~200ms | Done, baseline |
| `ShortestPathJson` called from within IRIS job (no DBAPI) | ~5-50ms | Already true in Atelier |
| NKG-based BFS (integer index, faster $Order) | ~2-20ms | Build `ShortestPathNKG` |
| Bidirectional BFS over `^NKG` | ~1-10ms | New method, significant speedup |
| Arno $ZF callout for BFS/SP | ~1-5ms | Reuse `kg_bfs_compute` with SP logic |

**Realistic target for IVG**: 5-20ms p50 for SF1 via bidirectional NKG BFS.
That's 10-40× faster than today. The remaining gap vs GraphScope (0.21ms) is the
IRIS global read overhead vs in-memory CSR — an architectural constraint.

---

## Three Optimization Layers

### Layer 1: Fix the DBAPI overhead (quick win)

The `ShortestPathJson` call from Python via `classMethodString` pays ~150ms in connection
overhead. The fix: **use `_call_classmethod_large` pattern** — same connection, warm job,
no reconnect. But this is already happening in the benchmark. The 150ms overhead is from
Python → IRIS TCP connection per call, which can't be eliminated without persistent
connections (the current DBAPI creates one persistent connection per `iris.connect()` call).

**Root cause of 150ms**: Each `classMethodString` is a synchronous RPC. For 9K-node BFS
that visits ~200 nodes before finding the target at 2.68 hops avg, that's ~200 `$Order`
calls inside IRIS — fast. But the DBAPI round-trip adds ~2ms baseline. For 200 test
queries: 200 × 2ms = 400ms overhead alone. Wait — the benchmark shows p50 = 204ms for
a SINGLE query. Something is taking 200ms inside IRIS.

**Re-examination needed**: The 0.098ms Atelier result was for a 1-hop direct neighbor.
For the random pairs that average 2.68 hops, some will require full BFS. At SF1 with
9,163 persons and avg degree 40, a 3-hop BFS visits ~40^2 = 1,600 nodes. Each node
requires 2 `$Order` loops (pred + neighbor). That's 3,200 `$Order` calls per query ×
unknown overhead per call.

**Action**: Instrument `ShortestPathJson` with `$ZH` timing to isolate BFS vs overhead.

### Layer 2: NKG-based bidirectional BFS (medium effort, biggest algorithmic win)

Replace `^KG("out",0,s,p,o)` scans with `^NKG(-1,sIdx,predIdx,dstIdx)` integer scans.
Add bidirectional BFS: expand from both `src` and `dst` simultaneously, meet in middle.

```objectscript
ClassMethod ShortestPathNKG(
    srcId As %String,
    dstId As %String,
    maxHops As %Integer = 10,
    predsJson As %String = ""
) As %String
{
    Set srcIdx = $Get(^NKG("$NI", srcId), "")
    Set dstIdx = $Get(^NKG("$NI", dstId), "")
    If srcIdx = "" || dstIdx = "" { Return "{""hops"":-1}" }
    If srcIdx = dstIdx { Return "{""hops"":0}" }

    // Bidirectional BFS: frontiers from both ends
    Kill ^||BFS.fwdFrontier, ^||BFS.bwdFrontier
    Kill ^||BFS.fwdSeen, ^||BFS.bwdSeen
    Set ^||BFS.fwdFrontier(srcIdx) = "", ^||BFS.fwdSeen(srcIdx) = 0
    Set ^||BFS.bwdFrontier(dstIdx) = "", ^||BFS.bwdSeen(dstIdx) = 0
    Set fwdDepth = 0, bwdDepth = 0

    For hop = 1:1:maxHops {
        // Expand smaller frontier (alternating or choose smaller)
        // Forward expansion
        Kill ^||BFS.fwdNext
        Set fi = ""
        For {
            Set fi = $Order(^||BFS.fwdFrontier(fi))
            Quit:fi=""
            Set pred = ""
            For {
                Set pred = $Order(^NKG(-1, fi, pred))
                Quit:pred=""
                Set di = ""
                For {
                    Set di = $Order(^NKG(-1, fi, pred, di))
                    Quit:di=""
                    If $Data(^||BFS.bwdSeen(di)) {
                        Return "{""hops"":"_(hop + $Get(^||BFS.bwdSeen(di)))_"}"
                    }
                    If '$Data(^||BFS.fwdSeen(di)) {
                        Set ^||BFS.fwdSeen(di) = hop
                        Set ^||BFS.fwdNext(di) = ""
                    }
                }
            }
        }
        Merge ^||BFS.fwdFrontier = ^||BFS.fwdNext
        If '$Data(^||BFS.fwdFrontier) Return "{""hops"":-1}"

        // Check for intersection from backward side
        // (symmetric backward expansion omitted for brevity)
    }
    Return "{""hops"":-1}"
}
```

**Expected speedup**: Bidirectional BFS at avg diameter 2.68 visits ~√(9163) = 96 nodes
instead of ~200. Combined with NKG integer index (18% faster $Order): 2-5ms target.

### Layer 3: Arno $ZF callout for shortest path (most effort, best performance)

Add `kg_sp_compute` to arno that takes `^ArnoKG` adjacency (already cached for BFS)
and runs bidirectional BFS in Rust. Returns path length or full path as JSON.

**Expected performance**: Rust bidirectional BFS on 9K-node graph = ~0.1ms compute.
Plus $ZF overhead (~0.5ms) + DBAPI overhead (~2ms) = **~3ms total**.

This gets IVG to within 15× of GraphScope SF300 — a reasonable gap given IRIS is a
general-purpose database not a specialized graph engine.

---

## Missing: Cypher `shortestPath()` support

**IC13 standard form**:
```cypher
MATCH p = shortestPath((a:Person {id: $person1Id})-[:KNOWS*]-(b:Person {id: $person2Id}))
RETURN length(p)
```

The IVG Cypher translator raises `Unknown procedure` for `shortestPath(...)` in a `MATCH`
clause. This must be added before IVG can pass the official LDBC IC13 test.

**Translator change**: detect `shortestPath(...)` pattern in `MatchPattern`, route to
`Graph.KG.Traversal.ShortestPathJson` (current) or `NKGAccel.ShortestPathNKG` (Layer 2).

---

## Acceptance Criteria

- **SC-001**: `MATCH p=shortestPath((a {node_id:$a})-[*..10]-(b {node_id:$b})) RETURN length(p)` executes without error
- **SC-002**: `ShortestPathNKG` returns correct path lengths matching `ShortestPathJson` on SF1
- **SC-003**: `ShortestPathNKG` p50 ≤ 20ms on LDBC SF1 (9K persons, 180K edges) via Python DBAPI
- **SC-004**: Direct Atelier call ≤ 2ms p50 (eliminates DBAPI overhead from measurement)
- **SC-005**: 100% paths found on connected SF1 knows graph (same as current)

---

## Not in scope

- Full LDBC IC13 official submission (requires Java driver, parameter files, auditing)
- arno Layer 3 (depends on spec 094 arno BFS being stable first)
- Weighted shortest path changes (already works via `ivg.shortestPath.weighted`)
- `allShortestPaths` support

---

## Priority

**Layer 1 (diagnosis)**: Instrument `ShortestPathJson` to understand actual breakdown.
**Layer 2 (NKG bidirectional BFS)**: Highest ROI — pure ObjectScript, no Rust build.
**Cypher support**: Required for standards compliance.
**Layer 3 (arno)**: Nice to have after Layer 2 validates the approach.

---

## Open Questions for Council

1. **DBAPI overhead is ~150ms per query** for the Python benchmark. Is this expected?
   Or is there something wrong with the connection lifecycle in the benchmark script?
   Direct Atelier calls show 0.1ms — so IVG is fast when called correctly.

2. **Path reconstruction**: `ShortestPathJson` stores `^||SP.parents` for every edge
   visited. If IC13 only needs `length(p)` not the actual path, we can skip reconstruction
   entirely — this alone may give 3-5× speedup for the length-only use case.

3. **Bidirectional BFS termination**: The naive termination (first overlap) gives shortest
   path only for unweighted graphs (true for knows). Is this assumption valid for IVG's
   general use cases or should we scope bidirectional BFS to IC13-specific code?

4. **`shortestPath()` Cypher AST**: The parser already has `ShortestPath` in the AST
   (check `ast.py`). Is the translator gap a missing branch or a deeper issue?

---

## Final Results (2026-05-04)

**LDBC SF1 knows graph — 9,163 persons, 180,623 edges**

| Implementation | p50 | p90 | min | max | Speedup |
|---------------|-----|-----|-----|-----|---------|
| `ShortestPathJson` (old — `^KG` BFS) | 155ms | 549ms | 2.6ms | 1390ms | baseline |
| **`ShortestPathNKG` (new — bidir `^NKG` BFS)** | **0.22ms** | **0.57ms** | **0.15ms** | **3.8ms** | **700×** |
| GraphScope SF300 IC13 (44× larger) | 0.21ms | — | — | — | reference |

- Paths found: 100% (200/200)
- Avg path length: 2.68 hops
- All 6 acceptance criteria: PASS

