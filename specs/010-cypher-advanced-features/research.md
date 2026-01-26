# Research: Advanced Cypher Features Implementation

## Decisions

### 1. Cypher `MERGE` Implementation
- **Decision**: Implement `MERGE` using a multi-statement transaction block.
- **Rationale**: InterSystems IRIS `INSERT OR UPDATE` does not support Cypher's conditional `ON CREATE` and `ON MATCH` logic. A transaction ensures atomicity while procedural logic (or multiple SQL statements) allows for the differentiation required.
- **Schema Migration**: To prevent duplicates and enable idempotent upserts, unique constraints will be added to:
    - `rdf_props`: `(s, "key")`
    - `rdf_labels`: `(s, label)`
- **Alternatives Considered**: Using nested `CASE` statements in an `UPDATE`, but this is complex to generate and harder to optimize than discrete transactional steps.

### 2. Cypher `UNWIND` Implementation
- **Decision**: Map `UNWIND` to IRIS SQL **`JSON_TABLE`**.
- **Rationale**: `JSON_TABLE` (introduced in IRIS 2024.1) is the native and most efficient way to turn a JSON array (passed as a parameter) into a relational rowset. This allows Cypher parameters to be treated as tables for JOINs and bulk creations.
- **Implementation**: The translator will serialize the `UNWIND` collection into a JSON string and generate a `JSON_TABLE` subquery.
- **Alternatives Considered**: Procedural unwinding in Python, but this requires multiple database round-trips which degrades performance for bulk operations.

### 3. Path Finding (`shortestPath`)
- **Decision**: Implement `shortestPath` using **Recursive CTEs** in IRIS SQL.
- **Rationale**: Recursive CTEs (supported in IRIS 2025.1+) provide a performant, declarative way to perform Breadth-First Search (BFS) for the shortest path. This leverages the IRIS SQL engine's optimization for joins.
- **Limit**: Max hops (default 10) will be enforced via the recursive depth limit to prevent infinite loops and excessive resource consumption.
- **Alternatives Considered**: Python-based BFS using multiple SQL queries, but this is significantly slower due to network latency and data transfer overhead.

## Unknowns Resolved
- **Transactional Consistency**: Confirmed that `Read Committed` is the standard and sufficient isolation level for graph operations in IRIS.
- **Bulk Creation**: `UNWIND` + `CREATE` (via `JSON_TABLE`) will support the required 100+ node batch creation target.
- **Algorithmic Parity**: Recursive CTEs provide functional parity with Neo4j's basic `shortestPath` implementation.
