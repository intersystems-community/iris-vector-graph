# LDBC Graphalytics Benchmark: Comprehensive Research Report

## Executive Summary

LDBC Graphalytics is an **industrial-grade benchmark** developed by the Linked Data Benchmark Council (LDBC) for evaluating graph processing platforms. It standardizes graph algorithm implementation, testing, and performance reporting to enable objective comparison of diverse systems—from distributed frameworks (Spark GraphX, Giraph) to embedded databases (Neo4j, ArcadeDB) to specialized OLAP engines.

The benchmark is **deterministic, reproducible, and rigorous**, with validation harnesses, standard datasets, reference implementations, and deep metrics that capture both performance (speed) and robustness (failures, variability).

---

## 1. Benchmark Architecture & Structure

### Core Components

**Framework** (Java-based, open-source, Apache 2.0)
- **graphalytics-core**: Test harness, execution framework, performance monitoring
- **graphalytics-validation**: Reference outputs, deterministic validation checker
- **graphalytics-plugins-granula**: Detailed performance metrics collection
- Modular design allows new platforms to be added by implementing the driver interface

**Specification** (v1.0.5, updated Apr 2023)
- 43-page formal specification (arXiv:2011.15028v6)
- Defines algorithms, datasets, validation rules, scoring, and reporting standards
- Published as technical spec + VLDB paper (2015)

### Testing Harness
1. **Isolation**: Each algorithm is loaded + run separately (per-algorithm reload)
2. **Measurement**: Tracks load_time, processing_time, makespan per run
3. **Validation**: Output is automatically validated against reference results
4. **Repetition**: Multiple runs with configurable repetitions
5. **Robustness**: Measures failure rates, performance variability, weak/strong scalability

### Performance Metrics
- **load_time**: Time to import graph into system (measured separately from algorithm execution)
- **processing_time**: Pure algorithm execution time on loaded graph
- **makespan**: Total elapsed time (wall-clock, can be parallel)
- **Scalability**: Weak scaling (bigger graphs on more nodes), strong scaling (same graph on more nodes)
- **Robustness**: Coefficient of variation (CV) across runs, failure rates

---

## 2. The Six Kernel Algorithms

Graphalytics defines exactly **six deterministic algorithms** chosen to cover diverse graph analysis patterns:

### BFS (Breadth-First Search)
**What**: Single-source shortest path in unweighted graphs  
**Compute**: Partition vertices into levels based on distance from source  
**Output**: Vertex → distance_from_source  
**Optimal Data Structure**: 
- Frontier queue (expandable array or deque)
- Visited bitset (O(V) space, O(1) lookup)
- Optional: Direction optimization (push when frontier small, pull when frontier large)
- CSR format ideal for cache locality + parallel traversal

**Complexity**: O(V + E)  
**Implementation Notes**: Can use push/pull frontier switching for 2-3x speedup on large graphs

---

### PageRank (PR)
**What**: Iterative probabilistic importance score for graph vertices  
**Compute**: Iterate: PR[v] = (1-d)/N + d * Σ(PR[u] / out_degree[u]) for u → v  
- Default: 30 iterations, damping factor d = 0.85, tolerance = 0.0001
- Convergence-based: Stop when Σ|ΔPR| < threshold

**Output**: Vertex → rank_score  
**Optimal Data Structure**:
- Backward CSR (incoming edges) for pull-based computation
- Two arrays: current + next scores (ping-pong updates)
- Out-degree array precomputed
- No object allocation in hot loop

**Complexity**: O(iterations × E), typically 30-40 iterations  
**Implementation Notes**: Pull-based (iterate over outgoing) > push-based due to branch predictability

---

### WCC (Weakly Connected Components)
**What**: Partition vertices into connected components (treating edges as undirected)  
**Compute**: Synchronous min-label propagation:
1. Each vertex initializes label = own ID
2. Iterate: label[v] = min(label[v], min(label[neighbors]))
3. Stop when no labels change

