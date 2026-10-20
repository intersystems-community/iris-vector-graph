<!-- markdownlint-disable MD013 -->

# Research: what the native executor must reproduce

Facts gathered 2026-10-20 from the code (paths relative to the repo; Q = `iris_vector_graph/_engine/query.py`,
T = `iris_vector_graph/cypher/translator.py`, S = `iris_vector_graph/schema.py`) and from measurements on SNB SF0.1
(`SNBCLEANB`, hub `Person:910`).

## A. Measurements

| Work                                                | Today (SQL) | Native           |
| --------------------------------------------------- | ----------- | ---------------- |
| IC3 walk (`KNOWS*2..2`, 1393 ends)                  | 144 ms      | 3.8 ms (`^KG`)   |
| IC1 walk (`KNOWS*1..3`, 1538 ends)                  | 425 ms      | 12 ms (`^KG`)    |
| IC1 per-end work (label + `firstName`, 1538 ends)   | ~140 ms     | 2 ms (globals)   |
| same, prepared dynamic SQL in-process               |             | 12 ms            |
| IC3 joins around the walk (nodes + label per end)   | ~85 ms      | (per-row probes) |

The SQL plan for a JSON_TABLE end joined to `nodes` and `rdf_labels` is index-driven; the cost is ~30 µs per probe.

## B. The pipeline a native result must equal

- `execute_cypher` (Q:764) runs, before parsing: approx count distinct, the k-hop fast path (Q:798; default graph
  only, unlike the SQL path), introspection shortcuts, EXPLAIN/SHOW, constraint DDL no-ops.
- After `translate_to_sql` and `execute_sql`, rows go through, in order (Q:1015-1026): `_drop_internal_columns`,
  `_decode_bool_text_columns`, `_decode_rel_prop_text_columns`, `_decode_numeric_expr_columns`,
  `_decode_map_text_columns`, `_decode_mixed_expr_columns`, then `column_name_map` renames. A native result must
  either produce the decoded values directly or produce the same raw text and pass through the same decoders.
- Var-length queries go through `_route_var_length` (Q:1121), which applies client-side DISTINCT (first row by
  `repr(row)`) and a client-side LIMIT in some routes.
- SQL errors return `IVGResult(error=str(e))` with empty rows (`stores/iris_sql_store.py:1135-1160`); translation
  errors raise.

## C. Rendering

- `RETURN n`: three columns `{a}_id`, `{a}_labels` (JSON array text, unordered `JSON_ARRAYAGG`, `'[]'` when none),
  `{a}_props` (JSON array of `{"key","value"}` with string values, NULL when none) (T:21486-21488, 571-599).
- A relationship: `{"type":p,"props":qualifiers or {}}` (T:21455-21467). A path: `{"nodes":[ids],"rels":[types]}`.
- `collect(n)`: array of `{"_id","_labels","_props"}`.
- Unaliased `n.prop` → SQL alias `n_prop`, mapped back; function calls → sanitized source text, `_2`, `_3` on
  duplicates.

## D. Value semantics

- `rdf_props.val` is untyped text (`prop_values.py`): booleans `"true"`/`"false"` (legacy `"1"`/`"0"`/`"True"`),
  JSON for lists and maps, `str()` otherwise.
- Range comparisons with a property cast it to DOUBLE unless the other side is a string literal or parameter
  (T:12760-12773). `<=`/`>=` decompose for NaN. Literal-type mismatches fold in Python.
- ORDER BY uses `%EXACT(CY_SORT_KEY(expr,'x'))`; numeric-looking text sorts as a number (S:1876).
- `min`/`max` over a property decode as int (rounded); `sum` is int when whole; `count` int; `avg` float.
- `CY_CMP`, `CY_SORT_KEY`, `CY_EXP_ORDKEY` are ObjectScript UDFs (classes `User.funcCY*`) the executor can call
  directly, so ordering and comparison rules have one implementation.

## E. Graph scope

- No `USE GRAPH` → no `graph_id` predicate anywhere (T:4130-4162): a node id in two graphs yields one `nodes` row
  per graph, labels and properties are unioned across graphs, edges from any graph meet the same-id node in any
  graph. `USE GRAPH g` scopes every alias (T:321-366); CTE/stage pipelines are knowingly unscoped (T:337-339).
- `CY_PROPS_MAP` reads `WHERE s = ?` with no graph predicate.

## F. Storage (all text columns `%EXACT`)

| Table        | Data `$List` (to confirm per probe)                         | Index used natively             |
| ------------ | ----------------------------------------------------------- | ------------------------------- |
| `rdf_props`  | graph_id, s, key, val, val_key — `val` at position 4 (seen) | `pkprops(graph_id, s, key, ID)` |
| `rdf_labels` | graph_id, s, label                                          | `^KG("label", gkey, label, s)`  |
| `rdf_edges`  | graph_id, s, p, o_id, qualifiers, ekey (RowID = edge_id)    | `^KG("out"/"in", gkey, …)`      |
| `nodes`      | node_id, graph_id, created_at                               | PK `(node_id, graph_id)`        |

`rdf_props` globals at SNBCLEANB: data `^t52S.DMCe.1`, `pkprops` `^t52S.DMCe.3`, subscripts
`($Char(0) for '', s, key, ID)`. Names are hashed per table creation: resolve from `%Dictionary.CompiledStorage` /
`CompiledStorageIndex` each call (or per process, keyed by the class's compile timestamp).

## G. Privileges (docs/research/least-privilege-spike.md)

- A connection needs `%DB_<res>:R`; class-method calls need `%Native_ClassExecution:U` (else `<PROTECT>`).
- SQL table privileges are what `docs/OPERATIONS.md` and `docs/FHIR_GRAPH.md` promise; direct global reads do not
  check them.

## H. Known traps

- ObjectScript strings cap at ~3.6 MB: a result returned as one string overflows (as `CY_VLP_PATHS` did at three
  hops); results need paging (a process-private global and chunked reads).
- `bulk_load_session` drops `idx_props_*` but not `pkprops`; `%NOINDEX` loads leave index globals behind the data
  until `%BuildIndices` (tests/conftest.py `ledger_reset` rebuilds edge indices for that reason).
- `parse_query` returns a cached, shared AST (`parser.py:1868-1885`); a planner must not mutate it.
