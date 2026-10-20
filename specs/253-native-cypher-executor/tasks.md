<!-- markdownlint-disable MD013 -->

# Tasks: read-only Cypher executed natively

Tests first in every phase; each must fail before the code that makes it pass. E2E in `TCKJ`
(`IVG_NAMESPACE=TCKJ IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972`).

## Phase 0: SQL-path fixes the native path will match (FR-009, FR-012)

- [ ] T001 [test] E2E: a node id in two graphs; with no `USE GRAPH`: `MATCH (n {id: $x}) RETURN n.name` is one row per
      graph, each with its own value; `labels(n)` per graph; an edge stored in g1 never reaches the g2 node; one-hop,
      var-length and `count(*)` shapes.
- [ ] T002 Translator: all-graphs reads correlate `graph_id` between joined aliases (nodes, labels, props, edges,
      the VLP UDF ends, `CY_PROPS_MAP`, labels/properties subqueries).
- [ ] T003 [test] `min`/`max` over a float property return the float; over an int property the int; mixed stays mixed.
- [ ] T004 Fix `_arith_operand_type` / the `int_expr_columns` routing for `min`/`max` of a property.
- [ ] T005 [test] the k-hop fast path answers as the SQL path on a two-graph fixture.
- [ ] T006 Fix: the fast path reads every graph, or declines when more than one graph holds edges.
- [ ] T007 Gate: unit, TCK 3896/3896, CHANGELOG (results change where a node id lives in several graphs).

## Phase 1: plumbing (US1, US4)

- [ ] T010 [test] unit: planner returns `None` for writes, procedure calls, unsupported functions; engine option
      `native_cypher=False`; gates (not indexed, in `bulk_load_session`, missing SELECT privilege) take the SQL path.
- [ ] T011 [test] unit: shadow comparator (FR-013): ordered, multiset, partial order under LIMIT/SKIP; message names
      the first differing row.
- [ ] T012 [test] E2E: property reader equals `SELECT val` for every row of a fixture (default and named graphs,
      long values, missing keys); a mismatched layout declines to SQL.
- [ ] T013 [test] E2E: an executor exception falls back to SQL with one warning; paging returns 25,000 rows intact.
- [ ] T014 Planner skeleton, `Graph.KG.Cypher.Exec` (Run / ReadPage / Drop), `Graph.KG.Cypher.Props`, privilege check,
      shadow mode, fallback, paging.

## Phase 2: family A, anchored fixed-hop patterns (US1, US2)

- [ ] T020 [test] shadow E2E per shape: anchor by id / label+property; one and two hops each direction, type
      alternatives, multigraph parallel edges; WHERE on properties, labels, comparisons; RETURN properties, nodes,
      relationships, DISTINCT, ORDER BY (CY_SORT_KEY rules), SKIP, LIMIT; the SNB IS1-IS6, IC2, IC7, IC8 queries.
- [ ] T021 Anchor, Expand, Filter, Project, Distinct, Sort, Skip/Limit; expression interpreter (property access,
      comparisons, boolean, IN, STARTS/ENDS WITH, CONTAINS, IS NULL, parameters, literals).
- [ ] T022 Gate: unit, TCK, T020 green in shadow; timings for the family's SNB queries at SF0.1.

## Phase 3: family B, variable length and aggregates (US2)

- [ ] T030 [test] shadow E2E: var-length ends and trails, min/max hops, every direction; count, count(DISTINCT),
      sum/min/max/avg/collect, grouping keys; IC1, IC3, IC5, IC6, IC9, BI05-08, BI16/17, BI aggregates.
- [ ] T031 VarExpand, Aggregate, collect rendering.
- [ ] T032 Gate as T022.

## Phase 4: family C, broad read Cypher (US3)

- [ ] T040 [test] shadow E2E: OPTIONAL MATCH, WITH (projection, DISTINCT, aggregation, WHERE, ORDER/LIMIT), UNWIND,
      multiple MATCH, unanchored label scans, CASE, list/map literals, list functions; BI11.
- [ ] T041 Optional, Apply, Unwind, the remaining expressions.
- [ ] T042 Gate as T022.

## Phase 5: gate and default (US1-US4)

- [ ] T050 TCK in shadow: 3896/3896, zero differences, coverage share reported (SC-001, SC-004).
- [ ] T051 SNB SF0.1, 40 queries in shadow: zero differences (SC-002).
- [ ] T052 Default on (FR-010); CHANGELOG; docs (USER_GUIDE, OPERATIONS: privileges, opt-out).
- [ ] T053 SNB SF0.1 same machine against Neo4j/Memgraph; AWS SF1 (SC-003).
