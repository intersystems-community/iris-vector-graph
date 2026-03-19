# LDBC Graphalytics: Algorithm Quick Reference

## Algorithm Complexity & Data Structure Matrix

| Algorithm | What | Complexity | Best DS | Bottleneck | Parallelism |
|-----------|------|-----------|---------|-----------|------------|
| **BFS** | Unweighted single-source distance | O(V+E) | CSR + frontier | I/O locality | Vertex-parallel |
| **PageRank** | Link-based importance score | O(k·E) | Backward CSR | Matrix-vector mult | Vertex-parallel |
| **WCC** | Undirected component discovery | O(d·E) | Bidirect CSR | Synchronization | Vertex-parallel |
| **CDLP** | Community label consensus | O(k·E·log d) | CSR + sort | Mode-finding sort | Vertex-parallel |
| **LCC** | Triangle counting per vertex | O(E·√E) | Sorted CSR | Triangle detection | Vertex-parallel (no contention) |
| **SSSP** | Weighted single-source distance | O((V+E)·log V) | CSR + heap | Heap ops | Vertex-parallel + dynamic scheduling |

---

## Detailed Algorithm Specifications

### 1. BFS (Breadth-First Search)

**Input**: Graph G = (V, E), source vertex s  
**Output**: dist[v] = shortest unweighted distance from s to v

**Algorithm**:
```
dist[v] = ∞ for all v
dist[s] = 0
frontier = [s]

while frontier is not empty:
    next_frontier = []
    for each v in frontier:
        for each neighbor u of v:
            if dist[u] == ∞:
                dist[u] = dist[v] + 1
                append u to next_frontier
    frontier = next_frontier
```

**Key Optimizations**:
- **Direction switching**: When frontier > |V|/20, switch to pull (iterate neighbors of unvisited)
- **Bitset visited**: O(V/8) space, O(1) atomic check + set
- **Dense frontier**: Keep in contiguous array, not linked list

**Determinism**: Naturally deterministic (BFS traversal order is fixed)

**IRIS Implementation Notes**:
- Use native bitset (RLE-compressed)
- Parallel frontier expansion: partition vertices by ID ranges
- Multi-source variant: initialize frontier with multiple sources simultaneously

---

### 2. PageRank (PR)

**Input**: Graph G = (V, E), damping factor d = 0.85, max iterations k = 30

**Output**: rank[v] = PageRank score for each vertex

**Algorithm**:
```
rank[v] = 1/|V| for all v

for iteration = 1 to k:
    new_rank[v] = (1-d)/|V| + d * Σ(rank[u] / out_degree[u]) for all u → v
    rank = new_rank
```

**Key Optimizations**:
- **Backward CSR**: Store incoming edges for pull-based computation
- **Ping-pong arrays**: Avoid allocation per iteration
- **Out-degree precompute**: Store out_degree[u] separately
- **No normalization**: Store raw scores, normalize at output

**Determinism**: Exactly reproducible across runs (no floating-point accumulation errors if careful)

**IRIS Implementation Notes**:
- Precompute out_degree during CSR construction
- Use double precision for scores
- Early termination: check Σ|rank_new - rank_old| < ε
- Convergence typically ~5-15 iterations, but spec uses fixed 30

---

### 3. WCC (Weakly Connected Components)

**Input**: Graph G = (V, E) [edges treated as undirected]  
**Output**: component[v] = component ID for each vertex

**Algorithm**:
```
label[v] = v for all v  // Initialize each vertex as its own component

changed = true
while changed:
    changed = false
    for each v in V:
        new_label = min(label[v], min(label[u] for all u adjacent to v))
        if new_label < label[v]:
            label[v] = new_label
            changed = true
```

**Key Optimizations**:
- **Synchronous propagation**: Update all in parallel, swap arrays each iteration
- **Atomic min**: Use compare-and-swap for parallel safety
- **Early termination**: Converges in O(diameter) iterations (typically 3-10)

**Determinism**: Naturally deterministic (synchronous min-propagation)

**IRIS Implementation Notes**:
- Use atomic min operations on label arrays
- Bidirectional CSR critical for undirected graphs
- Barrier synchronization between iterations
- Converges much faster than PR typically

---

### 4. CDLP (Community Detection Label Propagation)

**Input**: Graph G = (V, E), max iterations k = 30  
**Output**: community[v] = community ID for each vertex

**Algorithm**:
```
label[v] = hash(v) % num_partitions for all v  // Initial random partition

for iteration = 1 to k:
    for each v in V:
        labels_of_neighbors = [label[u] for u in neighbors(v)]
        new_label[v] = most_frequent_label(labels_of_neighbors)
        // Tie-breaking: if tie, use smallest label ID
    label = new_label
```

**Key Optimizations**:
- **Mode-finding**: Sort neighbor labels O(d·log d), not histogram O(d)
- **Synchronous update**: Swap label arrays each iteration
- **Frequency map**: Local histogram per vertex (efficient for small degrees)

**Determinism**: Must use deterministic tie-breaking (sort, not randomized max)

**IRIS Implementation Notes**:
- Local frequency sorting: use radix sort if labels densely packed
- Synchronous barrier between iterations
- Can pre-allocate neighbor label buffers
- Converges in ~5-15 iterations typically

---

### 5. LCC (Local Clustering Coefficient)

**Input**: Graph G = (V, E)  
**Output**: lcc[v] = local clustering coefficient for each vertex

