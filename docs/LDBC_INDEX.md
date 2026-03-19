# LDBC Graphalytics Research: Complete Index

**Research Date**: March 18, 2026  
**Status**: Comprehensive benchmark analysis complete  
**Sources**: Official LDBC framework, ArcadeDB reference implementation (2024), VLDB papers, arXiv specifications

---

## 📚 Research Documents

### 1. **LDBC_GRAPHALYTICS_RESEARCH.md** (18 KB, 456 lines)
Complete technical reference covering:
- Benchmark architecture & framework structure
- Deep dive on all 6 kernel algorithms (BFS, PageRank, WCC, CDLP, LCC, SSSP)
- Full dataset catalog (20+ datasets, XS to XL scales)
- Scoring & ranking system explained
- Existing platform drivers (GraphBLAS, Umbra, Neo4j, ArcadeDB)
- CSR (Compressed Sparse Row) data structure details
- Step-by-step guide to building a competitive driver
- Performance characteristics with real benchmarks

**Use this for**: Understanding Graphalytics completely, designing implementation

---

### 2. **LDBC_ALGORITHMS_QUICKREF.md** (9.8 KB, 319 lines)
Practical algorithm implementation guide:
- Algorithm complexity matrix (6 algorithms, all key metrics)
- Detailed pseudocode for each algorithm
- Key optimizations and data structures
- Determinism checklist
- Common implementation patterns (CSR construction, parallel iteration)
- Performance targets (ArcadeDB vs Neo4j benchmarks)

**Use this for**: Implementation, tuning, algorithmic decisions

---

### 3. **LDBC_COMPETITIVE_ANALYSIS.md** (9.0 KB, 266 lines)
Competitive landscape & viability assessment:
- 2024 performance comparisons (ArcadeDB, Neo4j, Kuzu, DuckPGQ, Memgraph, HugeGraph)
- Why ArcadeDB wins (and where others fail)
- Minimum viability checklist for IRIS driver
- Performance targets to beat Neo4j GDS
- Leaderboard requirements & current standings
- Go/No-Go decision framework for IRIS

**Use this for**: Strategic decision-making, competitive positioning

---

## 📊 Key Findings

### The Six Algorithms

| Algorithm | Complexity | Best For | Data Structure | Speed (ArcadeDB) |
|-----------|-----------|----------|----------------|------------------|
| **BFS** | O(V+E) | Traversal | CSR + frontier queue | 0.13s |
| **PageRank** | O(k·E) | Ranking | Backward CSR | 0.48s |
| **WCC** | O(d·E) | Components | Bidirectional CSR | 0.30s |
| **CDLP** | O(k·E·log d) | Communities | CSR + sorting | 3.67s |
| **LCC** | O(E·√E) | Clustering | Sorted CSR | **27.41s** (bottleneck) |
| **SSSP** | O((V+E)·log V) | Shortest paths | CSR + binary heap | 3.53s |

**Total time (all 6)**: ~35.5 seconds on 633K vertex, 34M edge graph

---

### Datasets

**Benchmark includes**:
- 20+ datasets from tiny (9 vertices) to massive (434M vertices, 1B edges)
- Both synthetic (Facebook-like, Graph500 random) and real (citations, social networks)
- Total size: 350 GB compressed, 1.5 TB uncompressed
- All with pre-computed reference outputs for validation

**Test dataset used in research**:
- `datagen-7_5-fb`: 633K vertices, 34M edges (Small scale)
- `datagen-8_4-fb`: 3.8M vertices, 269M edges (Medium scale)

---

### Competitive Performance (datagen-7_5-fb)

| System | BFS | PR | WCC | CDLP | LCC | SSSP | Total |
|--------|-----|----|----|------|-----|------|-------|
| **ArcadeDB** ⭐ | 0.13s | 0.48s | 0.30s | 3.67s | 27.41s | 3.53s | **35.5s** |
| Neo4j GDS | 1.91s | 11.15s | 0.75s | 6.43s | 45.78s | N/A | ~66s* |
| Kuzu | 0.86s | 4.30s | 0.43s | N/A | N/A | N/A | ~6s* |
| DuckPGQ | 2,754s! | 6.14s | 13.93s | N/A | 38.59s | N/A | **UNUSABLE** |
| Memgraph | 11.72s | 16.90s | **CRASH** | N/A | N/A | N/A | **CRASH** |
| HugeGraph | 0.54s | 4.01s | 6.71s | 62.70s | 272.04s | N/A | ~346s* |

*Only partial algorithm coverage

**Key insight**: **ArcadeDB is the only system that implements all 6 algorithms successfully**

---

## 🎯 Critical Success Factors for IRIS Driver

### Must-Haves
- ✅ All 6 algorithms (not 3-4)
- ✅ CSR or dense adjacency structure
- ✅ Deterministic outputs (exactly matching reference)
- ✅ < 100s total on datagen-8_4-fb (3.8M vertices)

### To Beat Neo4j GDS
- BFS < 3s, PageRank < 20s, WCC < 1.5s, CDLP < 10s, LCC < 60s, SSSP < 10s
- Total: < 80 seconds

### To Match ArcadeDB
- BFS < 0.5s, WCC < 0.5s, PR < 1s, CDLP < 5s, LCC < 30s, SSSP < 5s
- Total: < 50 seconds (very ambitious for Java)

