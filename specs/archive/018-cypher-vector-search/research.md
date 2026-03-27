# Research: 018-cypher-vector-search

**Phase**: 0 — Outline & Research  
**Date**: 2026-02-21  
**Status**: Complete — all NEEDS CLARIFICATION resolved

---

## 1. Lexer Changes Required

### Decision
Add two new keyword tokens to `TokenType` in `iris_vector_graph/cypher/lexer.py`: `CALL = "CALL"` and `YIELD = "YIELD"`.

### Rationale
The lexer resolves keywords via `TokenType[upper_value]` enum name lookup (`lexer.py:255`). Adding enum members is the only required change — the dispatch logic already handles any keyword whose name matches an uppercase string. No procedural code changes in `_tokenize_identifier_or_keyword` are needed.

### Dotted procedure names (`ivg.vector.search`)
The lexer will tokenize `ivg.vector.search` as three separate tokens: `IDENTIFIER("ivg")`, `DOT`, `IDENTIFIER("vector")`, `DOT`, `IDENTIFIER("search")`. The parser reconstructs the dotted name via a `while peek().kind == DOT` loop. No new lexer token type is required.

### PIPE / YIELD items
`YIELD node, score` uses only `IDENTIFIER` and `COMMA` — no PIPE. No change to `PIPE` handling needed.

### Alternatives considered
- Single `QUALIFIED_IDENTIFIER` token type for dotted names — rejected: adds lexer complexity with no benefit; parser reconstruction is two lines.

---

## 2. Parser Changes Required

### Decision
Insert a `CALL` branch at the start of `parse()` (`parser.py:58`), before `parse_query_part()`. Parse dotted name, argument list, and optional `YIELD` items. Store result as `CypherProcedureCall` on `CypherQuery.procedure_call`.

### Grammar
```
procedure_call ::= CALL dotted_name LPAREN arg_list RPAREN ( YIELD yield_items )?
dotted_name    ::= IDENTIFIER ( DOT IDENTIFIER )*
arg_list       ::= expression ( COMMA expression )* | ε
yield_items    ::= IDENTIFIER ( COMMA IDENTIFIER )*
```

### YIELD scope injection (outer scope semantics)
`CypherProcedureCall.yield_items` (already exists in `ast.py:267`) holds the variable names. The **translator** — not the parser — pre-populates `context.variable_aliases` with the yielded names mapped to the vector search CTE name. This allows subsequent `MATCH (node)-[:REL]->(other)` to resolve `node` through the existing `translate_expression()` path without any parser changes.

### Where to insert
`parse()` line 63: check `if self.peek().kind == TokenType.CALL` before the first `parse_query_part()` call.

### Alternatives considered
- `CALL` handled inside `parse_query_part()` — rejected: spec requires CALL to be a leading clause, not interleaved with MATCH.
- Separate `parse_call_query()` entry point — rejected: overcomplicates the existing single `parse()` entry point.

---

## 3. Translator Changes Required

### Decision
Add `translate_procedure_call()` to `translator.py`. The procedure call is translated into a named CTE (`VecSearch`) that is prepended to `context.stages`. Subsequent MATCH clauses reference this CTE via the existing stage-alias mechanism.

### Target SQL for Mode 1 (pre-computed vector `list[float]`)
```sql
SELECT TOP {limit}
    n.node_id AS node,
    VECTOR_COSINE(e.emb, TO_VECTOR(?)) AS score
FROM Graph_KG.nodes n
JOIN Graph_KG.rdf_labels l ON l.s = n.node_id
JOIN Graph_KG.kg_NodeEmbeddings e ON e.id = n.node_id
WHERE l.label = ?
ORDER BY score DESC
```
Parameters: `[json.dumps(query_vector), label_value]`

### Target SQL for Mode 2 (text + IRIS `EMBEDDING()`)
```sql
SELECT TOP {limit}
    n.node_id AS node,
    VECTOR_COSINE(e.emb, EMBEDDING(?, ?)) AS score
FROM Graph_KG.nodes n
JOIN Graph_KG.rdf_labels l ON l.s = n.node_id
JOIN Graph_KG.kg_NodeEmbeddings e ON e.id = n.node_id
WHERE l.label = ?
ORDER BY score DESC
```
Parameters: `[query_text, config_name, label_value]`

### CTE integration
- CTE name: `VecSearch` (not `Stage{n}` to avoid collision with existing stage numbering)
- `context.stages` is prepended with `"VecSearch AS (\n{sql}\n)"`
- `context.variable_aliases["node"] = "VecSearch"` and `context.variable_aliases["score"] = "VecSearch"` are pre-set
- `context.from_clauses` for subsequent MATCH starts with `VecSearch`

### Note on `VECTOR_DOT_PRODUCT`
Spec FR-006 allows both `cosine` and `dot_product`. The translator emits `VECTOR_COSINE` or `VECTOR_DOT_PRODUCT` based on the `similarity` key in the options map. Default is `cosine`.

### Alternatives considered
- Running the procedure call as a Python-side call and injecting results — rejected: defeats the purpose of SQL-level vector search and breaks composability.
- Subquery instead of CTE — rejected: CTE is already the pattern for multi-stage queries in the existing translator.

---

## 4. IRIS `EMBEDDING()` Detection Strategy

### Decision
**Lazy SQL probe, result cached on the engine instance.** Mirrors the existing `_ppr_sql_function_available` pattern in `engine.py:1008–1068`.

