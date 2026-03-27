# Data Model: openCypher Query Endpoint

**Feature**: openCypher-to-SQL translation
**Date**: 2025-10-02
**Status**: Phase 1 Design

---

## 1. Cypher AST (Abstract Syntax Tree)

### 1.1 CypherQuery

Top-level query representation.

**Fields**:
```python
@dataclass
class CypherQuery:
    """Root AST node for a complete Cypher query"""
    match_clauses: List[MatchClause]
    where_clause: Optional[WhereClause]
    return_clause: ReturnClause
    order_by_clause: Optional[OrderByClause]
    skip_clause: Optional[int]
    limit_clause: Optional[int]
    union_clause: Optional[UnionClause]
```

**Relationships**:
- Contains 1+ `MatchClause`
- Contains 0-1 `WhereClause`
- Contains exactly 1 `ReturnClause`
- Contains 0-1 `OrderByClause`
- Contains 0-1 `UnionClause`

**Validation Rules**:
- At least one MATCH clause required (except for CALL-only queries)
- RETURN clause is mandatory
- SKIP/LIMIT must be non-negative integers

---

### 1.2 MatchClause

Represents a MATCH or OPTIONAL MATCH clause.

**Fields**:
```python
@dataclass
class MatchClause:
    """MATCH or OPTIONAL MATCH pattern"""
    optional: bool  # True for OPTIONAL MATCH
    pattern: GraphPattern
```

**Relationships**:
- Contains exactly 1 `GraphPattern`

---

### 1.3 GraphPattern

A sequence of nodes and relationships forming a path pattern.

**Fields**:
```python
@dataclass
class GraphPattern:
    """Graph pattern: (n1)-[r]->(n2)-[r2]->(n3) etc."""
    nodes: List[NodePattern]
    relationships: List[RelationshipPattern]
```

**Relationships**:
- Contains 1+ `NodePattern`
- Contains 0+ `RelationshipPattern` (len(relationships) = len(nodes) - 1)

**Validation Rules**:
- Number of relationships = number of nodes - 1
- Relationships must connect adjacent nodes in sequence

---

### 1.4 NodePattern

Represents a node in a MATCH pattern.

**Fields**:
```python
@dataclass
class NodePattern:
    """Node pattern: (variable:Label {property: value})"""
    variable: str  # Variable name (e.g., 'n', 'protein')
    labels: List[str]  # Node labels (e.g., ['Protein'])
    properties: Dict[str, Any]  # Property filters {key: value}
```

**Example Cypher**: `(p:Protein {id: 'PROTEIN:TP53'})`
**Parsed as**:
```python
NodePattern(
    variable='p',
    labels=['Protein'],
    properties={'id': 'PROTEIN:TP53'}
)
```

**Validation Rules**:
- Variable name must be unique within query scope
- Labels list can be empty (unlabeled node)
- Property values must be primitive types (string, int, float, bool)

---

### 1.5 RelationshipPattern

Represents a relationship in a MATCH pattern.

**Fields**:
```python
@dataclass
class RelationshipPattern:
    """Relationship: -[r:TYPE]-> or -[r:TYPE*1..3]->"""
    variable: Optional[str]  # Optional variable name
    types: List[str]  # Relationship types
    direction: str  # 'outgoing' | 'incoming' | 'bidirectional'
    properties: Dict[str, Any]  # Property filters
    variable_length: Optional[VariableLength]  # For *1..3 syntax
```

**VariableLength**:
```python
@dataclass
class VariableLength:
    min_hops: int  # Minimum path length
    max_hops: int  # Maximum path length
```

**Example Cypher**: `-[r:INTERACTS_WITH*1..3]->`
**Parsed as**:
```python
RelationshipPattern(
    variable='r',
    types=['INTERACTS_WITH'],
    direction='outgoing',
    properties={},
    variable_length=VariableLength(min_hops=1, max_hops=3)
)
```

