# Data Model: Named Path Bindings

**Feature**: 025-named-path-bindings | **Date**: 2026-03-27

## New AST Entity: NamedPath

```
NamedPath
├── variable: str          # The path variable name (e.g., "p")
└── pattern: GraphPattern  # The bound graph pattern
```

**Relationships**:
- Owned by `MatchClause` via new field `named_paths: List[NamedPath]`
- References existing `GraphPattern` (no change to GraphPattern)

**Validation**:
- `variable` must be a valid Cypher identifier (non-empty, starts with letter or underscore)
- `pattern` must be a valid `GraphPattern` (existing validation in `GraphPattern.__post_init__`)

## Modified Entity: MatchClause

```
MatchClause
├── patterns: List[GraphPattern]            # Existing (unchanged)
├── named_paths: List[NamedPath]            # NEW — default empty list
└── optional: bool                          # Existing (unchanged)
```

A MATCH clause can contain a mix of named and unnamed patterns. Named paths appear in `named_paths`; their underlying `GraphPattern` is also appended to `patterns` so JOIN generation proceeds as normal.

## Translation Context Extensions

```
TranslationContext (existing, extended)
├── named_paths: Dict[str, NamedPath]           # NEW — path variable name → AST node
├── path_node_aliases: Dict[str, List[str]]     # NEW — path var → ordered node SQL aliases
└── path_edge_aliases: Dict[str, List[str]]     # NEW — path var → ordered edge SQL aliases
```

Populated during MATCH → JOIN translation. Consumed during RETURN/WITH expression translation.

## Output Format: PathResult (JSON)

When `RETURN p` is executed, the SQL result column contains a JSON string:

```json
{
  "nodes": ["node_id_1", "node_id_2", "node_id_3"],
  "rels": ["PREDICATE_1", "PREDICATE_2"]
}
```

- `nodes` array length = number of `NodePattern` elements in the pattern
- `rels` array length = number of `RelationshipPattern` elements = nodes - 1
- Order follows traversal direction (start → end)

## No Schema Changes

This feature adds no tables, columns, or indexes to the IRIS database. All changes are in the Cypher parser/translator layer. The existing `Graph_KG` schema is sufficient.
