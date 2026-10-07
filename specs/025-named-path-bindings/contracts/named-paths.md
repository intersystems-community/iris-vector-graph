# Contracts: Named Path Bindings

**Feature**: 025-named-path-bindings | **Date**: 2026-03-27

## Contract 1: AST — NamedPath dataclass

```python
@dataclass(slots=True)
class NamedPath:
    variable: str
    pattern: GraphPattern
```

**Invariants**:
- `variable` is non-empty string
- `pattern` is a valid `GraphPattern` (passes existing __post_init__ validation)

## Contract 2: AST — MatchClause extension

```python
@dataclass(slots=True)
class MatchClause:
    patterns: List[GraphPattern]
    named_paths: List[NamedPath] = field(default_factory=list)  # NEW
    optional: bool = False
```

**Backward compatibility**: `named_paths` defaults to empty list. All existing code that constructs `MatchClause(patterns=[...])` or `MatchClause(patterns=[...], optional=True)` continues to work unchanged.

## Contract 3: Parser — named path detection

**Input**: Cypher string containing `MATCH p = (a)-[r]->(b) ...`

**Behavior**: When `parse_match_clause` encounters `IDENTIFIER EQUALS LPAREN`, it:
1. Consumes the IDENTIFIER (path variable name)
2. Consumes the EQUALS token
3. Calls `parse_graph_pattern()` as normal
4. Wraps the result in `NamedPath(variable=name, pattern=graph_pattern)`
5. Appends to both `match.named_paths` and `match.patterns`

**Non-named patterns**: When the next token after MATCH is `LPAREN` (no `IDENT =` prefix), behavior is unchanged.

## Contract 4: Translator — RETURN p

**Input**: `ReturnItem` where `expression` is `Variable(name="p")` and `p` is in `context.named_paths`

**Output SQL fragment**:
```sql
JSON_OBJECT('nodes': JSON_ARRAY(n0.node_id, n1.node_id, ...), 'rels': JSON_ARRAY(e0.p, e1.p, ...)) AS p
```

## Contract 5: Translator — length(p)

**Input**: `FunctionCall(function_name="length", arguments=[Variable(name="p")])` where `p` is a named path

**Output SQL fragment**: Integer literal equal to `len(path.pattern.relationships)`

## Contract 6: Translator — nodes(p)

**Input**: `FunctionCall(function_name="nodes", arguments=[Variable(name="p")])` where `p` is a named path

**Output SQL fragment**: `JSON_ARRAY(n0.node_id, n1.node_id, ...)`

## Contract 7: Translator — relationships(p)

**Input**: `FunctionCall(function_name="relationships", arguments=[Variable(name="p")])` where `p` is a named path

**Output SQL fragment**: `JSON_ARRAY(e0.p, e1.p, ...)`

## Contract 8: Error — invalid path reference

**Input**: `FunctionCall(function_name="nodes", arguments=[Variable(name="x")])` where `x` is NOT in `context.named_paths`

**Output**: Raises `CypherTranslationError("'x' is not a named path variable")`
