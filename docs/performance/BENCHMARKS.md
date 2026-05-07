# IRIS Vector Graph — Performance Benchmarks

## Why LDBC SNB?

IVG is a general-purpose graph engine built on IRIS globals — not a toy benchmark target.
To make performance claims meaningful, we needed a standard workload that:

1. **Uses real graph topology** — LDBC Social Network Benchmark generates synthetic but
   statistically realistic social graphs with power-law degree distributions, the same
   structural pattern found in knowledge graphs, citation networks, and enterprise data.

2. **Has published comparison numbers** — GES (GraphScope Execution Service) and other
   systems have published LDBC SNB Interactive query latencies, giving us an honest
   apples-to-apples reference point.

3. **Stresses the right operations** — SNB Interactive queries IC2, IC3, IC7, IC13 cover
   the exact patterns IVG is designed for: 1-hop neighbors, 2-hop traversal with LIMIT,
   COUNT DISTINCT over multi-hop sets, and shortest path. These are the workloads that
   matter for graph RAG, knowledge graph querying, and social/entity graph analytics.

### Scale factors

LDBC SNB uses scale factors (SF) to control dataset size. We benchmarked at SF10:
- ~62K Person nodes, ~3.87M KNOWS edges, ~54M total edges across all types
- SF1000 (used by GES) is 100× larger, run on large server clusters

**Hardware comparison caveat:** GES numbers reflect SF1000 on a large server cluster.
IVG numbers are SF10 on a MacBook Pro (M3 Ultra, 128GB RAM, local Docker). Direct numeric
comparison is suggestive, not definitive — the important signal is order-of-magnitude
positioning, not exact ratios.

---

## Current Results (v1.83.0)

### Test Environment

- **Hardware**: MacBook Pro (M3 Ultra, 128GB RAM), macOS, local Docker
- **IRIS**: 2025.1 Enterprise (Build 230), Docker
- **Dataset**: LDBC SNB SF10
- **Comparison**: GES/GraphScope published LDBC SNB Interactive numbers

### Query Map

| Query ID | Pattern | What It Measures |
|---|---|---|
| IC2 | 1-hop neighbors of a Person | Hot-path neighbor lookup, fundamental to graph traversal |
| IC3 | 2-hop neighbors with LIMIT / COUNT | Multi-hop expand; tests frontier explosion handling |
| IC13 | Shortest path between two Persons | BFS with path reconstruction |

### Query Latency (p50)

| Query | IVG p50 | GES SF1000 p50 | Notes |
|---|---|---|---|
| IC13 ShortestPath (SF1) | 0.22ms | 2.69ms | IVG faster |
| IC13 ShortestPath (SF10) | 2.1–3.2ms | 2.69ms | Comparable |
| IC2 1-hop COUNT | **0.29ms** | 0.14ms | Competitive; was 2.8ms before `KHopCount` fast path |
| IC2 1-hop IDs | **0.9ms** | — | `KHopNeighborIds` newline-delimited, no JSON overhead |
| IC3 2-hop LIMIT 1000 | **1.2ms** | 4.19ms | **3.5× faster than GES**; was 14–22ms before `KHop2NeighborIds` |
| IC3 2-hop COUNT DISTINCT | 70ms | — | `KHop2Count`; was 195ms. 10ms target needs pre-aggregation |
| IC3 approx COUNT DISTINCT | 5.3ms | — | HLL sketch; 74× vs exact but ~89% accuracy on social graphs |

### Key Takeaways

- **Shortest path** is competitive with GES at SF10. At SF1 IVG is 12× faster.
- **1-hop neighbor lookup** (`IC2`) is within 2× of GES at much smaller scale and single-node hardware.
- **2-hop LIMIT** (`IC3`) is the standout: IVG is 3.5× *faster* than GES because `KHop2NeighborIds`
  short-circuits after `maxResults` hits, while GES traverses the full frontier first.
- **2-hop exact COUNT** at 70ms is the remaining gap. The full 38K-node dedup walk dominates.
  Pre-aggregated 2-hop degree stats at `BuildNKG` time would close this to sub-millisecond.

### What "fast path" means

IVG's `execute_cypher` detects certain high-frequency patterns at query parse time and routes
them to optimized ObjectScript methods that bypass the SQL translator entirely:

| Pattern | Routed to | Mechanism |
|---|---|---|
| `MATCH (s {node_id:$x})-[:P]->(n) RETURN count(n)` | `KHopCount` | O(1) lookup of `^KG("degp",s,p)` |
| `MATCH (s {node_id:$x})-[:P]->(n) RETURN n.node_id` | `KHopNeighborIds` | `$Order` scan, no JSON |
| `MATCH (s {node_id:$x})-[:P*2]->(n) RETURN count(n)` | `KHop2Count` | 2-pass `$Order` with process-private dedup |
| `MATCH (s {node_id:$x})-[:P*2]->(n) RETURN n.node_id LIMIT k` | `KHop2NeighborIds(k)` | Early-exit on `maxResults` |

All other Cypher goes through the normal parse → translate → SQL path.

---

## Ingestion Throughput

| Method | Throughput | Notes |
|---|---|---|
| `bulk_ingest_edges` (direct `^KG` write) | 190–312K edges/s | Bypasses SQL; `^NKG` not updated until `rebuild_nkg()` |
| `bulk_create_edges` (SQL batch) | ~50K edges/s | Includes index maintenance; `^KG` rebuilt automatically |
| `BuildKG` (SF10, full rebuild) | 71s | Rebuilds `^KG` adjacency index from `rdf_edges` SQL |
| `BuildNKG` (SF10, full rebuild) | 422s | Rebuilds `^NKG` integer index used by Arno/BFS acceleration |

**Note on `bulk_ingest_edges`:** High throughput comes at a cost — `^NKG` (the integer-keyed
index used for Arno-accelerated BFS) is not updated. Call `engine.rebuild_nkg()` after bulk
loads before issuing variable-length Cypher path queries. The engine emits a `RuntimeWarning`
if you skip this step.

---

## How to Reproduce

```bash
# Load LDBC SF10 data (requires LDBC data generator output)
conda run -n py312 python tests/benchmarks/ldbc_full_loader.py \
  --data-dir /tmp/sf10_out/graphs/csv/bi/composite-merged-fk/ \
  --port 4972

# Run IC2/IC3 profiling
IRIS_PORT=4972 conda run -n py312 python tests/benchmarks/ic2_profile.py

# Run the full benchmark suite (synthetic data, no LDBC required)
conda run -n py312 python tests/benchmarks/bench.py --datasets M --runs 20
```

The benchmark suite in `tests/benchmarks/bench.py` uses synthetic R-MAT graphs
(no LDBC data required) and reports `ivg-os` (ObjectScript BFS) and `ivg-arno`
(Rust-accelerated BFS on Enterprise) numbers side by side.

---

## Legacy: ACORN-1 Prototype (pre-v1.50)

> The numbers below are from an early ACORN-1 prototype benchmarked against the STRING
> protein interaction database on different hardware. The methodology and dataset are not
> comparable to the LDBC numbers above. Retained for historical context only.

ACORN-1 optimization delivered **21.7× ingestion improvement** over the baseline for
biomedical knowledge graph operations (10K proteins, 50K interactions, 8 workers,
STRING confidence threshold ≥400).

| Metric | Baseline | ACORN-1 |
|---|---|---|
| Ingestion rate | 29 proteins/s | 476 proteins/s |
| Total time (10K proteins) | 345s | 21s |
| Index build time | 120s | 0.054s |
| 1-hop traversal | 1.2ms avg | 0.25ms avg |
