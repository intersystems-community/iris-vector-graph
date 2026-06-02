# IVG Performance Benchmarks

**Updated**: 2026-06-02  
**Platform**: MacBook Pro M3 Ultra, 128GB RAM  
**IRIS**: Community Edition 2026.1 + Enterprise 2026.2.0AI (Docker, localhost)  
**Methodology**: 3–5-run average after cache warm, unless noted.

---

## Graph Algorithm Benchmarks (v2.0.0)

### Betweenness Centrality

Measured on synthetic Erdős-Rényi graphs and Zachary's karate club.  
`Tier 1` = native Rust accelerator (requires accelerator library deployed).  
Tier 2` = ObjectScript parallel (ObjectScript `%SYSTEM.WorkMgr` 8×, no arno needed).

**Sampled = 200 sources** (Brandes-Pich approximation):

| Fixture | IVG Rust | IVG OS-par | networkx |
|---------|----------|-----------|---------|
| karate (34n, 78e) | **0.3ms** | 5ms | ~2ms |
| ER(500, 1230e) | **2.3ms** | 167ms | ~80ms |
| ER(2000, 5936e) | **8ms** | 500ms | ~8s |

**Exact** (all N sources, ground truth):

| Fixture | IVG Rust | IVG OS-par | networkx |
|---------|----------|-----------|---------|
| karate (34n) | **0.9ms** | 4.5ms | ~2ms |
| ER(500) | **3.7ms** | 341ms | ~80ms |
| ER(2000) | **43ms** | 4,700ms | ~8s |

**Neighborhood betweenness** (2-hop disease neighborhood, `hops=2, sample_size=200`):

| Graph size | Neighborhood | IVG Rust | IVG OS-par | Notes |
|-----------|-------------|----------|-----------|-------|
| karate (34n) | 26n | **0.4ms** | 3ms | Exact (26 < 200 sample budget) |
| ER(2000) | ~200n | **0.5ms** | 15ms | Subgraph extracted from Rust cache |
| Biomedical KG (10M+n) | ~5K n | **~10ms** | ~500ms | O(neighborhood), not O(graph) |

The neighborhood method's performance depends only on the extracted subgraph size, not the total KG size. A 10M-node graph with a 5K-node 2-hop neighborhood runs as fast as a standalone 5K-node graph.

---

### Community Detection

Leiden runs via `leidenalg` (igraph backend) in IRIS embedded Python when
available — canonical Leiden (Traag 2019). Without igraph+leidenalg it falls back
to networkx Louvain, a **different, lower-quality** algorithm (karate ARI ≈ 0.62
vs Leiden's ≈ 1.0).

| Algorithm | Fixture | IVG (leidenalg, embedded) | Fallback | networkx |
|-----------|---------|---------------------------|----------|---------|
| Leiden | karate (34n) | **14ms** (4 communities, ARI≈1.0) | Louvain (ARI≈0.62) | ~2ms (Louvain) |
| Leiden | ER(500, 2437e) | ~50ms | — | ~200ms |
| Leiden | ER(2000, 9941e) | ~265ms | — | ~2s |
| Triangle count | karate | **<1ms** | — | ~0.5ms |
| SCC | karate | **<1ms** | — | ~0.5ms |
| K-core | karate | **<1ms** | — | ~0.3ms |

---

### Closeness & Eigenvector Centrality

Closeness has two server-side tiers, selected automatically. Correctness is
identical (Pearson r = 1.000000 vs `networkx.harmonic_centrality` in both tiers);
only speed differs.

| Tier | Requires | Implementation |
|------|----------|----------------|
| Fast | igraph in IRIS embedded Python | `Graph.KG.Communities.ClosenessJsonPy` — C-backed igraph closeness, in-process |
| Fallback | nothing | `Graph.KG.NKGAccel.ClosenessGlobal` — pure-ObjectScript sequential all-pairs BFS over `^NKG` |

**Closeness (harmonic), measured 2026-06-02 on `ivg-iris-enterprise` (IRIS 2026.2.0AI):**

| Fixture | Fast (igraph) | Fallback (ObjectScript) | Speedup | Pearson vs networkx |
|---------|---------------|-------------------------|---------|---------------------|
| ER(500, 2437e)  | **19ms**  | 840ms     | 44×  | 1.000000 |
| ER(2000, 9941e) | **119ms** | 22,131ms  | 186× | 1.000000 |

The ObjectScript fallback is O(V·(V+E)) (one BFS per node). For deployments that
cannot install igraph into embedded Python, spec 191 Path B replaces it with a
Multi-Source BFS (MSBFS, 64-source bit-packed frontiers) to close most of this gap
with no dependency.

| Algorithm | Fixture | Fast | Fallback | networkx |
|-----------|---------|------|----------|---------|
| Closeness (harmonic) | karate (34n) | **<1ms** | ~2ms | ~0.5ms |
| Eigenvector | karate (34n) | **<1ms** | ~2ms | ~1ms |

---

## Arno Tier 1 vs Tier 2 vs Tier 3

| Tier | Implementation | Requires | Betweenness ER(2000) sampled |
|------|---------------|----------|------------------------------|
| 1 — Rust | Rust rayon parallel | `accelerator_callout.so` + `^NKG` | **8ms** |
| 2 — OS-par | ObjectScript `%SYSTEM.WorkMgr` 8× + `$BITLOGIC` BFS | `^NKG` | 500ms |
| 3 — LazyKG | Python Brandes over `^KG` Native API | nothing | ~60s |

Dispatch is automatic: call `engine.betweenness_centrality()` and the fastest available tier fires.

---

## Graph Traversal & Query Latency

From the earlier v1.97.0 benchmark run (8.9K nodes, 31K edges, M3 Ultra, Community IRIS 2025.1):

| Operation | p50 | p95 | Notes |
|-----------|-----|-----|-------|
| 1-hop traversal | 570µs | 682µs | `$Order` on `^KG` |
| 2-hop BFS | 17.6ms | 18.2ms | SQL BFS fallback |
| 3-hop BFS | 33.7ms | 34.7ms | SQL BFS fallback |
| 1-hop (arno fast path) | **0.3ms** | — | `^NKG` adjacency index |
| Temporal window query | 0.1ms | — | O(results) B-tree |
| GetAggregate (1 bucket) | 0.085ms | — | Pre-aggregated |
| GetAggregate (288 buckets) | 0.160ms | — | O(buckets), not O(edges) |
| VecIndex search (1K, 128-dim) | 4ms | — | RP-tree + `$vectorop` |
| HNSW search (143K, 768-dim) | 1.7ms | — | Native IRIS VECTOR |
| PLAID search (500 docs, 4 tokens) | 14ms | — | Centroid + MaxSim |
| BM25 search (174 nodes, 3-term) | 0.3ms | — | `$Order` posting-list |
| PPR (10K nodes) | 62ms | — | Pure ObjectScript |

---

## Methodology Notes

- All algorithm benchmarks run against `ivg-iris` (Community) or
  `ivg-iris-enterprise` (Enterprise + arno) containers, localhost, no network overhead.
- "arno" benchmarks require `libarno_callout.so` deployed and `^NKG` built. Note:
  the currently-shipped `.so` registers only `kg_bfs_global`; betweenness, closeness,
  Leiden, and NKG-build Rust functions are not in that build, so those run via their
  ObjectScript / embedded-Python tiers regardless of arno being loaded (see spec 191 R8).
- Closeness (fast tier) and Leiden run in IRIS **embedded Python** via igraph /
  leidenalg — in-process, no data leaves the database. They fall back to ObjectScript /
  networkx when those packages are absent.
- networkx comparisons run on the same Python process with the same input graph.
- Betweenness "sampled=200" uses the same 200-source budget for all engines.
- Betweenness "exact" uses all N sources (full Brandes).
- ER(n, p) = Erdős-Rényi random graph, seed=42.
