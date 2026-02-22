# Quickstart: Cypher Vector Search (`ivg.vector.search`)

**Feature**: 018-cypher-vector-search  
**Date**: 2026-02-21

---

## Prerequisites

- `iris-vector-graph` installed (v1.7.0+)
- InterSystems IRIS 2024.1+ (Mode 1); IRIS 2024.3+ required for Mode 2 (auto-vectorization)
- Nodes with stored embeddings in `kg_NodeEmbeddings`
- HNSW index on `kg_NodeEmbeddings.emb` (see setup below)

---

## Setup

### 1. Initialize the engine

```python
from iris_vector_graph import IRISGraphEngine

engine = IRISGraphEngine(
    host="localhost",
    port=1972,
    namespace="USER",
    username="_SYSTEM",
    password="SYS",
    embedding_dimension=768
)
engine.initialize_schema()
```

### 2. Create the HNSW index (once, after schema init)

```python
engine.execute_sql("""
    CREATE INDEX HNSW_NodeEmb
    ON Graph_KG.kg_NodeEmbeddings(emb)
    AS HNSW(M=16, efConstruction=100, Distance='Cosine')
""")
```

### 3. Store some nodes with embeddings

```python
# Create nodes
engine.create_node("PROTEIN:TP53", labels=["Protein"], properties={"name": "TP53"})
engine.create_node("PROTEIN:BRCA1", labels=["Protein"], properties={"name": "BRCA1"})

# Store embeddings (pre-computed by your embedding model)
engine.store_embedding("PROTEIN:TP53", embedding=[0.12, 0.34, ...])  # 768-dim
engine.store_embedding("PROTEIN:BRCA1", embedding=[0.11, 0.36, ...])
```

---

## Basic Usage

### Mode 1 — Pre-computed vector (always available)

```python
import numpy as np

# Your query embedding (from any model)
query_vec = my_embedding_model.encode("tumor suppressor").tolist()

results = engine.execute_cypher(
    """
    CALL ivg.vector.search('Protein', 'embedding', $vec, 10)
    YIELD node, score
    RETURN node, score
    """,
    params={"vec": query_vec}
)

for row in results:
    print(f"Node: {row['node']['id']}, Score: {row['score']:.4f}")
    print(f"  Labels: {row['node']['labels']}")
    print(f"  Properties: {row['node']['properties']}")
```

### Mode 2 — Text string, IRIS auto-vectorizes (IRIS 2024.3+)

Requires a registered `%Embedding.Config` entry:

```sql
-- Run once in IRIS SQL (requires %USE_EMBEDDING privilege)
INSERT INTO %Embedding.Config (Name, Configuration, EmbeddingClass, Description)
VALUES ('minilm',
        '{"modelName":"sentence-transformers/all-MiniLM-L6-v2",
          "hfCachePath":"/path/to/hf_cache",
          "maxTokens": 256}',
        '%Embedding.SentenceTransformers',
        'MiniLM 384-dim local model')
```

```python
results = engine.execute_cypher(
    """
    CALL ivg.vector.search('Protein', 'embedding', $query, 10,
                           {embedding_config: 'minilm'})
    YIELD node, score
    RETURN node, score
    """,
    params={"query": "tumor suppressor DNA repair"}
)
```

---

## Composing with Graph Traversal

Vector search yields flow directly into subsequent `MATCH` clauses:

```python
results = engine.execute_cypher(
    """
    CALL ivg.vector.search('Protein', 'embedding', $vec, 5)
    YIELD node, score
    MATCH (node)-[:INTERACTS_WITH]->(partner)
    RETURN node.id AS source, score, partner.id AS partner
    ORDER BY score DESC
    """,
    params={"vec": query_vec}
)
```

---

## Changing Similarity Metric

```python
# Use dot product instead of cosine
results = engine.execute_cypher(
    """
    CALL ivg.vector.search('Protein', 'embedding', $vec, 10,
                           {similarity: 'dot_product'})
    YIELD node, score
    RETURN node, score
    """,
    params={"vec": query_vec}
)
```

---

## Error Handling

```python
try:
    results = engine.execute_cypher(
        "CALL ivg.vector.search('Protein', 'embedding', $vec, 10, "
        "{similarity: 'euclidean'}) YIELD node, score RETURN node, score",
        params={"vec": query_vec}
    )
except ValueError as e:
    print(e)
# ValueError: Invalid similarity metric 'euclidean'. Valid: 'cosine', 'dot_product'
```

```python
# Mode 2 without embedding_config raises immediately
try:
    engine.execute_cypher(
        "CALL ivg.vector.search('Protein', 'embedding', $text, 10) "
        "YIELD node, score RETURN node, score",
        params={"text": "some query"}
    )
except ValueError as e:
    print(e)
# ValueError: embedding_config required in options when query_input is a text string
```

---

## Running the E2E Tests

```bash
# Ensure the iris_vector_graph container is running (managed by idt)
idt start iris_vector_graph

# Run all feature 018 tests
cd /path/to/iris-vector-graph
pytest tests/e2e/test_cypher_vector_search.py -v
pytest tests/integration/test_cypher_vector_search.py -v
pytest tests/unit/test_cypher_vector_search.py -v
```

To skip IRIS-dependent tests (unit only):
```bash
SKIP_IRIS_TESTS=true pytest tests/unit/test_cypher_vector_search.py -v
```
