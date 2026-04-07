# IRIS Vector Graph Quickstart

## Prerequisites

- Docker + Docker Compose
- Python 3.10+

---

## 1. Start the IRIS container

```bash
git clone https://github.com/intersystems-community/iris-vector-graph
cd iris-vector-graph
docker compose up -d
```

The container `iris-vector-graph-iris-1` starts on port 1972 (mapped dynamically by docker-compose).

---

## 2. Install the package

```bash
pip install iris-vector-graph              # core only
pip install iris-vector-graph[full]        # + FastAPI/GraphQL
pip install iris-vector-graph[plaid]       # + numpy/sklearn for PLAID
```

---

## 3. Connect and initialize

```python
import iris
from iris_vector_graph.engine import IRISGraphEngine

conn = iris.connect(hostname="localhost", port=1972, namespace="USER",
                    username="_SYSTEM", password="SYS")
engine = IRISGraphEngine(conn, embedding_dimension=768)
engine.initialize_schema()   # idempotent — installs SQL schema + ObjectScript classes
```

---

## 4. Load data

```python
engine.create_node("gene:BRCA1", labels=["Gene"], properties={"name": "BRCA1"})
engine.create_node("drug:Olaparib", labels=["Drug"], properties={"name": "Olaparib"})
engine.create_edge("gene:BRCA1", "TARGETS", "drug:Olaparib")

# OBO ontology (e.g. NCI Thesaurus)
engine.load_obo("path/to/Thesaurus.obo")
```

---

## 5. Query

### Cypher

```python
result = engine.execute_cypher(
    "MATCH (g:Gene)-[:TARGETS]->(d:Drug) RETURN g.id, d.id LIMIT 10"
)
print(result["rows"])
```

### BM25 lexical search

```python
engine.bm25_build("ncit", text_props=["name", "definition"])
hits = engine.bm25_search("ncit", "ankylosing spondylitis HLA-B27", k=10)
# [("NCIT:C34718", 8.4), ...]
```

### Vector search

```python
engine.vec_create_index("genes", dim=384, metric="cosine")
engine.vec_insert("genes", "gene:BRCA1", embedding_vector)
engine.vec_build("genes")
hits = engine.vec_search("genes", query_vector, k=5)
```

### `ivg` Cypher procedures

```python
# BM25 in Cypher
engine.execute_cypher(
    "CALL ivg.bm25.search('ncit', $q, 5) YIELD node, score RETURN node, score",
    {"q": "diabetes"}
)

# Personalized PageRank
engine.execute_cypher(
    "CALL ivg.ppr(['gene:BRCA1'], 0.85, 20) YIELD node, score RETURN node, score"
)
```

---

## 6. Run tests

```bash
pytest tests/unit/ -q
```

E2E tests require the container to be running. They attach to `iris-vector-graph-main` via `IRISContainer.attach()`.

---

## Directory structure

```
iris-vector-graph/
├── iris_src/src/Graph/KG/    # ObjectScript classes
├── iris_vector_graph/         # Python package
│   ├── engine.py              # IRISGraphEngine (all Python wrappers)
│   ├── operators.py           # IRISGraphOperators (kg_TXT, PPR, etc.)
│   ├── cypher/                # Cypher parser + translator
│   └── embedded.py            # EmbeddedConnection for in-IRIS Python
├── tests/unit/                # All tests (unit + E2E)
├── specs/                     # Feature specifications
└── docs/                      # Documentation
```

---

## Further reading

- [Python SDK Reference](../python/PYTHON_SDK.md)
- [Architecture](../architecture/ARCHITECTURE.md)
- [Schema Reference](../architecture/ACTUAL_SCHEMA.md)
- [Testing Policy](../TESTING_POLICY.md)
