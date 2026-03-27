# Enhancement: Named Path Bindings (`p = (a)-[r]->(b)`)

**Date**: 2026-03-26
**Status**: Requested
**Affects**: `iris_vector_graph/cypher/ast.py`, `parser.py`, `translator.py`

---

## Problem

IVG's Cypher parser recognises node and relationship patterns but has no mechanism
to bind a matched path to a variable. This means callers cannot:

- Access the full path as a value (`RETURN p`)
- Use path functions: `length(p)`, `nodes(p)`, `relationships(p)`
- Pass a path to further processing stages

The openCypher specification (§ 6.3) requires named path support. It is absent from
`ast.py` (no `NamedPath` dataclass), `parser.py` (no assignment parse rule), and
`translator.py` (no SQL rendering for path objects).

---

## Proposed API (Cypher syntax)

```cypher
MATCH p = (a:Protein)-[:INTERACTS_WITH*1..3]->(b:Protein)
RETURN p, length(p), nodes(p), relationships(p)
```

```cypher
MATCH p = (src)-[r]->(dst)
WHERE src.id = $id
RETURN nodes(p) AS path_nodes, length(p) AS hops
```

---

## AST Changes

Add to `iris_vector_graph/cypher/ast.py`:

```python
@dataclass(slots=True)
class NamedPath:
    """Named path binding: p = (a)-[r]->(b)"""
    variable: str
    pattern: GraphPattern
```

Extend `MatchClause`:

```python
@dataclass(slots=True)
class MatchClause:
    patterns: List[GraphPattern]
    named_paths: List[NamedPath] = field(default_factory=list)  # NEW
    optional: bool = False
```

Add path functions to `FunctionCall` resolution in translator:
- `length(p)` → hop count of the path
- `nodes(p)` → list of node IDs in traversal order
- `relationships(p)` → list of relationship IDs in traversal order

---

## SQL Translation Strategy

IRIS SQL has no native path type. Two approaches:

**Phase 1 (MVP) — JSON array serialisation**

Translate `RETURN nodes(p)` → `JSON_ARRAY(node_ids...)` built from the JOIN chain.
For variable-length paths, use the existing recursive CTE / UNION ALL traversal and
aggregate node IDs into a JSON array column.

```sql
-- nodes(p) for fixed (a)-[r]->(b)
SELECT JSON_ARRAY(a.node_id, b.node_id) AS p_nodes,
       1 AS p_length
FROM Graph_KG.nodes a
JOIN Graph_KG.rdf_edges r ON r.s = a.node_id
JOIN Graph_KG.nodes b ON b.node_id = r.o_id
```

**Phase 2 — Variable-length paths**

Extend the existing `generate_shortest_path_sql` helper (translator.py:808) to also
return `path_nodes` and `path_relationships` JSON arrays alongside the path string.

---

## IRIS SQL Constraints

- No native PATH type — paths must be serialised as JSON arrays or comma-delimited strings
- `JSON_ARRAY` available in IRIS 2023.1+ (already used in codebase)
- `length(p)` for variable-length paths requires counting JOIN depth from CTE recursion
- `RETURN p` (full path object) translates to a JSON object: `{"nodes": [...], "rels": [...]}`

---

## Files to Change

| File | Change |
|------|--------|
| `iris_vector_graph/cypher/ast.py` | Add `NamedPath` dataclass; extend `MatchClause` |
| `iris_vector_graph/cypher/parser.py` | Parse `var =` prefix before pattern in MATCH |
| `iris_vector_graph/cypher/translator.py` | Render named path as JSON array columns; handle `length()`, `nodes()`, `relationships()` |
| `tests/unit/test_named_paths.py` | Unit tests (parse, translate, function calls) |
| `tests/integration/test_named_paths_e2e.py` | E2E: fixed path, variable-length path, path functions |

---

## Acceptance Criteria

- [ ] `NamedPath` AST node added with `variable` + `pattern` fields
- [ ] Parser handles `p = (a)-[r]->(b)` in MATCH clause
- [ ] `RETURN p` translates to JSON object `{"nodes": [...], "rels": [...]}`
- [ ] `length(p)` returns integer hop count
- [ ] `nodes(p)` returns ordered list of node IDs
- [ ] `relationships(p)` returns ordered list of relationship IDs
- [ ] Variable-length path `p = (a)-[*1..3]->(b)` supported (Phase 2)
- [ ] Unit tests: parse named path, translate fixed + variable-length
- [ ] E2E test: named path query against live IRIS with ≥3-node graph

---

## Known Limitation

`RETURN p` produces a JSON representation, not a native Cypher Path object.
Callers consuming the result in Python receive a dict, not a path cursor.
This is acceptable for Phase 1; a Python `PathResult` wrapper class can be
added in Phase 2.