### Probe SQL
```python
cursor.execute("SELECT EMBEDDING('__ivg_probe__', '__nonexistent_config__')")
```
- If IRIS raises an error containing "EMBEDDING" + "not found"/"undefined"/"unknown function" → `EMBEDDING()` not supported.
- Any other error (e.g., config-not-found error) → `EMBEDDING()` is supported, config just doesn't exist.
- Success → `EMBEDDING()` fully supported.

### Cache location
Instance attribute `_embedding_function_available: Optional[bool] = None`. Set on first invocation of Mode 2 path only. Mode 1 callers incur zero probe overhead (FR-003d).

### Alternatives considered
- Version number comparison (`$ZVERSION` parsing) — rejected: brittle; spec explicitly requires capability probe, not version check.
- Catching raw SQL error from real query — rejected: user-supplied config name could cause confusing error messages; clean probe is safer.
- Eager init check — rejected: spec FR-003d requires lazy detection; Mode 1 must have zero overhead.

---

## 5. SQL Schema: `kg_NodeEmbeddings`

### Key facts

| Column | Type | Notes |
|--------|------|-------|
| `id` | `VARCHAR(256) %EXACT` | PK, FK to `nodes.node_id` |
| `emb` | `VECTOR(DOUBLE, N)` | N is `embedding_dimension` (default 768) |
| `metadata` | `%Library.DynamicObject` | Optional JSON |

- `kg_NodeEmbeddings` is in `VALID_GRAPH_TABLES` (`security.py:10`) — `_table('kg_NodeEmbeddings')` works today.
- **No HNSW index is created by `initialize_schema()`** — it exists only in static SQL files and the migration utility.
- IRIS automatically routes `SELECT TOP N ... ORDER BY VECTOR_COSINE(emb, TO_VECTOR(?)) DESC` through the HNSW ANN path when the HNSW index exists. No SQL hint required.

### Decision: where to query
Query `kg_NodeEmbeddings` directly (not `kg_NodeEmbeddings_optimized`). The optimized table is a migration path; the primary table is the spec's stated target. Plan includes a task to ensure the HNSW index exists before tests run.

---

## 6. Node Hydration Pattern

### Decision
Two-query hydration post vector search. The vector search SQL returns `(node_id, score)` pairs. Node dicts are retrieved via the existing `engine.get_nodes(ids)` call, which runs two separate queries (labels, then properties). Results are zipped.

### Rationale
Joining `kg_NodeEmbeddings`, `rdf_labels`, `rdf_props` in a single SQL for hydration would produce a cross-product and is not the existing pattern. `get_nodes()` is already correct and battle-tested. The translator's CTE produces `(node, score)` columns; the Python engine layer calls `get_nodes()` on the returned `node` IDs to produce the full node dicts.

### Note on column aliasing
The CTE exposes `node_id AS node`. When the YIELD variable `node` is referenced in a subsequent `MATCH (node)-[:REL]->(other)`, the translator must join `rdf_edges` using `VecSearch.node` as the subject ID, not further expand it into a hydrated dict at SQL level. The hydration happens at the Python result-processing layer in `execute_cypher()`.

---

## 7. iris-devtester e2e Pattern

### Current state (Principle IV violation)
The existing `tests/integration/conftest.py` uses `os.getenv("IRIS_PORT", 1972)` — hardcoded default port, no container management. The primary `tests/conftest.py` uses `IRISContainer(image=...).start()` with container name `"iris-vector-graph-main"` — not `"iris_vector_graph"`.

### Decision for feature 018 e2e tests
New e2e test file `tests/e2e/test_cypher_vector_search.py` will use:
```python
import os
import pytest
from iris_devtester import IRISContainer

SKIP = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"

@pytest.fixture(scope="session")
def iris_conn():
    container = IRISContainer.attach("iris_vector_graph")
    port = container.get_exposed_port(1972)
    # ... connect
```
This is fully compliant with Constitution Principle IV.

### Existing conftest violations
The plan includes tasks to fix `tests/integration/conftest.py` and `tests/conftest.py`. These are pre-existing issues; the fix is scoped as a separate task group in the plan (non-blocking for 018 implementation, but blocking for 018 e2e tests).

---

## 8. `VECTOR_DOT_PRODUCT` Support

### Decision
Both `cosine` and `dot_product` are supported as translator options. The translator emits:
- `{similarity: 'cosine'}` → `VECTOR_COSINE(e.emb, TO_VECTOR(?))`
- `{similarity: 'dot_product'}` → `VECTOR_DOT_PRODUCT(e.emb, TO_VECTOR(?))`
- Any other value → `ValueError` with message listing valid options

`VECTOR_DOT_PRODUCT` is not currently used in any active code path — only `VECTOR_COSINE` is. This feature will be the first active use of `VECTOR_DOT_PRODUCT` in the library.

---

## All NEEDS CLARIFICATION resolved

| Item | Resolution |
|------|-----------|
| CALL token in lexer | Add `CALL = "CALL"`, `YIELD = "YIELD"` to `TokenType` enum |
| Dotted name tokenization | Three IDENTIFIER+DOT tokens; parser reconstructs string |
| YIELD scope injection | Translator pre-populates `variable_aliases` from `yield_items` |
| Vector input mode 1 | `list[float]` → `json.dumps()` → `TO_VECTOR(?)` |
| Vector input mode 2 | `str` + `embedding_config` → `EMBEDDING(?, ?)` with lazy IRIS probe |
| Node shape | Two-query hydration via `get_nodes()` after SQL returns `(node_id, score)` |
| HNSW index auto-use | Yes, via `TOP N + ORDER BY VECTOR_COSINE(...) DESC` — no hint needed |
| e2e test container | `IRISContainer.attach("iris_vector_graph").get_exposed_port(1972)` |
