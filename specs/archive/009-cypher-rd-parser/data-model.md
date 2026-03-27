# Data Model: Cypher AST Enhancements

## Entities

### Lexer
Internal component for tokenization.
- `source`: Input query string.
- `tokens`: Stream of `Token` objects.

### Token
Atomic unit of Cypher syntax.
- `kind`: Type of token (e.g., `MATCH`, `WITH`, `IDENTIFIER`, `STRING_LITERAL`).
- `value`: Optional text value.
- `pos`: Source position for error reporting.

### Abstract Syntax Tree (AST)
The enriched AST will include new nodes for multi-stage queries.

#### QueryPart
Represents a single stage in a chained query.
- `match_clauses`: List of `MatchClause`.
- `where_clause`: Optional `WhereClause`.
- `with_clause`: Optional `WithClause` (pipes results to next `QueryPart`).

#### WithClause
Projections and aggregations that redefine the available scope.
- `items`: List of `ReturnItem`.
- `where_clause`: Optional filter applied AFTER projection.
- `distinct`: Boolean flag.

#### AggregationFunction
Standard Cypher aggregates.
- `name`: `count`, `sum`, `avg`, `min`, `max`, `collect`.
- `expression`: Argument to aggregate.
- `distinct`: Boolean flag.

### Root Entity: Statement
The top-level query structure.
- `query_parts`: Ordered list of `QueryPart`.
- `return_clause`: Final projection.
- `order_by_clause`: Final sorting.
- `skip/limit`: Final pagination.

## Validation Rules
- **Variable Scoping**: A `QueryPart` can only access variables explicitly projected by the preceding `WITH` clause.
- **Aggregation**: If a `WITH` or `RETURN` clause contains an `AggregationFunction`, all non-aggregated items become grouping keys.
- **Direction**: Support `<-[]-`, `-[]->`, and `-[]-` directions.

## Database Mapping (CTEs)
Each `QueryPart` maps to a Common Table Expression (CTE) in IRIS SQL.
- `Stage1 AS (SELECT ... FROM nodes ...)`
- `Stage2 AS (SELECT ... FROM Stage1 ...)`
- `SELECT ... FROM StageN`
