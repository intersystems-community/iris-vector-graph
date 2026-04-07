# IRIS Vector Graph Python SDK (v1.46.0)

This guide documents the Python API surface for `iris-vector-graph` v1.46.0.

---

## 1) Install

```bash
pip install iris-vector-graph
pip install iris-vector-graph[full]
pip install iris-vector-graph[plaid]
```

- **Core** (`iris-vector-graph`): minimal install, includes `intersystems-irispython`.
- **`[full]`**: adds FastAPI/GraphQL and common data tooling.
- **`[plaid]`**: adds `numpy` + `scikit-learn` for `plaid_build()`.

---

## 2) Connection

```python
import iris

# External (host process)
conn = iris.connect(
    hostname="localhost",
    port=1972,
    namespace="USER",
    username="_SYSTEM",
    password="SYS",
)

# Embedded (inside IRIS Language=python method)
from iris_vector_graph.embedded import EmbeddedConnection
conn = EmbeddedConnection()
```

---

## 3) `IRISGraphEngine`

```python
from iris_vector_graph.engine import IRISGraphEngine

engine = IRISGraphEngine(
    conn,
    embedding_dimension=768,   # required before initialize_schema()
)
engine.initialize_schema()     # idempotent — safe to call on every startup
```

### Core graph methods

```python
engine.create_node("node:1", labels=["Entity"], properties={"name": "Example"})
engine.create_edge("node:1", "RELATED_TO", "node:2", qualifiers={"confidence": 0.9})

node  = engine.get_node("node:1")       # dict | None
nodes = engine.get_nodes(["node:1"])    # list[dict]
ok    = engine.delete_node("node:2")    # bool
```

### Bulk ingest

```python
engine.bulk_create_nodes([
    {"id": "n:1", "labels": ["Gene"], "properties": {"name": "BRCA1"}},
    {"id": "n:2", "labels": ["Drug"], "properties": {"name": "Olaparib"}},
])
engine.bulk_create_edges([
    {"source": "n:1", "predicate": "TARGETS", "target": "n:2"},
])
```

### Cypher

```python
result = engine.execute_cypher(
    "MATCH (n:Gene)-[:TARGETS]->(d:Drug) RETURN n.id, d.id LIMIT 10"
)
# result = {"columns": [...], "rows": [...], "sql": "...", "params": [...]}
```

---

## 4) BM25Index API

Pure ObjectScript Okapi BM25 lexical search over `^BM25Idx` globals. No SQL tables, no Enterprise license required.

```python
# Build index over all nodes using their 'name' and 'definition' properties
result = engine.bm25_build("ncit", text_props=["name", "definition"], k1=1.5, b=0.75)
# {"indexed": 200000, "avgdl": 8.4, "vocab_size": 45000}

# Search — returns list of (node_id, score) tuples, sorted by score DESC
hits = engine.bm25_search("ncit", "ankylosing spondylitis HLA-B27", k=10)
# [("NCIT:C34718", 8.4), ("NCIT:C2890", 6.1), ...]

# Incremental add / update
engine.bm25_insert("ncit", "NCIT:C99999", "new concept text here")

# Metadata
info = engine.bm25_info("ncit")
# {"N": 200001, "avgdl": 8.4, "vocab_size": 45001}  |  {} if not found

# Remove
engine.bm25_drop("ncit")
```

### kg_TXT automatic upgrade

If a `"default"` BM25 index exists, `IRISGraphOperators.kg_TXT()` routes through it automatically instead of using the LIKE fallback:

```python
engine.bm25_build("default", text_props=["name"])   # one-time setup

from iris_vector_graph.operators import IRISGraphOperators
ops = IRISGraphOperators(conn)
ops.graph_engine = engine
hits = ops.kg_TXT("diabetes", k=20)   # uses BM25 automatically
```

### Cypher procedure

```python
engine.execute_cypher(
    "CALL ivg.bm25.search('ncit', $q, 10) YIELD node, score RETURN node, score",
    {"q": "ankylosing spondylitis"}
)
```

### API summary

