# Implementation Plan: SQL Table Bridge

**Branch**: `040-sql-table-bridge` | **Date**: 2026-04-03 | **Spec**: [spec.md](spec.md)

---

## Summary

Add `map_sql_table` / `map_sql_relationship` / `attach_embeddings_to_table` to `IRISGraphEngine`, backed by two new metadata tables (`Graph_KG.table_mappings`, `Graph_KG.relationship_mappings`). Intercept `translate_node_pattern` in the Cypher translator: when a label has a SQL mapping, replace the `Graph_KG.nodes + rdf_labels JOIN` with a direct `FROM mapped_table alias`. Relationship mappings replace the `rdf_edges JOIN` with a FK join or via-table join. Mappings cached in engine instance; invalidated on write.

---

## Technical Context

**Language/Version**: Python 3.11
**Primary files changed**:
- `iris_vector_graph/engine.py` — 6 new public methods
- `iris_vector_graph/schema.py` — 2 new DDL tables
- `iris_vector_graph/cypher/translator.py` — `translate_node_pattern` + `translate_relationship_pattern` interception
- `iris_vector_graph/security.py` — `sanitize_identifier` already handles external table names (no allowlist change needed — mapped tables bypass `_table()`)

**New tables**:
- `Graph_KG.table_mappings` — persists node label → SQL table mappings
- `Graph_KG.relationship_mappings` — persists predicate → FK/via-table mappings

**Container**: `iris_vector_graph` (verified: `docker-compose.yml` line 4, `tests/conftest.py` line 186)
**Test count baseline**: 353 (verified: `pytest tests/unit/ --co -q`)
**Schema prefix**: `Graph_KG` (verified: `engine.py:59` `set_schema_prefix("Graph_KG")`)

---

## Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Library-First | ✅ | All changes in `iris_vector_graph/` |
| II. Compatibility-First | ✅ | `translate_node_pattern` change is additive — unmapped labels unchanged (FR-014, SC-007) |
| III. Test-First | ✅ | Tests written before implementation in each phase |
| IV. E2E Testing | ✅ | E2E tests against live `iris_vector_graph` container. Container name verified from `docker-compose.yml`. |
| V. Simplicity | ✅ | No new abstraction layers — bridge is a cache dict + two SQL tables + translator intercept |
| VI. Grounding | ✅ | Container `iris_vector_graph` (docker-compose.yml:4), schema `Graph_KG` (engine.py:59), test count 353 (pytest --co). All verified before writing. |

**Gate**: All green. Proceed.

---

## Phase 0: Research Findings

### Decision 1: Translator intercept point

**Decision**: Intercept at `translate_node_pattern` by checking `context._engine.get_table_mapping(label)` before the standard `Graph_KG.nodes` FROM clause is appended.

**Rationale**: `translate_node_pattern` (translator.py:951) is the single point where every node label routes to `Graph_KG.nodes`. Adding a check here is minimal, targeted, and backward-compatible — if `get_table_mapping(label)` returns None, the existing path runs unchanged.

**Key code path verified**:
```python
# Current (translator.py:956-957):
nodes_tbl = _table('nodes')
if not context.from_clauses: context.from_clauses.append(f"{nodes_tbl} {alias}")
# Bridge intercept goes HERE — before this block
```

### Decision 2: Mapped table security

**Decision**: External mapped table names use `sanitize_identifier()` (alphanumeric + underscore + dot only) but do NOT go through `_table()` / `validate_table_name()` since they are not in `VALID_GRAPH_TABLES`. The fully-qualified table name is stored at `map_sql_table` time after `sanitize_identifier` validation. No SQL injection risk from the stored name since it is validated at registration.

**Rationale**: Adding every user-mapped table to `VALID_GRAPH_TABLES` is not feasible (dynamic). `sanitize_identifier` already allows `Schema.Table` format with dot. Validated at registration time, not at query time.

### Decision 3: Cache implementation

**Decision**: `_table_mapping_cache: Dict[str, dict] | None = None` on the engine instance. Lazily loaded on first Cypher query involving label routing. Invalidated (set to None) when `map_sql_table` or `remove_table_mapping` is called. Re-loaded on next access. Thread safety is not required (same assumption as rest of engine).

**Rationale**: Simple dict, zero overhead when cache is warm. Consistent with rest of engine's stateful-but-not-thread-safe design.

### Decision 4: Property column routing in RETURN

**Decision**: When a mapped node alias resolves to a SQL table, `translate_expression` for `PropertyReference(n, 'some_col')` emits `alias.some_col` directly (same column name as in the mapped table). The `rdf_props` JOIN path is NOT taken for mapped nodes. Unmapped nodes continue to use `rdf_props`.

**Rationale**: Mapped tables have native SQL columns — joining `rdf_props` would return no results since mapped rows aren't in `rdf_props`. The translator must know whether a node alias is mapped to resolve property access correctly. Store this in `context.mapped_node_aliases: Dict[str, str]` (alias → sql_table_name).

### Decision 5: `attach_embeddings_to_table` threading

