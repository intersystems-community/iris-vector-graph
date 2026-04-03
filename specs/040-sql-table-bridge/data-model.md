# Data Model: SQL Table Bridge (040)

## New Tables

### Graph_KG.table_mappings

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `label` | VARCHAR(255) | PRIMARY KEY | Cypher label (e.g., `Patient`) |
| `sql_table` | VARCHAR(500) | NOT NULL | Fully-qualified SQL table (e.g., `HealthShare.Patient`) |
| `id_column` | VARCHAR(255) | NOT NULL | Column whose value forms node ID: `{label}:{id_value}` |
| `prop_columns` | VARCHAR(4000) | NULL | JSON array of exposed property columns; NULL = all scalar columns |
| `registered_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | Audit field |

**Primary key**: `label` — one SQL table per label (enforced at application level; duplicate label raises ValueError)

### Graph_KG.relationship_mappings

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `source_label` | VARCHAR(255) | PK (composite) | Source node label |
| `predicate` | VARCHAR(255) | PK (composite) | Edge predicate (e.g., `HAS_ENCOUNTER`) |
| `target_label` | VARCHAR(255) | PK (composite) | Target node label |
| `target_fk` | VARCHAR(255) | NULL | FK column on **target** table referencing source PK |
| `via_table` | VARCHAR(500) | NULL | M:M join table (NULL if target_fk is set) |
| `via_source` | VARCHAR(255) | NULL | Column in via_table → source PK |
| `via_target` | VARCHAR(255) | NULL | Column in via_table → target PK |

**Primary key**: `(source_label, predicate, target_label)` — one mapping per label-predicate-label triple
**Constraint**: exactly one of `target_fk` or `via_table` MUST be non-NULL (enforced at application level)

## Engine Cache Fields

```
IRISGraphEngine._table_mapping_cache: Dict[str, dict] | None
  Key: label (str)
  Value: {label, sql_table, id_column, prop_columns, registered_at}
  None = not yet loaded; populated on first access; invalidated on write

IRISGraphEngine._rel_mapping_cache: Dict[tuple, dict] | None
  Key: (source_label, predicate, target_label)
  Value: {predicate, source_label, target_label, target_fk, via_table, via_source, via_target}
  None = not yet loaded; same lifecycle as _table_mapping_cache
```

## Translator Context Additions

```
TranslationContext.mapped_node_aliases: Dict[str, dict]
  Key: SQL alias (e.g., "n0")
  Value: table_mappings row dict for the label bound to that alias
  Used by: translate_expression to route property access to direct column instead of rdf_props
```

## Entity Relationships

```
IRISGraphEngine
  ├── _table_mapping_cache ──→ Graph_KG.table_mappings
  └── _rel_mapping_cache ───→ Graph_KG.relationship_mappings

Cypher query execution:
  translate_node_pattern
    └── get_table_mapping(label) ─── cache hit → mapped_table JOIN
                                 └── cache miss → Graph_KG.nodes JOIN (unchanged)

  translate_relationship_pattern
    └── get_rel_mapping(src, pred, tgt) ── found → FK/via JOIN
                                       └── not found → rdf_edges JOIN (unchanged)

  translate_expression (PropertyReference)
    └── alias in mapped_node_aliases → alias.column_name
    └── alias not mapped → rdf_props JOIN (unchanged)
```