- `bm25_build(name, text_props, k1=1.5, b=0.75) -> dict`  — `{"indexed", "avgdl", "vocab_size"}`
- `bm25_search(name, query, k=10) -> list[tuple[str, float]]`
- `bm25_insert(name, doc_id, text) -> bool`
- `bm25_drop(name) -> None`
- `bm25_info(name) -> dict`  — `{}` if index not found

---

## 5) VecIndex API

Lightweight ANN index backed by ObjectScript globals (`Graph.KG.VecIndex`).

```python
engine.vec_create_index("my_idx", dim=384, metric="cosine", num_trees=4, leaf_size=50)
engine.vec_insert("my_idx", "doc:1", [0.1, 0.2, 0.3])
engine.vec_bulk_insert("my_idx", [
    {"id": "doc:2", "embedding": [0.2, 0.3, 0.4]},
])
engine.vec_build("my_idx")

hits = engine.vec_search("my_idx", [0.1, 0.2, 0.3], k=5, nprobe=8)
multi_hits = engine.vec_search_multi("my_idx", [[0.1, 0.2, 0.3]], k=5)

info = engine.vec_info("my_idx")
engine.vec_expand("my_idx", seed_id="doc:1", k=5)
engine.vec_drop("my_idx")
```

### API summary

- `vec_create_index(name, dim, metric="cosine", num_trees=4, leaf_size=50) -> dict`
- `vec_insert(index_name, doc_id, embedding) -> None`
- `vec_bulk_insert(index_name, items) -> int`
- `vec_build(index_name) -> dict`
- `vec_search(index_name, query_embedding, k=10, nprobe=8) -> list`
- `vec_search_multi(index_name, query_embeddings, k=10, nprobe=8) -> list`
- `vec_info(index_name) -> dict`
- `vec_drop(index_name) -> None`
- `vec_expand(index_name, seed_id, k=5) -> list`

---

## 6) Generic Vector Search (v1.45.0+)

Search any IRIS VECTOR column, not just `kg_NodeEmbeddings`:

```python
# Search a specific table/column
hits = engine.vector_search(
    table="MySchema.DocChunk",
    vector_col="VectorChunk",
    query_embedding=[0.1, 0.2, ...],
    top_k=10,
)

# Fused search across multiple vector tables
hits = engine.multi_vector_search(
    sources=[
        {"table": "Graph_KG.kg_NodeEmbeddings", "vector_col": "emb"},
        {"table": "RAG.SourceDocuments",        "vector_col": "embedding"},
    ],
    query_embedding=[0.1, 0.2, ...],
    top_k=10,
    fusion="rrf",
)

# Node embedding
engine.embed_nodes(
    model="sentence-transformers/all-MiniLM-L6-v2",
    text_fn=lambda node: node.get("name", ""),
    where="label = 'Gene'",
    batch_size=64,
)
```

---

## 7) PLAID API

ColBERT-style multi-vector retrieval (`Graph.KG.PLAIDSearch`).

```python
docs = [
    {"id": "doc:1", "tokens": [[0.1, 0.2], [0.3, 0.4]]},
    {"id": "doc:2", "tokens": [[0.2, 0.1], [0.4, 0.3]]},
]
engine.plaid_build("plaid_idx", docs, n_clusters=None, dim=2)
results = engine.plaid_search("plaid_idx", query_tokens=[[0.1, 0.2]], k=10, nprobe=4)
engine.plaid_insert("plaid_idx", "doc:3", token_embeddings=[[0.5, 0.6]])
info = engine.plaid_info("plaid_idx")
engine.plaid_drop("plaid_idx")
```

`plaid_build()` requires `[plaid]` install extras (`numpy`, `scikit-learn`).

---

## 8) Temporal Graph API

