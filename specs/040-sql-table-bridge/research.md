# Research: SQL Table Bridge (040)

## Decision 1: Translator intercept point

**Decision**: Intercept at `translate_node_pattern` in `translator.py:951`.
**Rationale**: Single routing point. Check `context._engine.get_table_mapping(label)` before the `Graph_KG.nodes` FROM clause is appended. If None → existing path unchanged. If found → replace with direct mapped table.
**Verified**: `translate_node_pattern` at line 951 unconditionally does `context.from_clauses.append(f"{_table('nodes')} {alias}")`. This is the exact line to intercept.

## Decision 2: Security for external table names

**Decision**: `sanitize_identifier()` (already in `security.py`) validates external table names at registration time. `_table()` / `VALID_GRAPH_TABLES` not used for mapped tables.
**Rationale**: `sanitize_identifier` allows `Schema.Table.Column` with alphanumeric + underscore + dot — correct for IRIS fully-qualified names. No SQL injection risk since validated at `map_sql_table` call time.
**Verified**: `security.py:31` pattern `r'^[a-zA-Z0-9_\.]+$'` — covers `HealthShare.Patient`, `RAG.SourceDocuments` etc.

## Decision 3: Cache implementation

**Decision**: `_table_mapping_cache: Dict[str, dict] | None` on engine instance. Lazily loaded on first cache miss. Invalidated (set to None) on any write (`map_sql_table`, `remove_table_mapping`).
**Rationale**: Zero overhead on warm path. Consistent with rest of engine design. Thread safety not required.
**Alternative rejected**: Re-read DB per query (adds one SQL round-trip to every Cypher execution).

## Decision 4: Property access for mapped nodes

**Decision**: Add `context.mapped_node_aliases: Dict[str, dict]` (alias → mapping dict). In `translate_expression`, when `PropertyReference(variable, property_name)` and `alias in context.mapped_node_aliases` → emit `alias.property_name` directly. When `property_name in ('id', 'node_id')` → emit `alias.{id_column}`.
**Rationale**: Mapped tables have native SQL columns. `rdf_props` JOIN path returns nothing for mapped rows. Must distinguish at translation time.
**Implementation detail**: `context.mapped_node_aliases` set in `translate_node_pattern` when mapping found.

## Decision 5: `translate_relationship_pattern` intercept

**Decision**: In `translate_relationship_pattern`, after `edge_alias` is set, check if BOTH `source_label` and `target_label` have a relationship mapping. If yes: replace `rdf_edges JOIN` with either:
- **FK-based**: `JOIN target_table {target_alias} ON {target_alias}.{target_fk} = {source_alias}.{id_column}`
- **Via-table**: `JOIN via_table vt ON vt.{via_source} = {source_alias}.{id_column} JOIN target_table {target_alias} ON {target_alias}.{id_column} = vt.{via_target}`
**Verified**: `translate_relationship_pattern` at line ~985. The `rdf_edges JOIN` is added at lines 1068-1080.

## Decision 6: `table_mappings` new metadata tables

**Decision**: Create `Graph_KG.table_mappings` and `Graph_KG.relationship_mappings` in `GraphSchema.initialize_schema()`.
**Rationale**: Mappings must survive engine restart (FR-002). Using the same `Graph_KG` schema as other IVG metadata tables for consistency.
**IRIS DDL constraint**: IRIS SQL `CREATE TABLE IF NOT EXISTS` — use the same pattern as existing DDL in `schema.py`.

## Decision 7: `attach_embeddings_to_table` ID format

**Decision**: Node IDs stored as `{label}:{id_value}` in `kg_NodeEmbeddings`. E.g., `Patient:P001`.
**Rationale**: Consistent with temporal edge patterns and the existing FHIR bridge (`MeSH:D001234`). Avoids collisions between different mapped tables with overlapping ID values.
**Verified**: spec FR-010 states this explicitly.

## Infrastructure Verification (Constitution VI)

| Detail | Value | Source |
|--------|-------|--------|
| Container name | `iris_vector_graph` | `docker-compose.yml:4` |
| IRIS port | Dynamic (devtester) | `docker-compose.yml:5` — `"${IRIS_PORT:-1972}:1972"` |
| Schema prefix | `Graph_KG` | `engine.py:59` `set_schema_prefix("Graph_KG")` |
| Test baseline | 353 | `pytest tests/unit/ --co -q` |
| `sanitize_identifier` location | `security.py:18` | Verified allows `Schema.Table` |
| `translate_node_pattern` location | `translator.py:951` | Verified intercept point |
