# Quickstart: Subquery Clauses (CALL { ... })

**Feature**: 026-subquery-call

## Usage

### Independent subquery — aggregation

```cypher
CALL {
    MATCH (n:Drug)
    RETURN count(n) AS drug_count
}
RETURN drug_count
```

Result: single row with the total count of Drug-labeled nodes.

### Independent subquery — projection

```cypher
CALL {
    MATCH (n:Gene)
    RETURN n.name AS gene_name
}
RETURN gene_name
```

Result: one row per Gene node with its name.

### Correlated subquery — per-node degree

```cypher
MATCH (p:Protein)
CALL {
    WITH p
    MATCH (p)-[:INTERACTS_WITH]->(partner)
    RETURN count(partner) AS degree
}
RETURN p.id, degree
```

Result: each Protein row includes its interaction count. Proteins with no interactions get `degree = 0`.

### IN TRANSACTIONS (Phase 1: parsed, no-op)

```cypher
CALL {
    MATCH (n:Deprecated)
    DELETE n
} IN TRANSACTIONS OF 500 ROWS
```

Phase 1: Executes as a single transaction. Phase 2 will implement batched execution.

## Scope Rules

| Variable | Visible inside subquery? |
|----------|--------------------------|
| Outer variable (no WITH) | No — independent subquery |
| Outer variable (WITH p) | Yes — correlated subquery |
| Subquery output (RETURN) | Yes — available in outer clauses |
| Subquery internal variables | No — scoped to subquery only |

## Limitations (Phase 1)

- Correlated subqueries limited to single-column aggregates (e.g., `count`, `sum`)
- Multi-column correlated results require Phase 2 (LATERAL join)
- `IN TRANSACTIONS` parsed but not actually batched
- Nested subqueries not supported
