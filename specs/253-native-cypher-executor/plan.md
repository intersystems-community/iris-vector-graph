<!-- markdownlint-disable MD013 -->

# Plan: read-only Cypher executed natively

## Shape

```text
execute_cypher ─ parse_query ─┬─ native_plan(ast) → Plan | None ── None ──→ translate_to_sql → execute_sql (today)
                              │
                              └─ Plan ── gates: Indexed(), not in bulk_load_session, SELECT privileges (FR-003/011/014)
                                          │
                                          └─ Graph.KG.Cypher.Exec.Run(planJson, paramsJson) → page tag
                                               └─ Python reads pages → raw rows → the same decoders as SQL (Q:1015-1026)
                                          any exception → SQL path, warning once per reason (FR-007)
```

- **Planner** (`iris_vector_graph/cypher/native_plan.py`): AST → Plan JSON, or `None` for anything outside the
  supported set. Never mutates the cached AST. Emits the same column names and the same raw value forms the
  translator's SQL would (`{a}_id`/`_labels`/`_props`, relationship and path JSON, `n_prop` aliases with the
  `column_name_map`), and the translator's `int/sum/float/bool/prop_text/mixed` column sets, so the existing decoders
  run unchanged and stay the one place values are typed.
- **Executor** (`Graph.KG.Cypher.*`): a row pipeline over process-private globals. Operators: `Anchor` (node by id,
  by label + property via `idx_props_key_valkey`, or a label scan via `^KG("label")`), `Expand` (one hop over
  `^KG("out"/"in")`, types, direction, ekey children), `VarExpand` (`Graph.KG.VarLength` walks, ends or trails),
  `Filter` (expression tree), `Optional`, `Project`, `Aggregate`, `Distinct`, `Unwind`, `Sort`, `Skip`/`Limit`,
  `Apply` (for `WITH` stages and multiple `MATCH` clauses). Expressions are evaluated by an interpreter over the plan's
  expression JSON; ordering and comparison call the CY UDF implementations (`User.funcCYSORTKEY`, `User.funcCYCMP`) so
  there is one implementation of each rule.
- **Property reader** (`Graph.KG.Cypher.Props`): resolves `rdf_props`' data and `pkprops` globals from `%Dictionary`
  once per process (keyed by the class's compile time), checks the slot names (`graphid, s, key, val`), reads
  `$Order(pk(g, s, key, ""))` then `$ListGet(data(id), 4)`; any mismatch → prepared SQL for the rest of the call.
- **Shadow mode** (`IVG_CYPHER_SHADOW=1` or `engine.cypher_shadow=True`): run both, compare by FR-013, raise
  `NativeMismatch` with query, parameters, first differing row.
- **Results**: `^||IVG.CypherOut(tag, n) = $ListBuild(col1, ...)`, read 1,000 rows per call (FR-015).

## Graph scope (FR-009)

Rows carry `(node_id, graph_key)` per node variable. An expansion from a node of graph `g` reads `^KG(..., g, ...)`
only. Labels and properties are read for the node's own graph. All-graphs anchors iterate graph keys.

## Phases

0. Prerequisite SQL fixes (FR-009, FR-012), each test-first, CHANGELOG, TCK green: all-graphs reads correlate
   `graph_id` across aliases; `min`/`max` of a property keep the value's type; the k-hop fast path reads every graph
   (or declines when more than one graph holds edges).
1. Plumbing: engine option, gates, privilege check, fallback, paging, shadow comparator, property reader with probe,
   executor skeleton that runs an empty plan. Unit tests for the comparator and planner `None` cases; E2E for the
   reader against SQL on every `rdf_props` row of a fixture.
2. Family A, anchored fixed-hop patterns: anchor + expand + filter + project + distinct + order + limit (IS1-IS6,
   IC2, IC7, IC8, IC13-free). Shadow E2E per query shape; TCK subset.
3. Family B, variable length and aggregates (IC1, IC3, IC5, IC6, IC9, BI aggregates, BI05-08, BI16/17).
4. Family C, broad read Cypher: `OPTIONAL MATCH`, `WITH` pipelines, `UNWIND`, multiple `MATCH`, unanchored scans
   (BI11), list/map/CASE expressions the TCK uses.
5. Gate and default: full TCK in shadow (SC-001), SNB SF0.1 in shadow (SC-002), coverage report (SC-004), then
   default on (FR-010); SNB SF0.1 same machine and the AWS SF1 run for SC-003.

## Risks

- Value rendering drift between planner output and translator output: the shadow gate on the TCK is the check;
  every rendering rule is taken from the translator's code path, cited in the planner.
- `%Dictionary` storage changes across IRIS versions: the probe declines, SQL answers.
- Performance of the expression interpreter: measured per family against the hand-built IC1 (research.md §A).
