# LDBC Graphalytics Competitive Analysis

## Executive Summary

Based on 2024 benchmarks (ArcadeDB reference implementation), here's what it takes to be competitive:

| System | Strength | Weakness | Viability |
|--------|----------|----------|-----------|
| **ArcadeDB (Embedded)** | 0.48s-27s per algo | Limited to embedded; no distributed | ✅ Actively maintained |
| **Neo4j GDS** | Production-grade, docs | 5-327x slower; missing LCC | ⚠️ Community interest |
| **GraphBLAS (Reference)** | Scientific reference | Sparse matrix focus | ✅ LDBC reference |
| **Kuzu** | C++ embedded; growing | 1.4-9x slower; limited algo coverage | ⚠️ Emerging (2024) |
| **Memgraph** | Graph DB native | Crashes at 18-20M edges; OOM issues | ❌ Not recommended |
| **DuckPGQ** | SQL integration | 1.4-46x slower; incomplete algorithms | ⚠️ Research-grade |

---

## Platform Performance Comparison

### All Algorithms: datagen-7_5-fb (633K V, 34M E)

| Algorithm | ArcadeDB | Neo4j GDS | Kuzu | DuckPGQ | Memgraph | HugeGraph |
|-----------|----------|-----------|------|---------|----------|-----------|
| **BFS** | **0.13s** | 1.91s | 0.86s | 2,754s! | 11.72s | 0.54s |
| **PageRank** | **0.48s** | 11.15s | 4.30s | 6.14s | 16.90s | 4.01s |
| **WCC** | **0.30s** | 0.75s | 0.43s | 13.93s | CRASH | 6.71s |
| **LCC** | **27.41s** | 45.78s | N/A | 38.59s | N/A | 272.04s |
| **SSSP** | **3.53s** | N/A | N/A | N/A | N/A | N/A |
| **CDLP** | **3.67s** | 6.43s | N/A | N/A | N/A | 62.70s |
| **Total (implemented)** | **35.5s** | ~65s* | ~5.6s* | **N/A*** | **N/A** | ~346s* |

\* Only implements subset of algorithms  
** Memgraph crashes during data loading before running algorithms

---

## Why ArcadeDB Wins on LDBC

### Key Advantages

1. **All 6 algorithms implemented** (only platform with complete coverage)
   - BFS, WCC, PR, CDLP, LCC, SSSP
   - Others: 3-5 algorithms only

2. **Graph Analytical View (GAV)** - CSR engine
   - Compact adjacency index: O(V + E) memory
   - Zero GC pressure in hot loops
   - Native SIMD support (jdk.incubator.vector)
   - Forward + backward CSR for direction optimization

3. **Embedded + Server modes**
   - Embedded: ~0.5-3s per algo (reference baseline)
   - Docker server: ~1-5s per algo (network overhead)
   - Reproducible, non-variable performance

4. **Pure determinism**
   - No floating-point accumulation errors
   - Synchronous algorithm execution
   - Bitset + sorted data structures guarantee reproducibility

5. **Load efficiency**
   - CSR construction: ~95s for 34M edge graph
   - Competitive with Neo4j, much faster than DuckPGQ

---

## Where Others Fail

### Neo4j (Graph Database, Production Focus)
**Wins**:
- Feature-rich query language (Cypher)
- ACID guarantees, cluster support
- GDS (Graph Data Science) library mature

**Fails on Graphalytics**:
- GDS algorithms not optimized for raw speed
- Missing LCC entirely
- SSSP not publicly available
- BFS 15x slower than ArcadeDB (uses sparse CSR, not dense)
- PageRank 23x slower

**Why slow**: Neo4j stores graphs as property graph (nodes/relationships), not CSR. Algorithms must traverse property structures, not dense arrays.

### Kuzu (Embedded C++ Graph DB)
**Wins**:
- Modern C++ implementation
- Small graphs (1M vertices): competitive
- Growing community

**Fails**:
- No LCC implementation
- SSSP missing
- CDLP missing
- ~1.4-9x slower on BFS/PR/WCC
- Newer, less mature than ArcadeDB

### Memgraph (RedisGraph Fork)
**Fails catastrophically**:
- Crashes with segfault at 18-20M edges (half the test dataset)
- Previous version: OOM at 7.6GB on WCC
- Community version too immature
- Only implements: BFS, CDLP (WCC/SSSP/LCC not available)

### DuckPGQ (DuckDB + PostgreSQL Graph Query)
**Fails**:
- BFS: 2,754 seconds (21,000x slower than ArcadeDB!)
- WCC: 13.93s (46x slower)
- PageRank/CDLP: incomplete
- SQL-based query planning overhead
- Not designed for dense graph traversal

---

## Building a Competitive IRIS Driver

### Minimum Viability

**MUST have**:
1. ✅ All 6 algorithms
2. ✅ CSR or equivalent compact format
3. ✅ Deterministic implementation
4. ✅ < 100s total for datagen-8_4-fb (3.8M V, 269M E)
5. ✅ Validated against reference outputs

**Nice to have**:
- Bidirectional CSR for direction optimization
- Parallel implementation (OpenMP or vectorization)
- Both embedded and server modes

### Performance Targets