**Validation Rules**:
- Direction must be: 'outgoing', 'incoming', or 'bidirectional'
- Variable length: min_hops ≥ 1, max_hops ≤ 10 (configurable), min_hops ≤ max_hops
- Types list can be empty (any relationship type)

---

### 1.6 WhereClause

Boolean expression for filtering.

**Fields**:
```python
@dataclass
class WhereClause:
    """WHERE clause with boolean expressions"""
    expression: BooleanExpression
```

**BooleanExpression** (recursive):
```python
@dataclass
class BooleanExpression:
    operator: str  # 'AND' | 'OR' | 'NOT' | 'EQUALS' | 'LIKE' | 'IN' | etc.
    operands: List[Union[BooleanExpression, PropertyReference, Literal]]
```

**Example Cypher**: `WHERE p.name LIKE '%cancer%' AND p.score > 0.5`
**Parsed as**:
```python
BooleanExpression(
    operator='AND',
    operands=[
        BooleanExpression(
            operator='LIKE',
            operands=[PropertyReference('p', 'name'), Literal('%cancer%')]
        ),
        BooleanExpression(
            operator='GT',
            operands=[PropertyReference('p', 'score'), Literal(0.5)]
        )
    ]
)
```

---

### 1.7 ReturnClause

Specifies query output columns.

**Fields**:
```python
@dataclass
class ReturnClause:
    """RETURN clause with projections"""
    distinct: bool
    items: List[ReturnItem]
```

**ReturnItem**:
```python
@dataclass
class ReturnItem:
    expression: Expression  # Property, variable, aggregation, etc.
    alias: Optional[str]  # AS alias
```

**Example Cypher**: `RETURN DISTINCT p.name AS protein, count(r) AS interactions`
**Parsed as**:
```python
ReturnClause(
    distinct=True,
    items=[
        ReturnItem(
            expression=PropertyReference('p', 'name'),
            alias='protein'
        ),
        ReturnItem(
            expression=AggregationFunction('count', [Variable('r')]),
            alias='interactions'
        )
    ]
)
```

---

### 1.8 CypherProcedureCall

Custom procedure invocation (e.g., vector search).

**Fields**:
```python
@dataclass
class CypherProcedureCall:
    """CALL procedure syntax"""
    procedure_name: str  # e.g., 'db.index.vector.queryNodes'
    arguments: List[Any]  # Procedure arguments
    yield_items: Optional[List[str]]  # YIELD variable names
```

**Example Cypher**: `CALL db.index.vector.queryNodes('protein_embeddings', 10, [0.1, 0.2]) YIELD node, score`
**Parsed as**:
```python
CypherProcedureCall(
    procedure_name='db.index.vector.queryNodes',
    arguments=['protein_embeddings', 10, [0.1, 0.2, ...]],
    yield_items=['node', 'score']
)
```

---

## 2. SQL Translation Artifacts

### 2.1 SQLQuery

Generated SQL query with parameters.

**Fields**:
```python
@dataclass
class SQLQuery:
    """Translated SQL query ready for execution"""
    sql: str  # SQL query string with placeholders
    parameters: List[Any]  # Parameterized values
    query_metadata: QueryMetadata
```

**Example**:
```python
SQLQuery(
    sql="""
        SELECT DISTINCT n.node_id
        FROM nodes n
        INNER JOIN rdf_labels l ON n.node_id = l.s AND l.label = ?
        LIMIT ?
    """,
    parameters=['Protein', 10],
    query_metadata=QueryMetadata(...)
)
```

---

### 2.2 QueryMetadata

Metadata about query execution plan.

**Fields**:
```python
@dataclass
class QueryMetadata:
    """Query execution metadata"""
    estimated_rows: Optional[int]  # Query planner estimate
    index_usage: List[str]  # Indexes expected to be used
    optimization_applied: List[str]  # Applied optimizations
    complexity_score: int  # 1-10 scale based on joins, recursion
```

