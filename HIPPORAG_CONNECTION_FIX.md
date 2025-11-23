# HippoRAG Connection Issues - iris-vector-graph Fix

**Date**: 2025-11-23
**iris-vector-graph version**: 1.1.7 (pending)
**Commit**: 25e3a3f

---

## Issue Summary

The HippoRAG team reported that **Functional Index PPR was falling back to Pure Python PPR** due to a connection type mismatch error:

```
TypeError: argument 1 must be irissdk.IRISConnection, not IRISConnection
```

This occurred when `iris-vector-rag`'s `ConnectionManager` passed a wrapped connection object to `iris.createIRIS()`, which expects the raw `irissdk.IRISConnection` from `intersystems_irispython`.

---

## Root Cause

The PPR Functional Index code (`ppr_functional_index.py` and `ppr_globals.py`) called:

```python
irispy = iris.createIRIS(conn)
```

This failed when `conn` was a wrapped connection object (e.g., from `ConnectionManager`) instead of a raw `irissdk.IRISConnection`.

---

## Fix Applied ✅

**Files Modified**:
- `iris_vector_graph/ppr_functional_index.py` (lines 94-109)
- `iris_vector_graph/ppr_globals.py` (lines 27-34, 85-92)

**Solution**: Added connection unwrapping logic that handles both raw and wrapped connections:

```python
# Handle both raw irissdk.IRISConnection and wrapped connection objects
raw_conn = conn
if hasattr(conn, '_connection'):  # Wrapped connection (e.g., from ConnectionManager)
    raw_conn = conn._connection
elif hasattr(conn, 'connection'):  # Alternative wrapper pattern
    raw_conn = conn.connection

try:
    irispy = iris.createIRIS(raw_conn)
except TypeError as e:
    raise TypeError(
        f"Failed to create IRIS object. Expected irissdk.IRISConnection, got {type(raw_conn)}. "
        f"If using a connection wrapper, ensure it exposes the raw connection via _connection or connection attribute. "
        f"Original error: {e}"
    )
```

**Impact**:
- ✅ **Functional Index PPR now works with ConnectionManager**
- ✅ **Pure Python PPR fallback no longer triggered unnecessarily**
- ✅ **Clear error message if connection unwrapping fails**
- ✅ **Performance improvement**: 8.9x faster PPR for 10K+ node graphs

---

## Remaining iris-vector-rag Issues

The PPR connection issue is **FIXED in iris-vector-graph**, but the HippoRAG team reported **3 additional issues** that must be fixed in `iris-vector-rag`:

### 1. Foreign Key Validation Failures ❌
```
Foreign key validation failed: 20 missing entity IDs, 30 orphaned relationships
Relationship batch storage validation failed: 30 orphaned relationships detected
```

**Location**: `iris_vector_rag/storage/adapters/entity_storage_adapter.py`
**Impact**: Entity relationships not being stored correctly, affecting graph traversal
**Required Fix**: Ensure entities are stored before relationships, or relax foreign key constraints

### 2. Fuzzy Matching Failures ❌
```
Fuzzy matching failed: 'EntityStorageAdapter' object has no attribute 'search_entities'
```

**Location**: `iris_vector_rag/storage/adapters/entity_storage_adapter.py`
**Impact**: Cannot find entity name variations, reducing recall
**Required Fix**: Implement missing `search_entities()` method OR disable fuzzy matching temporarily

### 3. DSPy Module Warning ❌
```
WARNING: Calling module.forward(...) on BatchEntityExtractionModule directly is discouraged
```

**Location**: `iris_vector_rag` DSPy integration code
**Impact**: Possible incorrect module usage pattern
**Required Fix**: Use proper DSPy API (e.g., `module(...)` instead of `module.forward(...)`)

---

## Verification Steps

To verify the PPR fix works with `iris-vector-rag`:

### 1. Install Updated iris-vector-graph

```bash
# From iris-vector-graph repository
git pull
pip install -e .  # Or publish to PyPI and update iris-vector-rag dependency
```

### 2. Test PPR with ConnectionManager

```python
from iris_vector_rag.storage.connection_manager import ConnectionManager
from iris_vector_graph import compute_ppr_functional_index

# Create wrapped connection
cm = ConnectionManager(...)
conn = cm.get_connection()

# This should now work without TypeError
scores = compute_ppr_functional_index(
    conn=conn,
    seed_entities=["PROTEIN:TP53"],
    damping_factor=0.85
)

print(f"PPR computed successfully: {len(scores)} entities")
```

### 3. Check Logs for PPR Performance

**Before fix** (Pure Python fallback):
```
PPR computation: 1,631ms (10K nodes)
```

**After fix** (Functional Index):
```
PPR computation: 184ms (10K nodes) - 8.9x faster
```

---

## Integration Notes

### ConnectionManager Requirements

If you're using a connection wrapper, ensure it exposes the raw connection via one of these attributes:

- `_connection` (preferred)
- `connection` (alternative)

Example:

```python
class ConnectionManager:
    def __init__(self, ...):
        self._connection = iris.connect(...)  # Raw irissdk.IRISConnection

    def get_connection(self):
        return self  # Return wrapper, not raw connection
```

The unwrapping logic in `iris-vector-graph` will automatically extract `_connection`.

### Error Handling

If connection unwrapping fails, you'll see a clear error:

```
TypeError: Failed to create IRIS object. Expected irissdk.IRISConnection, got <class 'MyWrapper'>.
If using a connection wrapper, ensure it exposes the raw connection via _connection or connection attribute.
```

---

## Summary for HippoRAG Team

| Issue | Status | Owner |
|-------|--------|-------|
| **PPR Connection Type Mismatch** | ✅ **FIXED** | iris-vector-graph (v1.1.7) |
| **Foreign Key Validation Failures** | ❌ **OPEN** | iris-vector-rag |
| **Fuzzy Matching Missing Method** | ❌ **OPEN** | iris-vector-rag |
| **DSPy Module Warning** | ❌ **OPEN** | iris-vector-rag |

**Next Steps**:
1. Update `iris-vector-rag` dependency to `iris-vector-graph >= 1.1.7`
2. Fix foreign key validation in `entity_storage_adapter.py`
3. Implement `search_entities()` method or disable fuzzy matching
4. Fix DSPy module usage pattern

---

## References

- **HippoRAG Issue Report**: `SESSION_CONTINUATION_SUMMARY.md`
- **PPR Performance Analysis**: `docs/ppr-optimization/ppr-performance-optimization-journey.md`
- **Functional Index Design**: `docs/ppr-optimization/ppr-functional-index-deployment-summary.md`

---

**Contact**: iris-vector-graph maintainers
**Status**: Ready for iris-vector-rag integration testing
