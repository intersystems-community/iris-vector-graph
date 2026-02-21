# Contract: `ivg.vector.search` Cypher Procedure

**Feature**: 018-cypher-vector-search  
**Date**: 2026-02-21  
**Type**: Internal Python API + Cypher grammar extension

---

## Cypher Grammar Contract

### Syntax

```cypher
CALL ivg.vector.search(label, property, query_input, limit [, options])
YIELD node, score
[MATCH (node)-[:REL]->(other)]
[WHERE ...]
[RETURN ...]
```

### Arguments

| Position | Name | Cypher Type | Python Binding | Required |
|----------|------|-------------|---------------|----------|
| 0 | `label` | String literal or `$param` | `str` | Yes |
| 1 | `property` | String literal or `$param` | `str` (reserved, currently must be `'embedding'`) | Yes |
| 2 | `query_input` | `$param` | `list[float]` (Mode 1) or `str` (Mode 2) | Yes |
| 3 | `limit` | Integer literal or `$param` | `int > 0` | Yes |
| 4 | `options` | Map literal `{key: value, ...}` | `dict[str, Any]` | No |

### Options Map

| Key | Values | Default | Notes |
|-----|--------|---------|-------|
| `similarity` | `"cosine"` \| `"dot_product"` | `"cosine"` | Similarity function |
| `embedding_config` | String | None | Required for Mode 2 (text input) |

### YIELD Variables

| Variable | Type | Description |
|----------|------|-------------|
| `node` | Node Dict | `{"id": str, "labels": list[str], "properties": dict}` |
| `score` | `float` | Similarity score. Cosine: `[0.0, 1.0]`. Dot product: unbounded. |

### Composability

`YIELD` variables enter outer query scope directly. No `WITH` clause required before using them in subsequent `MATCH`:

```cypher
CALL ivg.vector.search('Protein', 'embedding', $vec, 5) YIELD node, score
MATCH (node)-[:INTERACTS_WITH]->(partner)
RETURN node.id, score, partner.id
```

---

## Python API Contract

### `execute_cypher(query, params)` (existing method on `IRISGraphEngine`)

No signature change. Feature is transparent — the parser, translator, and engine handle the `CALL` clause automatically.

```python
results = engine.execute_cypher(
    "CALL ivg.vector.search('Protein', 'embedding', $vec, 10) YIELD node, score",
    params={"vec": [0.1, 0.2, ...]}  # list[float]
)
# results: list[dict]
# Each dict: {"node": {"id": ..., "labels": [...], "properties": {...}}, "score": 0.87}
```

### Mode 2 (text input, IRIS 2024.3+)

```python
results = engine.execute_cypher(
    "CALL ivg.vector.search('Protein', 'embedding', $text, 10, {embedding_config: 'minilm'}) YIELD node, score",
    params={"text": "tumor suppressor DNA repair"}
)
```

---

## SQL Translation Contract

### Mode 1 (pre-computed vector) — `similarity: 'cosine'` (default)

```sql
WITH VecSearch AS (
    SELECT TOP {limit}
        n.node_id AS node,
        VECTOR_COSINE(e.emb, TO_VECTOR(?)) AS score
    FROM Graph_KG.nodes n
    JOIN Graph_KG.rdf_labels l ON l.s = n.node_id
    JOIN Graph_KG.kg_NodeEmbeddings e ON e.id = n.node_id
    WHERE l.label = ?
    ORDER BY score DESC
)
SELECT VecSearch.node, VecSearch.score
FROM VecSearch
```

Parameters: `[json.dumps(query_vector), label_value]`

### Mode 1 — `similarity: 'dot_product'`

Replace `VECTOR_COSINE` with `VECTOR_DOT_PRODUCT`. Parameters identical.

### Mode 2 (text + IRIS EMBEDDING)

```sql
WITH VecSearch AS (
    SELECT TOP {limit}
        n.node_id AS node,
        VECTOR_COSINE(e.emb, EMBEDDING(?, ?)) AS score
    FROM Graph_KG.nodes n
    JOIN Graph_KG.rdf_labels l ON l.s = n.node_id
    JOIN Graph_KG.kg_NodeEmbeddings e ON e.id = n.node_id
    WHERE l.label = ?
    ORDER BY score DESC
)
SELECT VecSearch.node, VecSearch.score
FROM VecSearch
```