**Output**: Vertex → component_id  
**Optimal Data Structure**:
- CSR (forward + backward) for undirected edges
- Two label arrays (current + next, swap each iteration)
- Atomic min operations for parallel updates
- Typically converges in O(diameter) iterations

**Complexity**: O(diameter × E)  
**Implementation Notes**: Low diameter => fast convergence; synchronous propagation ensures determinism

---

### CDLP (Community Detection via Label Propagation)
**What**: Partition vertices into communities based on label consensus (variant of Louvain)  
**Compute**: Synchronous label propagation with mode-finding:
1. Initialize: label[v] = v's partition_id (random or hierarchical)
2. Iterate: label[v] = most_frequent_label(label[neighbors])
3. Stop after fixed iterations (max 30)

**Output**: Vertex → community_id  
**Optimal Data Structure**:
- CSR (undirected or directed, depending on graph)
- Two label arrays (swap each iteration)
- Frequency map per vertex (sort to find mode = O(log degree))
- Local histogram vs global histogram (affects determinism)

**Complexity**: O(iterations × E × log d) where d = avg degree  
**Implementation Notes**: Mode-finding must be deterministic (sort by frequency, break ties by label value). Sort-based > histogram due to determinism guarantee.

---

### LCC (Local Clustering Coefficient)
**What**: Triangle count for each vertex's neighborhood  
**Compute**: For each vertex v, count triangles in the subgraph induced by v's neighbors
- LCC[v] = (triangles_in_neighbors) / (max_possible_edges_in_neighbors)
- Formula: LCC[v] = 2 × triangles / (deg[v] × (deg[v] - 1))

**Output**: Vertex → clustering_coefficient (float [0, 1])  
**Optimal Data Structure**:
- CSR with sorted neighbor lists
- Sorted-merge triangle counting: O(min(deg[u], deg[v]))
- Avoid explicit adjacency matrix (too expensive)
- Parallel: vertex-parallel (each vertex computes own LCC independently)

**Complexity**: O(E × sqrt(E)) for entire graph; per-vertex: O(deg[v]²)  
**Implementation Notes**: 
- Memory requirement: Must NOT materialize adjacency matrix
- Sorted neighbor lists critical for efficient merge
- Can parallelize vertex-wise without contention

---

### SSSP (Single Source Shortest Path)
**What**: Dijkstra's algorithm: compute shortest path distances from source vertex to all others  
**Compute**:
1. Initialize: distance[source] = 0, distance[all others] = ∞
2. Use binary min-heap: extract min, relax outgoing edges
3. Terminate when heap empty

**Output**: Vertex → shortest_distance_from_source  
**Optimal Data Structure**:
- CSR with edge weights in columnar format
- Binary min-heap (on distance + vertex ID for determinism)
- Distance array + visited bitset
- Weights stored contiguously (cache-friendly)

**Complexity**: O((V + E) × log V)  
**Implementation Notes**: 
- Heap operations dominate; must use efficient min-heap (not priority queue)
- Determinism: heap ties broken by vertex ID
- Weighted vs unweighted: LDBC uses weighted (real-world graphs)

---

## 3. Benchmark Datasets

### Dataset Characteristics
The benchmark includes **20+ datasets** across multiple scales:

| Dataset | Vertices | Edges | Scale | Size (compressed) | Type |
|---------|----------|-------|-------|-------------------|------|
| example-directed | 10 | 17 | XS | — | synthetic |
| example-undirected | 9 | 12 | XS | — | synthetic |
| cit-Patents | 3.8M | 16M | XS | 119 MB | real (citations) |
| datagen-7_5-fb | 633K | 34M | S | 162 MB | synthetic (Facebook-like) |
| datagen-7_6-fb | 754K | 42M | S | 200 MB | synthetic (Facebook-like) |
| datagen-7_7-zf | 13M | 32M | S | 434 MB | synthetic (Zipfian) |
| datagen-8_0-fb | 1.7M | 107M | M | 502 MB | synthetic (Facebook-like) |
| datagen-8_4-fb | 3.8M | 269M | M | 1.2 GB | synthetic (Facebook-like) |
| datagen-8_9-fb | 10.6M | 848M | L | — | synthetic (Facebook-like) |
| datagen-9_0-fb | 12.9M | 1B | L | — | synthetic (Facebook-like) |
| graph500-22 | 2.4M | 64M | M | — | synthetic (random) |
| graph500-25 | 17M | 523M | L | — | synthetic (random) |
| graph500-28 | 121M | 4.2B | XL | — | synthetic (random) |
| com-friendster | 65M | 1.8B | XL | 6.7 GB | real (social network) |

