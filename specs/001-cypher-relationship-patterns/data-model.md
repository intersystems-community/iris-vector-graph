# Data Model: Cypher AST and Schema Mapping

## Entities

### NodePattern
Represents a node in a MATCH pattern.
- `variable`: Optional string (e.g., `n` in `(n:Label)`)
- `labels`: List of strings (e.g., `['Label']`)
- `properties`: Dictionary of key-value pairs for property constraints

### RelationshipPattern
Represents a relationship in a MATCH pattern.
- `variable`: Optional string (e.g., `r` in `-[r:TYPE]->`)
- `types`: List of strings (e.g., `['TYPE1', 'TYPE2']` in `[:TYPE1|TYPE2]`)
- `direction`: Enum (OUTGOING, INCOMING, BOTH)
- `variable_length`: Optional object with `min_hops` and `max_hops`

### GraphPattern
A collection of nodes and relationships forming a path.
- `nodes`: Ordered list of `NodePattern`
- `relationships`: Ordered list of `RelationshipPattern`

## Database Mapping

### Relationship Translation
- `s`: Maps to source node `node_id`
- `o_id`: Maps to target node `node_id`
- `p`: Maps to relationship `types` (uses `IN` for multiple types)

### SQL Join Structure
```sql
FROM nodes {n0}
JOIN rdf_edges {e0} ON {e0}.s = {n0}.node_id AND {e0}.p IN (...)
JOIN nodes {n1} ON {n1}.node_id = {e0}.o_id
```

## State Transitions
N/A - The parser and translator are stateless transformers.
