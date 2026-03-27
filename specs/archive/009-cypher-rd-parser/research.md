# Research: Cypher Recursive-Descent Parser and Multi-Stage Translation

## Decisions

### 1. Parser Implementation Pattern
- **Decision**: Hand-written recursive-descent parser using a dedicated `Lexer` and `Parser` class.
- **Rationale**: Python 3.11 features like `match/case` and `slots=True` on `@dataclass` provide a clean and high-performance way to implement hand-written parsers. This avoids the dependency on heavy parser generators while fulfilling the "recursive-descent" requirement.
- **Pattern**:
    - `Token` (dataclass with `slots=True`, `frozen=True`)
    - `Lexer` (cursor-based, `match/case` for dispatch)
    - `Parser` (grammar rule methods, `peek()`/`expect()` pattern)

### 2. Multi-stage Query Mapping (WITH clause)
- **Decision**: Map Cypher `WITH` clauses to SQL Common Table Expressions (CTEs) in InterSystems IRIS 2025.1+.
- **Rationale**: CTEs provide a linear, 1:1 mapping with Cypher stages, making the generated SQL much more readable and maintainable than nested subqueries. IRIS 2025.1+ has optimized CTE support with predicate pushdown and adaptive execution.
- **Variable Scoping**: Only variables explicitly projected in the CTE `SELECT` list will be available to subsequent stages, enforcing Cypher's strict scoping rules.

### 3. Aggregation and Grouping
- **Decision**: The translator will explicitly track non-aggregated expressions in `RETURN` and `WITH` clauses to generate required `GROUP BY` clauses.
- **Rationale**: IRIS SQL requires explicit `GROUP BY` when aggregates are mixed with non-aggregates to achieve Cypher's implicit grouping behavior.
- **Function Mapping**: 
    - `count()`, `sum()`, `avg()`, `min()`, `max()` $\rightarrow$ standard SQL equivalents.
    - `collect()` $\rightarrow$ `JSON_ARRAYAGG()` (IRIS-native JSON aggregation).

## Unknowns Resolved
- **Hand-written parser vs Library**: Decision made to implement hand-written RD parser for flexibility and alignment with instructions.
- **WITH clause performance**: CTEs confirmed as performant and idiomatic for IRIS 2025.1+.
- **Grouping logic**: Strategy identified for explicit `GROUP BY` generation based on AST inspection.
