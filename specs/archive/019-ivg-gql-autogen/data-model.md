# Data Model: Auto-Generating GraphQL Layer

This document defines the logical entities and mapping rules for the auto-generated GraphQL schema over IRIS Graph Stores.

## Entities

### Node (Generic)
Represents any entity in the IRIS graph.

| Field | Type | Description |
|-------|------|-------------|
| `id` | ID! | The unique node identifier (`node_id` in IRIS). |
| `labels` | [String!]! | All labels assigned to the node. |
| `p_<prop>` | String | Top-level property fields discovered via sampling. |
| `properties` | [Property!]! | Generic map of all properties for discovery. |
| `outgoing` | [Relationship!]! | Relationships where this node is the source (`s`). |
| `incoming` | [Relationship!]! | Relationships where this node is the target (`o_id`). |

**Validation Rules**:
- Property keys matching reserved keywords (`id`, `labels`, `properties`, etc.) MUST be prefixed with `p_`.
- Embedding vectors MUST be excluded from the `properties` map.

### Relationship
Represents an edge between two nodes.

| Field | Type | Description |
|-------|------|-------------|
| `predicate` | String! | The edge type (`p` in IRIS `rdf_edges`). |
| `targetId` | ID! | The node ID of the other end of the relationship. |
| `node` | Node! | The resolved node at the other end. |

### SemanticSearchResult
The result of a vector similarity search.

| Field | Type | Description |
|-------|------|-------------|
| `score` | Float! | Vector similarity score (0.0 to 1.0). |
| `node` | Node! | The matched graph node. |

## Schema Discovery Logic

1. **Label Discovery**: Query `DISTINCT label` from `rdf_labels`.
2. **Property Sampling**: For each label, query `rdf_props` for the first 1,000 nodes of that label to collect unique keys.
3. **Type Generation**: Create a GraphQL `Node` type for each label containing sampled properties as fields.
4. **Keyword Handling**: Check property keys against a blacklist: `['id', 'labels', 'properties', 'outgoing', 'incoming', 'neighbors']`. If match, prefix with `p_`.

## Relationship Traversal Rules

- **Neighbors**: A helper field `neighbors(predicate: String, direction: Direction)` where `Direction` is `OUTGOING` or `INCOMING`.
- **Default**: Relationships are resolved one-hop only for MVP.
