# Quickstart: Recursive-Descent Cypher Parser

## Overview
This feature introduces a more powerful Cypher parser that supports multi-stage queries, aggregations, and advanced matching patterns.

## New Capabilities

### 1. Chained Queries (WITH)
Find accounts with many transactions and then retrieve their owners:
```cypher
MATCH (a:Account)-[r]->(t:Transaction)
WITH a, count(t) AS txn_count
WHERE txn_count > 5
MATCH (a)-[:OWNED_BY]->(p:Person)
RETURN p.name, txn_count
```

### 2. Aggregations
Calculate summary statistics for a specific account type:
```cypher
MATCH (a:Account {account_type: 'Checking'})
RETURN count(a) AS total_accounts, avg(a.risk_score) AS avg_risk
```

### 3. Advanced Directionality
Find all nodes connected to a suspicious account regardless of direction:
```cypher
MATCH (a:Account)-[r]-(b)
WHERE a.node_id = 'ACCOUNT:MULE1'
RETURN b.node_id, type(r)
```

## Running Tests
To verify the new parser functionality:
```bash
# Unit tests for Lexer and Parser
pytest tests/unit/cypher/

# Integration tests against live IRIS
pytest tests/integration/test_cypher_rd.py
```
