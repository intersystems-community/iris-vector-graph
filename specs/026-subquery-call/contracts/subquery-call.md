# Contracts: Subquery Clauses (CALL { ... })

**Feature**: 026-subquery-call | **Date**: 2026-03-27

## Contract 1: AST — SubqueryCall dataclass

```python
@dataclass(slots=True)
class SubqueryCall:
    inner_query: 'CypherQuery'
    import_variables: List[str] = field(default_factory=list)
    in_transactions: bool = False
    transactions_batch_size: Optional[int] = None
```

## Contract 2: AST — QueryPart extension

```python
@dataclass(slots=True)
class QueryPart:
    clauses: List[Union[MatchClause, UnwindClause, UpdatingClause,
                        WhereClause, SubqueryCall]] = field(default_factory=list)
    with_clause: Optional[WithClause] = None
```

## Contract 3: Parser — CALL disambiguation

**Input**: Token stream starting with `CALL`

**Behavior**: In `parse_query_part`, when `CALL` is encountered:
1. Peek next token
2. If `LBRACE` → call `parse_subquery_call()`
3. If `IDENTIFIER` → fall through to existing `parse_procedure_call()` flow (handled at top level)

## Contract 4: Parser — parse_subquery_call

**Input**: Token stream starting at `CALL { ...`

**Behavior**:
1. Consume `CALL`, consume `LBRACE`
2. Check if first inner token is `WITH` → extract import variables
3. Parse inner query (recursive: `parse_query_part` + `parse_return_clause`)
4. Consume `RBRACE`
5. Check for `IN TRANSACTIONS [OF N ROWS]` suffix
6. Return `SubqueryCall(inner_query, import_variables, in_transactions, batch_size)`

## Contract 5: Translator — independent subquery → CTE

**Input**: `SubqueryCall` with `import_variables == []`

**Output SQL**: A new CTE appended to `context.stages`:
```sql
SubQueryN AS (
    SELECT [inner query columns]
    FROM [inner query joins]
    WHERE [inner query conditions]
)
```
Outer context's `variable_aliases` updated to map subquery RETURN aliases to `SubQueryN`.

## Contract 6: Translator — correlated subquery → scalar subquery

**Input**: `SubqueryCall` with `import_variables == ["p"]` and single-column aggregate RETURN

**Output SQL**: A scalar subquery in SELECT:
```sql
COALESCE((SELECT COUNT(...) FROM ... WHERE ... AND e.s = outer_alias.node_id), 0) AS degree
```

The COALESCE wrapping ensures FR-011 (preserve outer row with 0 when subquery matches nothing).

## Contract 7: Error — missing RETURN in subquery

**Input**: `CALL { MATCH (n) }` (no RETURN clause)

**Output**: Raises `CypherParseError("Subquery must contain a RETURN clause")`

## Contract 8: Error — scope violation

**Input**: Independent subquery referencing outer variable without WITH import

**Output**: Raises `ValueError("Variable 'x' is not defined in subquery scope")` during translation
