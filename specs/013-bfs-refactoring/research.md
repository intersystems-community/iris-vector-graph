# Research: BFS Traversal Refactoring

**Feature**: 013-bfs-refactoring  
**Date**: 2026-01-26

## Research Summary

Research focused on two areas: optimizing the internal structure of Embedded Python methods in InterSystems IRIS and establishing a robust benchmarking methodology for graph traversal.

---

## Research Item 1: Internal Helper Methods in IRIS Embedded Python

**Question**: How to best structure internal helpers within a class using `Language = python`?

**Decision**: Use `Private ClassMethod` with an underscore prefix for shared/internal logic; use nested functions for logic strictly local to one method.

**Rationale**:
- `Private ClassMethod` (e.g., `ClassMethod _InternalHelper() [ Language = python, Private ]`) provides the best balance of encapsulation and reusability. It is enforced at the IRIS kernel level and visible to debugging tools.
- Nested functions within a method provide zero overhead (native Python calls) and perfect encapsulation but are not reusable.
- Passing `%DynamicObject` and `%DynamicArray` is efficient as they are passed by reference (proxy objects). Direct property/index access in Python is preferred over JSON round-trips unless performing extremely high-frequency manipulations where native Python `dict`/`list` might be faster.

---

## Research Item 2: Synthetic Graph Generation for Benchmarking

**Question**: How to generate representative graphs for BFS performance testing?

**Decision**: Implement a stream-based generator for **k-Regular graphs** (uniform branching) and **R-MAT** (scale-free/hubs) that writes directly to IRIS globals.

**Rationale**:
- **k-Regular graphs** provide a predictable worst-case scenario for BFS frontier growth.
- **R-MAT** (used by Graph500) provides realistic power-law degree distributions.
- **Stream-based generation** ($O(1)$ memory) is necessary to scale to 100k+ nodes without exhausting Python memory.
- **Metric**: Use **TEPS** (Traversed Edges Per Second) as the primary performance indicator.

---

## Research Item 3: Storage Optimization for BFS

**Question**: What is the most efficient IRIS global structure for adjacency lists?

**Decision**: Use `^Graph(sourceNode, targetNode) = ""` for sparse access.

**Rationale**: `iris.gref.order` provides efficient iteration over the second subscript, which is the idiomatic way to implement adjacency lists in IRIS.

---

## Unknowns Resolved

- ✅ Helper method signatures and visibility (Private ClassMethod).
- ✅ Dynamic object passing (Pass-by-reference).
- ✅ Graph generation algorithms (k-Regular, R-MAT).
- ✅ Performance metrics (TEPS).