**Example**:
```python
QueryMetadata(
    estimated_rows=150,
    index_usage=['idx_rdf_labels_label', 'pk_nodes'],
    optimization_applied=['label_pushdown', 'property_pushdown'],
    complexity_score=3
)
```

---

### 2.3 TranslationContext

Context maintained during AST-to-SQL translation.

**Fields**:
```python
@dataclass
class TranslationContext:
    """Stateful context for SQL generation"""
    variable_mappings: Dict[str, str]  # Cypher var → SQL alias
    temp_tables: List[str]  # Temporary tables created
    join_sequence: List[str]  # JOIN clauses built incrementally
    where_conditions: List[str]  # WHERE conditions
    parameters: List[Any]  # Accumulated parameters
    subquery_counter: int  # For unique subquery aliases
```

**Usage**: Passed through translator to accumulate SQL fragments.

---

## 3. Query Results

### 3.1 QueryResult

Successful query execution result.

**Fields**:
```python
@dataclass
class QueryResult:
    """Successful Cypher query result"""
    columns: List[str]  # Column names
    rows: List[List[Any]]  # Data rows
    row_count: int  # Total rows returned
    execution_time_ms: float  # Total execution time
    translation_time_ms: float  # Cypher→SQL translation time
    trace_id: str  # For debugging/logging
```

**JSON Schema** (for REST API):
```json
{
  "type": "object",
  "properties": {
    "columns": {"type": "array", "items": {"type": "string"}},
    "rows": {"type": "array", "items": {"type": "array"}},
    "rowCount": {"type": "integer"},
    "executionTimeMs": {"type": "number"},
    "translationTimeMs": {"type": "number"},
    "traceId": {"type": "string"}
  },
  "required": ["columns", "rows", "rowCount", "executionTimeMs", "traceId"]
}
```

**Example**:
```json
{
  "columns": ["protein_id", "interaction_count"],
  "rows": [
    ["PROTEIN:TP53", 127],
    ["PROTEIN:EGFR", 93]
  ],
  "rowCount": 2,
  "executionTimeMs": 12.3,
  "translationTimeMs": 2.1,
  "traceId": "cypher-20251002-abc123"
}
```

---

### 3.2 QueryError

Failed query execution result.

**Fields**:
```python
@dataclass
class QueryError:
    """Query execution error"""
    error_type: str  # 'syntax' | 'translation' | 'execution' | 'timeout'
    message: str  # Human-readable error message
    line: Optional[int]  # Line number in Cypher query
    column: Optional[int]  # Column number in Cypher query
    error_code: str  # Machine-readable error code
    suggestion: Optional[str]  # How to fix the error
    trace_id: str  # For debugging
```

**JSON Schema** (for REST API):
```json
{
  "type": "object",
  "properties": {
    "errorType": {"type": "string", "enum": ["syntax", "translation", "execution", "timeout"]},
    "message": {"type": "string"},
    "line": {"type": "integer"},
    "column": {"type": "integer"},
    "errorCode": {"type": "string"},
    "suggestion": {"type": "string"},
    "traceId": {"type": "string"}
  },
  "required": ["errorType", "message", "errorCode", "traceId"]
}
```

**Example**:
```json
{
  "errorType": "syntax",
  "message": "Unexpected token 'RETRUN' at line 2, column 1",
  "line": 2,
  "column": 1,
  "errorCode": "SYNTAX_ERROR",
  "suggestion": "Did you mean 'RETURN'?",
  "traceId": "cypher-20251002-def456"
}
```

---

## 4. Configuration

### 4.1 CypherConfig

Configuration for Cypher endpoint behavior.

**Fields**:
```python
@dataclass
class CypherConfig:
    """Cypher endpoint configuration"""
    max_query_depth: int = 10  # Max hops for variable-length paths
    query_timeout_seconds: int = 30  # Query execution timeout
    enable_query_cache: bool = True  # Cache translated SQL
    cache_size: int = 1000  # LRU cache size
    enable_optimization: bool = True  # Apply SQL optimizations
    enable_index_hints: bool = False  # Inject index hints (experimental)
    log_queries: bool = True  # Log all queries
    log_performance: bool = True  # Log execution times
```

