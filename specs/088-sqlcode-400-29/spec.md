# Spec 088: SQLCODE -400/-29 in large MATCH queries

## Status: Pending

## Problem

3 crashes: 2× SQLCODE -400 "Fatal error", 1× SQLCODE -29 "Field not found".

### -400 Pattern (crashes 1, 8)

Both are very large queries (12-16 path patterns) with no identifiable unique
Cypher feature — they hit a SQLCODE -400 "Fatal error occurred" in IRIS.
This is IRIS's generic internal error, typically caused by:
- A generated SQL query that's syntactically valid but semantically triggers
  an IRIS optimizer/executor bug
- A join chain that's too deep for IRIS's optimizer (stack overflow in plan)
- A CASE/expression that returns an unexpected type

Example (crash 1, 12+ paths, directed and undirected mix):
```cypher
MATCH (n0 :L1)<-[r0 :T4]-(n1 :L4 :L0 :L3{k18:'9EYZ0o'})<-[r1 :T3]-(n2 :L2)
      <-[r2 :T22]-(n3 :L0{k5:'q60Lf'})<-[r3 :T10]-(n4 :L6{k39:-3465})<-[r4 :T29]-(n5 :L4 :L1{id:9})
      <-[r5 :T28]-(n6 :L... [12+ more paths]
RETURN ... (aggregates)
```

### -29 Pattern (crash 2)

```cypher
MATCH (n0 :L0{k2:false})-[r0 :T1]->(n1 :L1 :L0{k4:-5247})-[r1 :T8]->(n2 :L2),
      (n3 :L2 :L1)-[r2 :T0]->(n4 :L0{k1:-7826})-[r3 :T10]->(n5 :L2{k12:'bbiVJYp3q'})-[r4 :T8]->(n6
      ...[8+ more paths]
WHERE ...
RETURN ...
```

The SQLCODE -29 "Field not found" is for a specific alias that's not visible in scope.
This is the same class of problem as specs 086/087 but triggered by a -29 rather than
params mismatch.

## Root Cause Hypotheses

### For -400:
IRIS internal error when the generated SQL has 30+ JOINs in a single query.
IRIS has an undocumented limit on join chain depth; beyond ~25-30 JOINs, the
optimizer/executor may crash internally.

**Mitigation strategy**: break large MATCH queries into CTEs. When the join count
exceeds a threshold (e.g. 20 JOINs), wrap the MATCH in a stage CTE automatically.

### For -29:
In 8+ path queries with multiple comma-separated patterns, the translator may
assign the same alias index to two different joins (counter collision) OR
reference an alias that was defined inside a nested UNION ALL subquery but
referenced in the outer WHERE clause.

## Acceptance Criteria

### -400 tests:
1. `test_large_match_12_paths_no_400` — 12-path MATCH executes without SQLCODE -400
2. `test_join_depth_cte_split` — queries with >20 JOINs automatically split into CTEs
3. `test_gqs_regression_crash1_400` — crash 1 query executes clean

### -29 tests:
4. `test_8_path_match_field_not_found` — 8-path query with multiple props doesn't get -29
5. `test_alias_counter_no_collision` — verify no two aliases share the same counter
6. `test_gqs_regression_crash2_29` — crash 2 query executes clean

## Implementation Notes

### -400 mitigation (join depth limit):
In `build_stage_sql` or the final SQL assembly, count the total JOINs.
If count > 20, wrap the MATCH portion in a CTE:

```sql
WITH MatchResult AS (
  SELECT n0.node_id AS n0, n1.node_id AS n1, ...
  FROM ... [all the JOINs] ...
  WHERE ...
)
SELECT ... FROM MatchResult [RETURN translations]
```

This gives IRIS's optimizer a smaller unit to plan and avoids the deep join crash.

### -29 fix:
Audit `context.next_alias()` to ensure counters are not reset mid-query.
Check specifically that UNION ALL undirected patterns don't share the alias
counter with outer query aliases.

Also check: when a node appears in both the MATCH clause and a later WHERE 
sub-expression (EXISTS pattern, etc.), the alias is correctly resolved.
