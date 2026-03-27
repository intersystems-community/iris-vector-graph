# Research: Cypher Relationship Pattern Enhancements

## Decisions

### 1. Regex Pattern for Relationships
- **Decision**: Update `REL_PATTERN` to `r'-\[(?:(\w+))?(?::([\w|]+))?(?:\*(\d+)\.\.(\d+))?\]->'`.
- **Rationale**: This pattern correctly handles optional variable bindings, single or multiple (pipe-separated) relationship types, and variable-length path markers.
- **Alternatives Considered**: Using multiple regexes for different cases, but a single comprehensive regex with optional groups is more maintainable for the MVP parser.

### 2. SQL Translation for Multi-Type Relationships
- **Decision**: Translate multiple types to an `IN` clause: `edge_alias.p IN (?, ?, ...)`.
- **Rationale**: Standard SQL way to handle multiple OR conditions on the same column.
- **Alternatives Considered**: Multiple `OR` conditions (`p = 'T1' OR p = 'T2'`), but `IN` is cleaner and more idiomatic for IRIS SQL.

### 3. JOIN Ordering and Correctness
- **Decision**: Use explicit JOIN conditions linking the target node to the edge.
- **Rationale**: The current implementation produces a cross-join for the target node (`JOIN nodes n1`) which is only narrowed down later. A more robust approach is `JOIN nodes {target_alias} ON {target_alias}.node_id = {edge_alias}.o_id`.
- **Alternatives Considered**: Keeping current structure and relying on WHERE clause, but explicit JOIN ON is safer and often better optimized by IRIS.

### 4. Graph Pattern Parsing
- **Decision**: Switch from `re.split` to a sequential parsing approach or a more robust split pattern that handles multiple arrows and nodes in a chain.
- **Rationale**: `re.split` with a fixed arrow pattern is fragile for complex paths. Sequential extraction of nodes and relationships ensures the order is preserved and all elements are captured.
- **Alternatives Considered**: Using a full Cypher grammar (like Antlr), but that's out of scope for the MVP "regex-based" parser.

## Unknowns Resolved
- **Multi-type support**: Regex verified; SQL `IN` clause identified.
- **JOIN order**: Fixed by moving the target node connection into the JOIN ON clause.
- **Variable length**: Regex captures it; translator logic needs to be aware of it (even if simplified for MVP).
