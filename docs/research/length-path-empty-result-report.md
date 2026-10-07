<!-- markdownlint-disable MD013 MD060 -->

# Draft: variable-length path with `length(path)` returns no rows (not filed)

Status: **RESOLVED locally in spec 025's amendment of 2026-10-07; this draft is not filed and need not be.** Originally **DRAFT, not filed, kept local.** Found 2026-10-07 while timing `execute_cypher`
(`docs/research/cypher-stage-timing.md`). Target: `intersystems-community/iris-vector-graph`.
Not searched for duplicates.

## Summary

A variable-length pattern whose RETURN uses a path function returns **zero rows, with no
error**, when the source is bound by a property or label. The same pattern without the
path function returns the right rows.

## Reproduction

Graph: 5,000 `Proposition` nodes, each with a property `id` equal to its node id and
three outgoing `depends_on` edges (SQL confirms `prop23` has three).

```cypher
MATCH path = (p:Proposition {id: 'prop23'})-[:depends_on*1..3]->(dep)
RETURN dep.id, length(path) AS depth            -- 0 rows, no error

MATCH path = (p:Proposition {id: 'prop23'})-[:depends_on*1..3]->(dep)
RETURN dep.id                                   -- 39 rows
```

| Variant                                                            |                                             Rows |
| ------------------------------------------------------------------ | -----------------------------------------------: |
| `RETURN dep.id` (source `{id: ...}` with label)                    |                                               39 |
| `RETURN dep.id, length(path) AS depth`, same source                |                                                0 |
| `RETURN length(path) AS d` only                                    |                                                0 |
| `RETURN dep.id, nodes(path)`                                       |                                                0 |
| `*1..1` with `length(path)`                                        |                                                0 |
| source `{node_id: 'prop23'}` with no label, `dep.id, length(path)` | 39, but **the `length(path)` column is missing** |

Same result with `IVG_DISABLE_ARNO=1`.

## Cause (read from the code, then confirmed by running pieces of it)

Two separate defects.

1. **Wrong source id.** `_route_var_length` sends any query with `return_path_funcs` to
   `_execute_var_length_cypher` (`_engine/query.py:1113`). That function ignores the
   `src_id_param` the router just resolved and takes the **first string in the SQL
   parameter list** that does not start with `Graph_KG` (`query.py:2124-2127`). For this
   query the list begins with the label parameter, so BFS starts from a node called
   `Proposition` and finds nothing. The node id is inlined in the SQL as a literal
   (`n0.node_id = CAST('prop23' AS VARCHAR(256))`), not bound, so it is not in the list.
   The `{node_id: ...}` form happens to work because no label parameter precedes it.
2. **The path function is dropped.** `_execute_var_length_cypher` has no code for
   `return_path_funcs`. Its id-only fast path (`query.py:2262`) returns one column and
   ignores the rest of the SELECT. Path functions are implemented only in
   `_execute_var_length_labeled_path_funcs`, which is reached only when no source id is
   bound; and that function's source extraction (`extract_vlp_source_ids`, Path 1) takes
   **every node with the label** and ignores an id binding, then calls
   `store.query_nodes(label_filter=...)`, whose `IN (...)` list passes IRIS's parameter
   limit at about 1,000 nodes and fails with SQLCODE -1. That error is swallowed at
   `logger.debug` (`query.py:104`) and the function returns `[]`. So a label-bound
   path-function query returns nothing on any graph with more than about 1,000 nodes of
   that label.

## Impact

`productivity-framework`'s `query_builder.py` runs this shape
(`MATCH path = (p:Proposition {id: $prop_id})-[:depends_on*1..N]->(dep:Proposition)
RETURN ..., length(path) AS depth ORDER BY depth ASC`) and gets an empty list. Nothing
signals that the answer is wrong.

## Suggested direction

- Route `return_path_funcs` with a resolved source id to the path-function executor with
  `source_ids=[that id]`, rather than to `_execute_var_length_cypher`.
- Make `extract_vlp_source_ids` prefer an id binding over a label when both exist.
- Do not swallow the `query_nodes` failure at debug level; chunk the `IN` list.
- A test: the table above, as parametrized cases, on a graph over 1,000 nodes of one label.
