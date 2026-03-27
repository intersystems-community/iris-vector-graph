# Research: Subquery Clauses (CALL { ... })

**Feature**: 026-subquery-call | **Date**: 2026-03-27

## R1: Parser Disambiguation — CALL procedure vs CALL { subquery }

**Decision**: Peek for `LBRACE` after consuming `CALL`. If `LBRACE` → subquery path. If `IDENTIFIER` → existing procedure path.

**Rationale**: The parser already handles `CALL ivg.vector.search(...)`. The token after `CALL` is unambiguous: `{` can only mean subquery (property maps never follow `CALL`), while `IDENTIFIER` means procedure name.

**Alternatives considered**:
- New `CALL_SUBQUERY` token in lexer: Rejected — adds lexer complexity, the parser can disambiguate trivially.

## R2: Independent Subquery → CTE Translation

**Decision**: Translate `CALL { inner_query } RETURN ...` as a CTE. The inner query is fully translated via the existing `translate_to_sql` pipeline (recursive call), then wrapped in a named CTE. Outer query references CTE columns.

**Rationale**: CTEs are the established pattern in the translator — `VecSearch`, `Neighbors`, `PPR`, and multi-stage `StageN` all use CTEs. Adding another CTE is zero new infrastructure.

**SQL output example**:
```sql
WITH SubQuery0 AS (
    SELECT n0.node_id AS n, p0.val AS name
    FROM Graph_KG.nodes n0
    JOIN Graph_KG.rdf_labels l1 ON l1.s = n0.node_id AND l1.label = ?
    JOIN Graph_KG.rdf_props p0 ON p0.s = n0.node_id AND p0."key" = ?
)
SELECT SubQuery0.name AS name FROM SubQuery0
```

## R3: Correlated Subquery → Scalar Subquery in SELECT

**Decision**: For Phase 1, correlated subqueries with a single aggregation column translate to a scalar subquery in the SELECT list, with COALESCE for zero-match safety (FR-011).

**Rationale**: Scalar subqueries work on all IRIS versions. LATERAL joins (IRIS 2023.1+) are Phase 2 — they enable multi-column correlated results but add complexity.

**SQL output example**:
```sql
SELECT n0.node_id AS p_id,
       COALESCE((SELECT COUNT(n1.node_id)
                 FROM Graph_KG.rdf_edges e0
                 JOIN Graph_KG.nodes n1 ON n1.node_id = e0.o_id
                 WHERE e0.s = n0.node_id AND e0.p = ?), 0) AS degree
FROM Graph_KG.nodes n0
JOIN Graph_KG.rdf_labels l0 ON l0.s = n0.node_id AND l0.label = ?
```

**Alternatives considered**:
- LATERAL join: Deferred to Phase 2 — correct but restricts to IRIS 2023.1+ and adds join infrastructure.
- Python-side correlation: Rejected — N+1 query problem, defeats server-side execution.

## R4: Lexer Token Additions

**Decision**: Add `TRANSACTIONS` and `ROWS` as keyword tokens. `OF` and `IN` already exist.

**Rationale**: `IN TRANSACTIONS OF 500 ROWS` requires recognizing these as keywords, not identifiers. The lexer already has a keyword map — adding 2 entries is trivial.

## R5: Scope Isolation Strategy

**Decision**: The parser creates a fresh `SubqueryCall` with `import_variables` populated from the inner `WITH` clause (if present). The translator creates a child `TranslationContext` that starts empty (independent) or inherits only the imported variables (correlated). Outer variables not in `import_variables` are not in the child context's `variable_aliases`.

**Rationale**: The translator already uses `TranslationContext(parent=...)` for multi-stage queries. Subquery scope isolation is the same pattern but with selective inheritance instead of full copy.
