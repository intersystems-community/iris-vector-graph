# Contracts: Cypher CAST Functions

| Input | Output SQL |
|-------|-----------|
| `toInteger(expr)` | `CAST(expr AS INTEGER)` |
| `toFloat(expr)` | `CAST(expr AS DOUBLE)` |
| `toString(expr)` | `CAST(expr AS VARCHAR(4096))` |
| `toBoolean(expr)` | `CASE WHEN LOWER(expr) IN ('true','1','yes','y') THEN 1 ELSE 0 END` |
| `COUNT(DISTINCT expr)` | `COUNT(DISTINCT expr)` |