**Decision**: Runs synchronously in the calling thread. No background workers. Caller uses `progress_callback` for feedback (already in spec). For 50K rows at 1K/min, a notebook/script user waits ~50 minutes — acceptable given the logging already in `load_networkx`.

**Alternatives considered**: asyncio background task — rejected (adds complexity, not in constitution scope).

### Decision 6: `map_sql_table` called twice for same label

**Decision**: UPDATE the existing mapping (upsert semantics). Invalidates cache. Consistent with edge case spec: "update not error."

**Implementation**: `INSERT OR UPDATE` / `REPLACE INTO` equivalent in IRIS SQL: `UPDATE ... WHERE label = ?; if rowcount == 0: INSERT ...`

---

## Phase 1: Data Model

### New tables added to `GraphSchema.initialize_schema()`

**`Graph_KG.table_mappings`**
```sql
CREATE TABLE Graph_KG.table_mappings (
    label        VARCHAR(255) NOT NULL PRIMARY KEY,
    sql_table    VARCHAR(500) NOT NULL,
    id_column    VARCHAR(255) NOT NULL,
    prop_columns VARCHAR(4000),  -- JSON array or NULL (= all columns)
    registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
```

**`Graph_KG.relationship_mappings`**
```sql
CREATE TABLE Graph_KG.relationship_mappings (
    predicate    VARCHAR(255) NOT NULL,
    source_label VARCHAR(255) NOT NULL,
    target_label VARCHAR(255) NOT NULL,
    target_fk    VARCHAR(255),  -- FK column on target table; NULL if via_table
    via_table    VARCHAR(500),  -- join table for M:M; NULL if target_fk
    via_source   VARCHAR(255),  -- col in via_table → source PK
    via_target   VARCHAR(255),  -- col in via_table → target PK
    PRIMARY KEY (source_label, predicate, target_label)
)
```

Both tables added to `GraphSchema._DDL_STATEMENTS` and `GraphSchema._EXISTING_TABLES_CHECK`.

### Engine cache fields (added to `IRISGraphEngine.__init__`)

```python
self._table_mapping_cache: Optional[Dict[str, dict]] = None
self._rel_mapping_cache: Optional[Dict[tuple, dict]] = None
```

---

## Phase 2: Python API Contracts

### New `IRISGraphEngine` methods

```python
def map_sql_table(
    self,
    table: str,                          # fully-qualified: "Schema.Table"
    id_column: str,                      # PK column → becomes node_id prefix
    label: str,                          # Cypher label
    property_columns: list[str] = None,  # None = all columns
) -> dict:
    """Register existing SQL table as a virtual node set.
    Returns: {"label": str, "sql_table": str, "id_column": str, "registered_at": str}
    Raises: ValueError if table/column not found, or label already mapped to different table.
    """

def map_sql_relationship(
    self,
    source_label: str,
    predicate: str,
    target_label: str,
    target_fk: str = None,    # FK on target table → source PK
    via_table: str = None,    # M:M join table
    via_source: str = None,   # via_table col → source PK
    via_target: str = None,   # via_table col → target PK
) -> dict:
    """Register relationship between two mapped labels.
    Returns: {"source_label", "predicate", "target_label", "target_fk", ...}
    Raises: ValueError if neither target_fk nor via_table provided.
    Raises: ValueError if source_label or target_label not registered as mapped nodes.
    """

def attach_embeddings_to_table(
    self,
    label: str,                           # must already be mapped
    text_columns: list[str],              # columns to concatenate for embedding
    batch_size: int = 1000,
    force: bool = False,                  # re-embed existing rows
    progress_callback = None,             # callable(n_done, n_total)
) -> dict:
    """Generate and store embeddings for all rows in a mapped table.
    Returns: {"embedded": int, "skipped": int, "total": int}
    Raises: TableNotMappedError if label not registered.
    """

def list_table_mappings(self) -> dict:
    """Return all registered node and relationship mappings.
    Returns: {"nodes": [...], "relationships": [...]}
    """

def remove_table_mapping(self, label: str) -> None:
    """Remove a node mapping and all its associated relationship mappings.
    Invalidates cache.
    Raises: ValueError if label not found.
    """

def reload_table_mappings(self) -> None:
    """Force-reload mapping cache from database. For multi-process scenarios."""
```

### Translator context additions

```python
# Added to TranslationContext.__init__:
self.mapped_node_aliases: Dict[str, str] = {}  # alias → sql_table_name
```

### Translator intercept in `translate_node_pattern`

```python
# Before the existing `nodes_tbl = _table('nodes')` block:
engine = getattr(context, '_engine', None)
if engine and node.labels:
    for label in node.labels:
        mapping = engine.get_table_mapping(label)  # returns None if not mapped
        if mapping:
            sql_table = mapping['sql_table']
            id_col = mapping['id_column']
            # Replace Graph_KG.nodes with direct mapped table
            if not context.from_clauses:
                context.from_clauses.append(f"{sql_table} {alias}")
            else:
                context.join_clauses.append(f"JOIN {sql_table} {alias} ON ...")
            context.mapped_node_aliases[alias] = mapping
            # Do NOT add rdf_labels JOIN
            return  # skip standard node pattern
```

### `translate_expression` update for mapped properties

