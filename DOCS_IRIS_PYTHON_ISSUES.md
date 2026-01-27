# Technical Report: Embedded Python Parsing & Runtime Issues in IRIS 2025.1

**Date**: 2026-01-26  
**Environment**: InterSystems IRIS 2025.1.0.211.0 (Containerized)  
**Context**: Project `iris-vector-graph`

## Summary
During the refactoring of graph traversal and vector search methods, we encountered severe stability issues with "Hybrid" classes (ObjectScript + Embedded Python). The IRIS class parser frequently fails with `ERROR #5559` when encountering standard Python 3.11 syntax, and the Python runtime fails to resolve certain IRIS-namespaced classes.

---

## Issue 1: Parser Sensitivity to Python Literals (ERROR #5559)

The IRIS class parser (compiling `.cls` files) often fails to correctly identify the boundaries of `[ Language = python ]` blocks when specific Python literals are used.

### Reproducible Patterns:
1.  **F-Strings**: Usage of `f"{var}"` frequently triggers `ERROR #5559: The class definition for class 'X' could not be parsed correctly, possibly due to non-matching {} or () characters`.
2.  **Dictionary Comprehensions**: Syntax like `{k: v for k, v in ...}` is misinterpreted by the parser as an ObjectScript block delimiter.
3.  **Nested Braces in Queries**: Multi-line SQL strings containing curly braces (e.g., JSON logic) inside a Python block cause parsing failures.

### Impact:
Developers are forced to use less idiomatic Python (e.g., `dict()` instead of `{}` literals, string concatenation instead of f-strings) to satisfy the class compiler.

---

## Issue 2: Conflict with 'iris.' Class Prefix

When an IRIS class is defined with a package name starting with `iris` (e.g., `iris.vector.graph.GraphOperators`), the Embedded Python runtime encounters a namespace conflict.

### Behavior:
- Inside an IRIS session, `import iris` loads the InterSystems Python bridge.
- Attempting to call `iris.cls("iris.vector.graph.GraphOperators")` fails with `ModuleNotFoundError` or `AttributeError`.
- It appears the Python bridge's own namespace shadowing prevents resolution of IRIS classes that share the `iris.` prefix.

### Impact:
This forces architectural changes (renaming classes or packages) to avoid common naming conventions that happen to collide with the bridge module name.

---

## Issue 3: Incompatibility between Parameters and Python Methods

In some configurations, adding a standard IRIS `Parameter` to a class containing Python-language methods triggers a global parsing failure for the class.

### Observation:
A class that compiles successfully with only Python methods will fail with `ERROR #5559` the moment a single `Parameter X = 1;` is added, even if that parameter is not referenced in the Python code.

---

## Recommended Workarounds (Current Project)
1.  **Separation of Concerns**: Move all SQL-heavy or complex logic to "Pure ObjectScript" helper classes.
2.  **Wrappers**: Use Python methods only as thin wrappers around ObjectScript logic.
3.  **Avoid Literals**: Use `list()`, `dict()`, and `str.join()` instead of literal syntax in hybrid classes.
4.  **Namespace Hygiene**: Avoid the `iris.` package prefix for custom classes.
