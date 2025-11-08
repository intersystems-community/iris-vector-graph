# Embedded Python API Issues & Weirdness

**Date**: 2025-11-08
**Context**: PPR Functional Index embedded Python implementation
**IRIS Version**: 2025.1 (Community Edition via Docker)

## Issue 1: gref().iterator() Method Does Not Exist ❌

**Expected**: `iris.gref('^PPR').iterator(['deg'])` should work like external client API

**Actual**: `AttributeError: 'iris.gref' object has no attribute 'iterator'`

**Workaround**: Use `ppr_global["deg"].order("")` pattern instead

**Impact**: API inconsistency between external (`irispy.iterator()`) and embedded (`iris.gref()[sub].order()`)

---

## Issue 2: NoneType on Missing Subscript Levels ⚠️

**Behavior**: When accessing `ppr_global["deg"]` and the subscript level doesn't exist, it returns `None` instead of an empty object with `.order()` method.

**Actual Error**: `AttributeError: 'NoneType' object has no attribute 'order'`

**Workaround**: Wrap all Global traversal in `try/except (AttributeError, TypeError)` blocks

**Code Pattern**:
```python
try:
    node_id = ppr_global["deg"].order("")
    while node_id:
        # process
        node_id = ppr_global["deg"].order(node_id)
except (AttributeError, TypeError):
    pass  # No data at this subscript level
```

**Impact**: Requires defensive programming for every Global access

---

## Issue 3: Embedded Python Cannot See Global Data 🔴 CRITICAL

**Symptom**: `iris.gref('^PPR')["deg"]` returns `None` even when ^PPR Global contains data

**Verified**:
- External client (`irispy.iterator('^PPR', 'deg')`) sees 1000 nodes with data
- Embedded Python (`iris.gref('^PPR')["deg"]`) returns `None`
- Data definitely exists in the Global

**Debug Output from Embedded Python**:
```json
{
  "error": "No nodes found",
  "debug": {
    "deg_count": 0,
    "in_count": 0,
    "deg_error": "deg subscript is None",
    "in_error": "in subscript is None"
  }
}
```

**Root Cause**: **Namespace/scope isolation issue** - embedded Python appears to be running in a different Global scope than the data

**Hypothesis**:
1. ^PPR Global may be namespace-specific (e.g., in USER namespace)
2. Embedded Python may be running in a different namespace (e.g., %SYS)
3. Need to use fully qualified Global reference (`^["USER"]PPR`) or ObjectScript interop

**Impact**: **BLOCKING** - Embedded Python cannot be used for PPR until this is resolved

**Workaround Needed**: Use ObjectScript native implementation instead

---

## Issue 4: Performance Characteristics ✅

**Good News**: When embedded Python works, it's **2.62x faster** than Pure Python baseline!

**Measurements**:
- Pure Python (SQL extraction): 13.60ms for 1K nodes
- Embedded Python (iris.gref): 5.19ms for 1K nodes (but returns empty results)
- External Client (intersystems_irispython): 20,141ms for 1K nodes (1,480x slower)

**Analysis**: This confirms the hypothesis that embedded Python has low overhead compared to external client API.

---

## Recommendations for IRIS Dev Team

1. **Add `iterator()` method to `iris.gref()` objects** for API consistency with external client
2. **Return empty object (not None)** for missing subscript levels to enable `.order()` chaining
3. **Document the difference** between external (`irispy.iterator()`) and embedded (`gref()[].order()`) APIs
4. **Provide debugging tools** for embedded Python to inspect Global state at runtime
5. **Document namespace visibility** for embedded Python - clarify when/how embedded Python can access Globals from different namespaces
6. **Add `iris.eval()` or equivalent** for executing ObjectScript expressions from embedded Python

---

## Resolution Status: BLOCKED - Moving to ObjectScript

After extensive investigation and multiple API pattern attempts following IRIS development guidance:

### What We Tried:
1. ✅ **.keys() method** - **WORKS** but extremely slow (59,026ms for 1K nodes vs 14ms baseline = 4,000x slower)
2. ❌ **.orderiter() method** - Returns zero results (empty iterator)
3. ❌ **.query() method** - Returns zero results (empty iterator)
4. ❌ **Extended global references** (`^|"USER"|PPR`, `^["USER"]PPR`) - Still zero results with query/orderiter

### Key Findings:
- `.keys()` is the **only working method** for embedded Python gref traversal BUT has terrible performance
- `.orderiter()` and `.query()` appear broken or incompatible with embedded Python context
- Extended global references don't solve the iteration API issues
- External client API works perfectly (1000 nodes visible) but has network/IPC overhead (20s vs 14ms)

### Performance Summary (1K nodes):
- **Pure Python**: 14ms ✅ BEST
- **Embedded Python (.keys())**: 59,026ms ❌ 4,000x slower
- **Embedded Python (.query()/.orderiter())**: 0 results ❌ BROKEN
- **External Client**: 20,013ms ❌ 1,400x slower

**Decision**: Proceed to pure ObjectScript PPR implementation as originally recommended. This will:
- Avoid all Python API limitations (native ObjectScript $ORDER is fast)
- Achieve sub-millisecond performance (<10ms for 10K nodes target)
- Provide production-ready solution without Python dependencies
- Use proven ObjectScript Global traversal patterns

**Files Created for Reference**:
- `src/iris/Graph/KG/PPRCompute.cls` - Attempted embedded Python (only .keys() works, too slow)
- `scripts/performance/benchmark_embedded_ppr.py` - Benchmark script
- `docs/embedded-python-issues.md` - Complete issue documentation for IRIS dev team
- `docs/ppr-performance-optimization-journey.md` - Full optimization journey documentation
