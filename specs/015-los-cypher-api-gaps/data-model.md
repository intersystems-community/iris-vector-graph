# Data Model: LOS Cypher & API Integration

## RDF Schema Mapping

This feature utilizes the existing RDF schema in InterSystems IRIS but enhances how it is accessed and filtered.

### 1. Nodes (`Graph_KG.nodes`)
- `node_id` (VARCHAR): Primary identifier.
- Mapped to `n.id` in Cypher.

### 2. Labels (`Graph_KG.rdf_labels`)
- `s` (VARCHAR): Node identifier (Subject).
- `label` (VARCHAR): Label string.
- Mapped to `labels(n)` in Cypher.

### 3. Properties (`Graph_KG.rdf_props`)
- `s` (VARCHAR): Node identifier (Subject).
- `key` (VARCHAR): Property key.
- `val` (VARCHAR): Property value (stored as string).
- Mapped to `n.property` and `properties(n)` in Cypher.
- Numeric comparisons use `CAST(val AS DOUBLE)`.

### 4. Edges (`Graph_KG.rdf_edges`)
- `s` (VARCHAR): Source node identifier.
- `p` (VARCHAR): Predicate (Relationship type).
- `o_id` (VARCHAR): Object identifier (Target node).
- Mapped to `(a)-[r]->(b)` where `type(r)` is the predicate `p`.

### 5. Embeddings (`Graph_KG.kg_NodeEmbeddings`)
- `id` (VARCHAR): Node identifier.
- `emb` (VECTOR): Vector data.
- `metadata` (JSON): Optional metadata.

## Cypher to SQL Translation Mapping

| Cypher Expression | SQL Translation Pattern |
|-------------------|-------------------------|
| `MATCH (n)` | `SELECT n0.node_id FROM nodes n0` |
| `RETURN n` | `SELECT n0.node_id, (SELECT JSON_ARRAYAGG(label) ...), (SELECT JSON_ARRAYAGG(JSON_OBJECT('key':"key", 'value':val)) ...)` |
| `labels(n)` | `(SELECT JSON_ARRAYAGG(label) FROM rdf_labels WHERE s = n.node_id)` |
| `properties(n)` | `(SELECT JSON_ARRAYAGG(JSON_OBJECT('key':"key", 'value':val)) FROM rdf_props WHERE s = n.node_id)` |
| `n.prop >= 0.5` | `CAST(p.val AS DOUBLE) >= 0.5` |
| `n.name CONTAINS 'foo'` | `p.val LIKE '%foo%'` |
| `ORDER BY n.prop DESC` | `ORDER BY p.val DESC NULLS LAST` |
| `LIMIT 10` | `LIMIT 10` |
| `type(r)` | `alias.p` |
