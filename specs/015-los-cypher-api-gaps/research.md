# Research: LOS Cypher & API Integration Gaps

## 1. Node Data Retrieval (RETURN n)

**Decision**: Use subqueries with `JSON_ARRAYAGG` for labels and properties.

**Rationale**: Since `JSON_OBJECTAGG` is not available in IRIS 2025.1, we cannot aggregate properties into a single JSON object directly in SQL. By using `JSON_ARRAYAGG(JSON_OBJECT('key':"key", 'value':val))`, we get an array of key-value pairs that can be easily parsed into a dictionary in the Python `IRISGraphEngine.execute_cypher` method.

**Alternatives Considered**:
- `LISTAGG`: Fragile with escaping and string length limits.
- Stored Procedures: More complex to deploy and maintain.

## 2. Type Coercion for Comparisons

**Decision**: Use `CAST(p.val AS DOUBLE)` for numeric comparisons.

**Rationale**: IRIS SQL's `CAST` function returns `0` when it fails to convert a string to a number, rather than throwing an error. This is acceptable for the LOS use cases (e.g., `confidence >= 0.7`) as non-numeric properties will simply fail the comparison.

**Alternatives Considered**:
- `TO_NUMBER`: Same behavior as `CAST`.
- `TRY_CAST`: Not available in IRIS 2025.1.

## 3. String Pattern Matching

**Decision**: Use `LIKE` with `%` wildcards.

**Rationale**: Standard SQL mapping for `CONTAINS`, `STARTS WITH`, and `ENDS WITH`.
- `CONTAINS 'foo'` -> `LIKE '%foo%'`
- `STARTS WITH 'foo'` -> `LIKE 'foo%'`
- `ENDS WITH 'foo'` -> `LIKE '%foo'`

## 4. Embedding Storage

**Decision**: Implement `store_embedding` and `store_embeddings` in `IRISGraphEngine`.

**Rationale**: Using `TO_VECTOR(?)` in a direct SQL `INSERT` is the standard way to store embeddings in IRIS. Dimension validation will be performed by checking the input list length against the expected dimension for the table.

## 5. type(r) Function

**Decision**: Confirm usage of `alias.p` in the translator.

**Rationale**: The existing schema stores the relationship type in the `p` (predicate) column of the `rdf_edges` table. `type(r)` should simply project this column.
