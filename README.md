# IRIS Vector Graph

**The ultimate Graph + Vector + Text Retrieval Engine for InterSystems IRIS.**

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![InterSystems IRIS](https://img.shields.io/badge/IRIS-2025.1+-purple.svg)](https://www.intersystems.com/products/intersystems-iris/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

IRIS Vector Graph is a high-performance knowledge graph system that combines **graph traversal**, **HNSW vector similarity**, and **lexical search** in a single, unified database.

![GraphQL Playground](docs/images/graphql-playground.png)

<p align="center">
  <img src="docs/images/api-home.png" width="45%" />
  <img src="docs/images/api-docs.png" width="45%" />
</p>

---

## 🚀 Why IRIS Vector Graph?

- **Multi-Query Power**: Query your graph via **SQL**, **openCypher**, or **GraphQL** — all on the same data.
- **Blazing Fast Vectors**: Native HNSW indexing delivering **~1.7ms** search latency (vs 5.8s standard).
- **Zero-Dependency Integration**: Built with IRIS Embedded Python — no external vector DBs or graph engines required.
- **Production-Ready**: The engine behind [iris-vector-rag](https://github.com/intersystems-community/iris-vector-rag) for advanced RAG pipelines.

---

## ⚡ 30-Second Quick Start

```bash
# 1. Clone & Sync
git clone https://github.com/isc-tdyar/iris-vector-graph.git && cd iris-vector-graph
uv sync

# 2. Spin up IRIS
# Uses iris-devtester for automatic dynamic port and password handling
python scripts/setup_iris.py 

# 3. Start API
uvicorn api.main:app --reload
```

Visit:
- **GraphQL Playground**: [http://localhost:8000/graphql](http://localhost:8000/graphql)
- **API Docs**: [http://localhost:8000/docs](http://localhost:8000/docs)

---

## 🛠 Unified Query Engines

### openCypher
```cypher
MATCH (p:Protein {id: "PROTEIN:TP53"})-[:interacts_with*1..2]->(target)
RETURN p.name, target.name
```

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

### SQL (Hybrid)
```sql
SELECT TOP 10 id, 
       kg_RRF_FUSE(id, vector, 'cancer suppressor') as score
FROM nodes
ORDER BY score DESC
```

---

## 📊 Performance at a Glance

| Operation | Standard IRIS | ACORN-1 (HNSW) | Gain |
|-----------|---------------|----------------|------|
| **Vector Search** | 5,800ms | **1.7ms** | **3400x** |
| **Graph Hop** | 1.0ms | **0.09ms** | **11x** |
| **Ingestion** | 29 nodes/s | **6,496 nodes/s** | **224x** |

---

## 🎮 Interactive Demos

Experience the power of IRIS Vector Graph through our interactive demo applications.

### Biomedical Research Demo
Explore protein-protein interaction networks with vector similarity and D3.js visualization.
![Biomedical Demo](docs/images/demo-biomedical.png)
*View the [Biomedical Architecture](docs/images/demo-biomedical-architecture.png) popup in the UI.*

### Fraud Detection Demo
Real-time fraud scoring with 130M transactions and bitemporal audit trails.
![Fraud Demo](docs/images/demo-fraud.png)
*View the [Fraud Architecture](docs/images/demo-fraud-architecture.png) popup in the UI.*

To run the demos:
```bash
# Start the demo server
export PYTHONPATH=$PYTHONPATH:$(pwd)/src
uv run uvicorn src.iris_demo_server.app:app --port 8200 --host 0.0.0.0
```
Visit [http://localhost:8200](http://localhost:8200) to begin.

---

## 🤖 iris-vector-rag Integration

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

## 📚 Documentation & Links

- 📖 **[Detailed Architecture](docs/architecture/ARCHITECTURE.md)**
- 🧬 **[Biomedical Domain Examples](examples/domains/biomedical/)**
- 🧪 **[Full Test Suite](tests/)**
- 🤖 **[iris-vector-rag Integration](https://github.com/intersystems-community/iris-vector-rag)**
- 📜 **[Verbose README](docs/README_VERBOSE.md)** (Legacy)

---

**Author: Thomas Dyar** (thomas.dyar@intersystems.com)


