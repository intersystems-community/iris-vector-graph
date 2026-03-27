# Quickstart: PyOps Vector Conversion Extraction

**Feature**: 011-pyops-vector-extraction  
**Date**: 2026-01-26

## Overview

This refactoring extracts duplicated vector conversion logic into a shared helper method in `Graph.KG.PyOps`.

## Changes Required

### 1. Add Parameter (compile-time constant)

Add at class level in PyOps.cls:

```objectscript
Parameter EMBEDDING_DIMENSION = 768;
```

### 2. Add Internal Helper Method

Add new method `_vectorToJson`:

```python
ClassMethod _vectorToJson(vec As %DynamicArray) As %String [ Language = python, Internal ]
{
    import json
    
    if vec is None:
        raise ValueError("vector required")
    
    dim = int(iris.cls("Graph.KG.PyOps")._GetParameter("EMBEDDING_DIMENSION"))
    if vec._Size() != dim:
        raise ValueError(f"Expected {dim}-dimensional vector, got {vec._Size()}")
    
    vector_list = []
    for i in range(vec._Size()):
        val = vec._Get(i)
        try:
            vector_list.append(float(val))
        except (TypeError, ValueError):
            raise ValueError(f"Invalid vector element at index {i}: expected numeric value")
    
    return json.dumps(vector_list)
}
```

### 3. Update VectorSearch

Replace lines 9-19 with:

```python
def VectorSearch(vec, k, labelFilter):
    vector_json = iris.cls("Graph.KG.PyOps")._vectorToJson(vec)
    # ... rest of method unchanged
```

### 4. Update HybridSearch

Replace lines 40-48 with:

```python
def HybridSearch(vec, k, labelFilter, queryText):
    vector_json = iris.cls("Graph.KG.PyOps")._vectorToJson(vec)
    # ... rest of method unchanged
```

## Verification

1. **Before refactoring**: Run existing VectorSearch and HybridSearch with valid/invalid inputs, record results
2. **After refactoring**: Run same tests, verify identical results
3. **Check**: Confirm only one location contains conversion logic

## Files Modified

- `iris_src/src/Graph/KG/PyOps.cls` (only file)
