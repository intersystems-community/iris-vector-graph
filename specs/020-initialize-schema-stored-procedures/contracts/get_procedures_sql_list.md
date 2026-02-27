# Contract: GraphSchema.get_procedures_sql_list

**Module**: `iris_vector_graph.schema`  
**Class**: `GraphSchema`  
**Method**: `get_procedures_sql_list`  
**Feature**: 020-initialize-schema-stored-procedures

---

## Signature

### Before (broken)
```python
@staticmethod
def get_procedures_sql_list(table_schema: str = "Graph_KG") -> List[str]:
    # SyntaxError: module is unimportable
```

### After (fixed)
```python
@staticmethod
def get_procedures_sql_list(
    table_schema: str = "Graph_KG",
    embedding_dimension: int = 1000,
) -> List[str]:
    """
    Return ordered SQL DDL statements to install retrieval stored procedures.

    Args:
        table_schema: SQL schema name containing the data tables
                      (kg_NodeEmbeddings, rdf_labels, docs). Default: "Graph_KG".
        embedding_dimension: Vector dimension for the DECLARE clause inside
                             kg_KNN_VEC. Must match the emb column dimension in
                             kg_NodeEmbeddings. Default: 1000 (backward-compatible
                             fallback; callers should always pass the real dimension).

    Returns:
        List of SQL strings in execution order. Each string is a complete,
        self-contained DDL statement suitable for cursor.execute().
    """
```

---

## Preconditions

- `table_schema` MUST be a valid IRIS SQL schema identifier (no injection risk — value comes from `set_schema_prefix()` which uses `sanitize_identifier()`).
- `embedding_dimension` MUST be a positive integer > 0. No validation performed inside this method — caller is responsible.

---

## Postconditions

- Returns a `List[str]` with exactly **4** elements (in order):
  1. `"CREATE SCHEMA iris_vector_graph"` — bare schema creation (no `IF NOT EXISTS`; caller wraps in try/except)
  2. `CREATE OR REPLACE PROCEDURE iris_vector_graph.kg_KNN_VEC(...)` with `VECTOR(DOUBLE, {embedding_dimension})`
  3. `CREATE OR REPLACE PROCEDURE iris_vector_graph.kg_TXT(...)`
  4. `CREATE OR REPLACE PROCEDURE iris_vector_graph.kg_RRF_FUSE(...)`
- All procedure bodies reference `{table_schema}.kg_NodeEmbeddings` and `{table_schema}.rdf_labels` using the provided `table_schema`.
- No side effects (pure function — does not execute SQL, does not touch the database).

---

## Invariants

- The list is always non-empty (minimum 4 elements).
- `"VECTOR(DOUBLE, {embedding_dimension})"` appears exactly once in the returned list (inside `kg_KNN_VEC`).
- `table_schema` appears in the `FROM` clauses of all procedure bodies.
- All statements are `CREATE OR REPLACE` (safe for idempotent re-execution).

---

## Error Conditions

| Condition | Behaviour |
|-----------|-----------|
| `embedding_dimension=0` | Not validated here — IRIS DDL execution will fail when `cursor.execute()` is called |
| `embedding_dimension` is `None` | `f"VECTOR(DOUBLE, {None})"` produces invalid SQL — caller (`initialize_schema`) validates before calling |

---

## Example Usage

```python
stmts = GraphSchema.get_procedures_sql_list(
    table_schema="Graph_KG",
    embedding_dimension=384,
)
assert len(stmts) == 4
assert "VECTOR(DOUBLE, 384)" in stmts[1]  # kg_KNN_VEC DDL
assert "Graph_KG.kg_NodeEmbeddings" in stmts[1]
```

---

## Regression Guarantee

The dead code block (old version of this method, lines 436–520 of the broken `schema.py`) is removed. The surviving implementation (lines 351–434) is the canonical version. No behavioral change to the returned SQL — only the `embedding_dimension` substitution is new.