```python
# In translate_expression, PropertyReference handling:
if alias in context.mapped_node_aliases:
    mapping = context.mapped_node_aliases[alias]
    id_col = mapping['id_column']
    if expr.property_name in ("id", "node_id"):
        return f"{alias}.{id_col}"
    # All other properties: direct column access
    return f"{alias}.{sanitize_identifier(expr.property_name)}"
```

---

## Phase 3: Test Plan

### Unit tests (mock engine, no IRIS) — `tests/unit/test_sql_table_bridge.py`

| # | Test | Verifies |
|---|------|---------|
| U1 | `map_sql_table` stores to `table_mappings` and populates cache | FR-001, FR-002 |
| U2 | `map_sql_table` called twice → updates existing mapping (upsert) | Edge case |
| U3 | `get_table_mapping` returns cached value; DB not hit on second call | NFR-006 |
| U4 | `remove_table_mapping` clears cache | FR-013 |
| U5 | Unmapped label → `get_table_mapping` returns None | FR-014 |
| U6 | Cypher MATCH with mapped label generates SQL against mapped table (no `Graph_KG.nodes`) | FR-003 |
| U7 | Cypher MATCH with unmapped label generates SQL against `Graph_KG.nodes` (no regression) | FR-014, SC-007 |
| U8 | Cypher MATCH with FK relationship generates correct JOIN | FR-006 |
| U9 | Cypher MATCH with via-table relationship generates correct JOIN | FR-006 |
| U10 | Mixed mapped + native label in one MATCH | FR-008 |
| U11 | `translate_expression` for mapped node property → `alias.column_name` | FR-009 |
| U12 | `translate_expression` for native node property → `rdf_props` JOIN | FR-009 |
| U13 | `map_sql_relationship` without target_fk or via_table → ValueError | Edge case |
| U14 | SQL mapping wins over native rows: FR-016 check | FR-016 |

### E2E tests (live IRIS) — `tests/unit/test_sql_table_bridge.py::TestSQLTableBridgeE2E`

| # | Test | Verifies |
|---|------|---------|
| E1 | Create a real SQL table; `map_sql_table`; run `MATCH (n:T)` → correct rows | SC-001 |
| E2 | Cypher vs direct SQL result comparison (identical results) | SC-001 |
| E3 | `MATCH (n:T) RETURN count(n)` matches `SELECT COUNT(*)` | US1 AC3 |
| E4 | FK relationship traversal returns correct joined rows | SC-005, US2 AC1 |
| E5 | Via-table M:M traversal | US2 AC2 |
| E6 | WHERE filter pushdown: verify generated SQL contains WHERE on mapped table | FR-009 |
| E7 | Zero rows written to `Graph_KG.nodes` during mapped queries | SC-004 |
| E8 | `remove_table_mapping` → label falls back to `Graph_KG.nodes` | US5 AC2 |
| E9 | `map_sql_table` on missing table → clear error | FR-015 |
| E10 | `map_sql_table` performance on metadata-only table (<5s, any size) | SC-002 |
| E11 | `attach_embeddings_to_table` → embeddings in `kg_NodeEmbeddings` | US4 AC1 |
| E12 | `attach_embeddings_to_table` idempotent: re-run skips existing | US4 AC3, FR-011 |
| E13 | `attach_embeddings_to_table(force=True)` re-embeds all | FR-011 |
| E14 | Full regression: all 353 existing unit tests still pass | SC-007, NFR-004 |

---

## Phase 4: File Changeset

| File | Change |
|------|--------|
| `iris_vector_graph/schema.py` | Add `table_mappings` and `relationship_mappings` DDL to `_DDL_STATEMENTS` |
| `iris_vector_graph/engine.py` | Add 6 new public methods + `_table_mapping_cache` + `_rel_mapping_cache` fields |
| `iris_vector_graph/cypher/translator.py` | Intercept in `translate_node_pattern` + `translate_relationship_pattern` + `translate_expression` property routing + `context.mapped_node_aliases` field |
| `iris_vector_graph/cypher/ast.py` | No changes |
| `iris_vector_graph/security.py` | No changes (`sanitize_identifier` already handles Schema.Table format) |
| `tests/unit/test_sql_table_bridge.py` | New file: 14 unit + 14 E2E tests |
| `pyproject.toml` | Version → 1.44.0 |
| `README.md` | Add SQL Table Bridge section |

---

## Phase 5: Version and Delivery

**Version**: `1.44.0`
**Delivery checklist**:
- [ ] 14 unit tests written (TDD — fail first)
- [ ] 14 E2E tests written (TDD — fail first)
- [ ] DDL added to `GraphSchema`
- [ ] `map_sql_table` / `map_sql_relationship` / `attach_embeddings_to_table` / `list` / `remove` / `reload` implemented
- [ ] Translator intercept implemented + `mapped_node_aliases` context field
- [ ] `translate_expression` updated for mapped property access
- [ ] All 353 + new tests pass
- [ ] SC-001 (Cypher = SQL), SC-003 (2× perf), SC-004 (zero writes) verified
- [ ] README updated
- [ ] Committed, tagged, published
