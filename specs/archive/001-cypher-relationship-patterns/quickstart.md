# Quickstart: Cypher Relationship Patterns

## Overview
This feature enables more flexible relationship querying in the Cypher API. You can now use multiple relationship types, variable bindings for relationships, and untyped relationship queries.

## Examples

### 1. Multi-Type Relationships
Find transactions that are either outgoing OR incoming for a specific account:
```cypher
MATCH (t:Transaction)-[:FROM_ACCOUNT|TO_ACCOUNT]->(a:Account)
WHERE a.node_id = 'ACCOUNT:MULE1'
RETURN t.amount, t.timestamp
```

### 2. Untyped Relationships
Find all nodes connected to a suspicious account regardless of relationship type:
```cypher
MATCH (a:Account)-[r]->(b)
WHERE a.node_id = 'ACCOUNT:MULE1'
RETURN type(r), b.node_id
```

### 3. Relationship Variables
Access relationship properties or metadata:
```cypher
MATCH (t:Transaction)-[r:FROM_ACCOUNT]->(a:Account)
RETURN r, t.amount
```

## Supported Syntax
- `MATCH (a)-[:T1]->(b)` : Single type
- `MATCH (a)-[:T1|T2]->(b)` : Multiple types
- `MATCH (a)-[]->(b)` : Any type
- `MATCH (a)-[r:T1]->(b)` : Variable binding
- `MATCH (a)-[r]->(b)` : Variable binding, any type
- `MATCH (a)-[:T1*1..3]->(b)` : Variable-length paths (preserved from MVP)
