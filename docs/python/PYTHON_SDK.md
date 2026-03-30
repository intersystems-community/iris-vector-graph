# IRIS Vector Graph Python SDK (v1.28.0)

This guide documents the current Python API surface for `iris-vector-graph` v1.28.0.

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

Canonical connection pattern:

```python
import iris

conn = iris.connect(
    hostname="localhost",
    port=1972,
    namespace="USER",
    username="_SYSTEM",
    password="SYS",
)
```

---

## 3) `IRISGraphEngine`

```python
from iris_vector_graph.engine import IRISGraphEngine

engine = IRISGraphEngine(
    conn,
    embedding_dimension=768,   # required before initialize_schema()
    embedder=None,             # optional
    embedding_config=None,     # optional IRIS EMBEDDING() config name
)
```

### Core methods

```python
engine.initialize_schema(auto_deploy_objectscript=True)

engine.create_node(
    node_id="node:1",
    labels=["Entity"],
    properties={"name": "Example"},
)

engine.create_edge(
    source_id="node:1",
    predicate="RELATED_TO",
    target_id="node:2",
    qualifiers={"confidence": 0.9},
)

node = engine.get_node("node:1")
nodes = engine.get_nodes(["node:1", "node:2"])

ok = engine.delete_node("node:2")

anchors = engine.get_kg_anchors(["E11.9", "J18.0"], bridge_type="icd10_to_mesh")
```

### Notes

- `initialize_schema()` is idempotent and installs SQL schema/procedures.
- `create_node()`, `create_edge()`, `delete_node()` return `bool`.
- `get_node()` returns `dict | None`; `get_nodes()` returns `list[dict]`.
- `get_kg_anchors()` returns only bridge targets that exist in the KG.

---

## 4) VecIndex API

Lightweight ANN index backed by ObjectScript globals (`Graph.KG.VecIndex`).

```python
engine.vec_create_index("my_idx", dim=384, metric="cosine", num_trees=4, leaf_size=50)

engine.vec_insert("my_idx", "doc:1", [0.1, 0.2, 0.3])

engine.vec_bulk_insert("my_idx", [
    {"id": "doc:2", "embedding": [0.2, 0.3, 0.4]},
    {"id": "doc:3", "embedding": [0.3, 0.4, 0.5]},
])

engine.vec_build("my_idx")

hits = engine.vec_search("my_idx", [0.1, 0.2, 0.3], k=5, nprobe=8)
multi_hits = engine.vec_search_multi("my_idx", [[0.1, 0.2, 0.3], [0.3, 0.2, 0.1]], k=5, nprobe=8)

info = engine.vec_info("my_idx")

engine.vec_expand("my_idx", seed_id="doc:1", k=5)

engine.vec_drop("my_idx")
```

### API summary

- `vec_create_index(name, dim, metric="cosine", num_trees=4, leaf_size=50) -> dict`
- `vec_insert(index_name, doc_id, embedding) -> None`
- `vec_bulk_insert(index_name, items) -> int`  (`items=[{"id","embedding"}, ...]`)
- `vec_build(index_name) -> dict`
- `vec_search(index_name, query_embedding, k=10, nprobe=8) -> list`
- `vec_search_multi(index_name, query_embeddings, k=10, nprobe=8) -> list`
- `vec_info(index_name) -> dict`
- `vec_drop(index_name) -> None`
- `vec_expand(index_name, seed_id, k=5) -> list`

---

## 5) PLAID API

ColBERT-style multi-vector retrieval (`Graph.KG.PLAIDSearch`).

```python
docs = [
    {"id": "doc:1", "tokens": [[0.1, 0.2], [0.3, 0.4]]},
    {"id": "doc:2", "tokens": [[0.2, 0.1], [0.4, 0.3]]},
]

engine.plaid_build("plaid_idx", docs, n_clusters=None, dim=2)

results = engine.plaid_search("plaid_idx", query_tokens=[[0.1, 0.2], [0.3, 0.4]], k=10, nprobe=4)

engine.plaid_insert("plaid_idx", "doc:3", token_embeddings=[[0.5, 0.6], [0.7, 0.8]])

info = engine.plaid_info("plaid_idx")

engine.plaid_drop("plaid_idx")
```

