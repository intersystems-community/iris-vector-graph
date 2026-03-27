# Research: PyOps Vector Conversion Extraction

**Feature**: 011-pyops-vector-extraction  
**Date**: 2026-01-26

## Research Summary

This is a focused refactoring task with minimal technical unknowns. The existing codebase already demonstrates the patterns needed.

---

## Research Item 1: InterSystems IRIS Python Method Visibility

**Question**: How to mark a Python method as Internal in IRIS ObjectScript classes?

**Decision**: Use `[ Internal ]` keyword after method signature

**Rationale**: This is the standard IRIS convention visible in the existing codebase. Internal methods are excluded from documentation and signal they are not part of the public API.

**Alternatives Considered**:
- Underscore prefix (`_methodName`) - Python convention but not IRIS-idiomatic
- Private keyword - Does not exist in IRIS ObjectScript

**Implementation Pattern**:
```
ClassMethod _vectorToJson(vec As %DynamicArray) As %String [ Language = python, Internal ]
```

---

## Research Item 2: Parameter Definition in IRIS Classes

**Question**: How to define a compile-time constant for embedding dimension?

**Decision**: Use IRIS `Parameter` declaration at class level

**Rationale**: Parameters in IRIS are compile-time constants, accessible from both ObjectScript and embedded Python methods via `cls._GetParameter("NAME")` or direct reference.

**Alternatives Considered**:
- Python module-level constant - Would require separate Python file
- Class property with default - Not compile-time, adds runtime overhead

**Implementation Pattern**:
```
Parameter EMBEDDING_DIMENSION = 768;

// Access in Python:
dim = int(iris.cls("Graph.KG.PyOps")._GetParameter("EMBEDDING_DIMENSION"))
```

---

## Research Item 3: Existing Vector Conversion Pattern

**Question**: What is the current implementation that needs extraction?

**Decision**: Extract the 4-line conversion loop into helper method

**Current Pattern** (duplicated in VectorSearch lines 15-19 and HybridSearch lines 43-48):
```python
vector_list = []
for i in range(vec._Size()):
    vector_list.append(float(vec._Get(i)))
vector_json = json.dumps(vector_list)
```

**New Pattern**:
```python
@classmethod
def _vectorToJson(cls, vec):
    """Convert %DynamicArray to JSON string with validation."""
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
```

---

## Research Item 4: Error Message Consistency

**Question**: What error messages exist currently and how to unify them?

**Decision**: Standardize on the more descriptive format

**Current State**:
- VectorSearch: `"vector required"` and `"Expected 768-dimensional vector..."`
- HybridSearch: `"Expected 768-dimensional vector..."` (combines null and dimension check)

**Unified Messages**:
| Condition | Message |
|-----------|---------|
| Null vector | `"vector required"` |
| Wrong dimension | `"Expected {dim}-dimensional vector, got {actual}"` |
| Non-numeric element | `"Invalid vector element at index {i}: expected numeric value"` |

---

## Unknowns Resolved

All NEEDS CLARIFICATION items from Technical Context have been resolved:
- ✅ Method visibility pattern identified
- ✅ Constant definition pattern identified  
- ✅ Existing code pattern documented
- ✅ Error message strategy unified
