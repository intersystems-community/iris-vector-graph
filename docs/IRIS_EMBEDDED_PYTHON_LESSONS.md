# IRIS Embedded Python Lessons Learned

**Critical findings from fraud server implementation (2025-10-03)**

## iris.sql.exec() Syntax Limitations

### ❌ STORED PROCEDURES WITH PARAMETERS DON'T WORK
```python
# THIS FAILS with "Invalid method formalspec format"
iris.sql.exec("""
    CREATE OR REPLACE PROCEDURE my_proc(IN param VARCHAR(256))
    LANGUAGE OBJECTSCRIPT
    {
        WRITE param
        QUIT
    }
""")
```

**Why**: The CREATE PROCEDURE syntax that works in SQL files (.sql) uses different parsing rules than `iris.sql.exec()`. Parameter declarations with `IN`/`OUT` keywords and datatypes fail when executed via the Python API.

**Solution**: Compute features directly in Python using SQL queries instead of stored procedures.

### ✅ DATETIME PARAMETERS
```python
# ❌ WRONG - Python datetime objects don't work
from datetime import datetime, timedelta
ts_24h = datetime.utcnow() - timedelta(hours=24)
iris.sql.exec("SELECT * FROM table WHERE ts >= ?", (payer_id, ts_24h))
# Error: "Invalid Dynamic Statement Parameter"

# ✅ CORRECT - Use SQL DATEADD function
iris.sql.exec("""
    SELECT * FROM table
    WHERE ts >= DATEADD(hour, -24, CURRENT_TIMESTAMP)
""", payer_id)
```

**Why**: IRIS doesn't automatically convert Python datetime objects to IRIS timestamp format. Attempting to pass datetime objects as parameters results in "Invalid Dynamic Statement Parameter" error.

**Solution**: Use SQL date/time functions (`DATEADD`, `CURRENT_TIMESTAMP`, etc.) directly in queries instead of parameterized datetime values.

### ✅ RESULT SET ITERATION
```python
# ❌ WRONG - iris.sql.exec() doesn't return result set with fetchone()
result = iris.sql.exec("SELECT COUNT(*) FROM table")
row = result.fetchone()  # Error: "Property fetchone not found"

# ✅ CORRECT - Iterate or convert to list
result = iris.sql.exec("SELECT COUNT(*) FROM table")
rows = list(result)
count = rows[0][0] if rows else 0
```

**Why**: Unlike standard DB-API 2.0 result sets, `iris.sql.exec()` returns an iterator (`iris.%SYS.Python.SQLResultSet`) that doesn't implement `.fetchone()` or `.fetchall()` methods.

**Solution**: Use `list(result)` or direct iteration to consume results.

## IRIS Schema Limitations

### ❌ VECTOR TYPE REQUIRES LICENSE
```sql
-- THIS FAILS in Community Edition with "Vector Search not permitted with current license"
CREATE TABLE embeddings (
    id INT PRIMARY KEY,
    emb VECTOR(DOUBLE, 768) NOT NULL
)
```

**Why**: VECTOR datatype and vector search capabilities require InterSystems IRIS with Vector Search license. Community Edition does not support vector operations.

**Solution**: Skip vector-based features when using Community Edition, or use licensed IRIS.

## Docker Development Workflow

### ❌ RESTART DOESN'T RELOAD CODE
```bash
# THIS DOESN'T WORK - Code changes not reflected
docker restart iris-fraud-embedded
```

**Why**: When Dockerfile uses `COPY` to embed code into the image, the code is baked into the image at build time. Restarting the container uses the old image with old code.

**Solution**: Rebuild the image when code changes:
```bash
docker-compose -f docker-compose.fraud-embedded.yml build iris-fraud-embedded
docker-compose -f docker-compose.fraud-embedded.yml up -d
```

Or use volume mounts for development (slower startup but no rebuild needed).

## Working Patterns

### ✅ Feature Computation in Python
```python
def compute_features(entity_id: str) -> dict:
    """Compute features using direct SQL queries"""

    # Rolling 24h transaction count and sum
    result = iris.sql.exec("""
        SELECT
            COUNT(*) AS deg_24h,
            COALESCE(SUM(amount), 0.0) AS tx_amt_sum_24h
        FROM gs_events
        WHERE entity_id = ?
        AND ts >= DATEADD(hour, -24, CURRENT_TIMESTAMP)
    """, entity_id)

    rows = list(result)
    deg_24h = rows[0][0] if rows else 0
    tx_amt_sum_24h = rows[0][1] if rows else 0.0

    return {"deg_24h": deg_24h, "tx_amt_sum_24h": tx_amt_sum_24h}
```

### ✅ Simple OBJECTSCRIPT Procedures (No Parameters)
```python
# Parameterless procedures work
iris.sql.exec("""
    CREATE OR REPLACE PROCEDURE test_proc()
    LANGUAGE OBJECTSCRIPT
    {
        WRITE "Hello World"
        QUIT
    }
""")
```

## References

- IRIS Embedded Python docs: https://docs.intersystems.com/irislatest/csp/docbook/DocBook.UI.Page.cls?KEY=AEPY
- CREATE PROCEDURE docs: https://docs.intersystems.com/irislatest/csp/docbook/DocBook.UI.Page.cls?KEY=RSQL_createprocedure
- iris-pgwire project: ../iris-pgwire (reference implementation for embedded Python patterns)

## Date
2025-10-03

## Status
**VALIDATED** - All patterns tested and confirmed working in fraud server implementation.