Parameters: `[query_text, config_name, label_value]`

### Composed with MATCH

```sql
WITH VecSearch AS (
    SELECT TOP 5 n.node_id AS node, VECTOR_COSINE(e.emb, TO_VECTOR(?)) AS score
    FROM Graph_KG.nodes n
    JOIN Graph_KG.rdf_labels l ON l.s = n.node_id
    JOIN Graph_KG.kg_NodeEmbeddings e ON e.id = n.node_id
    WHERE l.label = ?
    ORDER BY score DESC
)
SELECT VecSearch.node, VecSearch.score, e2.o_id AS partner
FROM VecSearch
JOIN Graph_KG.rdf_edges e2 ON e2.s = VecSearch.node AND e2.p = 'INTERACTS_WITH'
```

---

## Error Contract

| Condition | Exception | Message |
|-----------|-----------|---------|
| Unknown `similarity` value | `ValueError` | `"Invalid similarity metric '{val}'. Valid: 'cosine', 'dot_product'"` |
| `query_input` is `str` but no `embedding_config` | `ValueError` | `"embedding_config required in options when query_input is a text string"` |
| IRIS does not support `EMBEDDING()` | `UnsupportedOperationError` | `"IRIS EMBEDDING() function not available. Requires IRIS 2024.3+. Use a pre-computed vector instead."` |
| `limit <= 0` | `ValueError` | `"limit must be a positive integer"` |
| `label` fails security validation | `ValueError` | (from `validate_table_name`) |
| Unknown procedure name | `ValueError` | `"Unknown procedure: '{name}'. Only 'ivg.vector.search' is supported."` |
| Label not found / no embeddings | (none) | Empty result list `[]` — no error |

---

## Unit Test Contract (`tests/unit/test_cypher_vector_search.py`)

The following must be testable without IRIS:

1. Parser: `CALL ivg.vector.search('Label', 'emb', $vec, 10) YIELD node, score` → `CypherProcedureCall(procedure_name='ivg.vector.search', arguments=[...], yield_items=['node', 'score'])`
2. Parser: options map parsed into `CypherProcedureCall.options`
3. Translator: Mode 1 SQL contains `TO_VECTOR(?)` and `VECTOR_COSINE`
4. Translator: Mode 2 SQL contains `EMBEDDING(?, ?)`
5. Translator: `similarity='dot_product'` → SQL contains `VECTOR_DOT_PRODUCT`
6. Translator: Invalid `similarity` → `ValueError`
7. Translator: Text input without `embedding_config` → `ValueError`
8. Translator: `limit=0` → `ValueError`
9. Translator: `CALL ... YIELD node, score MATCH (node)-[:REL]->(other)` → CTE + JOIN in single SQL
10. Translator: `yield_items` variables pre-populated in `variable_aliases`

## Integration Test Contract (`tests/integration/test_cypher_vector_search.py`)

Runs against live IRIS, validates SQL layer:

1. SQL executes without error against real `kg_NodeEmbeddings`
2. Results are ordered by score descending
3. Results contain valid node IDs referencing existing nodes
4. `limit` is respected — exactly N rows returned (or fewer if dataset smaller)
5. Label filter correctly excludes nodes with wrong labels
6. `VECTOR_DOT_PRODUCT` path executes without error

## E2E Test Contract (`tests/e2e/test_cypher_vector_search.py`)

Full round-trip via `IRISContainer.attach("los-iris")`:

1. `execute_cypher` returns hydrated node dicts with correct shape
2. `score` values are numeric and in expected range for cosine
3. Composed query `CALL ... YIELD ... MATCH ... RETURN` returns multi-hop results
4. Mode 2 test (if IRIS 2024.3+ available, else skipped with `pytest.skip`)
5. Empty result when label does not exist — no exception
6. Error raised for invalid `similarity` value
7. Benchmark: top-10 cosine search completes in < 100ms against seeded dataset