**Formula**:
```
lcc[v] = 2 * triangles_in_neighborhood(v) / (degree[v] * (degree[v] - 1))

where triangles_in_neighborhood(v) = number of edges (u, w) where u, w are neighbors of v
```

**Algorithm**:
```
for each vertex v:
    neighbors = get_sorted_neighbors(v)
    triangle_count = 0
    
    for each pair (u, w) in neighbors:
        if edge exists (u, w):
            triangle_count += 1
    
    lcc[v] = 2.0 * triangle_count / (deg[v] * (deg[v] - 1))
```

**Key Optimizations**:
- **Sorted-merge triangle counting**: O(min(deg[u], deg[w])) per pair
- **CSR with sorted adjacency lists**: Critical for merge efficiency
- **Avoid adjacency matrix**: Would be O(V²) memory
- **Skip low-degree vertices**: lcc[v] = 0 if deg[v] < 2

**Determinism**: Naturally deterministic

**IRIS Implementation Notes**:
- **BOTTLENECK ALGORITHM**: LCC is typically 5-20x slower than others
- Sorted neighbor lists **MUST** be pre-computed during CSR construction
- Parallel: vertex-parallel, no contention (each vertex independently computes LCC)
- Vectorization opportunity: SIMD merge for degree sequences

---

### 6. SSSP (Single Source Shortest Path)

**Input**: Graph G = (V, E, weights), source vertex s  
**Output**: distance[v] = shortest weighted distance from s to v

**Algorithm** (Dijkstra):
```
distance[v] = ∞ for all v
distance[s] = 0
heap = {(0, s)}  // (distance, vertex) pairs

while heap is not empty:
    (d, u) = extract_min(heap)
    if distance[u] < d:
        continue  // Already processed
    
    for each edge (u, v) with weight w:
        if distance[u] + w < distance[v]:
            distance[v] = distance[u] + w
            insert (distance[v], v) into heap
```

**Key Optimizations**:
- **Binary min-heap**: O(log V) per operation, total O((V+E)·log V)
- **Columnar weights**: Store weights[i] contiguously with edge_targets[i]
- **Distance array**: Cache-friendly, supports atomic updates
- **Skip duplicate popping**: Check if dist[u] < popped distance before relaxing

**Determinism**: Deterministic if heap tie-breaking is fixed (by vertex ID)

**IRIS Implementation Notes**:
- Binary heap essential (AVL tree or treap slower)
- Precompute edge weights in columnar format during CSR construction
- Parallel variant: Δ-stepping or parallel Dijkstra (complex, not typical for LDBC)
- Single-source only (per Graphalytics spec): fixed source per run

---

## Common Implementation Patterns

### CSR Construction Pipeline

```
Phase 1: Read edges
  - Scan edge file
  - Count out-degrees: degree[u]++
  - Assign dense vertex IDs (if needed)

Phase 2: Compute offsets
  - offsets[0] = 0
  - offsets[v+1] = offsets[v] + degree[v]
  - Total = offsets[|V|]

Phase 3: Fill edges
  - targets[] array of size Total
  - For each edge (u, v):
    - targets[offsets[u]] = v
    - offsets[u]++
  - Fix offsets: offsets[v] = offsets[v] - original_degree[v]

Result: offsets[] (size V+1), targets[] (size E)
Access neighbors of v: targets[offsets[v]:offsets[v+1]]
```

### Parallel Iteration Pattern (All Algorithms)

```
// Safe parallel iteration over vertices
#pragma omp parallel for schedule(static)
for (int v = 0; v < V; v++) {
    for (int i = offsets[v]; i < offsets[v+1]; i++) {
        int u = targets[i];
        // Process edge (v, u)
        // NO writes to arrays indexed by other vertices
    }
}
```

### Synchronous Iteration Pattern (WCC, CDLP, PR)

```
// Array swap pattern
double[] current = new double[V];
double[] next = new double[V];

for (int iteration = 0; iteration < max_iterations; iteration++) {
    #pragma omp parallel for
    for (int v = 0; v < V; v++) {
        next[v] = compute_new_value(v, current);
    }
    // Barrier implicit in omp for
    
    swap(current, next);  // Java: use reference assignment
}
```

---

## Determinism Checklist

- [ ] **No randomization**: All choices deterministic (e.g., tie-breaking by ID)
- [ ] **No floating-point accumulation**: Use Kahan summation if needed
- [ ] **No hash-table iteration**: Sort before iterating (CDLP mode-finding)
- [ ] **Synchronous updates**: No interleaved async updates
- [ ] **Same iteration count**: PR, CDLP use fixed iterations (not early termination)
- [ ] **Identical output format**: Vertex ordering, precision (e.g., 6 decimal places for LCC)

---

## Performance Targets (Embedded, Single Core, datagen-7_5-fb: 633K V, 34M E)

| Algorithm | ArcadeDB | Neo4j | Target |
|-----------|----------|-------|--------|
| BFS | 0.13s | ~2s | < 1s |
| WCC | 0.30s | ~0.75s | < 1s |
| PR | 0.48s | ~11s | < 2s |
| CDLP | 3.67s | ~6.4s | < 5s |
| LCC | 27.41s | ~45s | < 30s |
| SSSP | 3.53s | N/A | < 5s |

**Total**: ~35 seconds (all 6 algorithms)

---

## References

- LDBC Graphalytics Spec v1.0.5: https://arxiv.org/pdf/2011.15028v6.pdf
- ArcadeDB Reference: https://github.com/ArcadeData/ldbc_graphalytics_platforms_arcadedb

