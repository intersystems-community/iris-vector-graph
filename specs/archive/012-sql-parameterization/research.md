# Research: SQL Parameterization Security Fix

**Feature**: 012-sql-parameterization  
**Date**: 2026-01-26

## Research Summary

The goal is to eliminate SQL injection vulnerabilities in `GraphOperators.cls` by replacing string interpolation with parameterized queries. The primary target is the `TOP {k}` clause in `kgTXT`.

---

## Research Item 1: InterSystems IRIS SQL Parameterization for `TOP` clause

**Question**: Does InterSystems IRIS support parameterizing the `TOP` clause using `?` placeholders?

**Decision**: Yes, use `TOP ?` with parameter binding.

**Rationale**: Empirical testing in the `iris_vector_graph` container confirms that `iris.sql.exec("SELECT TOP ? ...", k)` executes successfully. This is the most secure and idiomatic way to handle dynamic limits in IRIS SQL.

**Alternatives Considered**:
- **Strict Integer Validation + f-string**: Rejected. While safe if `int()` coercion is strictly enforced, it doesn't align with the requirement to use parameter binding and doesn't benefit from statement caching as effectively.
- **`TOP (SELECT ?)`**: Unnecessary complexity as `TOP ?` is supported.

---

## Research Item 2: Input Coercion and Defaulting in Python

**Question**: How to robustly handle the `k` parameter which may be passed as a string, float, or null?

**Decision**: Use a helper pattern: `k = min(max(1, int(k or 50)), 1000)`.

**Rationale**:
- `k or 50`: Handles `None` or empty strings by defaulting to 50.
- `int(...)`: Coerces strings and floats. Will raise `ValueError` for non-numeric strings, which should be caught or allowed to bubble up as a 400-level error (depending on caller handling).
- `max(1, ...)`: Enforces positive integer constraint.
- `min(..., 1000)`: Enforces the 1000 result limit to prevent resource exhaustion.

---

## Research Item 3: Existing Query Consistency

**Question**: Are there other vulnerable queries in `GraphOperators.cls`?

**Decision**: Review `kgKNNVEC` and `kgRRF_FUSE`.

**Analysis**:
- `kgKNNVEC`: Uses `similarities[:k]` for slicing results in memory. The SQL queries (lines 13, 16) are either static or correctly parameterized. No injection risk found here, but `k` validation should still be applied for consistency.
- `kgRRF_FUSE`: Uses `rrf_scores[:k]`. It calls `kgKNNVEC` and `kgTXT`. Security depends on the downstream methods.
- `kgTXT`: **VULNERABLE**. Line 47 uses f-string for `TOP {k}`.

---

## Unknowns Resolved

- ✅ `TOP ?` support verified.
- ✅ Validation logic defined.
- ✅ Scope of work confirmed (primary focus on `kgTXT`, secondary focus on consistent validation in `kgKNNVEC` and `kgRRF_FUSE`).
