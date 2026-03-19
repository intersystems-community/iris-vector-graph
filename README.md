# IRIS Vector Graph

**The ultimate Graph + Vector + Text Retrieval Engine for InterSystems IRIS.**

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![InterSystems IRIS](https://img.shields.io/badge/IRIS-2025.1+-purple.svg)](https://www.intersystems.com/products/intersystems-iris/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://github.com/intersystems-community/iris-vector-graph/blob/main/LICENSE)

IRIS Vector Graph is a general-purpose graph utility built on InterSystems IRIS that supports and demonstrates knowledge graph construction and query techniques. It combines **graph traversal**, **HNSW vector similarity**, and **lexical search** in a single, unified database.

---

## Why IRIS Vector Graph?

- **Multi-Query Power**: Query your graph via **SQL**, **openCypher (v1.3 with DML)**, or **GraphQL** — all on the same data.
- **Transactional Engine**: Beyond retrieval — support for `CREATE`, `DELETE`, and `MERGE` operations.
- **Blazing Fast Vectors**: Native HNSW indexing delivering **~1.7ms** search latency (vs 5.8s standard).
- **Zero-Dependency Integration**: Built with IRIS Embedded Python — no external vector DBs or graph engines required.
- **Production-Ready**: The engine behind [iris-vector-rag](https://github.com/intersystems-community/iris-vector-rag) for advanced RAG pipelines.

---

## Installation

```bash
pip install iris-vector-graph
```

Note: Requires **InterSystems IRIS 2025.1+** with the `irispython` runtime enabled.

## Quick Start

```bash
# 1. Clone & Sync
git clone https://github.com/intersystems-community/iris-vector-graph.git && cd iris-vector-graph
uv sync

# 2. Spin up IRIS
docker-compose up -d

# 3. Start API
uvicorn api.main:app --reload
```

Visit:
- **GraphQL Playground**: [http://localhost:8000/graphql](http://localhost:8000/graphql)
- **API Docs**: [http://localhost:8000/docs](http://localhost:8000/docs)

---

## Unified Query Engines

### openCypher (Advanced RD Parser)
IRIS Vector Graph features a custom recursive-descent Cypher parser supporting multi-stage queries and transactional updates:

```cypher
// Complex fraud analysis with WITH and Aggregations
MATCH (a:Account)-[r]->(t:Transaction)
WITH a, count(t) AS txn_count
WHERE txn_count > 5
MATCH (a)-[:OWNED_BY]->(p:Person)
RETURN p.name, txn_count
```

**Supported Clauses:** `MATCH`, `OPTIONAL MATCH`, `WITH`, `WHERE`, `RETURN`, `UNWIND`, `CREATE`, `DELETE`, `DETACH DELETE`, `MERGE`, `SET`, `REMOVE`.

### GraphQL
```graphql
query {
  protein(id: "PROTEIN:TP53") {
    name
    interactsWith(first: 5) { id name }
    similar(limit: 3) { protein { name } similarity }
  }
}
```

### SQL (Hybrid Search)
```sql
SELECT TOP 10 id, 
       kg_RRF_FUSE(id, vector, 'cancer suppressor') as score
FROM nodes
ORDER BY score DESC
```

### Auto-Generating GraphQL
IRIS Vector Graph includes a generic, zero-config GraphQL layer that automatically builds a schema by introspecting your graph data.

```python
from iris_vector_graph import IRISGraphEngine, gql

engine = IRISGraphEngine(conn)
# Starts a FastAPI/Strawberry server with sampled properties and bi-directional traversal
gql.serve(engine, port=8000)
```

**Features:**
- **Dynamic Types**: Auto-generates types like `Protein` or `Account` based on discovered labels.
- **Top-level Properties**: Maps sampled properties to schema fields with keyword collision handling.
- **Bi-directional**: Follow both `incoming` and `outgoing` relationships.
- **Connection Pooling**: Safely handles concurrency within IRIS Community connection limits.

---

## Scaling & Performance

The integration of a native **HNSW (Hierarchical Navigable Small World)** functional index directly into InterSystems IRIS provides massive scaling benefits for hybrid graph-vector workloads. 

By keeping the vector index in-process with the graph data, we achieve **subsecond multi-modal queries** that would otherwise require complex application-side joins across multiple databases.

### Performance Benchmarks (2026 Refactor)
- **High-Speed Traversal**: **~1.84M TEPS** (Traversed Edges Per Second).
- **Sub-millisecond Latency**: 2-hop BFS on 10k nodes in **<40ms**.
- **RDF 1.2 Support**: Native support for **Quoted Triples** (Metadata on edges) via subject-referenced properties.
- **Query Signatures**: O(1) hop-rejection using ASQ-inspired Master Label Sets.

### Why fast vector search matters for graphs
Consider a "Find-and-Follow" query common in fraud detection:
1.  **Find** the top 10 accounts most semantically similar to a known fraudulent pattern (Vector Search).
2.  **Follow** all outbound transactions from those 10 accounts to identify the next layer of the money laundering ring (Graph Hop).

In a standard database without HNSW, the first step (vector search) can take several seconds as the dataset grows, blocking the subsequent graph traversals. With `iris-vector-graph`, the vector lookup is reduced to **~1.7ms**, enabling the entire hybrid traversal to complete in a fraction of a second.

---

## Interactive Demos

Experience the power of IRIS Vector Graph through our interactive demo applications.

### Biomedical Research Demo
Explore protein-protein interaction networks with vector similarity and D3.js visualization.

### Fraud Detection Demo
Real-time fraud scoring with transaction networks, Cypher-based pattern matching, and bitemporal audit trails.

To run the CLI demos:
```bash
export PYTHONPATH=$PYTHONPATH:.
# Cypher-powered fraud detection
python3 examples/demo_fraud_detection.py

# SQL-powered "drop down" example
python3 examples/demo_fraud_detection_sql.py
```

To run the Web Visualization demos:
```bash
# Start the demo server
uv run uvicorn src.iris_demo_server.app:app --port 8200 --host 0.0.0.0
```
Visit [http://localhost:8200](http://localhost:8200) to begin.

---

## iris-vector-rag Integration

IRIS Vector Graph is the core engine powering [iris-vector-rag](https://github.com/intersystems-community/iris-vector-rag). You can use it in your RAG pipelines like this:

```python
from iris_vector_rag import create_pipeline

# Create a GraphRAG pipeline powered by this engine
pipeline = create_pipeline('graphrag')

# Combined vector + text + graph retrieval
result = pipeline.query(
    "What are the latest cancer treatment approaches?",
    top_k=5
)
```

---

## Documentation

- [Detailed Architecture](https://github.com/intersystems-community/iris-vector-graph/blob/main/docs/architecture/ARCHITECTURE.md)
- [Biomedical Domain Examples](https://github.com/intersystems-community/iris-vector-graph/tree/main/examples/domains/biomedical/)
- [Full Test Suite](https://github.com/intersystems-community/iris-vector-graph/tree/main/tests/)
- [iris-vector-rag Integration](https://github.com/intersystems-community/iris-vector-rag)
- [Verbose README](https://github.com/intersystems-community/iris-vector-graph/blob/main/docs/README_VERBOSE.md) (Legacy)

---

## Changelog

### v1.13.0 (2026-03-18)
- **Cypher `ivg.neighbors`**: New procedure — `CALL ivg.neighbors($sources, 'MENTIONS', 'out') YIELD neighbor`. Supports `out`/`in`/`both` direction with optional predicate filter. Generates efficient `IN (?,?,...)` CTE.
- **Cypher `ivg.ppr`**: New procedure — `CALL ivg.ppr($seeds, 0.85, 20) YIELD node, score`. Calls `Graph_KG.kg_PPR` server-side, wraps JSON result via `JSON_TABLE` for tabular output.
- **Cypher `ivg.vector.search` Mode 3**: String query without `embedding_config` is now treated as a **node ID** — uses HNSW subquery activation (`SELECT e2.emb WHERE e2.id = ?`) with self-exclusion. String WITH `embedding_config` remains Mode 2 (EMBEDDING function).

### v1.12.0 (2026-03-18)
- **`kg_KNN_VEC` accepts node ID**: `ops.kg_KNN_VEC("PMID:630", k=10)` detects non-JSON input and uses server-side subquery `VECTOR_COSINE(emb, (SELECT emb WHERE id = ?))` — lets IRIS constant-fold and activate HNSW index. ~50ms vs ~400ms for literal-vector-through-bridge.

### v1.11.0 (2026-03-18)
- **`kg_NEIGHBORS`**: New 1-hop neighborhood primitive on `IRISGraphOperators`. Supports `out`/`in`/`both` direction, optional predicate filter, chunked `IN` queries for >500 source IDs. Follows NetworkX/APOC naming conventions.
- **`kg_MENTIONS`**: Convenience alias — `ops.kg_MENTIONS(article_ids)` = `ops.kg_NEIGHBORS(article_ids, predicate="MENTIONS")`.
- **Documentation overhaul**: v1.10.2 changelog, embedded Python bridge constraint matrix, `IRISGraphOperators` API reference in PYTHON_SDK, call-context architecture docs.

### v1.10.2 (2026-03-18)
- **Pure ObjectScript PageRank**: Rewrote `Graph.KG.PageRank.RunJson` as pure ObjectScript — eliminates all `iris.gref`/`iris.cls` dependencies. Works from every call context: SQL stored procedure, native API bridge, and embedded Python. Previous `Language = python` implementation only worked inside IRIS embedded Python, failing through the external `classMethodValue()` bridge.
- **850x Vector Search Fix**: `kg_KNN_VEC` HNSW path now queries `Graph_KG.kg_NodeEmbeddings` (canonical table) with `TO_VECTOR(?, DOUBLE)`. Previously queried non-existent `kg_NodeEmbeddings_optimized` (FLOAT) causing -259 datatype mismatch → 42s brute-force fallback on 143K vectors vs 50ms with HNSW.
- **Personalized PageRank API**: New `ops.kg_PPR(seed_entities, damping, max_iterations)` method on `IRISGraphOperators`. Primary path calls `Graph.KG.PageRank.RunJson` via native API; falls back to SQL function; returns `List[Tuple[str, float]]` sorted by score.
- **kg_PPR SQL Function Auto-Install**: `GraphSchema.get_procedures_sql_list()` now includes `kg_PPR` calling `Graph.KG.PageRank.RunJson` (pure ObjectScript). Previously referenced non-existent `PageRankEmbedded.ComputePageRank`.
- **^KG Subscript Fix**: `kg_GRAPH_WALK` now accesses `^KG("out", entity, predicate)` — previously used `^KG(entity, predicate)` (missing "out" prefix), causing the fast `^KG` global path to always return empty and fall back to SQL.
- **GraphOperators.cls Schema Fix**: All SQL queries in `iris.vector.graph.GraphOperators` changed from `SQLUSER.*` to `Graph_KG.*` schema references.
- **Comprehensive E2E Tests**: 10 unit tests + 11 e2e tests against live IRIS verify all operator wiring fixes. Star-graph PPR topology test, HNSW no-fallback test, vector-graph search expansion test, idempotent schema init test.

### v1.9.0 (2026-02-28)
- **ObjectScript Fast Paths**: Deployed `.cls` layer for PPR and BFS graph traversal — native IRIS ObjectScript execution for maximum throughput
- **Reliable Test Infrastructure**: Eliminated `MockContainer` — `iris_test_container` now uses `IRISContainer.attach()` to connect to existing containers; session fixture blocks until `test/test` credentials are verified before yielding
- **Correct Vector Dimensions**: Schema setup now drops and recreates `kg_NodeEmbeddings` (768-dim) to prevent silent 384→768 mismatch failures
- **iris-devtester 1.14.0**: All container references use `container_name="iris-vector-graph-main"` for deterministic multi-container environments
- **Auto-Generating GraphQL**: Connection pooling, `DYNAMIC_TYPES.clear()` on rebuild, dot-notation column name fixes
- **FastAPI `/health` endpoint**: Always available regardless of engine state; `api_client` test fixture injects live engine
- **Test isolation**: Numeric-comparison Cypher tests scoped to prefix; atomic fixture cleanup prevents FK-order rollback races

### v1.8.0 (2026-01-15)
- **NodePK Feature**: Primary-key node table (`Graph_KG.nodes`) with FK constraints on all edge/label/prop tables
- **Migration Utilities**: `discover_nodes`, `bulk_insert_nodes`, `validate_migration`, `execute_migration` in `scripts/migrations/`
- **Cypher Vector Search**: Native `ivg.vector.search` procedure for Cypher-embedded HNSW queries
- **Stored Procedure Install**: `kg_KNN_VEC` server-side path with Python fallback

### v1.7.0 (2026-01-01)
- **Schema Stored Procedures**: Initialized via `iris_vector_graph.schema.initialize_schema()`
- **GraphQL Auto-Generation**: Zero-config schema introspection from live graph labels

### v1.6.0 (2025-01-31)
- **High-Performance Batch API**: New `get_nodes(node_ids)` reduces database round-trips by 100x+ for large result sets
- **Advanced Substring Search**: Integrated IRIS `iFind` indexing for sub-20ms `CONTAINS` queries on 10,000+ records
- **GraphQL Acceleration**: Implemented `GenericNodeLoader` to eliminate N+1 query patterns in GQL traversals
- **Transactional Batching**: Optimized `bulk_create_nodes/edges` with `executemany` and unified transactions
- **Functional Indexing**: Native JSON-based edge confidence indexing for fast complex filtering

### v1.5.4 (2025-01-31)
- **Schema Cleanup**: Removed invalid `VECTOR_DIMENSION` call from schema utilities
- **Refinement**: Engine now relies solely on inference and explicit config for dimensions

### v1.5.3 (2025-01-31)
- **Robust Embeddings**: Fixed embedding dimension detection for IRIS Community 2025.1
- **API Improvements**: Added `embedding_dimension` param to `IRISGraphEngine` for manual override
- **Auto-Inference**: Automatically infers dimension from input if detection fails
- **Code Quality**: Major cleanup of `engine.py` to remove legacy duplicates

### v1.5.2 (2025-01-31)
- **Engine Acceleration**: Ported high-performance SQL paths for `get_node()` and `count_nodes()`
- **Bulk Loading**: New `bulk_create_nodes()` and `bulk_create_edges()` methods with `%NOINDEX` support
- **Performance**: Verified 80x speedup for single-node reads and 450x for counts vs standard Cypher

### v1.5.1 (2025-01-31)
- **Extreme Performance**: Verified 38ms latency for 5,000-node property queries (at 10k entity scale)
- **Subquery Stability**: Optimized `REPLACE` string aggregation to avoid IRIS `%QPAR` optimizer bugs
- **Scale Verified**: Robust E2E stress tests confirm industrial-grade performance for 10,000+ nodes

### v1.4.9 (2025-01-31)
- **Exact Collation**: Added `%EXACT` to VARCHAR columns for case-sensitive matching
- **Performance**: Prevents default `UPPER` collation behavior in IRIS 2024.2+
- **Case Sensitivity**: Ensures node IDs, labels, and property keys are case-sensitive

### v1.4.8 (2025-01-31)
- **Fix SUBSCRIPT error**: Removed `idx_props_key_val` which caused errors with large values
- **Improved Performance**: Maintained composite indexes that don't include large VARCHAR columns

### v1.4.7 (2025-01-31)
- **Revert to VARCHAR(64000)**: LONGVARCHAR broke REPLACE; VARCHAR(64000) keeps compatibility
- **Large Values**: 64KB property values, REPLACE works, no CAST needed

### ~~v1.4.5/1.4.6~~ (deprecated - use 1.4.7)
- v1.4.5 used LONGVARCHAR which broke REPLACE function
- v1.4.6 used CAST which broke on old schemas

### v1.4.4 (2025-01-31)
- **Bulk Loading Support**: `%NOINDEX` INSERTs, `disable_indexes()`, `rebuild_indexes()`
- **Fast Ingest**: Skip index maintenance during bulk loads, rebuild after

### v1.4.3 (2025-01-31)
- **Composite Indexes**: Added (s,key), (s,p), (p,o_id), (s,label) based on TrustGraph patterns
- **12 indexes total**: Optimized for label filtering, property lookups, edge traversal

### v1.4.2 (2025-01-31)
- **Performance Indexes**: Added indexes on rdf_labels, rdf_props, rdf_edges for fast graph traversal
- **ensure_indexes()**: New method to add indexes to existing databases
- **Composite Index**: Added (key, val) index on rdf_props for property value lookups

### v1.4.1 (2025-01-31)
- **Embedding API**: Added `get_embedding()`, `get_embeddings()`, `delete_embedding()` methods
- **Schema Prefix in Engine**: All engine SQL now uses configurable schema prefix

### v1.4.0 (2025-01-31)
- **Schema Prefix Support**: `set_schema_prefix('Graph_KG')` for qualified table names
- **Pattern Operators Fixed**: `CONTAINS`, `STARTS WITH`, `ENDS WITH` now work correctly
- **IRIS Compatibility**: Removed recursive CTEs and `NULLS LAST` (unsupported by IRIS)
- **ORDER BY Fix**: Properties in ORDER BY now properly join rdf_props table
- **type(r) Verified**: Relationship type function works in RETURN/WHERE clauses

---

**Author: Thomas Dyar** (thomas.dyar@intersystems.com)