### Dataset Properties
- **Vertex degrees**: Power-law distribution (realistic) or uniform (Graph500)
- **Directionality**: Both directed and undirected
- **Weights**: Edge weights [1, 100] for SSSP
- **Bipartiteness**: Some synthetic datasets designed for specific patterns

### Total Size
- **Compressed** (zstd): ~350 GB
- **Uncompressed**: ~1.5 TB disk space
- **Download scripts** provided for incremental fetch (by scale: test, S, M, L, XL, 2XL+)

### Reference Outputs
For each (dataset, algorithm) pair, pre-computed reference outputs included:
- Validation against reference output detects algorithm bugs
- Exact floating-point comparison (deterministic reproducibility required)

---

## 4. Scoring & Ranking System

### Performance Report Structure

**Per-run metrics**:
```json
{
  "algorithm": "PageRank",
  "dataset": "datagen-8_4-fb",
  "load_time": 95.04,      // seconds
  "processing_time": 16.12, // seconds
  "makespan": 48.80,        // wall-clock seconds
  "timestamp": "2025-03-18T10:30:00Z"
}
```

### Scoring Rules

1. **Validation**: Algorithm output must match reference output exactly
   - Floating-point: Epsilon-based comparison for numeric stability
   - Integer: Exact match required

2. **Per-algorithm ranking** (on a single dataset):
   - **Execution Time**: processing_time (primary)
   - **Load Time**: Important but often platform-specific (not heavily weighted)
   - **Makespan**: Important for parallelism evaluation

3. **Cross-algorithm score**:
   - Geometric mean of execution times (avoids outliers)
   - Normalized per dataset to avoid bias toward fast algorithms

4. **Platform score**:
   - Harmonic or geometric mean across all (dataset, algorithm) pairs
   - Sometimes split by scale (XS, S, M, L, XL)

### Reporting Metrics

**Deep Metrics** (from Granula plugin):
- CPU utilization
- Memory usage peak/average
- I/O throughput
- Network communication (if distributed)
- Variance across runs (robustness)

**Competition Ranking**:
- Public leaderboard: https://ldbcouncil.org/benchmarks/graphalytics/
- Separated by graph size (Small, Medium, Large)
- Platforms must implement ALL 6 algorithms to be ranked

---

## 5. Existing Platform Drivers

### Official/Community-Maintained Drivers

| Platform | Repository | Language | Status | License |
|----------|-----------|----------|--------|---------|
| **GraphBLAS** | ldbc_graphalytics_platforms_graphblas | C/CUDA | ✅ Reference | Apache 2.0 |
| **Umbra** | ldbc_graphalytics_platforms_umbra | C++ | ✅ Reference | Apache 2.0 |
| **Neo4j** | atlarge-research/graphalytics-platforms-neo4j | Java | ✅ Community | Apache 2.0 |
| **Spark GraphX** | (archived) | Scala | ⚠️ Historical | Apache 2.0 |
| **Giraph** | (archived) | Java | ⚠️ Historical | Apache 2.0 |
| **ArcadeDB** | ArcadeData/ldbc_graphalytics_platforms_arcadedb | Java | ✅ Recent (2024) | Apache 2.0 |

