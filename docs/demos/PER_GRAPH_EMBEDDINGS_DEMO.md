<!-- markdownlint-disable MD013 -->

# Per-Graph Embeddings Demo

Two graphs hold the same node ID, each with vectors from a different model at a
different width, and neither can see the other's. Runnable against
`ivg-iris-enterprise` (port 31972):

```bash
DOCKER_CONTEXT=orbstack IVG_TEST_CONTAINER=ivg-iris-enterprise \
    python examples/demo_per_graph_embeddings.py
```

The script is `examples/demo_per_graph_embeddings.py`. It creates only the two
graphs it owns (`demo_umls`, `demo_hpo`), and erases both on the way out whether
it succeeds or fails.

## What it connects to

By container **name**, resolved from `IVG_TEST_CONTAINER` and defaulting to
`ivg-iris-enterprise` — never by auto-discovery. See
[KNOWN_ISSUES](../KNOWN_ISSUES.md) for why: discovery answered another project's
container on this machine, and a demo has no way to notice it wrote to the wrong
instance.

## Setup

```python
from iris_vector_graph import IRISGraphEngine

NODE = "demo:gene:BRCA1"
GRAPH_UMLS, MODEL_UMLS, WIDTH_UMLS = "demo_umls", "biobert", 768
GRAPH_HPO, MODEL_HPO, WIDTH_HPO = "demo_hpo", "minilm", 384

engine = IRISGraphEngine(conn)
```

## Part 1 — The same node ID in two graphs

`Graph_KG.nodes` is keyed `(graph_id, node_id)` as of 4.0.0, so one entity can
exist in two graphs at once. In 3.2.0 `UNIQUE (node_id)` made this impossible,
which is what made two embeddings for one entity impossible.

```python
for graph in (GRAPH_UMLS, GRAPH_HPO):
    engine.create_node(NODE, labels=["Gene"], graph=graph)
```

## Part 2 — Two routes, two models, two widths

A route is a physical table, picked from `(graph_id, model_key)`. IRIS enforces a
declared `VECTOR` width at INSERT (SQLCODE -104), so two widths cannot share one
column — a filter would not be enough.

```python
engine.store_embedding(NODE, vec_768, graph=GRAPH_UMLS, model_key=MODEL_UMLS)
engine.store_embedding(NODE, vec_384, graph=GRAPH_HPO, model_key=MODEL_HPO)

route = engine.resolve_route(graph=GRAPH_UMLS, model_key=MODEL_UMLS)
print(route.table_name, route.dimension)
```

Measured:

```text
demo_umls  biobert   width 768  -> kg_emb_bbe9f56f1df304a9 (5 vectors)
demo_hpo   minilm    width 384  -> kg_emb_fd03b0d56285c0dc (5 vectors)
```

The table name is a hash of the route's identity, and `resolve_route` reads it
from the registry rather than recomputing it — the hash says what a *new* route
would be called, not what an existing one is called.

## Part 3 — Each graph's KNN returns only its own neighbours

```python
hits = engine.kg_KNN_VEC(query, k=10, graph=GRAPH_UMLS, model_key=MODEL_UMLS)
```

`k=10` over five rows returns five. The other graph's five are not candidates,
because they are not in the route's table at all.

## Part 4 — A wrong-width query is refused

A 768-wide query vector against the 384-wide route raises `ValueError` rather
than scoring a reshaped value (ADR-0005).

## Part 5 — Omitting `graph` reads the default graph

```python
engine.embedding_count()                                        # 0
engine.embedding_count(graph=GRAPH_UMLS, model_key=MODEL_UMLS)  # 5
```

There is no parameter value that means every graph. A cross-graph search is a
different question and needs a different method.

## Part 6 — Erasing one graph leaves the other

```python
engine.erase_graph(GRAPH_UMLS)
engine.embedding_count(graph=GRAPH_UMLS, model_key=MODEL_UMLS)  # 0
engine.embedding_count(graph=GRAPH_HPO, model_key=MODEL_HPO)    # 5, unchanged
```

## Part 7 — Recall is measured, not assumed

```python
recall = engine.measure_route_recall(graph=GRAPH_HPO, model_key=MODEL_HPO, k=4, probes=2)
print(recall.recall)  # 1.0
```

`1.0` here means the route's search was exact on this data, at this `k`, for
these probes, at `recall.measured_at`. It is not a claim about the index in
general — that is the only kind of recall claim spec 227 accepts.

## Full run

```text
Per-Graph Embedding Models (4.0.0)
==================================
  connected to ivg-iris-enterprise via OrbStack DNS 192.168.138.28:1972
[1/7] Connecting and clearing the demo's own graphs... OK (1466.81ms)
    demo:gene:BRCA1 now exists in demo_umls and demo_hpo
[2/7] Writing the same node ID into two graphs... OK (79.83ms)
    demo_umls  biobert   width 768  -> kg_emb_bbe9f56f1df304a9 (5 vectors)
    demo_hpo   minilm    width 384  -> kg_emb_fd03b0d56285c0dc (5 vectors)
[3/7] Two routes, two models, two widths... OK (656.85ms)
    demo_umls  k=10 returned 5: ['demo:gene:BRCA1', 'demo:gene:BRCA1:n3', 'demo:gene:BRCA1:n2'] …
    demo_hpo   k=10 returned 5: ['demo:gene:BRCA1', 'demo:gene:BRCA1:n1', 'demo:gene:BRCA1:n0'] …
[4/7] Each graph's KNN returns only its own neighbours... OK (30.62ms)
    refused as it should be: ValueError
[5/7] A wrong-width query is refused, not reshaped... OK (0.25ms)
    embedding_count() (default graph) = 0
    embedding_count(graph='demo_umls') = 5
    the demo's vectors are in neither default-graph route
[6/7] Omitting graph reads the default graph, not every graph... OK (6.20ms)
    demo_umls after erase: 0 vectors
    demo_hpo unchanged:    5 -> 5 vectors
    measured recall@4 on that route: 1.0
[7/7] Erasing one graph leaves the other's vectors... OK (104.42ms)

Demo completed successfully in 2.35s
```

## Related

- [USER_GUIDE — Per-Graph Embedding Models](../USER_GUIDE.md)
- [Migrating to 4.0.0](../migration/v4.0.0.md)
- [Versioned Graph Demo](VERSIONED_GRAPH_DEMO.md)