---

## 5. Entity Relationships Diagram

```
┌─────────────────┐
│  CypherQuery    │
└────────┬────────┘
         │ 1
         │ contains
         │ 1..*
┌────────▼────────┐       ┌──────────────┐
│  MatchClause    │───────│ GraphPattern │
└─────────────────┘  1    └──────┬───────┘
                                  │ contains
                    ┌─────────────┴──────────────┐
                    │ 1..*                  0..* │
         ┌──────────▼──────────┐   ┌─────────────▼──────────────┐
         │  NodePattern        │   │ RelationshipPattern         │
         └─────────────────────┘   └────────────────────────────┘
                                             │ 0..1
                                   ┌─────────▼─────────┐
                                   │ VariableLength    │
                                   └───────────────────┘

┌─────────────────┐
│  CypherQuery    │
└────────┬────────┘
         │ 1
         │ translates to
         │ 1
┌────────▼────────┐       ┌──────────────────┐
│   SQLQuery      │───────│ QueryMetadata    │
└─────────────────┘  1    └──────────────────┘
         │
         │ executes to
         │ 1
    ┌────▼────┐
    │ Result  │
    └────┬────┘
         │
    ┌────┴─────────────┐
    │                  │
┌───▼──────────┐  ┌───▼──────────┐
│ QueryResult  │  │ QueryError   │
└──────────────┘  └──────────────┘
```

---

## 6. State Transitions

### Query Processing State Machine

```
[Received Cypher Query]
         │
         ▼
    [Parsing] ──────────► [Syntax Error] ──► [Return QueryError]
         │ success
         ▼
   [AST Validation] ─────► [Semantic Error] ─► [Return QueryError]
         │ valid
         ▼
  [SQL Translation] ──────► [Translation Error] ─► [Return QueryError]
         │ success
         ▼
   [Optimization] ────────► (skip if disabled)
         │
         ▼
  [SQL Execution] ────────► [Execution Error] ──► [Return QueryError]
         │ success          [Timeout Error]
         ▼
  [Result Formatting]
         │
         ▼
  [Return QueryResult]
```

**Valid Transitions**:
1. Parsing → AST Validation (on success)
2. Parsing → Syntax Error (on failure)
3. AST Validation → SQL Translation (on success)
4. AST Validation → Semantic Error (on failure)
5. SQL Translation → Optimization (on success)
6. SQL Translation → Translation Error (on failure)
7. Optimization → SQL Execution (always)
8. SQL Execution → Result Formatting (on success)
9. SQL Execution → Execution Error / Timeout Error (on failure)
10. Result Formatting → Return QueryResult (always)
11. Any Error State → Return QueryError (terminal)

---

## 7. Validation Rules Summary

### Structural Validation

- **CypherQuery**: Must have ≥1 MATCH clause and exactly 1 RETURN clause
- **GraphPattern**: len(relationships) = len(nodes) - 1
- **VariableLength**: 1 ≤ min_hops ≤ max_hops ≤ 10 (configurable)
- **NodePattern**: Variable names unique within query scope
- **RelationshipPattern**: Direction ∈ {'outgoing', 'incoming', 'bidirectional'}

### Semantic Validation

- **Variable references**: All variables in WHERE/RETURN defined in MATCH
- **Property references**: Property exists on referenced variable
- **Aggregation**: Aggregation functions only in RETURN clause
- **Type compatibility**: Boolean expressions evaluate to boolean

### Performance Validation

- **Query depth**: Variable-length paths ≤ max_query_depth
- **Complexity**: Reject queries with >50 nodes (prevent Cartesian explosion)
- **Timeout**: Enforce query_timeout_seconds during execution

---

**Data Model Complete**: Ready for contract generation and implementation.
