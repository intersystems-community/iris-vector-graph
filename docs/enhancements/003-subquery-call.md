# Enhancement: Subquery Clauses (`CALL { ... }`)

**Date**: 2026-03-26
**Status**: Requested
**Affects**: `iris_vector_graph/cypher/ast.py`, `parser.py`, `translator.py`

---

## Problem

IVG's Cypher has no support for subquery clauses (`CALL { ... }`), the openCypher
mechanism for encapsulating a query fragment that runs in its own scope and yields
results back to the outer query. This blocks:

- Correlated subqueries (outer variable imported into subquery)
- Independent subqueries with aggregation that feed the outer pipeline
- Post-union aggregation patterns

The AST has no `SubqueryCall` node. The parser has no `{`-delimited clause rule.
The translator has no CTE nesting strategy for subqueries.

---

## Two Variants in Scope

### Variant A — Unit Subquery (independent)

No outer variables imported. Subquery runs once, results unioned into outer scope.

```cypher
CALL {
    MATCH (n:Drug) RETURN n, n.name AS name
}
RETURN name
```

Translates to: a CTE or derived table whose SELECT is injected into the outer FROM.

### Variant B — Correlated Subquery (imports outer variable)

Outer variable explicitly imported via `WITH` inside the subquery.

```cypher
MATCH (p:Protein)
CALL {
    WITH p
    MATCH (p)-[:INTERACTS_WITH]->(partner)
    RETURN count(partner) AS degree
}
RETURN p.id, degree
```

Translates to: a correlated derived table or lateral join in IRIS SQL.

---

## AST Changes

Add to `iris_vector_graph/cypher/ast.py`:

```python
@dataclass(slots=True)
class SubqueryCall:
    """CALL { ... } subquery clause."""
    inner_query: 'CypherQuery'
    import_variables: List[str] = field(default_factory=list)
    in_transactions: bool = False        # CALL { ... } IN TRANSACTIONS
    transactions_batch_size: Optional[int] = None
```

Extend `QueryPart` to accept `SubqueryCall` in its clauses list:

```python
@dataclass(slots=True)
class QueryPart:
    clauses: List[Union[MatchClause, UnwindClause, UpdatingClause,
                        WhereClause, SubqueryCall]] = field(default_factory=list)
    with_clause: Optional[WithClause] = None
```

---

## SQL Translation Strategy

### Variant A (independent) → CTE

```sql
WITH subquery_cte AS (
    SELECT n.node_id AS n, n_props.val AS name
    FROM Graph_KG.nodes n
    JOIN Graph_KG.rdf_labels lbl ON lbl.s = n.node_id AND lbl.label = 'Drug'
    JOIN Graph_KG.rdf_props n_props ON n_props.s = n.node_id AND n_props.key = 'name'
)
SELECT name FROM subquery_cte
```

### Variant B (correlated) → LATERAL join (IRIS 2023.1+)

```sql
SELECT p.node_id, sub.degree
FROM Graph_KG.nodes p
JOIN Graph_KG.rdf_labels lbl ON lbl.s = p.node_id AND lbl.label = 'Protein'
CROSS JOIN LATERAL (
    SELECT COUNT(partner.node_id) AS degree
    FROM Graph_KG.rdf_edges e
    JOIN Graph_KG.nodes partner ON partner.node_id = e.o_id
    WHERE e.s = p.node_id AND e.p = 'INTERACTS_WITH'
) sub
```

**IRIS SQL constraint**: `LATERAL` joins available in IRIS 2023.1+. If target
environment is older, correlated subqueries fall back to a scalar subquery
in the SELECT list (works for single-column aggregates only).

---

## `CALL { ... } IN TRANSACTIONS`

Used for write-heavy batch operations. Translates to chunked DML execution:

```cypher
CALL {
    MATCH (n:OldNode) DETACH DELETE n
} IN TRANSACTIONS OF 500 ROWS
```

Phase 1: treat `IN TRANSACTIONS` as a no-op hint (execute as single transaction).
Phase 2: implement batched execution via Python-side chunking of result sets.

---

## Scope Rules

| Variable | Visible inside subquery? |
|----------|--------------------------|
| Outer variable (no WITH) | ❌ No — independent subquery |
| Outer variable (WITH p) | ✅ Yes — correlated subquery |
| Subquery output (YIELD/RETURN) | ✅ Yes — available in outer clauses |
| Subquery internal variables | ❌ No — scoped to subquery only |

---

## Files to Change

| File | Change |
|------|--------|
| `iris_vector_graph/cypher/ast.py` | Add `SubqueryCall`; extend `QueryPart` |
| `iris_vector_graph/cypher/lexer.py` | Ensure `{` / `}` tokens are handled in clause context |
| `iris_vector_graph/cypher/parser.py` | Parse `CALL { ... }` and `CALL { WITH x ... }` blocks |
| `iris_vector_graph/cypher/translator.py` | Render as CTE (independent) or LATERAL join (correlated) |
| `tests/unit/test_subquery_call.py` | Unit: parse independent + correlated; IN TRANSACTIONS |
| `tests/integration/test_subquery_call_e2e.py` | E2E: independent + correlated against live IRIS |

---

## Acceptance Criteria

- [ ] `SubqueryCall` AST node with `inner_query`, `import_variables`, `in_transactions`
- [ ] Parser handles `CALL { MATCH ... RETURN ... }` (independent)
- [ ] Parser handles `CALL { WITH x MATCH ... RETURN ... }` (correlated)
- [ ] Independent subquery translates to CTE in IRIS SQL
- [ ] Correlated subquery translates to LATERAL join (IRIS 2023.1+) with scalar
  subquery fallback for single-column aggregates
- [ ] `IN TRANSACTIONS` parsed and treated as no-op in Phase 1
- [ ] Subquery output variables available in outer RETURN/WHERE
- [ ] Outer variables NOT visible inside subquery unless imported via WITH
- [ ] Unit tests: parse both variants, scope isolation, IN TRANSACTIONS flag
- [ ] E2E: independent + correlated subquery against live IRIS

---

## Phasing

**Phase 1 (MVP)**:
- Independent subquery → CTE translation
- `IN TRANSACTIONS` parsed, no-op
- Correlated single-column aggregate → scalar subquery in SELECT

**Phase 2**:
- Full LATERAL join for multi-column correlated subqueries
- `IN TRANSACTIONS OF N ROWS` batched execution
- Nested subqueries (`CALL { CALL { ... } }`)

---

## Reference

- openCypher spec §7 Subqueries: https://opencypher.org/
- IRIS SQL LATERAL join: IRIS 2023.1 SQL Reference, §JOIN syntax
- Neo4j subquery docs: https://neo4j.com/docs/cypher-manual/current/subqueries/
