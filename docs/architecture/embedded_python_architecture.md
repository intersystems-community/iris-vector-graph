# IRIS Embedded Python Architecture - Critical Design Constraints

**Date**: 2025-10-02
**Context**: NodePK PageRank optimization using embedded Python
**Reference**: ../rag-templates HybridGraphRAG implementation
**Setup Guide**: https://github.com/intersystems-community/iris-embedded-python-template

---

## Setup Requirements

For IRIS embedded Python to work correctly, you must:

1. **Use InterSystems IRIS with Python support** (2024.1+ or Container Python Framework)
2. **Configure Python environment in IRIS**:
   - See [iris-embedded-python-template](https://github.com/intersystems-community/iris-embedded-python-template) for CPF (Container Python Framework) setup
   - Or use standard IRIS Python configuration via Management Portal
3. **Compile ObjectScript classes with embedded Python** (`Language=python` methods)
4. **Ensure Python packages available to IRIS runtime** (not just client environment)

**Important**: The Python environment used by IRIS embedded Python is **separate** from your client Python environment. Packages must be installed in the IRIS Python environment.

See [`../iris-pgwire`](../../../iris-pgwire) for recent updates on embedded Python setup patterns.

---

## Core Architectural Constraint: HNSW Index Requires SQL-First

### The Fundamental Rule

**HNSW vector indexes REQUIRE SQL insertion and SQL-based queries.**

You **CANNOT** bypass SQL for vector similarity operations. The HNSW index is tightly coupled to IRIS SQL query planner.

### Why This Matters for PageRank

When designing PageRank and graph analytics:

1. **Pure graph queries** (no vector similarity): ✅ Can use embedded Python + globals
2. **Hybrid queries** (graph + vector): ❌ MUST use SQL for vector operations

---

## Correct Usage Patterns

### Pattern 1: Pure Graph Analytics (Embedded Python OK)

**Use Case**: PageRank, Connected Components, BFS, Shortest Path

**Implementation**: Embedded Python with optional global access

```objectscript
/// Pure graph PageRank - can use embedded Python
ClassMethod ComputePageRank(...) As %DynamicArray [ Language = python ]
{
    import iris.sql as sql

    # Get nodes and edges from SQL (or globals)
    cursor = sql.exec("SELECT s, o_id FROM rdf_edges")

    # PageRank computation in Python (no vector operations)
    for iteration in range(max_iterations):
        # ... pure graph algorithm ...

    return results
}
```

**Why this works**: No HNSW index involved, pure graph structure traversal.

---

### Pattern 2: Hybrid Graph + Vector (SQL REQUIRED)

**Use Case**: Vector similarity search → graph expansion → PageRank on subgraph

**Implementation**: SQL for vector search, then embedded Python for graph operations

```objectscript
/// Hybrid: Vector search + PageRank on subgraph
ClassMethod VectorGuidedPageRank(
    queryVector As %String,
    k As %Integer = 20
) As %DynamicArray [ Language = python ]
{
    import iris.sql as sql

    # Step 1: Vector k-NN search - MUST use SQL for HNSW index
    cursor = sql.exec("""
        SELECT TOP ? e.id, VECTOR_DOT_PRODUCT(e.emb, TO_VECTOR(?)) as similarity
        FROM kg_NodeEmbeddings e
        ORDER BY similarity DESC
    """, k, query_vector)

    seed_nodes = [row[0] for row in cursor]

    # Step 2: Expand to graph neighborhood via SQL
    cursor = sql.exec("""
        SELECT DISTINCT e.o_id
        FROM rdf_edges e
        WHERE e.s IN (?)
    """, ','.join(seed_nodes))

    subgraph_nodes = seed_nodes + [row[0] for row in cursor]

    # Step 3: PageRank on subgraph - can use embedded Python
    # (Now we're back to pure graph operations)
    cursor = sql.exec("""
        SELECT s, o_id FROM rdf_edges
        WHERE s IN (?) AND o_id IN (?)
    """, ','.join(subgraph_nodes), ','.join(subgraph_nodes))

    # Build adjacency and run PageRank (pure graph)
    adjacency = {}
    for src, dst in cursor:
        adjacency.setdefault(src, []).append(dst)

    # ... PageRank algorithm on subgraph ...

    return results
}
```

**Critical**: Vector search MUST happen in SQL. Graph operations CAN use embedded Python.

---

### Pattern 3: Re-ranking Results with Vector Similarity (SQL REQUIRED)

**Use Case**: PageRank → fetch top-K → re-rank by vector similarity

```objectscript
/// PageRank + Vector Re-ranking
ClassMethod PageRankWithVectorReranking(...) As %DynamicArray [ Language = python ]
{
    import iris.sql as sql

    # Step 1: Pure graph PageRank (embedded Python OK)
    # ... PageRank computation ...
    top_k_nodes = [...]  # Top-K by PageRank

    # Step 2: Re-rank by vector similarity - MUST use SQL
    cursor = sql.exec("""
        SELECT e.id, VECTOR_DOT_PRODUCT(e.emb, TO_VECTOR(?)) as similarity
        FROM kg_NodeEmbeddings e
        WHERE e.id IN (?)
        ORDER BY similarity DESC
    """, query_vector, ','.join(top_k_nodes))

    return cursor.fetchall()
}
```

---

## When to Use Embedded Python vs SQL

### Use Embedded Python When:

✅ **Pure graph traversal** (BFS, DFS, shortest path)
✅ **Iterative graph algorithms** (PageRank, Connected Components, Label Propagation)
✅ **Graph aggregations** (degree centrality, clustering coefficient)
✅ **Custom graph algorithms** that don't involve vectors
✅ **Building secondary indexes on graph structure** (via globals)

### MUST Use SQL When:

❌ **Vector similarity search** (k-NN, HNSW index queries)
❌ **Any VECTOR_DOT_PRODUCT operations**
❌ **Inserting vector embeddings** (HNSW index maintenance)
❌ **Hybrid vector + graph queries** (vector search portion)
❌ **Vector-based filtering or scoring**

---

## Architecture Diagram: Data Flow

```
┌─────────────────────────────────────────────────────────────┐
│  Application Layer                                          │
└─────────────────────────────────────────────────────────────┘
                          ↓
        ┌─────────────────┴─────────────────┐
        ↓                                   ↓
┌───────────────────┐              ┌───────────────────┐
│  SQL Interface    │              │ Embedded Python   │
│  (REQUIRED for    │              │ (Optional for     │
│   vector ops)     │              │  graph ops)       │
└───────────────────┘              └───────────────────┘
        ↓                                   ↓
        ↓                          ┌────────┴────────┐
        ↓                          ↓                 ↓
┌───────────────────┐    ┌─────────────────┐ ┌──────────────┐
│  HNSW Index       │    │  iris.sql.exec()│ │ iris.gref()  │
│  (kg_NodeEmbeddings)   │  (SQL queries)  │ │ (globals)    │
└───────────────────┘    └─────────────────┘ └──────────────┘
        ↓                          ↓                 ↓
┌─────────────────────────────────────────────────────────────┐
│  IRIS Globals Storage Layer                                 │
│  - ^kg.NodeEmbeddings (vectors - accessed via SQL HNSW)   │
│  - ^rdf.edges (edges - can access via SQL or globals)     │
│  - ^nodes (nodes - can access via SQL or globals)         │
└─────────────────────────────────────────────────────────────┘
```

---

## Reference Implementation: ../rag-templates HybridGraphRAG

Your `../rag-templates` implementation follows this pattern correctly:

1. **Vector insertion**: Always via SQL to maintain HNSW index
2. **Vector search**: Always via SQL to leverage HNSW index
3. **Graph operations**: Can use embedded Python or globals
4. **Hybrid workflows**: SQL for vector → embedded Python for graph

---

## PageRank Implementation Decision Matrix

| Use Case | Vector Involved? | Implementation | Rationale |
|----------|-----------------|----------------|-----------|
| **Pure PageRank on full graph** | ❌ No | Embedded Python | No HNSW needed, pure graph |
| **PageRank on semantically similar subgraph** | ✅ Yes (seed selection) | SQL (vector) → Embedded Python (graph) | Vector search needs HNSW |
| **PageRank with vector-based edge weights** | ✅ Yes (edge scoring) | SQL only | Edge weights from VECTOR_DOT_PRODUCT |
| **Re-rank PageRank results by similarity** | ✅ Yes (re-ranking) | Embedded Python (PageRank) → SQL (vector) | Final re-rank needs HNSW |

---

## Critical Gotchas

### ❌ WRONG: Trying to access HNSW index via globals

```objectscript
// This will NOT work - HNSW index is SQL-coupled
ClassMethod WrongApproach() [ Language = python ]
{
    import iris

    # WRONG: Cannot access HNSW-indexed vectors via globals
    embeddings = iris.gref('^kg.NodeEmbeddings')

    # WRONG: Cannot compute k-NN without SQL query planner
    for node_id in embeddings:
        similarity = dot_product(query_vector, embeddings[node_id])

    # This will be SLOW and bypass HNSW optimization!
}
```

### ✅ CORRECT: Use SQL for HNSW, embedded Python for graph

```objectscript
// Correct: SQL for vectors, embedded Python for graph
ClassMethod CorrectApproach() [ Language = python ]
{
    import iris.sql as sql

    # CORRECT: Use SQL for HNSW-indexed vector search
    cursor = sql.exec("""
        SELECT TOP 20 e.id, VECTOR_DOT_PRODUCT(e.emb, TO_VECTOR(?)) as similarity
        FROM kg_NodeEmbeddings e
        ORDER BY similarity DESC
    """, query_vector)

    seed_nodes = [row[0] for row in cursor]

    # CORRECT: Now use embedded Python for graph operations
    cursor = sql.exec("SELECT s, o_id FROM rdf_edges WHERE s IN (?)", seed_nodes)
    adjacency = {}
    for src, dst in cursor:
        adjacency.setdefault(src, []).append(dst)

    # PageRank on subgraph (pure graph, no vectors)
}
```

---

## Performance Implications

### Embedded Python Benefits (Pure Graph)

- **10-50x faster** than client-side Python for pure graph operations
- Direct global access possible for graph structure
- In-process execution eliminates network overhead

### SQL Requirements (Vector Operations)

- **100x faster** with HNSW vs linear scan (proven in benchmarks: 5800ms → 1.7ms)
- HNSW index ONLY accessible via SQL query planner
- Any vector similarity MUST go through SQL

### Hybrid Approach (Best of Both)

- Use SQL for vector search (HNSW benefit)
- Use embedded Python for graph algorithms (speed benefit)
- Combine results in embedded Python (in-process)

**Expected Performance**:
- Vector search: 1-10ms (HNSW-optimized SQL)
- Graph expansion: 0.1-1ms (embedded Python)
- PageRank on subgraph: 10-100ms (embedded Python, 1K-10K nodes)
- **Total**: 11-111ms for hybrid vector-guided PageRank

---

## Recommendations for NodePK Feature

### For Pure Graph Analytics (Current PageRank Implementation)

✅ **Use embedded Python** - no vectors involved, pure graph structure

### For Future Hybrid Workflows

1. Document that vector operations MUST use SQL
2. Show correct pattern: SQL → Embedded Python → SQL
3. Warn against trying to access HNSW index via globals
4. Provide hybrid examples (vector-guided graph algorithms)

### Documentation Updates Needed

1. Update `graph_analytics_roadmap.md` to clarify when embedded Python applies
2. Add this architecture doc to explain SQL/embedded Python boundaries
3. Update PageRank examples to show both pure and hybrid cases
4. Reference ../rag-templates as canonical hybrid implementation

---

---

## Graceful Degradation: The Fallback Pattern

### Philosophy

The `iris_vector_graph` package is designed to work in **two deployment scenarios**:

1. **Production IRIS** - ObjectScript classes loaded, HNSW tables created, SQL functions available
2. **Development/Testing IRIS** - Vanilla IRIS with just basic tables, no optimization setup

To support both scenarios, performance-critical operations implement a **fallback pattern**:

```
Try optimized path (IRIS-native) → Catch failure → Fall back to pure Python
```

### Fallback Matrix

| Method | Primary Path | Fallback Path | When Fallback Triggers |
|--------|-------------|---------------|------------------------|
| `kg_KNN_VEC` | HNSW index (`kg_NodeEmbeddings` table with `VECTOR` type) | Python CSV parsing (`kg_NodeEmbeddings_old` table with CSV strings) | `kg_NodeEmbeddings` table doesn't exist or has wrong type |
| `kg_PERSONALIZED_PAGERANK` | SQL function → ObjectScript embedded Python | Pure Python in `IRISGraphEngine` | `kg_PPR` SQL function doesn't exist |
| `kg_TXT` | SQL with JSON_TABLE | ❌ No fallback | Core IRIS SQL feature, always available |
| `kg_NEIGHBORHOOD_EXPANSION` | SQL with JSON_TABLE | ❌ No fallback | Core IRIS SQL feature, always available |
| `kg_RRF_FUSE` | Inherits from `kg_KNN_VEC` | Inherits fallback | Same as `kg_KNN_VEC` |
| `kg_VECTOR_GRAPH_SEARCH` | Inherits from `kg_KNN_VEC` | Inherits fallback | Same as `kg_KNN_VEC` |

### Design Principle

**Fallbacks exist only for optional performance enhancements, not core SQL features.**

- HNSW index is optional (requires special table and index setup)
- ObjectScript classes are optional (requires loading `.cls` files into IRIS)
- JSON_TABLE is a core IRIS SQL feature (always available, no fallback needed)

### Performance Implications

| Method | Optimized Path | Fallback Path | Slowdown |
|--------|---------------|---------------|----------|
| `kg_KNN_VEC` (10K vectors) | ~2ms (HNSW) | ~5800ms (Python scan) | ~2900x |
| `kg_PERSONALIZED_PAGERANK` (1K nodes) | ~10ms (embedded Python) | ~25ms (pure Python) | ~2.5x |
| `kg_PERSONALIZED_PAGERANK` (10K nodes) | ~50ms (embedded Python) | ~500ms (pure Python) | ~10x |

### Implementation Pattern

```python
# Class-level cache to avoid repeated failed attempts
class IRISGraphEngine:
    _optimization_available = None  # None = unknown, True/False = cached result

    def optimized_operation(self, ...):
        # Skip if we already know optimization is unavailable
        if self._optimization_available is False:
            return self._fallback_implementation(...)

        try:
            result = self._try_optimized_path(...)
            self._optimization_available = True  # Cache success
            return result
        except Exception as e:
            if "not found" in str(e).lower() or "does not exist" in str(e).lower():
                self._optimization_available = False  # Cache failure
                logger.info("Optimization unavailable, using fallback")
            return self._fallback_implementation(...)
```

**Key Features:**
1. **Try optimized first** - Get full performance when available
2. **Cache failure** - Don't retry failed SQL function calls on every request
3. **Log degradation** - Users know when fallback is active
4. **Same API contract** - Callers don't need to know which path was used

### Enabling the Optimized Path

To enable IRIS embedded Python for PageRank (10-50x speedup):

```bash
# 1. Load ObjectScript class into IRIS
IRIS> Do $system.OBJ.Load("/path/to/iris_src/src/PageRankEmbedded.cls", "ck")

# 2. Create SQL function (run in IRIS SQL tool)
\i sql/operators.sql
```

To enable HNSW vector search (2900x speedup):

```bash
# 1. Create optimized embeddings table with VECTOR type
\i sql/schema.sql  # Creates kg_NodeEmbeddings with HNSW index

# 2. Migrate embeddings from CSV to VECTOR format
# (Use migration script or re-ingest data)
```

### Testing Fallback Behavior

```python
# Force fallback for testing
from iris_vector_graph import IRISGraphEngine

# Reset cache to test fallback detection
IRISGraphEngine.reset_sql_function_cache()

# Or force fallback mode
IRISGraphEngine._ppr_sql_function_available = False
```

---

## Summary

**Golden Rule**: If vectors are involved, SQL is REQUIRED for that portion. Embedded Python is optional for pure graph operations.

**Fallback Rule**: Performance optimizations (HNSW, embedded Python) gracefully degrade to pure Python when not available. Core SQL features (JSON_TABLE) have no fallback because they're always available.

**NodePK PageRank**: Pure graph algorithm → embedded Python is perfect (10-50x speedup), with pure Python fallback

**Future Hybrid**: Vector search → SQL required → Graph expansion → embedded Python optional → Re-ranking → SQL required

This architecture ensures we leverage HNSW performance while maximizing embedded Python benefits for graph operations, with graceful degradation for simpler deployments.