### API summary

- `plaid_build(name, docs, n_clusters=None, dim=128) -> dict`
- `plaid_search(name, query_tokens, k=10, nprobe=4) -> list`
- `plaid_insert(name, doc_id, token_embeddings) -> None`
- `plaid_info(name) -> dict`
- `plaid_drop(name) -> None`

`plaid_build()` requires `[plaid]` dependencies (`numpy`, `scikit-learn`).

---

## 6) `IRISGraphOperators`

```python
from iris_vector_graph.operators import IRISGraphOperators

ops = IRISGraphOperators(conn)
```

### Vector + neighborhood

```python
vec_hits = ops.kg_KNN_VEC('[0.1, 0.2, 0.3]', k=10, label_filter=None)
neighbors = ops.kg_NEIGHBORS(["doc:1", "doc:2"], predicate="MENTIONS", direction="out")
mentions = ops.kg_MENTIONS(["doc:1", "doc:2"])  # alias for MENTIONS neighbors
```

### PageRank (unified global + personalized)

```python
global_pr = ops.kg_PAGERANK(damping=0.85, max_iterations=20)
personalized_pr = ops.kg_PAGERANK(seed_entities=["doc:1"], damping=0.85, max_iterations=20)
```

- `kg_PAGERANK(seed_entities=None, ...)`:
  - `seed_entities=None` -> global PageRank
  - `seed_entities=[...]` -> personalized PageRank

### Graph analytics kernels

```python
wcc = ops.kg_WCC(max_iterations=100)
cdlp = ops.kg_CDLP(max_iterations=10)

sub = ops.kg_SUBGRAPH(seed_ids=["doc:1"], k_hops=2)

guided = ops.kg_PPR_GUIDED_SUBGRAPH(
    seed_ids=["doc:1"],
    alpha=0.15,
    eps=1e-5,
    top_k=50,
    max_hops=5,
)
```

---

## 7) Cypher

Use `execute_cypher()` from `IRISGraphEngine`:

```python
result = engine.execute_cypher(
    "MATCH (n:Entity) RETURN n.id LIMIT 5",
    parameters=None,
)
```

### Named paths

```python
result = engine.execute_cypher(
    "MATCH p = (a)-[r]->(b) RETURN p, length(p), nodes(p), relationships(p)"
)
```

### `CALL { ... }` subqueries

```python
result = engine.execute_cypher(
    "MATCH (p:Protein) "
    "CALL { WITH p MATCH (p)-[:INTERACTS_WITH]->(q) RETURN count(q) AS deg } "
    "RETURN p.id, deg"
)
```

### `ivg` procedures

```python
# vector search
engine.execute_cypher(
    "CALL ivg.vector.search('Gene', 'embedding', [0.1, 0.2], 5) "
    "YIELD node, score RETURN node, score"
)

# 1-hop neighbors
engine.execute_cypher(
    "CALL ivg.neighbors(['A','B'], 'MENTIONS', 'out') "
    "YIELD neighbor RETURN neighbor"
)

# personalized PageRank
engine.execute_cypher(
    "CALL ivg.ppr(['A','B'], 0.85, 20) "
    "YIELD node, score RETURN node, score"
)
```

`execute_cypher()` returns a dict with keys like `columns`, `rows`, `sql`, `params`, and `metadata`.

---

## 8) Arno Acceleration

The engine supports optional Arno/NKG acceleration with automatic detection and fallback:

```python
khop_result = engine.khop(seed="node:1", hops=2, max_nodes=500)
ppr_result = engine.ppr(seed="node:1", alpha=0.85, max_iter=20, top_k=20)
walks = engine.random_walk(seed="node:1", length=20, num_walks=10)
```

Behavior:

- If `Graph.KG.NKGAccel` is available and supports the algorithm, accelerated path is used.
- If unavailable or an accelerated call errors, engine falls back to non-Arno paths.

---

## Related docs

- GraphQL support exists, but details are documented separately (see `README.md` and `iris_vector_graph/gql/`).
- NetworkX and Pandas integrations exist in the project examples/tests.
- Performance benchmark details: `docs/performance/BENCHMARKS.md` and `README.md`.
