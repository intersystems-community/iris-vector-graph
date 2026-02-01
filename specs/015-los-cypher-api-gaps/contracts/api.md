# API Contract: IRISGraphEngine Enhancements

## 1. get_node

Retrieves a complete node with all its labels and properties.

```python
def get_node(self, node_id: str) -> Optional[Dict[str, Any]]:
```

**Parameters**:
- `node_id`: The unique identifier of the node.

**Returns**:
- `Dict[str, Any]`: A dictionary containing:
  - `id`: The node identifier.
  - `labels`: A list of strings representing the node's labels.
  - `properties`: A dictionary of key-value pairs (excluding id and labels).
- `None`: If the node does not exist.

---

## 2. store_embedding

Stores a vector embedding for a specific node.

```python
def store_embedding(self, node_id: str, embedding: List[float], metadata: Optional[Dict[str, Any]] = None) -> bool:
```

**Parameters**:
- `node_id`: The unique identifier of the node.
- `embedding`: A list of floats representing the vector.
- `metadata`: Optional JSON-serializable dictionary.

**Returns**:
- `bool`: `True` if successful.

**Exceptions**:
- `ValueError`: If the node does not exist or embedding dimensions are invalid.

---

## 3. store_embeddings (Batch)

Stores multiple embeddings in a single atomic transaction.

```python
def store_embeddings(self, items: List[Dict[str, Any]]) -> bool:
```

**Parameters**:
- `items`: A list of dictionaries, each containing:
  - `node_id`: Node identifier.
  - `embedding`: Vector data.
  - `metadata`: (Optional) Metadata.

**Returns**:
- `bool`: `True` if all embeddings were stored successfully.

**Exceptions**:
- `ValueError`: If any node does not exist, any dimension is invalid, or if the batch operation fails (atomic rollback).
