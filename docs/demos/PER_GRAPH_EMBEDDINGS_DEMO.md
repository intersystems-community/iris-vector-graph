<!-- markdownlint-disable MD013 -->

# Per-graph embeddings demo

For application developers who keep vectors from more than one model: two graphs hold the same node ID, each with
vectors from a different model at a different width, and neither sees the other's. Every block below runs in order
on any namespace IVG is installed in, with vectors small enough to work the answers by hand. The longer script,
with 768- and 384-wide random vectors, is `examples/demo_per_graph_embeddings.py`.

## Setup

```python
import iris
from iris_vector_graph import IRISGraphEngine

conn = iris.connect("localhost", 1972, "USER", "_SYSTEM", "SYS")
engine = IRISGraphEngine(conn)
engine.initialize_schema()

NODE = "demo:gene:BRCA1"
GRAPH_UMLS, MODEL_UMLS = "demo_umls", "biobert"  # 3-wide vectors here
GRAPH_HPO, MODEL_HPO = "demo_hpo", "minilm"  # 2-wide
```

## 1. The same node ID in two graphs

`nodes` is keyed `(graph_id, node_id)` since 4.0.0, so one entity can exist in two graphs. Each graph gets the gene
and two neighbours:

```python
for graph in (GRAPH_UMLS, GRAPH_HPO):
    for node_id in (NODE, f"{NODE}:n1", f"{NODE}:n2"):
        engine.create_node(node_id, labels=["Gene"], graph=graph)
```

A query that names no graph reads every graph, and the same ID in two graphs is two nodes:

```python
engine.execute_cypher("MATCH (n {node_id: $id}) RETURN count(n)", {"id": NODE}).rows
# → [[2]]
```

## 2. Two routes, two models, two widths

A **route** is a physical table chosen by `(graph, model_key)`, created by the first write to the pair at that
write's width. IRIS enforces a `VECTOR` column's declared width at `INSERT`, so two widths cannot share a column.

```python
for node_id, vec in [(NODE, [1.0, 0.0, 0.0]), (f"{NODE}:n1", [0.0, 1.0, 0.0]), (f"{NODE}:n2", [0.6, 0.8, 0.0])]:
    engine.store_embedding(node_id, vec, graph=GRAPH_UMLS, model_key=MODEL_UMLS)
for node_id, vec in [(NODE, [0.0, 1.0]), (f"{NODE}:n1", [1.0, 0.0]), (f"{NODE}:n2", [0.8, 0.6])]:
    engine.store_embedding(node_id, vec, graph=GRAPH_HPO, model_key=MODEL_HPO)
```

The table is named `kg_emb_` and the first 16 hex digits of `sha256(graph || 0x00 || model_key)`, and recorded in
`embedding_registry`. `resolve_route` reads it from the registry, so it reports what the table is called, not what a
new one would be:

```python
route = engine.resolve_route(graph=GRAPH_UMLS, model_key=MODEL_UMLS)
(route.table_name, route.dimension)
# → ('kg_emb_bbe9f56f1df304a9', 3)
```

```python
engine.resolve_route(graph=GRAPH_HPO, model_key=MODEL_HPO).table_name
# → 'kg_emb_fd03b0d56285c0dc'
```

## 3. Each graph's search returns only its own nodes

The query `[1, 0, 0]` has cosine similarity 1.0 with the gene, 0.6 with `n2` and 0 with `n1` in the UMLS graph.
`k=10` over three rows returns three; the HPO graph's vectors are in another table and are never candidates:

```python
hits = engine.kg_KNN_VEC("[1.0,0.0,0.0]", k=10, graph=GRAPH_UMLS, model_key=MODEL_UMLS)
[node_id for node_id, _ in hits]
# → ['demo:gene:BRCA1', 'demo:gene:BRCA1:n2', 'demo:gene:BRCA1:n1']
```

## 4. A query of the wrong width is refused

A 3-wide query against the 2-wide route raises `ValueError`; it is not padded or cut to fit (ADR-0005):

```python
try:
    engine.kg_KNN_VEC("[1.0,0.0,0.0]", k=10, graph=GRAPH_HPO, model_key=MODEL_HPO)
    refused = None
except ValueError as e:
    refused = type(e).__name__
refused
# → 'ValueError'
```

## 5. Omitting `graph` reads the default graph

There is no value that means every graph: no `graph` is the default graph, whose route holds none of these vectors.

```python
(engine.embedding_count(), engine.embedding_count(graph=GRAPH_UMLS, model_key=MODEL_UMLS))
# → (0, 3)
```

## 6. Erasing one graph leaves the other

```python
engine.erase_graph(GRAPH_UMLS)
(engine.embedding_count(graph=GRAPH_UMLS, model_key=MODEL_UMLS), engine.embedding_count(graph=GRAPH_HPO, model_key=MODEL_HPO))
# → (0, 3)
```

## 7. Recall is measured, not assumed

`measure_route_recall` compares the route's search with an exhaustive scan of the same rows, for `probes` seed nodes
(the first by node ID), and records the result on the route's registry row:

```python
engine.measure_route_recall(graph=GRAPH_HPO, model_key=MODEL_HPO, k=2, probes=2).recall
# → 1.0
```

1.0 means the search was exact on these three rows, at this `k`, for these seeds, at the recorded time; it says
nothing about the index on other data.

## Related

- [User guide](../USER_GUIDE.md), the embedding sections
- [Migrating to 4.0.0](../migration/v4.0.0.md), where per-graph embeddings arrived
- [Versioned graph demo](VERSIONED_GRAPH_DEMO.md)
