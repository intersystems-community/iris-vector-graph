# Implementation Plan: Spec 100 — Cypher Variable-Length Path to BFS

**Branch**: `100-cypher-variable-path-to-bfs`  
**Date**: 2026-05-04

## Summary

Fix `MATCH (a)-[:R*1..N]-(b)` in IVG's Cypher translator to route to
`NKGAccel.BFSJson` instead of generating SQL CTEs. Current: 608ms, crashes at depth=3.
Target: <10ms, handles any depth.

## Technical Context

**Language**: Python  
**Primary file**: `iris_vector_graph/cypher/translator.py`  
**AST file**: `iris_vector_graph/cypher/ast.py`  
**Test target**: `iris-enterprise-2026` port 4972 OR `gqs-ivg-test` port 1972  
**LDBC data**: `/tmp/sf10_out/social_network-sf10-CsvBasic-LongDateFormatter/dynamic/`

## Key Code Locations

### ast.py — RelationshipPattern

```python
@dataclass
class RelationshipPattern:
    variable: Optional[str]
    types: List[str]
    min_hops: Optional[int]   # None = 1 (single hop)
    max_hops: Optional[int]   # None = 1 (single hop)
    direction: str            # "out", "in", "both"
    properties: Dict
```

When `min_hops` or `max_hops` is not None → variable-length path.

### translator.py — Current VL path handling

The translator currently generates CTEs when it sees variable-length paths.
Location: search for `min_hops` or `*` in translator.py to find the branch.

### Existing BFS call path (already works)

`CALL ivg.bfs($src, ['KNOWS'], 2) YIELD node` → routes to `NKGAccel.BFSJson` via
`_translate_call_procedure`. The fix reuses this exact path.

## Algorithm

### Detection

In `translate_match` or `_build_path_pattern`:
```python
if rel.min_hops is not None or rel.max_hops is not None:
    return _translate_vl_path_as_bfs(node_a, rel, node_b, context)
```

### Translation

```python
def _translate_vl_path_as_bfs(node_a, rel, node_b, context):
    src_id = resolve_node_id(node_a, context)
    predicates = rel.types  # [] = all predicates
    max_hops = rel.max_hops or 5
    min_hops = rel.min_hops or 1
    direction = rel.direction  # "out", "in", "both"
    
    # BFS returns [{s, p, o, w, step}]
    # Filter by step >= min_hops in post-processing
    # Map dst variable (node_b) to 'o' field
    # Register in context so RETURN clause can reference node_b.node_id
    
    return BFSExecutionPlan(src_id, predicates, max_hops, min_hops, direction, node_b_var)
```

### Execution

The BFS plan executes via `_call_classmethod_large(iris_obj, "Graph.KG.NKGAccel", "BFSJson", ...)`
and returns rows compatible with the RETURN clause.

## Edge Cases

- `[*]` (no bounds) → max_hops=10 default, min_hops=1
- `[*2]` (exact hop) → min_hops=max_hops=2  
- `[*..5]` (no min) → min_hops=1, max_hops=5
- `[r*1..2]` with `r` variable → expose step as r.step
- Multiple predicates `[:A|B*1..2]` → pass `["A","B"]` to BFSJson

## Performance Targets

| Query | Before | After |
|-------|--------|-------|
| `[*1..2]` LIMIT 50 | 608ms | <10ms |
| `[*1..3]` count | CRASH | <20ms |
| `[*1..4]` count | CRASH | <60ms |
