# Data Model: BFS Traversal Refactoring

**Feature**: 013-bfs-refactoring  
**Date**: 2026-01-26

## Storage Entities (IRIS Globals)

### Global: `^KG`

The Knowledge Graph is stored in a sparse multidimensional array (global).

| Subscript Path | Value | Description |
|----------------|-------|-------------|
| `^KG("label", label, s)` | `""` | Index of nodes (`s`) by their `label`. |
| `^KG("out", s, p, o)` | `""` | Adjacency list: Source `s` has predicate `p` leading to object `o`. |
| `^KG("in", o, p, s)` | `""` | Inverse adjacency list: Object `o` reached by predicate `p` from source `s`. |
| `^KG("deg", s)` | Integer | Total out-degree of node `s`. |
| `^KG("degp", s, p)` | Integer | Out-degree of node `s` for a specific predicate `p`. |

---

## Memory Entities (Python Proxy Objects)

### Entity: `StepObject` (%DynamicObject)

Represents a single hop in the BFS traversal.

| Attribute | Type | Description |
|-----------|------|-------------|
| `id` | Integer | Sequential identifier for the step in the current traversal. |
| `step` | Integer | The hop number (1-based). |
| `s` | String | Source node identifier. |
| `p` | String | Predicate/Edge label. |
| `o` | String | Object/Target node identifier. |

---

## Data Flow & Validation

1. **Input Validation**:
   - `srcId`: Must be coerced to string. If null/empty, returns empty array.
   - `maxHops`: Must be coerced to integer. If <= 0, returns empty array.
   - `preds`: Sequence of predicates to follow per hop.

2. **Traversal Logic**:
   - Frontier-based BFS.
   - `seen` set prevents cycles and redundant work.
   - Predicate sequences allow path-specific traversal.

3. **Output**:
   - `%DynamicArray` containing `StepObject` proxies.
