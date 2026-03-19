# Data Model: kg_SUBGRAPH

**Phase 1 Output** | **Date**: 2026-03-19

## SubgraphData

The primary return type for `kg_SUBGRAPH()`.

| Field | Type | Description |
|-------|------|-------------|
| `nodes` | `List[str]` | All node IDs in the subgraph |
| `edges` | `List[Tuple[str, str, str]]` | Edge triples: (source, predicate, target) |
| `node_properties` | `Dict[str, Dict[str, str]]` | Properties per node: `{node_id: {key: val}}` |
| `node_labels` | `Dict[str, List[str]]` | Labels per node: `{node_id: [label1, label2]}` |
| `node_embeddings` | `Dict[str, List[float]]` | Embeddings per node (empty if not requested) |
| `seed_ids` | `List[str]` | Original seed node IDs (for reference) |

### Invariants

- `set(seed_ids) ⊆ set(nodes)` (seeds that exist in the graph are in nodes)
- For every edge `(s, p, o)` in `edges`: both `s` and `o` are in `nodes`
- `len(nodes) <= max_nodes`
- No duplicate entries in `nodes` or `edges`
- `node_embeddings` is empty dict when `include_embeddings=False`

### JSON Wire Format (SubgraphJson return)

```json
{
  "nodes": ["A", "B", "C"],
  "edges": [
    {"s": "A", "p": "MENTIONS", "o": "B"},
    {"s": "B", "p": "INTERACTS", "o": "C"}
  ],
  "properties": {
    "A": {"name": "Article 1", "year": "2024"},
    "B": {"name": "Gene X"}
  },
  "labels": {
    "A": ["Article"],
    "B": ["Gene"],
    "C": ["Protein"]
  }
}
```

## Relationship to Existing Schema

| SubgraphData field | Source in ^KG | Source in SQL |
|-------------------|--------------|--------------|
| `nodes` | BFS frontier collection | `Graph_KG.nodes` |
| `edges` | `^KG("out", s, p, o)` | `Graph_KG.rdf_edges` |
| `node_properties` | `^KG("prop", s, key)` | `Graph_KG.rdf_props` |
| `node_labels` | `^KG("label", label, s)` | `Graph_KG.rdf_labels` |
| `node_embeddings` | N/A (SQL only for v1) | `Graph_KG.kg_NodeEmbeddings` |
