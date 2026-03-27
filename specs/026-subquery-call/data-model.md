# Data Model: Subquery Clauses (CALL { ... })

**Feature**: 026-subquery-call | **Date**: 2026-03-27

## New AST Entity: SubqueryCall

```
SubqueryCall
├── inner_query: CypherQuery          # The complete parsed subquery
├── import_variables: List[str]       # Variables imported via WITH (empty = independent)
├── in_transactions: bool             # IN TRANSACTIONS flag (Phase 1: no-op)
└── transactions_batch_size: int?     # OF N ROWS (Phase 1: ignored)
```

**Relationships**:
- Owned by `QueryPart` via extended `clauses` union type
- Contains a `CypherQuery` (recursive structure — subquery is itself a full query)

**Validation**:
- `inner_query` must have a `return_clause` (FR-008: subqueries must yield results)
- `import_variables` must be non-empty if the inner query references outer scope variables (FR-003/FR-004)

## Modified Entity: QueryPart

```
QueryPart
├── clauses: List[MatchClause | UnwindClause | UpdatingClause | WhereClause | SubqueryCall]  # EXTENDED
└── with_clause: WithClause?
```

**Backward compatibility**: `SubqueryCall` is added to the Union type. Existing code that only handles `MatchClause`/`UnwindClause`/etc. continues to work — `SubqueryCall` clauses are only present when parsed from `CALL { ... }` syntax.

## New Lexer Tokens

```
TRANSACTIONS → keyword "TRANSACTIONS"
ROWS         → keyword "ROWS"
```

`IN`, `OF`, `CALL`, `LBRACE`, `RBRACE` already exist.

## Translation Context Extensions

No new fields needed — subquery translation creates a child `TranslationContext` with selective variable inheritance. The CTE or scalar subquery is injected into the parent context's `stages` or `select_items`.

## No Schema Changes

This feature adds no tables, columns, or indexes to IRIS. All changes are in the Cypher parser/translator layer.
