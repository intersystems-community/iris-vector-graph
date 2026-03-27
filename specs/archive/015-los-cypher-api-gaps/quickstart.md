# Quickstart: LOS Cypher & API Integration

This guide shows how to use the new Cypher features and high-level APIs.

## 1. Retrieve Complete Node Data

You can now use `RETURN n` in Cypher to get the whole node object.

```python
results = engine.execute_cypher("MATCH (n:Person) WHERE n.name = 'Alice' RETURN n")
node = results[0]['n']
print(node['id'])         # 'node-123'
print(node['labels'])     # ['Person', 'Employee']
print(node['properties']) # {'name': 'Alice', 'age': '30'}
```

Or use the direct API:

```python
node = engine.get_node("node-123")
```

## 2. Store Embeddings

Store vectors without raw SQL.

```python
# Single
engine.store_embedding("node-123", [0.1, 0.2, 0.3], metadata={"source": "test"})

# Batch (Atomic)
engine.store_embeddings([
    {"node_id": "node-1", "embedding": [0.1, 0.2, 0.3]},
    {"node_id": "node-2", "embedding": [0.4, 0.5, 0.6]}
])
```

## 3. Advanced Cypher Filtering

Sort, limit, and compare.

```python
# Comparison and Pattern Matching
cypher = """
MATCH (n:Evidence)
WHERE n.confidence >= 0.7 
  AND n.source CONTAINS 'Scientific'
RETURN n.id
ORDER BY n.confidence DESC
LIMIT 5
"""
results = engine.execute_cypher(cypher)
```
