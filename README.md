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

---

## Scaling & Performance

The integration of a native **HNSW (Hierarchical Navigable Small World)** functional index directly into InterSystems IRIS provides massive scaling benefits for hybrid graph-vector workloads. 

By keeping the vector index in-process with the graph data, we achieve **subsecond multi-modal queries** that would otherwise require complex application-side joins across multiple databases.

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
Real-time fraud scoring with transaction networks and bitemporal audit trails.

To run the demos:
```bash
# Start the demo server
export PYTHONPATH=$PYTHONPATH:$(pwd)/src
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

**Author: Thomas Dyar** (thomas.dyar@intersystems.com)
