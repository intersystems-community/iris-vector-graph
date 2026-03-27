# Baseline Behavior: PyOps Vector Methods

**Captured**: 2026-01-26  
**Source**: `iris_src/src/Graph/KG/PyOps.cls` (pre-refactoring)

## T001: VectorSearch with Valid 768-dim Vector

**Input**: Valid %DynamicArray with 768 float elements, k=50, label=""

**Expected Behavior**:
1. Validates vector is not null and size > 0
2. Validates vector size == 768
3. Converts %DynamicArray to Python list of floats
4. Serializes to JSON string
5. Calls SQL procedure `kg_KNN_VEC(vector_json, k, label)`
6. Returns %DynamicArray of {id, score} objects

**Return Format**:
```json
[
  {"id": "entity_id_1", "score": 0.95},
  {"id": "entity_id_2", "score": 0.87},
  ...
]
```

---

## T002: HybridSearch with Valid 768-dim Vector

**Input**: Valid %DynamicArray with 768 float elements, text="query", k=50, c=60

**Expected Behavior**:
1. Validates vector is not null and size == 768 (combined check)
2. Converts %DynamicArray to Python list of floats
3. Serializes to JSON string
4. Calls SQL procedure `kg_RRF_FUSE(k, 200, 200, c, vector_json, text)`
5. Returns %DynamicArray of {id, score, extras: {vs, bm25}} objects

**Return Format**:
```json
[
  {"id": "entity_id_1", "score": 0.92, "extras": {"vs": 0.88, "bm25": 0.75}},
  {"id": "entity_id_2", "score": 0.85, "extras": {"vs": 0.80, "bm25": 0.70}},
  ...
]
```

---

## T003: Error Behavior with Null Vector

### VectorSearch

**Input**: `vec = None` or `vec._Size() == 0`

**Error Message**: `"vector required"`

**Code Location**: Line 9-10
```python
if vec is None or vec._Size() == 0:
    raise ValueError("vector required")
```

### HybridSearch

**Input**: `vec = None`

**Error Message**: `"Expected 768-dimensional vector (biomedical embeddings like BioBERT/PubMedBERT)"`

**Code Location**: Line 40-41
```python
if vec is None or vec._Size() != 768:
    raise ValueError("Expected 768-dimensional vector (biomedical embeddings like BioBERT/PubMedBERT)")
```

**Note**: HybridSearch has DIFFERENT error message for null vector than VectorSearch. This inconsistency will be fixed by the refactoring.

---

## T004: Error Behavior with Wrong-Dimension Vector

### VectorSearch

**Input**: %DynamicArray with size != 768 (e.g., 512 elements)

**Error Message**: `"Expected 768-dimensional vector (biomedical embeddings like BioBERT/PubMedBERT)"`

**Code Location**: Line 11-12
```python
if vec._Size() != 768:
    raise ValueError("Expected 768-dimensional vector (biomedical embeddings like BioBERT/PubMedBERT)")
```

### HybridSearch

**Input**: %DynamicArray with size != 768

**Error Message**: `"Expected 768-dimensional vector (biomedical embeddings like BioBERT/PubMedBERT)"`

**Code Location**: Line 40-41 (same combined check)

**Note**: Both methods have identical message for wrong dimension (good), but VectorSearch has a separate message for null (inconsistent).

---

## Baseline Summary

| Scenario | VectorSearch | HybridSearch | Consistent? |
|----------|--------------|--------------|-------------|
| Valid 768-dim | Returns results | Returns results | Yes |
| Null vector | "vector required" | "Expected 768-dimensional..." | **NO** |
| Empty vector (size 0) | "vector required" | "Expected 768-dimensional..." | **NO** |
| Wrong dimension | "Expected 768-dimensional..." | "Expected 768-dimensional..." | Yes |

**Key Findings**:
1. Null/empty vector handling is INCONSISTENT between methods
2. Dimension error messages are consistent
3. Refactoring will unify all error handling through shared helper