### Neo4j Driver Details
- **Embedded mode**: Uses Neo4j Java API
- **Server mode**: Uses Neo4j Graph Algorithms Library (GDS)
- Configuration: Select implementation at benchmark init time

### ArcadeDB Driver (Latest Example - 2024)
- **Mode**: Embedded Java
- **GAV (Graph Analytical View)**: CSR construction from OLTP storage
- **Algorithms**: All 6 kernels
- **Performance**: Competitive with/faster than Neo4j GDS

### Why Few Drivers?
1. **High barrier**: Requires implementing all 6 algorithms correctly (deterministically)
2. **Validation burden**: Must match reference outputs exactly
3. **Performance tuning**: Non-trivial optimization work
4. **Framework dependency**: Platform driver must integrate with LDBC harness

---

## 6. CSR (Compressed Sparse Row) Data Structure

### Why CSR?
Most high-performance graph algorithm implementations use **CSR** because:
- **Memory efficient**: O(V + E) space
- **Cache-friendly**: Dense arrays, predictable access patterns
- **Parallel-friendly**: Easy vertex partitioning, no locks needed
- **Enables direction optimization**: Forward + backward CSR

### CSR Layout

```c
// CSR adjacency matrix for undirected graph
int[] vertex_offsets = [0, 2, 5, 7, 10];  // Index into edge_ids for each vertex
int[] edge_targets = [1, 3, 0, 2, 3, 0, 1, 2, 1, 3];  // Neighbor IDs

// Access neighbors of vertex v:
for (int i = vertex_offsets[v]; i < vertex_offsets[v+1]; i++) {
  int neighbor = edge_targets[i];
  // process neighbor
}
```

### Bidirectional CSR (for undirected graphs)
```c
// Forward CSR (outgoing edges)
int[] offsets_out = [...];
int[] targets_out = [...];

// Backward CSR (incoming edges)
int[] offsets_in = [...];
int[] targets_in = [...];
```

### Edge Properties (Columnar Storage)
```c
// Edge weights stored separately
double[] edge_weights = [...];  // weights[i] = weight of edge at targets[i]

// Enables SIMD vectorization + cache efficiency
```

### Construction Cost
- **Pass 1**: Scan all edges, count degrees, assign dense IDs: O(V + E)
- **Pass 2**: Prefix-sum degrees, fill adjacency arrays: O(V + E)
- **Total**: O(V + E) with low constant factor

---

## 7. What It Takes to Build a Competitive Driver

### 1. Core Requirements
- [ ] Implement all 6 algorithms (deterministically)
- [ ] Build CSR or equivalent representation for fast traversal
- [ ] Integrate with LDBC framework (extends PlatformBenchmark)
- [ ] Load benchmark datasets in standard format (.v, .e, .properties)
- [ ] Validate outputs against reference files

### 2. Algorithm Implementation Quality
- **Correctness**: Must pass validation on ALL datasets
- **Determinism**: Identical output across runs (no randomization)
- **Efficiency**:
  - No redundant iterations (converge exactly when reference does)
  - No unnecessary data structures
  - Parallel where possible (but must remain deterministic)

### 3. Data Structure Optimization
- **For BFS/WCC**: CSR + bitset for visited
- **For PR/CDLP**: Bidirectional CSR + efficient iteration
- **For LCC**: Sorted-neighbor CSR for triangle counting
- **For SSSP**: CSR + binary heap + columnar weights

### 4. Performance Tuning
- **Memory layout**: Cache-friendly (avoid pointer chasing)
- **Parallelization**: OpenMP, CUDA, or native threading
- **Vectorization**: SIMD where applicable (especially for LCC triangle counting)
- **JVM-specific** (if Java): JIT compilation warm-up, GC tuning, -XX:+UseG1GC

