# API Contract: kg_SUBGRAPH

**Phase 1 Output** | **Date**: 2026-03-19

## Python API

### `IRISGraphOperators.kg_SUBGRAPH`

```
kg_SUBGRAPH(
    seed_ids: List[str],
    k_hops: int = 2,
    edge_types: Optional[List[str]] = None,
    include_properties: bool = True,
    include_embeddings: bool = False,
    max_nodes: int = 10000,
) -> SubgraphData
```

| Parameter | Type | Default | Constraint |
|-----------|------|---------|------------|
| seed_ids | List[str] | required | empty → empty result |
| k_hops | int | 2 | ≥ 0 |
| edge_types | Optional[List[str]] | None | None = all types |
| include_properties | bool | True | |
| include_embeddings | bool | False | |
| max_nodes | int | 10000 | > 0 |

**Returns**: `SubgraphData` (see data-model.md)

**Error behavior**: No exceptions for valid inputs. Nonexistent seeds silently excluded. Empty graph → empty SubgraphData.

---

## Server-Side API

### `Graph.KG.Subgraph.SubgraphJson`

```
ClassMethod SubgraphJson(
    seedJson As %String,
    maxHops As %Integer = 2,
    edgeTypesJson As %String = "",
    maxNodes As %Integer = 10000
) As %String
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| seedJson | %String | required | JSON array: `'["A","B"]'` |
| maxHops | %Integer | 2 | BFS depth limit |
| edgeTypesJson | %String | "" | JSON array of predicates, or "" for all |
| maxNodes | %Integer | 10000 | Node count safety cap |

**Returns**: JSON string (see data-model.md wire format)

---

## Cypher API (stretch)

```cypher
CALL ivg.subgraph($seeds, 2, {edge_types: ['MENTIONS']}) YIELD nodes, edges
RETURN nodes, edges
```

| Argument | Position | Required | Description |
|----------|----------|----------|-------------|
| seeds | 0 | yes | List of seed node IDs |
| k_hops | 1 | no (default 2) | BFS depth |
| options | 2 | no | `{edge_types: [...], max_nodes: N}` |

**Yields**: `nodes` (JSON array), `edges` (JSON array of triples)
