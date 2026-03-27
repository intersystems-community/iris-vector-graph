# Data Model: PyOps Vector Conversion Extraction

**Feature**: 011-pyops-vector-extraction  
**Date**: 2026-01-26

## Entities

### Vector (Input)

A %DynamicArray containing floating-point embedding values.

| Attribute | Type | Constraints |
|-----------|------|-------------|
| elements | float[] | Exactly EMBEDDING_DIMENSION elements (768) |
| element[i] | float | Must be numeric (int or float coercible) |

**Validation Rules**:
- Not null
- Size equals EMBEDDING_DIMENSION parameter
- All elements must be numeric (coercible to float)

**Invalid States**:
- `null` → Error: "vector required"
- `size != 768` → Error: "Expected 768-dimensional vector, got {size}"
- `element[i] not numeric` → Error: "Invalid vector element at index {i}: expected numeric value"

---

### VectorJSON (Output)

A JSON string representation suitable for SQL procedures.

| Attribute | Type | Constraints |
|-----------|------|-------------|
| value | string | Valid JSON array of floats |

**Example**: `"[0.123, -0.456, 0.789, ...]"` (768 elements)

---

## Class Structure

### Graph.KG.PyOps

```
┌─────────────────────────────────────────────────────────┐
│ Graph.KG.PyOps                                          │
├─────────────────────────────────────────────────────────┤
│ <<Parameter>>                                           │
│ + EMBEDDING_DIMENSION: Integer = 768                    │
├─────────────────────────────────────────────────────────┤
│ <<Internal>>                                            │
│ - _vectorToJson(vec: %DynamicArray): String             │
│                                                         │
│ <<Public>>                                              │
│ + VectorSearch(vec, k, labelFilter): %DynamicArray      │
│ + HybridSearch(vec, k, labelFilter, queryText): %DA     │
│ + MetaPath(srcId, preds, maxHops, dstLabel): %DA        │
└─────────────────────────────────────────────────────────┘
```

---

## Relationships

```
VectorSearch ──uses──► _vectorToJson
HybridSearch ──uses──► _vectorToJson
_vectorToJson ──reads──► EMBEDDING_DIMENSION
```

---

## State Transitions

N/A - This is a stateless conversion function.
