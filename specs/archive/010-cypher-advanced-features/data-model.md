# Data Model: Advanced Cypher AST and Mappings

## AST Entities

### 1. Updating Clauses
Extends the `QueryPart` to support graph modifications.
- **CREATE**: Defines a pattern to be inserted.
- **DELETE**: Variables to be removed. `DETACH DELETE` removes connected edges first.
- **MERGE**: Pattern to match or create. Includes `ON CREATE` and `ON MATCH` sub-clauses.
- **SET**: Update node/relationship properties or labels.
- **REMOVE**: Remove properties or labels.

### 2. Collection Expressions
Support for list literals and parameters used in `UNWIND`.
- **ListLiteral**: `[val1, val2, ...]`
- **UnwindClause**: Redefines the rowset by expanding a collection variable.

### 3. Path Algorithm Result
Structure for `shortestPath` and `allShortestPaths`.
- **Path**: `{ "nodes": [Node...], "relationships": [Rel...] }`

## IRIS SQL Mapping

### 1. DML Translation
- **CREATE (Node)**: `INSERT INTO nodes (node_id) VALUES (?)` + `INSERT INTO rdf_labels` + `INSERT INTO rdf_props`.
- **CREATE (Edge)**: `INSERT INTO rdf_edges (s, p, o_id) VALUES (?, ?, ?)`.
- **MERGE**: Procedural check via `SELECT 1 FROM nodes WHERE node_id = ?`.
    - If Match: Execute `ON MATCH` SET/REMOVE statements.
    - If Create: Execute `ON CREATE` INSERT + SET statements.
- **DELETE**: `DELETE FROM nodes WHERE node_id = ?`.
- **DETACH DELETE**: `DELETE FROM rdf_edges WHERE s = ? OR o_id = ?` followed by node delete.

### 2. UNWIND Mapping
- **SQL**: `JOIN JSON_TABLE(?, '$[*]' COLUMNS (...))`.

### 3. Algorithm Mapping
- **ShortestPath**: Recursive CTE over `rdf_edges`.
```sql
WITH RECURSIVE bfs (s, o_id, depth, path) AS (
  SELECT s, o_id, 1, CAST(s || '->' || o_id AS VARCHAR(1000))
  FROM rdf_edges WHERE s = :start
  UNION ALL
  SELECT b.s, e.o_id, b.depth + 1, b.path || '->' || e.o_id
  FROM bfs b JOIN rdf_edges e ON b.o_id = e.s
  WHERE b.depth < :max_hops AND b.path NOT LIKE '%' || e.o_id || '%'
)
SELECT TOP 1 path FROM bfs WHERE o_id = :end ORDER BY depth ASC
```

## Validation Rules
- **MERGE Uniqueness**: Requires natural keys (ID property) to be defined in the pattern.
- **Path Limits**: Max depth enforced at 10 hops to ensure performance (SC-004).
- **Transaction Scope**: All DML in a single query must be atomic.