### 5. LDBC Integration
```java
// Minimal driver implementation:
public class ArcadeDBPlatformDriver extends PlatformBenchmark {
  public void loadGraph(String path) { /* ... */ }
  
  public void breadthFirstSearch(long source, String output) { /* BFS */ }
  public void pageRank(int iterations, String output) { /* PR */ }
  public void weaklyConnectedComponents(String output) { /* WCC */ }
  public void communityDetectionLP(int iterations, String output) { /* CDLP */ }
  public void localClusteringCoefficient(String output) { /* LCC */ }
  public void singleSourceShortestPath(long source, String output) { /* SSSP */ }
  
  public void validateOutput(String algoName, String outputPath) { /* validation */ }
}
```

### 6. Validation & Testing
- Compare outputs byte-for-byte with reference results
- Test on ALL datasets (XS, S, M, L scales)
- Test on multiple runs (CV < 10% typically required)
- Automated CI/CD pipeline

---

## 8. Performance Characteristics: ArcadeDB Example (2024 Reference)

### Embedded ArcadeDB (datagen-7_5-fb: 633K vertices, 34M edges)

| Algorithm | Time | Structure |
|-----------|------|-----------|
| BFS | 0.13s | Frontier + bitset |
| WCC | 0.30s | Min-label propagation |
| PR (30 iter) | 0.48s | Pull-based iteration |
| CDLP (30 iter) | 3.67s | Label propagation + sort |
| LCC | 27.41s | Sorted-merge triangle counting |
| SSSP | 3.53s | Dijkstra + binary heap |

### vs Competitors (Same Dataset)
- **Neo4j GDS**: 5-327x slower depending on algorithm
- **Kuzu**: 1.4-9x slower
- **DuckPGQ**: 1.4-46x slower (no LCC)
- **Memgraph**: Crashes or 35x slower (limited algorithm support)

---

## 9. Official Documentation & Resources

### Key URLs
- **Main benchmark**: https://ldbcouncil.org/benchmarks/graphalytics/
- **Specification PDF**: https://ldbcouncil.org/ldbc_graphalytics_docs/graphalytics_spec.pdf
- **Framework GitHub**: https://github.com/ldbc/ldbc_graphalytics
- **Specification source**: https://github.com/ldbc/ldbc_graphalytics_docs
- **Datasets**: https://ldbcouncil.org/benchmarks/graphalytics/datasets/

### Papers
1. **VLDB 2015**: "LDBC Graphalytics: A Benchmark for Large-Scale Graph Analysis on Parallel and Distributed Platforms"
   - Original paper defining the benchmark
   - https://www.vldb.org/pvldb/vol9/p1317-iosup.pdf

2. **arXiv v1.0.5 (Apr 2023)**: Full technical specification
   - Latest version with algorithm details + validation rules
   - https://arxiv.org/pdf/2011.15028v6.pdf

---

## 10. Key Takeaways for Building an IRIS Driver

### Competitive Advantage Potential
1. **IRIS vector search + graph traversal**: Native support for both could be strong
2. **CSR + vector columns**: IRIS can store CSR structure efficiently, reuse for vectors
3. **ACID guarantees**: Built-in transactions for validation/testing phases
4. **Multi-model**: Store graph + metadata in same database

### Critical Success Factors
1. **All 6 algorithms must be implemented** (competitive drivers often stop at 3-4)
2. **Determinism is non-negotiable** (floating-point precision matters)
3. **CSR construction efficiency** (loading time is significant)
4. **Vectorization opportunity**: LCC triangle counting is main bottleneck
5. **Parallel implementations**: Must scale reasonably to larger datasets

### Benchmark Value
- Validates platform robustness + scalability
- Provides third-party validation (vs self-reported benchmarks)
- Opens doors to academic + commercial partnerships
- Positions IRIS as graph-capable database

---

## References

- LDBC Graphalytics Specification v1.0.5 (2023)
- LDBC Graphalytics GitHub: https://github.com/ldbc/ldbc_graphalytics
- ArcadeDB Driver (2024): https://github.com/ArcadeData/ldbc_graphalytics_platforms_arcadedb
- Original VLDB Paper (2015): https://www.vldb.org/pvldb/vol9/p1317-iosup.pdf

