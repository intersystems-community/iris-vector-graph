# Data Model: SQL Parameterization Security Fix

**Feature**: 012-sql-parameterization  
**Date**: 2026-01-26

## Entities

This feature is a security refactoring and does not introduce new entities or persistent storage schemas.

### Validation Domain: Query Parameters

| Attribute | Type | Constraints | Description |
|-----------|------|-------------|-------------|
| `k` | Integer | `1 <= k <= 1000` | Limits the number of returned results. Defaults to 50. |

### Interaction Logic

The primary change is the internal handling of the `k` parameter within `GraphOperators.cls`:

1.  **Input**: Received as Python `any` (often string or int from IRIS).
2.  **Coercion**: Attempt `int(k or 50)`.
3.  **Boundary Check**: `max(1, min(k, 1000))`.
4.  **SQL Execution**: Passed as a bound parameter to `iris.sql.exec("SELECT TOP ? ...", k)`.
