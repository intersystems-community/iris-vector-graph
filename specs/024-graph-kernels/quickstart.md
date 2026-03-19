# Quickstart: Graph Analytics Kernels

## Global PageRank
```python
from iris_vector_graph.operators import IRISGraphOperators
ops = IRISGraphOperators(conn)

# All nodes ranked by global importance
scores = ops.kg_PAGERANK(damping=0.85, max_iterations=20)
for node_id, score in scores[:10]:
    print(f"{node_id}: {score:.6f}")
```

## Weakly Connected Components
```python
components = ops.kg_WCC(max_iterations=100)
# Count components
from collections import Counter
comp_sizes = Counter(components.values())
print(f"{len(comp_sizes)} components, largest has {comp_sizes.most_common(1)[0][1]} nodes")
```

## Community Detection (CDLP)
```python
communities = ops.kg_CDLP(max_iterations=10)
from collections import Counter
comm_sizes = Counter(communities.values())
print(f"{len(comm_sizes)} communities detected")
for label, size in comm_sizes.most_common(5):
    print(f"  Community '{label}': {size} nodes")
```

## Combined with Retrieval Pipeline
```python
# Vector search → subgraph → analytics
seeds = ops.kg_KNN_VEC("PMID:630", k=10)
sg = ops.kg_SUBGRAPH([nid for nid, _ in seeds], k_hops=2, max_nodes=500)

# Which component is this subgraph in?
components = ops.kg_WCC()
seed_component = components.get(seeds[0][0])
same_component = [n for n in sg.nodes if components.get(n) == seed_component]
print(f"{len(same_component)}/{len(sg.nodes)} subgraph nodes in same component as seed")
```

## Running Tests
```bash
pytest tests/unit/test_graph_kernels.py -v
pytest tests/e2e/test_graph_kernels_e2e.py -v
```
