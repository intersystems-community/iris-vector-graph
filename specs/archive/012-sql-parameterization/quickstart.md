# Quickstart: SQL Parameterization Security Fix

**Feature**: 012-sql-parameterization  
**Date**: 2026-01-26

## Verification Scenarios

### 1. SQL Injection Attempt (Malicious `k`)

Test input that would cause a syntax error or malicious execution if interpolated.

- **Method**: `kgTXT(queryText, k)`
- **Input**: `queryText="protein", k="10; DROP TABLE rdf_edges;--"`
- **Expected**: `ValueError` raised during coercion OR execution succeeds with `k` treated as an invalid string (depending on caller). If coerced via `int()`, it will raise `ValueError` safely.

### 2. Boundary Conditions for `k`

Verify that validation and defaulting logic works as expected.

| Input `k` | Expected Internal `k` | Rationale |
|-----------|-----------------------|-----------|
| `None` | 50 | Default value |
| `""` | 50 | Empty string handling |
| `"100"` | 100 | Coercion from string |
| `0` | 1 | Positive integer constraint |
| `-5` | 1 | Positive integer constraint |
| `2000` | 1000 | Maximum limit enforcement |

### 3. Functional Parity

Verify that standard queries still return identical results.

- **Method**: `kgTXT("malaria", 10)`
- **Expected**: Returns top 10 results ordered by BM25 score, same as pre-fix behavior.

## Test Environment

All tests should be run in the Docker environment using the IRIS Python bridge.

```bash
docker exec -i iris_vector_graph iris session iris -U USER <<'EOF'
Set k = "10; DROP TABLE dummy;--"
Try {
    Set res = ##class(iris.vector.graph.GraphOperators).kgTXT("test", k)
    Write "Security Test: FAILED (No exception raised)", !
} Catch e {
    Write "Security Test: PASSED (Caught expected error: ", e.Name, ")", !
}
Halt
EOF
```