```python
import time

# Single edge
engine.create_edge_temporal(
    source="svc:auth", predicate="CALLS_AT", target="svc:payment",
    timestamp=int(time.time()), weight=42.7,
    attrs={"trace_id": "abc123"},
)

# Bulk ingest (~134K edges/sec)
engine.bulk_create_edges_temporal([
    {"s": "svc:auth", "p": "CALLS_AT", "o": "svc:pay", "ts": 1712000000, "w": 38.1},
])

# Window query
edges = engine.get_edges_in_window("svc:auth", "CALLS_AT", ts_start, ts_end)

# Pre-aggregated analytics (O(1))
avg  = engine.get_temporal_aggregate("svc:auth", "CALLS_AT", "avg", ts_start, ts_end)
groups = engine.get_bucket_groups("CALLS_AT", ts_start, ts_end)
```

---

## 9) SQL Table Bridge (v1.44.0+)

Map existing IRIS SQL tables as virtual graph nodes — zero data copy:

```python
engine.map_sql_table("MySchema.Patient", id_column="PatientID", label="Patient")
engine.map_sql_relationship("Patient", "HAS_ENCOUNTER", "Encounter", target_fk="PatientID")

# Now queryable via Cypher
engine.execute_cypher("MATCH (p:Patient)-[:HAS_ENCOUNTER]->(e:Encounter) RETURN p.id LIMIT 5")
```

---

## 10) `IRISGraphOperators`

```python
from iris_vector_graph.operators import IRISGraphOperators

ops = IRISGraphOperators(conn)
```

```python
# Text search — uses BM25 if 'default' index exists, LIKE fallback otherwise
hits = ops.kg_TXT("ankylosing spondylitis", k=20)

# Vector + neighborhood
vec_hits  = ops.kg_KNN_VEC('[0.1, 0.2, 0.3]', k=10)
neighbors = ops.kg_NEIGHBORS(["doc:1"], predicate="MENTIONS", direction="out")

# PageRank
global_pr = ops.kg_PAGERANK(damping=0.85, max_iterations=20)
ppr       = ops.kg_PAGERANK(seed_entities=["doc:1"], damping=0.85)

# Graph kernels
wcc     = ops.kg_WCC(max_iterations=100)
cdlp    = ops.kg_CDLP(max_iterations=10)
sub     = ops.kg_SUBGRAPH(seed_ids=["doc:1"], k_hops=2)
guided  = ops.kg_PPR_GUIDED_SUBGRAPH(seed_ids=["doc:1"], top_k=50, max_hops=5)
```

---

## 11) Cypher `ivg` Procedures

| Procedure | Signature | YIELD |
|-----------|-----------|-------|
| `ivg.vector.search` | `(label, property, query_input, limit)` | `node, score` |
| `ivg.neighbors` | `(sources, predicate, direction)` | `neighbor` |
| `ivg.ppr` | `(seeds, alpha, max_iter)` | `node, score` |
| `ivg.bm25.search` | `(name, query, k)` | `node, score` |

```python
# BM25 lexical search in Cypher
engine.execute_cypher(
    "CALL ivg.bm25.search('ncit', $q, 10) YIELD node, score "
    "RETURN node, score ORDER BY score DESC",
    {"q": "HLA-B27 spondylitis"}
)

# Vector search in Cypher
engine.execute_cypher(
    "CALL ivg.vector.search('Gene', 'embedding', [0.1, 0.2], 5) "
    "YIELD node, score RETURN node, score"
)

# PPR in Cypher
engine.execute_cypher(
    "CALL ivg.ppr(['svc:auth'], 0.85, 20) YIELD node, score RETURN node, score"
)
```

---

## 12) Arno Acceleration

```python
khop_result = engine.khop(seed="node:1", hops=2, max_nodes=500)
ppr_result  = engine.ppr(seed="node:1", alpha=0.85, max_iter=20, top_k=20)
walks       = engine.random_walk(seed="node:1", length=20, num_walks=10)
```

Falls back transparently if `Graph.KG.NKGAccel` is unavailable.

---

## Related docs

- Architecture: `docs/architecture/ARCHITECTURE.md`
- Schema reference: `docs/architecture/ACTUAL_SCHEMA.md`
- Performance benchmarks: `docs/performance/BENCHMARKS.md`
- Testing policy: `docs/TESTING_POLICY.md`