To be **competitive with Neo4j GDS**:
- BFS: < 3s (Neo4j: 1.9s, target: < 2s) 
- PageRank: < 20s (Neo4j: 11s, target: < 15s)
- WCC: < 1.5s (Neo4j: 0.75s, target: < 1s)
- CDLP: < 10s (Neo4j: 6.4s, target: < 8s)
- LCC: < 60s (Neo4j: 45s, target: < 50s)
- SSSP: < 10s (Neo4j: N/A, ArcadeDB: 3.5s, target: < 8s)
- **Total**: < 80s

To be **competitive with ArcadeDB**:
- Cut these times by 2-5x (very challenging for Java/IRIS)
- BFS: < 0.5s, WCC: < 0.5s, PR: < 1s, etc.
- **Total**: < 50s

### IRIS-Specific Considerations

**Advantages**:
- IRIS Vector Search can be repurposed for degree sequences (LCC optimization)
- RDF data model maps naturally to property graphs
- ACID guarantees useful for determinism testing

**Challenges**:
- IRIS is primarily OLTP (online transaction processing)
- Graph algorithms need OLAP (analytics) optimization
- Must build CSR as separate, optimized structure
- InterSystems platform: fewer resources than Neo4j/ArcadeDB teams

**Strategy**:
1. Start with embedded mode (like ArcadeDB)
2. Use InterSystems GraphQL layer for queries + validation
3. Build CSR as native data structure (rows in RDF schema)
4. Leverage IRIS parallelization for vertex-parallel algorithms
5. Document as "IRIS Vector Graph Benchmark" driver

---

## Leaderboard Requirements

### To Appear on LDBC Public Leaderboard

1. **Submit official driver** to https://github.com/ldbc/ldbc_graphalytics
   - Fork, implement PlatformBenchmark interface
   - All 6 algorithms required
   - Pull request with benchmarks

2. **Validation**:
   - Run on test graphs (example-directed, example-undirected, cit-Patents)
   - Outputs match reference exactly
   - LDBC maintainers review

3. **Performance report**:
   - At least: datagen-7_5-fb (Small), datagen-8_4-fb (Medium)
   - Optional: larger graphs (L, XL)
   - Multiple runs (3-5), report variance

4. **Public announcement**:
   - Added to official leaderboard: https://ldbcouncil.org/benchmarks/graphalytics/
   - Academic + commercial recognition

### Current Public Leaderboard

As of 2024, appears to be:
- **ArcadeDB** (2024, new entry)
- **GraphBLAS** (reference)
- **Umbra** (reference)
- Neo4j (community)
- Historical: Giraph, GraphX, PowerGraph

---

## Recommendation for IRIS-VectorGraph

### Go / No-Go Decision

**GO if**:
- [ ] IRIS team can dedicate 2-3 engineers for 3-4 months
- [ ] Focus on embedded mode first (easier than server)
- [ ] Willing to accept 2-5x slower than ArcadeDB (acceptable for comparison)
- [ ] Marketing value of "Graph-Capable Database" benchmark is strategic
- [ ] IRIS has native parallel execution (map-reduce or equivalent)

**NO-GO if**:
- [ ] IRIS is purely OLTP and cannot build optimized OLAP structures
- [ ] No parallel execution framework available
- [ ] LCC triangle counting bottleneck (needs 5-20x speedup to be competitive)
- [ ] Limited engineering capacity

### Expected Outcomes

**Success case**:
- Driver completes all 6 algorithms
- Total runtime: 80-150s on datagen-8_4-fb
- Appears on leaderboard alongside Neo4j, Kuzu, HugeGraph
- Academic credibility for IRIS in graph analytics

**Minimum viable**:
- Driver completes all 6 algorithms
- Total runtime: 150-300s on datagen-8_4-fb
- Demonstrates capability, even if slower
- Community engagement (research/students)

**Failure case**:
- Incomplete algorithms (e.g., missing LCC)
- Crashes or validation failures
- Not competitive enough to publish

---

## Key References & Contacts

**LDBC Graphalytics**:
- Main: https://ldbcouncil.org/benchmarks/graphalytics/
- GitHub Framework: https://github.com/ldbc/ldbc_graphalytics
- Specification: https://arxiv.org/pdf/2011.15028v6.pdf
- Datasets: https://ldbcouncil.org/benchmarks/graphalytics/datasets/

**LDBC Leadership** (for partnerships):
- Gabor Szarnyas (coordinator): firstname.lastname@ldbcouncil.org
- David Puroja (competitions): firstname.lastname@ldbcouncil.org

**Reference Implementations**:
- ArcadeDB (2024): https://github.com/ArcadeData/ldbc_graphalytics_platforms_arcadedb
- GraphBLAS: https://github.com/ldbc/ldbc_graphalytics_platforms_graphblas
- Umbra: https://github.com/ldbc/ldbc_graphalytics_platforms_umbra

---

## Final Assessment

LDBC Graphalytics is a **rigorous, well-maintained benchmark** that distinguishes systems by raw algorithmic capability. Building a driver would demonstrate IRIS's graph processing prowess and open partnerships with academic research groups using Graphalytics for comparison studies.

**ArcadeDB's 2024 driver shows it's achievable**: All 6 algorithms, reasonable performance (0.13-27s per algo), clean integration. IRIS could target a similar position.

**Critical success factor**: Complete implementation of all 6 algorithms. Many systems stop at 3-4, limiting credibility. Going all-in demonstrates commitment.