---

## 🏗️ Architecture Overview

### LDBC Framework
```
ldbc_graphalytics/
├── graphalytics-core/        # Test harness, metrics
├── graphalytics-validation/  # Reference outputs, validation
├── graphalytics-plugins-granula/  # Deep performance metrics
└── [Platform drivers]
    ├── ldbc_graphalytics_platforms_graphblas/
    ├── ldbc_graphalytics_platforms_umbra/
    ├── atlarge-research/graphalytics-platforms-neo4j/
    └── ArcadeData/ldbc_graphalytics_platforms_arcadedb/
```

### Typical Driver Implementation
```java
public class IrisPlatformDriver extends PlatformBenchmark {
  void loadGraph(String path)  // Import edges into CSR
  void breadthFirstSearch(long source, String output)
  void pageRank(int iterations, String output)
  void weaklyConnectedComponents(String output)
  void communityDetectionLP(int iterations, String output)
  void localClusteringCoefficient(String output)
  void singleSourceShortestPath(long source, String output)
}
```

---

## 📋 Research Checklist

### Topics Covered
- [x] Benchmark architecture & framework
- [x] Six kernel algorithms (detailed specifications)
- [x] Dataset catalog & sizes
- [x] Scoring & ranking system
- [x] Existing platform drivers (6 systems analyzed)
- [x] CSR data structure design
- [x] Building a competitive driver (step-by-step)
- [x] Performance benchmarks (2024 data)
- [x] Competitive landscape analysis
- [x] IRIS-specific considerations
- [x] Go/No-Go decision framework
- [x] Leaderboard requirements

### Not Covered (Out of Scope)
- Distributed/cluster implementations
- Advanced graph query languages (Cypher, SQL)
- Streaming/real-time variants
- Other LDBC benchmarks (SNB, BI, FINBENCH)

---

## 🔗 Essential Links

### Official LDBC Graphalytics
- **Website**: https://ldbcouncil.org/benchmarks/graphalytics/
- **Framework**: https://github.com/ldbc/ldbc_graphalytics
- **Specification v1.0.5**: https://arxiv.org/pdf/2011.15028v6.pdf
- **Datasets**: https://ldbcouncil.org/benchmarks/graphalytics/datasets/
- **Docs (LaTeX source)**: https://github.com/ldbc/ldbc_graphalytics_docs

### Reference Implementations
- **ArcadeDB (2024)**: https://github.com/ArcadeData/ldbc_graphalytics_platforms_arcadedb
- **GraphBLAS**: https://github.com/ldbc/ldbc_graphalytics_platforms_graphblas
- **Umbra**: https://github.com/ldbc/ldbc_graphalytics_platforms_umbra
- **Neo4j (community)**: https://github.com/atlarge-research/graphalytics-platforms-neo4j

### Academic Papers
- **VLDB 2015** (original): https://www.vldb.org/pvldb/vol9/p1317-iosup.pdf
- **Graphalytics Overview** (2016): https://homepages.cwi.nl/~boncz/snb-challenge/graphalytics-grades.pdf

---

## 💡 Key Insights for IRIS

### Competitive Advantages
1. **Vector search integration**: Can optimize LCC (bottleneck) using SIMD
2. **ACID guarantees**: Built-in determinism testing
3. **Multi-model**: RDF native, graph operations first-class
4. **Full-stack**: Can optimize from storage through query execution

### Main Challenges
1. **IRIS is OLTP-focused**: Graph algorithms need OLAP optimization
2. **LCC bottleneck**: Triangle counting dominates runtime (70% of total)
3. **Performance gap**: ArcadeDB already at 35s; beating it requires 2-5x speedup
4. **Resource constraint**: Smaller team than Neo4j/ArcadeDB

### Realistic Strategy
1. Start with embedded mode (not distributed)
2. Build CSR as optimized native structure
3. Target 80-150s runtime (beat DuckPGQ, approach Neo4j GDS)
4. Complete all 6 algorithms (critical for credibility)
5. Publish on leaderboard (position IRIS as graph-capable)

---

## 📞 For More Information

**LDBC Contacts** (for partnerships/submissions):
- Gabor Szarnyas (Coordinator): gabor.szarnyas@ldbcouncil.org
- David Puroja (Competitions): david.puroja@ldbcouncil.org

**Research Compiled By**: Librarian Agent (Claude)  
**Data Currency**: March 2026 (based on 2024-2025 benchmark results)

---

## 📖 How to Use These Documents

1. **Start here** (LDBC_INDEX.md) - You're reading it
2. **For understanding** → Read LDBC_GRAPHALYTICS_RESEARCH.md (full context)
3. **For implementation** → Read LDBC_ALGORITHMS_QUICKREF.md (code & pseudocode)
4. **For strategy** → Read LDBC_COMPETITIVE_ANALYSIS.md (decisions & positioning)

**Estimated reading time**:
- Index: 10 minutes
- Research: 45 minutes
- Quick ref: 30 minutes
- Competitive: 20 minutes
- **Total**: ~2 hours for complete understanding

---

**Last Updated**: March 18, 2026  
**Status**: Research Complete ✅

