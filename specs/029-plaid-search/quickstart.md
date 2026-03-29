# Quickstart: PLAID Multi-Vector Retrieval

**Feature**: 029-plaid-search

## Usage

### 1. Build a PLAID index

```python
from iris_vector_graph.engine import IRISGraphEngine
from sentence_transformers import SentenceTransformer

engine = IRISGraphEngine(conn, embedding_dimension=128)

# Prepare multi-vector documents (e.g., ColBERT token embeddings)
model = SentenceTransformer("colbert-ir/colbertv2.0")
docs = []
for text in ["Metformin treats diabetes", "Aspirin reduces inflammation", ...]:
    tokens = model.encode(text, output_value="token_embeddings")
    docs.append({"id": text[:20], "tokens": tokens.tolist()})

# Build index (Python K-means + ObjectScript inverted index)
result = engine.plaid_build("biomedical", docs)
# → {"nCentroids": 160, "nDocs": 500, "totalTokens": 26500}
```

### 2. Search with multi-vector query

```python
query = "What drugs treat diabetes?"
query_tokens = model.encode(query, output_value="token_embeddings").tolist()

results = engine.plaid_search("biomedical", query_tokens, k=5)
# → [{"id": "Metformin treats", "score": 0.94}, {"id": "Insulin regul", "score": 0.87}, ...]
```

### 3. Insert a new document

```python
new_tokens = model.encode("Empagliflozin SGLT2 inhibitor", output_value="token_embeddings")
engine.plaid_insert("biomedical", "Empagliflozin", new_tokens.tolist())
# Immediately searchable, no rebuild needed
```

### 4. Index info

```python
engine.plaid_info("biomedical")
# → {"name": "biomedical", "nCentroids": 160, "nDocs": 501, "dim": 128, "totalTokens": 26553}
```

## Performance

| Operation | Latency | Details |
|-----------|---------|---------|
| Build (500 docs) | ~2s | Python K-means ~50ms + ObjectScript inverted index ~1.5s |
| Search (4 query tokens) | ~9ms | Stage 1: 0.5ms, Stage 1.5: 0.1ms, Stage 2: 8.5ms |
| Insert (1 doc, ~53 tokens) | <5ms | Assign to nearest centroids + store tokens |

## Architecture

```
BUILD (batch, one-time):
  Python: sklearn K-means → centroids + assignments
  Python → IRIS: store centroids as $vector in ^PLAID globals
  ObjectScript: build inverted index ^PLAID("docCentroid", k, docId)

SEARCH (hot path, per query):
  Python: engine.plaid_search(name, query_tokens)
    → single classMethodValue call
  ObjectScript PLAIDSearch.Search():
    Stage 1: query tokens × centroids dot product ($vectorop)    → 0.5ms
    Stage 1.5: $ORDER on inverted index → candidate docs         → 0.1ms
    Stage 2: MaxSim scoring via $vectorop on candidates           → 8.5ms
    → JSON result back to Python
```
