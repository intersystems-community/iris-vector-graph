# Quickstart: kg_SUBGRAPH

**Phase 1 Output** | **Date**: 2026-03-19

## Basic Usage

```python
from iris_vector_graph.operators import IRISGraphOperators

ops = IRISGraphOperators(conn)

# Extract 2-hop subgraph from a seed article
subgraph = ops.kg_SUBGRAPH(
    seed_ids=["PMID:630"],
    k_hops=2,
)

print(f"Nodes: {len(subgraph.nodes)}")
print(f"Edges: {len(subgraph.edges)}")
print(f"Labels: {subgraph.node_labels}")
```

## With Edge Type Filtering

```python
# Only follow MENTIONS edges (skip CITES, INTERACTS, etc.)
subgraph = ops.kg_SUBGRAPH(
    seed_ids=["PMID:630", "PMID:193181"],
    k_hops=2,
    edge_types=["MENTIONS"],
)
```

## With Embeddings for ML

```python
# Include node embeddings for GNN feature matrix construction
subgraph = ops.kg_SUBGRAPH(
    seed_ids=["PROTEIN:TP53"],
    k_hops=2,
    include_embeddings=True,
    max_nodes=500,
)

# subgraph.node_embeddings: {"PROTEIN:TP53": [0.1, 0.2, ...], ...}
```

## MindWalk Pipeline

```python
# Full pipeline: vector search → mentions → subgraph → PPR
seeds = ops.kg_KNN_VEC("PMID:630", k=10)
anchors = ops.kg_NEIGHBORS([nid for nid, _ in seeds], predicate="MENTIONS")

# Extract rich subgraph around anchors for LLM context
context = ops.kg_SUBGRAPH(
    seed_ids=anchors[:20],
    k_hops=1,
    include_properties=True,
    include_embeddings=True,
    max_nodes=1000,
)

# Feed to LLM as structured context
for node in context.nodes:
    props = context.node_properties.get(node, {})
    labels = context.node_labels.get(node, [])
    print(f"[{','.join(labels)}] {node}: {props}")
```

## Running Tests

```bash
# Unit tests (no IRIS needed)
pytest tests/unit/test_subgraph.py -v

# E2E tests (requires live IRIS container)
pytest tests/e2e/test_subgraph_e2e.py -v
```
