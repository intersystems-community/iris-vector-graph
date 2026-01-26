# Quickstart: Advanced Cypher Features

## 1. Graph Modification (Write Ops)

### Create a Node
```cypher
CREATE (a:Account {id: 'ACC1', type: 'Savings'})
```

### Idempotent Merge
```cypher
MERGE (a:Account {id: 'ACC1'})
ON CREATE SET a.created = '2026-01-25'
ON MATCH SET a.last_updated = '2026-01-25'
```

### Detach Delete
```cypher
MATCH (a:Account {id: 'ACC1'})
DETACH DELETE a
```

## 2. Advanced Queries

### Optional Match (Resilient Traversal)
```cypher
MATCH (a:Account)
OPTIONAL MATCH (a)-[:OWNED_BY]->(p:Person)
RETURN a.id, p.name
```

### Unwind (Bulk Processing)
```cypher
UNWIND ['ACC1', 'ACC2', 'ACC3'] AS id
CREATE (:Account {id: id})
```

## 3. Graph Algorithms

### Shortest Path
```cypher
MATCH p = shortestPath((a:Account {id: 'A1'})-[:TRANSFER*..10]->(b:Account {id: 'A2'}))
RETURN p
```

## Running Verification
```bash
# Integration tests for Advanced Features
pytest tests/integration/test_cypher_advanced.py
```
