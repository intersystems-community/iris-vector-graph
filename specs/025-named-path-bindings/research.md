# Research: Named Path Bindings

**Feature**: 025-named-path-bindings | **Date**: 2026-03-27

## R1: Parser Lookahead Strategy for `p = (pattern)`

**Decision**: 2-token lookahead in `parse_match_clause` — peek for `IDENTIFIER` followed by `EQUALS` before the opening `LPAREN`.

**Rationale**: The parser already calls `parse_graph_pattern()` which expects to start with `(`. A named path is syntactically `IDENTIFIER EQUALS graph_pattern`. The `EQUALS` token already exists in the lexer (TokenType.EQUALS). We check: if next token is IDENTIFIER and the one after is EQUALS, consume both and record the variable name, then proceed with normal `parse_graph_pattern()`.

**Alternatives considered**:
- Lark grammar rule: Rejected — parser is hand-written recursive descent, not Lark-generated.
- New `ASSIGN` token distinct from `EQUALS`: Rejected — unnecessary; context (inside MATCH before `(`) is unambiguous.

## R2: SQL Translation for Path as JSON Object

**Decision**: `RETURN p` translates to a `JSON_OBJECT` expression that aggregates the node aliases and relationship predicates from the JOIN chain into `{"nodes": [...], "rels": [...]}`.

**Rationale**: IRIS SQL has `JSON_ARRAY` and `JSON_OBJECT` (2023.1+). The translator already knows all node aliases in the JOIN chain from `GraphPattern.nodes` and all relationship aliases from `GraphPattern.relationships`. For a 2-hop pattern `(a)-[r1]->(b)-[r2]->(c)`, the SQL becomes:

```sql
JSON_OBJECT(
  'nodes': JSON_ARRAY(n0.node_id, n1.node_id, n2.node_id),
  'rels': JSON_ARRAY(e0.p, e1.p)
)
```

**Alternatives considered**:
- Comma-delimited string: Rejected — harder to parse client-side, no standard structure.
- Multiple columns (p_nodes, p_rels, p_length): Rejected — spec requires `RETURN p` as single value.

## R3: Path Function Translation

**Decision**: `length(p)`, `nodes(p)`, and `relationships(p)` are translated by checking if the argument is a registered path variable in the translation context, then emitting the appropriate SQL fragment.

| Function | SQL Translation |
|----------|----------------|
| `length(p)` | Integer literal = number of relationships in the pattern (known at parse time for fixed-length) |
| `nodes(p)` | `JSON_ARRAY(n0.node_id, n1.node_id, ...)` |
| `relationships(p)` | `JSON_ARRAY(e0.p, e1.p, ...)` |

**Rationale**: For fixed-length patterns, the hop count is statically known from the AST. The node/edge aliases are already tracked in the TranslationContext. No runtime computation needed — just emit the right column references.

**Alternatives considered**:
- Runtime path length via COUNT: Rejected — unnecessary for fixed-length; would be needed for Phase 2 variable-length.

## R4: TranslationContext Path Registry

**Decision**: Add a `named_paths: Dict[str, NamedPath]` field to `TranslationContext` that maps path variable names to their AST `NamedPath` objects. Also store `path_node_aliases: Dict[str, List[str]]` and `path_edge_aliases: Dict[str, List[str]]` that map path variable names to the ordered SQL alias lists generated during JOIN construction.

**Rationale**: When the translator encounters `RETURN p` or `nodes(p)`, it looks up `p` in the path registry to find which SQL aliases correspond to the path's nodes and edges. This is populated during MATCH clause translation (when JOINs are generated), before RETURN clause translation (which needs the alias lists).

**Alternatives considered**:
- Resolve at parse time: Rejected — SQL aliases aren't known until translation.

## R5: Error Handling for Invalid Path References

**Decision**: When `length(x)`, `nodes(x)`, or `relationships(x)` is called where `x` is not a registered path variable, raise a `CypherTranslationError` with a message indicating that `x` is not a named path.

**Rationale**: FR-006 requires clear error reporting. The translator already raises `CypherTranslationError` for other invalid references.

**Alternatives considered**:
- Fall through to SQL (let IRIS error): Rejected — cryptic IRIS SQL errors are not user-friendly.
