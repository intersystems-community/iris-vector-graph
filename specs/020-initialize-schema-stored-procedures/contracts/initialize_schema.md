# Contract: IRISGraphEngine.initialize_schema

**Module**: `iris_vector_graph.engine`  
**Class**: `IRISGraphEngine`  
**Method**: `initialize_schema`  
**Feature**: 020-initialize-schema-stored-procedures

---

## Signature

```python
def initialize_schema(self) -> None:
    """
    Create the base schema tables and install retrieval stored procedures in IRIS.

    Safe to call on existing databases — "already exists" failures are silently
    ignored. Raises RuntimeError if any stored procedure DDL fails with an
    unexpected error.

    Raises:
        ValueError: If embedding_dimension has not been set.
        RuntimeError: If one or more stored procedure DDL statements fail with
                      a non-"already exists" error. The error message lists
                      how many procedures failed and includes the first error.
    """
```

---

## Preconditions

- `self.embedding_dimension` MUST be set (either via constructor or inferred from `store_embedding()`). Raises `ValueError` with a descriptive message if not set (unchanged from current behavior).
- A valid IRIS connection (`self.conn`) must be open.

---

## Postconditions (success path)

When `initialize_schema()` returns without raising:
- `Graph_KG` schema exists in IRIS
- All base tables exist: `nodes`, `rdf_labels`, `rdf_props`, `rdf_edges`, `kg_NodeEmbeddings`
- `kg_NodeEmbeddings.emb` column type is `VECTOR(DOUBLE, self.embedding_dimension)`
- `iris_vector_graph` SQL schema exists in IRIS
- The following procedures exist and are callable:
  - `iris_vector_graph.kg_KNN_VEC(queryInput, k, labelFilter, embeddingConfig)`
  - `iris_vector_graph.kg_TXT(q, k)`
  - `iris_vector_graph.kg_RRF_FUSE(k, k1, k2, c, queryVector, qtext)`
- The procedure `iris_vector_graph.kg_KNN_VEC` was created with `VECTOR(DOUBLE, self.embedding_dimension)`
- Transaction has been committed (`self.conn.commit()` called)

---

## Error Conditions

| Condition | Behaviour |
|-----------|-----------|
| `self.embedding_dimension is None` | Raises `ValueError` before any DDL is executed (unchanged) |
| `CREATE SCHEMA Graph_KG` fails with "already exists" | Silently ignored (unchanged) |
| `CREATE TABLE` fails with "already exists" | Silently ignored (unchanged) |
| `CREATE SCHEMA iris_vector_graph` fails with "already exists" | Silently ignored |
| `CREATE OR REPLACE PROC ...` fails with "already exists" | Silently ignored (idempotent re-run) |
| Any procedure DDL fails with other error (permission denied, bad SQL) | Logged at ERROR level; collected; `RuntimeError` raised after loop completes; transaction NOT committed |

---

## Execution Order

```
1. validate self.embedding_dimension (raises ValueError if None)
2. cursor = self.conn.cursor()
3. CREATE SCHEMA Graph_KG           [ignore "already exists"]
4. Execute get_base_schema_sql()    [ignore "already exists"]
5. GraphSchema.ensure_indexes()
6. self._get_embedding_dimension()  [log ERROR on mismatch]
7. CREATE SCHEMA iris_vector_graph  [ignore "already exists"]
8. CREATE OR REPLACE PROC kg_KNN_VEC(... VECTOR(DOUBLE, dim) ...)
9. CREATE OR REPLACE PROC kg_TXT(...)
10. CREATE OR REPLACE PROC kg_RRF_FUSE(...)
11. if procedure_errors → raise RuntimeError
12. self.conn.commit()
```

Steps 7–11 use the new failure-collection pattern. `commit()` is only reached if no unexpected errors occurred.

---

## Idempotency Contract

| Call sequence | Result |
|---------------|--------|
| First call on blank IRIS | Creates all objects; commits |
| Second call on same IRIS | Replaces procedures (CREATE OR REPLACE); ignores existing tables/schemas; commits |
| Call with different `embedding_dimension` | Replaces procedure DDL with new dimension; logs ERROR on dimension mismatch with existing table |

---

## Observable Side Effects

- **Logging** (unchanged from current):
  - `logger.warning(...)` for non-critical schema DDL failures (tables)
  - `logger.error(...)` for embedding dimension mismatch
- **Logging** (new):
  - `logger.error(...)` for each failed procedure DDL statement
- **Exception** (new):
  - `RuntimeError` if any procedure DDL fails unexpectedly

---

## Example

```python
# Fresh install — succeeds silently
engine = IRISGraphEngine(conn, embedding_dimension=384)
engine.initialize_schema()  # Returns None; all procedures installed

# Verify server-side path works
results = engine.kg_KNN_VEC(query_vector_json, k=5)
# No WARNING logged — server-side CALL succeeded
```
