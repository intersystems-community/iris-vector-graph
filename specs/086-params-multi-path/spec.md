# Spec 086: Incorrect params in large multi-path MATCH

## Status: Pending

## Problem

7 of 15 remaining GQS crashes are "Incorrect number of parameters" in queries with:
- 10–20 comma-separated MATCH patterns
- The same node variable (e.g. `n0`, `n1`) appearing 2–4 times across patterns,
  each occurrence with different inline property constraints `{k12:false}`, `{k14:false}`
- Complex WHERE clause with edge/node property arithmetic and string operators

Example trigger (simplified from GQS crash at line 1104, 5082-char query):
```cypher
MATCH (n0{k12:false})<-[r0 :T14]-(n1 :L0 :L3 :L2{k22:'val'})<-[r1 :T2]-(n2),
      (n0{k14:false})<-[r6 :T6]-(n4{k21:'other'})<-[r7 :T3]-(n1{k21:'MU6TK'}),
      (n6{k22:'9wu0MT'})-[r8 :T17]-(n7 :L0{k0:true})-[r9 :T5]->(n8 :L1{k7:'c9ORo'})-[r10 :T7]-(n1)
WHERE ((r11.k146) + ((n4.k2 + n1.k3 + r4.k87 + ...))) CONTAINS 'test'
RETURN n0.id
```

## Root Cause Hypothesis

When a node variable (e.g. `n1`) appears multiple times across comma-separated
patterns, the translator processes each occurrence as a reference — but when
the SECOND occurrence has inline properties `{k21:'MU6TK'}`, the
`translate_relationship_pattern` may call `context.add_join_param()` for those
property constraints, adding `?` to `join_params`, but the SQL fragment for
that occurrence doesn't contain a matching `?` placeholder (because the node
was already joined and its alias is reused).

The param count then diverges: SQL has fewer `?` than `params`.

## Acceptance Criteria

Given a 4-pattern MATCH with:
- Node `n1` appearing in patterns 1, 2, and 3 with different inline props each time
- WHERE clause with edge property arithmetic

When translated:
- `result.sql.count('?') == len(result.parameters[0])` (params match)
- Query executes against live IRIS without error
- Returns correct results (same as simplified equivalent query)

## Test Cases Required

1. `test_node_reuse_3_patterns_inline_props` — n1 in 3 patterns, each with unique prop
2. `test_node_reuse_params_match_5_patterns` — params match at 5 comma-patterns
3. `test_node_reuse_large_where` — complex arithmetic WHERE doesn't add phantom params
4. `test_gqs_regression_crash3` — exact crash 3 query (truncated to ~500 chars, reproducing the params mismatch)
5. `test_gqs_regression_crash5` — exact crash 5 query pattern

## Implementation Notes

In `translate_relationship_pattern`, when a node variable is already bound
(`variable in context.variable_aliases`), the node is treated as a reference
(no new `JOIN nodes` added). But inline properties `{k:v}` on the reference
may still call `context.add_join_param(k)` for rdf_props JOINs.

**Check**: Does this rdf_props JOIN use `?` in the SQL? If the JOIN is added
to `join_clauses` as `LEFT JOIN rdf_props pN ON pN.s = alias.node_id AND pN.key = ?`,
then the `?` IS in the SQL and it should match. If instead the constraint is
applied as a WHERE condition using `context.add_join_param()` (which adds to
`join_params`) but the SQL fragment uses a literal (not `?`), the count diverges.

Look at lines ~2010-2025 in translator.py — the second occurrence with props
should be handled identically to the first (rdf_props JOIN with `?` placeholder).
