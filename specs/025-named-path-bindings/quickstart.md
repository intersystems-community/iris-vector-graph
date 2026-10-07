# Quickstart: Named Path Bindings

**Feature**: 025-named-path-bindings

## Usage

### Bind a path and return it

```cypher
MATCH p = (a:Protein)-[r:INTERACTS_WITH]->(b:Protein)
WHERE a.id = 'PROTEIN:TP53'
RETURN p
```

Result: each row contains a JSON object `{"nodes": ["PROTEIN:TP53", "PROTEIN:MDM2"], "rels": ["INTERACTS_WITH"]}`.

### Use path functions

```cypher
MATCH p = (a)-[r1]->(b)-[r2]->(c)
WHERE a.id = 'DRUG:Aspirin'
RETURN nodes(p) AS path_nodes, relationships(p) AS path_rels, length(p) AS hops
```

| path_nodes | path_rels | hops |
|-----------|-----------|------|
| `["DRUG:Aspirin", "GENE:COX2", "PATHWAY:Inflammation"]` | `["TARGETS", "PARTICIPATES_IN"]` | 2 |

### Combine with filters

```cypher
MATCH p = (src)-[r:CAUSES]->(dst)
WHERE src.name = 'Hypertension'
RETURN nodes(p), length(p)
```

## Path Functions Reference

| Function | Returns | Example |
|----------|---------|---------|
| `RETURN p` | JSON object with `nodes` and `rels` arrays | `{"nodes": ["A", "B"], "rels": ["KNOWS"]}` |
| `length(p)` | Integer hop count | `2` |
| `nodes(p)` | JSON array of node IDs in traversal order | `["A", "B", "C"]` |
| `relationships(p)` | JSON array of predicate strings in traversal order | `["KNOWS", "WORKS_AT"]` |

## Limitations (Phase 1)

- Fixed-length patterns only (e.g., `(a)-[r]->(b)-[s]->(c)`)
- Variable-length patterns (`[*1..3]`) are Phase 2
- `WHERE length(p) > 2` (path expressions in WHERE) not supported — use post-query filtering
