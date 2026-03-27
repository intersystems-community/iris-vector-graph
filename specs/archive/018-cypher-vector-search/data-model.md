# Data Model: 018-cypher-vector-search

**Phase**: 1 — Design  
**Date**: 2026-02-21

---

## Existing Tables (read-only, no schema changes)

This feature does NOT introduce new tables. It operates over the existing schema.

### `Graph_KG.kg_NodeEmbeddings`

| Column | Type | Constraint | Notes |
|--------|------|-----------|-------|
| `id` | `VARCHAR(256) %EXACT` | PRIMARY KEY, FK → `nodes.node_id` | Node identifier |
| `emb` | `VECTOR(DOUBLE, N)` | — | N = `embedding_dimension` (default 768) |
| `metadata` | `%Library.DynamicObject` | NULLABLE | Optional JSON metadata |

**HNSW Index** (must exist for performant search):
```sql
CREATE INDEX HNSW_NodeEmb
ON Graph_KG.kg_NodeEmbeddings(emb)
AS HNSW(M=16, efConstruction=100, Distance='Cosine')
```
Note: `initialize_schema()` does NOT create this index. Must be created separately. The e2e test fixture MUST ensure it exists before tests run.

### `Graph_KG.rdf_labels`
Used to filter by label in the JOIN condition.

| Column | Type | Notes |
|--------|------|-------|
| `s` | `VARCHAR(256)` | FK → `nodes.node_id` |
| `label` | `VARCHAR(256)` | Node label string |

---

## New AST Nodes

### `CypherProcedureCall` (already exists in `ast.py:263`)

```python
@dataclass(slots=True)
class CypherProcedureCall:
    procedure_name: str                          # "ivg.vector.search"
    arguments: List[Union[Literal, Variable,
                          PropertyReference]]    # positional args
    yield_items: List[str]                       # ["node", "score"]
    options: Dict[str, Any] = field(...)        # {"similarity": "cosine",
                                                #  "embedding_config": "minilm"}
```

**Change required**: Add `options: Dict[str, Any]` field (currently not present). The options map corresponds to the 5th optional argument to the procedure.

---

## New Lexer Tokens

| Token | Enum value | Added to `lexer.py` |
|-------|-----------|---------------------|
| `CALL` | `"CALL"` | `TokenType` enum, after `DETACH` |
| `YIELD` | `"YIELD"` | `TokenType` enum, after `CALL` |

---

## Procedure Interface

### `ivg.vector.search`

**Signature (Cypher)**:
```
CALL ivg.vector.search(label, property, query_input, limit [, options])
YIELD node, score
```

| Argument | Position | Type | Required | Notes |
|----------|----------|------|----------|-------|
| `label` | 0 | `String` or `$param` | Yes | Node label to filter |
| `property` | 1 | `String` or `$param` | Yes | Reserved for future multi-property; currently must be `'embedding'` |
| `query_input` | 2 | `$param` → `list[float]` OR `str` | Yes | Mode 1: pre-computed vector. Mode 2: text string |
| `limit` | 3 | `Integer` or `$param` | Yes | Max results to return (TOP N) |
| `options` | 4 | Map literal `{key: value}` | No | Optional config map |

**Options map keys**:

| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `similarity` | `"cosine"` \| `"dot_product"` | `"cosine"` | SQL function to use |
| `embedding_config` | `String` | None | Required when `query_input` is a text string (Mode 2) |

---

## Yield Output

| Variable | Type | Description |
|----------|------|-------------|
| `node` | Node Dict | `{"id": str, "labels": list[str], "properties": dict}` — full node shape |
| `score` | `float` | Similarity score. `[0.0, 1.0]` for cosine; unbounded for dot product |

---

## Entities

### `VectorSearchResult` (runtime, not persisted)

Represents the output of one row from `ivg.vector.search`:

```
VectorSearchResult {
  node: NodeDict {
    id: str
    labels: list[str]
    properties: dict[str, Any]
  }
  score: float
}
```

---

## SQL Translation Map

| Cypher | Generated SQL fragment |
|--------|----------------------|
| Mode 1: `$vector` bound to `list[float]` | `VECTOR_COSINE(e.emb, TO_VECTOR(?))` with param `json.dumps(vector)` |
| Mode 2: `$text` bound to `str` + `embedding_config: 'minilm'` | `VECTOR_COSINE(e.emb, EMBEDDING(?, ?))` with params `(text, 'minilm')` |
| `similarity: 'dot_product'` | `VECTOR_DOT_PRODUCT(e.emb, TO_VECTOR(?))` |
| `limit: 10` | `SELECT TOP 10 ...` |
| `label: 'Protein'` | `WHERE l.label = ?` with JOIN on `rdf_labels` |

---

## CTE Integration

The procedure call is translated into a named CTE `VecSearch` prepended to the WITH chain:

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
),
-- subsequent MATCH stages reference VecSearch.node and VecSearch.score
```

`context.variable_aliases` is pre-populated:
```python
{"node": "VecSearch", "score": "VecSearch"}
```

---

## State Transitions

No new lifecycle states. The procedure produces a stateless read result; no writes are performed.

---

## Validation Rules

| Rule | Enforcement point |
|------|------------------|
| `label` and `property` strings MUST pass `validate_table_name` (no SQL injection) | Translator, before SQL emission |
| `similarity` MUST be `"cosine"` or `"dot_product"` | Translator, raises `ValueError` for others |
| `embedding_config` MUST be present if `query_input` is a `str` | Translator, raises `ValueError` if absent |
| `limit` MUST be a positive integer | Translator, raises `ValueError` if `≤ 0` |
| `yield_items` MUST contain at least `"node"` and `"score"` | Parser, logs warning if extra items present |
| IRIS `EMBEDDING()` capability check | Engine, lazy probe cached per instance |
