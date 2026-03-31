# Cypher Gap Analysis: Implementation Recommendations

**From**: arno team (Steve-and-Dan perspective)
**Date**: 2026-03-31
**Audience**: IVG team (Steve, Dan)
**Context**: 10 missing Cypher features ranked by biomedical impact

---

## Executive Summary

IVG's Cypher translator is well-structured: a recursive-descent parser emits a
clean AST, and a translator converts that AST into IRIS SQL using JOINs on
`rdf_edges` with CTEs for multi-stage queries. The architecture already supports
CALL procedures (ivg.vector.search, ivg.neighbors, ivg.ppr), UNWIND, CALL {}
subqueries, named paths, and a solid set of aggregation functions.

The 10 gaps below are ranked by biomedical impact. For each, we recommend:
- **Approach**: SQL-only, hybrid SQL+ObjectScript, or pure ObjectScript
- **Layers touched**: parser, translator, or both
- **Estimated effort**: T-shirt size (S/M/L/XL)
- **Key risk or gotcha**

**Critical architectural constraint**: IRIS does NOT support recursive CTEs
(`WITH RECURSIVE`). Variable-length path traversal must use either application-
level BFS or the existing ObjectScript `BFSFast` method over `^KG` globals.

---

## Architecture Reference

Before diving in, here's what the translator pipeline looks like today:

```
Cypher string
    → Lexer (lexer.py)        → Token stream
    → Parser (parser.py)      → AST (ast.py)
    → Translator (translator.py) → SQL with CTEs + params
    → engine.execute_cypher() → cursor.execute() → rows
```

Key translator facts:
- **Node patterns** → JOIN to `nodes` table, JOIN to `rdf_labels` for labels,
  JOIN to `rdf_props` for property access
- **Relationship patterns** → JOIN to `rdf_edges` with direction-aware ON
  conditions. Edge alias `eN`, node alias `nN`
- **Multi-stage queries** → CTEs named `Stage1`, `Stage2`, etc.
- **CALL procedures** → CTEs named `VecSearch`, `Neighbors`, `PPR` prepended
  before the main query
- **CALL {} subqueries** → Both correlated (scalar) and uncorrelated (CTE)
  forms supported
- **Named paths** → `NamedPath` AST node tracks which node/edge aliases belong
  to the path; `length()`, `nodes()`, `relationships()` resolve at translation
  time

Key ObjectScript facts:
- **`^KG` global structure**: `^KG("out", subject, predicate, object) = weight`,
  `^KG("in", object, predicate, subject) = weight`, `^KG("deg", node)`,
  `^KG("degp", node, predicate)`, `^KG("label", label, node)`,
  `^KG("prop", node, key) = value`
- **BFSFast**: Pure ObjectScript BFS over `^KG` globals. Takes `(srcId, preds,
  maxHops, dstLabel)`. Writes to `^||BFS.Results(i) = $lb(s,p,o,w,step)`.
  Sub-millisecond for 2-3 hop traversals on 300K edges.
- **BFSFastJson**: Wrapper that returns JSON array — the right entry point for
  Python callers via `_call_classmethod(conn, 'Graph.KG.Traversal', 'BFSFastJson', ...)`
- **SubgraphJson**: k-hop subgraph extraction with edge-type filtering
- **PPRGuidedJson**: Personalized PageRank + BFS pruning, all server-side

---

## Gap #1: Variable-Length Paths `[*1..3]` — CRITICAL

**Cypher**: `MATCH (d:Drug)-[:TARGETS*1..3]->(g:Gene) RETURN d, g`
**Biomedical**: Drug repurposing, disease mechanism chains, ontology traversal.
This is the single most important missing feature for graph analytics.

### Current State

The parser already handles `*min..max` syntax (parser.py:491-505) and produces
a `VariableLength(min_hops, max_hops)` object on `RelationshipPattern`. The AST
is ready. The translator **completely ignores** `variable_length` — the field is
never read in `translate_relationship_pattern()`.

### Recommended Approach: Hybrid — BFSFast CTE Bridge

**Do NOT attempt recursive CTEs** — IRIS doesn't support them. The existing
`algorithms/paths.py` already documents this: "IRIS does not support recursive
CTEs, so path-finding is implemented as application-level BFS."

**Do NOT generate N fixed-JOIN expansions** (e.g., UNION of 1-hop JOIN + 2-hop
JOIN + 3-hop JOIN). This works for `*1..2` but becomes unmanageable at `*1..5`
and doesn't handle the general case.

**Instead**: Use BFSFastJson as a server-side CTE source.

#### Implementation Plan

1. **Translator change** (`translate_relationship_pattern`): When
   `rel.variable_length` is not None, instead of emitting a single edge JOIN,
   emit a CTE that calls BFSFastJson:

   ```sql
   VarPath0 AS (
     SELECT j.s, j.p, j.o, j.step
     FROM JSON_TABLE(
       Graph_KG.Traversal_BFSFastJson(?, ?, ?, ?),
       '$[*]' COLUMNS(
         s VARCHAR(512) PATH '$.s',
         p VARCHAR(512) PATH '$.p',
         o VARCHAR(512) PATH '$.o',
         w DOUBLE PATH '$.w',
         step INTEGER PATH '$.step'
       )
     ) j
     WHERE j.step >= ? AND j.step <= ?
   )
   ```

   Parameters: `(source_node_id, preds_json, max_hops, dst_label, min_hops, max_hops)`

2. **Source node binding**: The source node comes from the preceding node pattern
   in the MATCH. If the source is already bound (e.g., by a label filter or
   property), the translator must emit a **correlated lateral join** or
   **iterate over the source set in Python**. For the common case where the
   source is a single node (property filter like `{id: 'DRUG:123'}`), the CTE
   approach works directly.

   For the general case (source is a set), use the existing `CALL {}` subquery
   pattern: emit the variable-length traversal as a correlated scalar subquery
   per source node. This reuses the existing `translate_subquery_call`
   infrastructure.

3. **Result binding**: The CTE's `o` column becomes the target node. JOIN the
   CTE to the target node pattern:
   ```sql
   JOIN VarPath0 vp0 ON vp0.s = source_alias.node_id
   JOIN nodes target_alias ON target_alias.node_id = vp0.o
   ```

4. **Predicate filtering**: BFSFast already accepts a `preds` array for per-hop
   predicate filtering. For `[:TARGETS*1..3]`, pass `'["TARGETS"]'`. For
   `[*1..3]` (no type), pass `""`.

5. **Path variable binding**: If the relationship has a variable
   (`-[r:TARGETS*1..3]->`), the variable should expose the path edges, not a
   single edge. Consider making `r` reference the CTE rows so that
   `relationships(r)` returns the collected predicates.

#### SQL Function Exposure

BFSFastJson is a ClassMethod, not a SQL function. You need to either:
- **Option A**: Create a SQL user-defined function that wraps it:
  ```sql
  CREATE FUNCTION Graph_KG.BFS_Path(src VARCHAR, preds VARCHAR, maxHops INT, label VARCHAR)
  RETURNS VARCHAR(MAXLEN)
  LANGUAGE OBJECTSCRIPT
  { Return ##class(Graph.KG.Traversal).BFSFastJson(src, preds, maxHops, label) }
  ```
- **Option B**: Call it from Python via `_call_classmethod` before executing the
  SQL, inject results as a temp table or JSON literal. This is what
  `operators.py` already does for PPR.

**Steve's take**: Option A is cleaner — it lets the SQL engine call the function
inline and the JSON_TABLE unwraps it in the same query plan. Option B requires
two round-trips and temp table management.

**Dan's take**: Option A, but watch out for the JSON serialization overhead. For
large result sets (>10K paths), BFSFastJson builds a `%DynamicArray` and
serializes to JSON. Consider adding a `BFSFastSQL` that returns a temp table
directly via `%SQL.Statement` for truly large traversals.

#### Effort: **L** (Large)
- Parser: Already done (VariableLength parsed)
- AST: Already done
- Translator: ~200 lines for the CTE bridge logic
- SQL function wrapper: ~20 lines ObjectScript
- Tests: Needs 10+ test cases (min/max combos, directed/undirected, with/without
  type filter, chained patterns)

#### Risk
- **Correlated multi-source**: If the source set is large (e.g., all Drug nodes),
  calling BFSFastJson per source node is O(N) function calls. For N > 1000,
  this will be slow. Mitigation: batch the source nodes and call a single
  multi-source BFS variant. BFSFast could be extended to accept a JSON array
  of source IDs.

---

## Gap #2: UNION / UNION ALL — CRITICAL

**Cypher**: `MATCH (g:Gene) RETURN g.name UNION MATCH (d:Drug) RETURN d.name`
**Biomedical**: Combining heterogeneous result types (genes + drugs + diseases
in a single result set), multi-label queries.

### Current State

Not in parser or translator. The parser's `parse()` method handles one complete
query and expects EOF. There's no loop for UNION.

### Recommended Approach: Parser + Post-Assembly

This is a **parser-level** change. The translator doesn't need to know about
UNION — it just translates each sub-query independently, and the final SQL is
assembled with `UNION [ALL]`.

#### Implementation Plan

1. **Lexer**: Add `UNION` token type (if not present — check lexer.py)
2. **Parser**: After parsing a complete `CypherQuery`, check for `UNION` token.
   If found, parse another complete query. Return a new AST node
   `UnionQuery(queries: List[CypherQuery], all: bool)`.
3. **Translator**: Add `translate_union()` that calls `translate_to_sql()` on
   each sub-query and joins them with `UNION` or `UNION ALL`.
4. **Column alignment**: Cypher UNION requires matching RETURN column counts.
   Validate at AST level.

#### Example Generated SQL
```sql
SELECT n0.node_id AS g_id, ... FROM nodes n0 JOIN rdf_labels l0 ...
UNION ALL
SELECT n1.node_id AS d_id, ... FROM nodes n1 JOIN rdf_labels l1 ...
```

**Steve's take**: This is structurally simple. The tricky part is column type
alignment — Cypher UNION requires same-arity RETURN clauses but not same-name.
SQL UNION requires same-arity AND compatible types. Since everything in IVG is
VARCHAR (node IDs, property values), this should Just Work.

**Dan's take**: Don't forget that each UNION branch may have its own CTEs
(e.g., one branch does CALL ivg.vector.search). The CTE names must not collide.
Use `VecSearch_0`, `VecSearch_1`, etc. or wrap each branch in its own `WITH`.

#### Effort: **M** (Medium)
- Lexer: UNION token (~5 lines)
- Parser: UNION loop after main parse (~30 lines)
- AST: new `UnionQuery` node (~10 lines)
- Translator: `translate_union` assembly (~40 lines)
- Tests: 5-8 cases

---

## Gap #3: CASE WHEN Expressions — CRITICAL

**Cypher**: `RETURN CASE WHEN g.score > 0.8 THEN 'high' ELSE 'low' END AS risk`
**Biomedical**: Conditional classification in RETURN (risk stratification,
expression level binning, confidence tiers).

### Current State

Not in parser. The `FunctionCall` and expression infrastructure exist but CASE
is a distinct syntactic form, not a function call.

### Recommended Approach: Parser + Translator Expression

CASE WHEN maps directly to SQL CASE WHEN — no semantic gap.

#### Implementation Plan

1. **AST**: Add `CaseExpression` node:
   ```python
   @dataclass(slots=True)
   class CaseWhen:
       condition: BooleanExpression
       result: Union[PropertyReference, Literal, Variable, FunctionCall]

   @dataclass(slots=True)
   class CaseExpression:
       cases: List[CaseWhen]
       else_result: Optional[...] = None
       # Simple CASE: test_expression for CASE expr WHEN val THEN ...
       test_expression: Optional[...] = None
   ```

2. **Lexer**: Add `CASE`, `WHEN`, `THEN`, `ELSE`, `END` tokens
3. **Parser**: Parse in `parse_primary_expression()` when token is CASE
4. **Translator**: In `translate_expression()`:
   ```python
   if isinstance(expr, ast.CaseExpression):
       parts = ["CASE"]
       for cw in expr.cases:
           cond = translate_boolean_expression(cw.condition, context)
           result = translate_expression(cw.result, context, segment)
           parts.append(f"WHEN {cond} THEN {result}")
       if expr.else_result:
           parts.append(f"ELSE {translate_expression(expr.else_result, context, segment)}")
       parts.append("END")
       return " ".join(parts)
   ```

**Steve's take**: 1:1 syntax mapping. Most straightforward gap to close. Do it
as a warmup before the harder ones.

#### Effort: **S** (Small)
- 50-70 lines across parser + translator
- Tests: 5 cases (searched CASE, simple CASE, nested, in WHERE, in ORDER BY)

---

## Gap #4: EXISTS {} Pattern Predicate — HIGH

**Cypher**: `WHERE EXISTS { (d)-[:TREATS]->(disease) }` or
`WHERE exists(d.toxicity)`
**Biomedical**: "Find drugs that treat ANY disease", "Find genes that have
expression data", subgraph existence checks.

### Current State

The `FunctionCall` handler has `"exists": "EXISTS"` in `_CYPHER_FN_MAP`, but
this only handles the scalar `exists(property)` form. The pattern predicate
form `EXISTS { pattern }` is not parsed.

### Recommended Approach: Parser + Correlated Subquery

#### Implementation Plan

1. **Lexer/Parser**: When `EXISTS` is followed by `{`, parse a graph pattern
   inside the braces (reuse `parse_graph_pattern()`).
2. **AST**: Add `ExistsExpression(pattern: GraphPattern)`.
3. **Translator**: Translate to a correlated SQL EXISTS subquery:
   ```sql
   EXISTS (
     SELECT 1 FROM rdf_edges e99
     WHERE e99.s = outer_alias.node_id AND e99.p = 'TREATS'
   )
   ```
   This reuses the same JOIN logic as `translate_relationship_pattern` but in
   a subquery context with correlated references to the outer scope.

**Steve's take**: The correlated subquery approach is exactly right for IRIS.
EXISTS subqueries are well-optimized by the IRIS query planner. The tricky part
is resolving variable references across the subquery boundary — variables from
the outer MATCH must be visible inside EXISTS.

**Dan's take**: Watch out for the two forms:
- `exists(n.property)` → `n_prop.val IS NOT NULL` (already works via IS_NOT_NULL)
- `EXISTS { (n)-[:REL]->(m) }` → correlated EXISTS subquery (new)

Don't confuse them in the parser. The `{` after EXISTS is the disambiguator.

#### Effort: **M** (Medium)
- Parser: ~40 lines
- Translator: ~60 lines (correlated subquery generation)
- Tests: 6 cases (simple pattern, multi-hop, with label filter, negated NOT EXISTS)

---

## Gap #5: Pattern Comprehension — HIGH

**Cypher**: `RETURN [(d)-[:HAS_GENE]->(g) | g.name] AS genes`
**Biomedical**: Inline collection of related entities without multi-stage
WITH clauses.

### Current State

Not in parser. List comprehensions are a distinct syntactic form.

### Recommended Approach: Parser + Correlated JSON_ARRAYAGG Subquery

#### Implementation Plan

1. **AST**: Add `PatternComprehension(pattern: GraphPattern, projection: Expression, filter: Optional[WhereClause])`.
2. **Parser**: In `parse_primary_expression()`, when token is `[` followed by
   `(` (graph pattern), parse as comprehension: `[pattern WHERE filter | projection]`.
3. **Translator**: Translate to a correlated subquery with JSON_ARRAYAGG:
   ```sql
   (SELECT JSON_ARRAYAGG(p99.val)
    FROM rdf_edges e99
    JOIN rdf_props p99 ON p99.s = e99.o_id AND p99."key" = 'name'
    WHERE e99.s = outer_alias.node_id AND e99.p = 'HAS_GENE')
   ```

**Steve's take**: This is really a correlated subquery with aggregation. The
parser work is the hard part (disambiguating `[` as list literal vs pattern
comprehension). Use 2-token lookahead: `[` + `(` = comprehension, `[` + literal
= list.

#### Effort: **M** (Medium)
- Parser: ~50 lines (comprehension syntax)
- Translator: ~40 lines (correlated JSON_ARRAYAGG)
- Tests: 5 cases

---

## Gap #6: Quantified Path Patterns `->+` — MEDIUM

**Cypher (GQL/openCypher 9)**: `MATCH (a)-[:KNOWS]->+(b)` or
`MATCH (a) (()-[:KNOWS]->())+ (b)`
**Biomedical**: Transitive closure (is gene A upstream of gene B through any
chain of regulation?).

### Current State

Not in parser or AST. The parser handles `*min..max` but not the `+` (one or
more) or `{n,m}` quantifier syntax.

### Recommended Approach: Desugar to Variable-Length Paths

Once Gap #1 (variable-length paths) is implemented:
- `->+` becomes `*1..10` (reasonable upper bound)
- `->*` becomes `*0..10`
- `->{2,5}` becomes `*2..5`

#### Implementation Plan

1. **Parser**: Extend `parse_relationship_pattern()` to handle `+` and `{n,m}`:
   - `*+` or just `+` after type → `VariableLength(1, DEFAULT_MAX)` (10)
   - `*` alone → `VariableLength(1, DEFAULT_MAX)`
   - `{n,m}` → `VariableLength(n, m)`
2. **No translator changes** — once desugared to VariableLength, Gap #1 handles it.

**Steve's take**: This is a parser sugar on top of Gap #1. Do it in the same PR.
Define `DEFAULT_MAX_HOPS = 10` as a constant — it's already enforced in the
VariableLength AST node's `__post_init__`.

#### Effort: **S** (Small) — contingent on Gap #1
- Parser: ~20 lines
- Tests: 3 cases

---

## Gap #7: REDUCE() for Path Scoring — MEDIUM

**Cypher**: `RETURN reduce(score = 0.0, r IN relationships(path) | score + r.weight) AS total`
**Biomedical**: Path confidence scoring (product/sum of edge weights along a
drug-target-disease chain).

### Current State

Not in parser. `reduce` is not a standard function — it's a fold expression.

### Recommended Approach: Python Post-Processing

IRIS SQL has no equivalent of a fold/reduce over a dynamic list. Attempting to
translate this to SQL would require either:
- Dynamic SQL generation per path length (fragile)
- A user-defined aggregate function (IRIS doesn't support CREATE AGGREGATE)

**Instead**: Handle reduce in the Python result-processing layer.

#### Implementation Plan

1. **AST**: Add `ReduceExpression(accumulator: str, init_value: Literal, variable: str, collection: Expression, expression: Expression)`.
2. **Parser**: Parse `reduce(acc = init, var IN collection | expr)` syntax.
3. **Translator**: When encountering ReduceExpression, mark it as
   "post-process" in query metadata. Emit the collection as a JSON array column
   (using JSON_ARRAYAGG over relationship properties).
4. **Engine**: In `execute_cypher()`, check metadata for post-process reduce
   operations. Apply the fold in Python over the returned JSON arrays.

**Dan's take**: This is the pragmatic approach. Don't try to push fold semantics
into SQL. The path is already materialized by BFSFast — just iterate the steps
in Python. For the common case (sum of weights), you could even add a
`BFSFastWeightedJson` variant that returns cumulative weight per path.

**Steve's take**: Agree, but add a `sum(weights)` shortcut in ObjectScript for
the 90% case. Most biomedical path scoring is just weight summation.

#### Effort: **M** (Medium)
- Parser: ~30 lines
- Post-processing logic: ~40 lines
- Tests: 4 cases (sum, product, conditional accumulation, nested)

---

## Gap #8: COUNT(DISTINCT ...) — HIGH (Easy Win)

**Cypher**: `RETURN count(DISTINCT g.category) AS unique_categories`
**Biomedical**: Cardinality queries (how many unique pathways? unique tissues?).

### Current State

The parser and translator already handle `count(DISTINCT expr)` — the
`AggregationFunction` AST node has a `distinct: bool` field, and the translator
emits `COUNT(DISTINCT ...)`. **This may already work.**

### Verification Needed

Test with: `MATCH (g:Gene) RETURN count(DISTINCT g.name) AS n`

If it works, close this gap as "already supported." If the `distinct` flag isn't
being parsed correctly, the fix is:

1. In `parse_primary_expression()` line 638: `distinct = self.matches(TokenType.DISTINCT)`
   — this is already there.
2. In translator line 928: `f"{fn}({'DISTINCT ' if expr.distinct else ''}{arg})"` — already there.

**Steve's take**: Test it first. I suspect it already works. If it does, add it
to the documentation and test suite rather than implementing anything.

#### Effort: **XS** (verify + test)
- Verify existing behavior: 15 minutes
- Add test cases if working: 30 minutes

---

## Gap #9: FOREACH Batch Updates — LOW

**Cypher**: `FOREACH (x IN $list | CREATE (n:Node {id: x}))`
**Biomedical**: Batch entity creation, bulk annotation updates.

### Current State

Not in parser. UNWIND + CREATE achieves the same result with existing syntax:
```cypher
UNWIND $list AS x
CREATE (n:Node {id: x})
```

### Recommended Approach: Desugar to UNWIND + Updating Clause

#### Implementation Plan

1. **AST**: Add `ForEachClause(variable: str, expression: Expression, clauses: List[UpdatingClause])`.
2. **Parser**: Parse `FOREACH (var IN expr | updating_clauses)`.
3. **Translator**: Rewrite to UNWIND + updating clauses internally.

**Steve's take**: Low priority. UNWIND already does this. If you implement it,
do it as syntactic sugar — literally rewrite the AST to UNWIND + CREATE before
translation. Zero new translator logic.

**Dan's take**: Agree. The only value is openCypher compatibility. For IVG users,
document UNWIND as the recommended pattern.

#### Effort: **S** (Small)
- Parser: ~30 lines
- Translator: ~15 lines (AST rewrite)
- Tests: 3 cases

---

## Gap #10: Type Coercion / String Functions — MEDIUM

**Cypher**: `toInteger(n.score)`, `toLower(n.name)`, `substring(n.desc, 0, 100)`,
`split(n.aliases, ';')`, `size(collect(g))`
**Biomedical**: Data normalization (case-insensitive matching), parsing
delimited fields, substring extraction from long descriptions.

### Current State

Partially supported. The translator already has `_CYPHER_FN_MAP` (translator.py
lines 959-987) mapping Cypher functions to IRIS SQL equivalents:

| Cypher | IRIS SQL | Status |
|--------|----------|--------|
| `toLower()` | `LOWER()` | **Mapped** |
| `toUpper()` | `UPPER()` | **Mapped** |
| `trim/ltrim/rtrim` | `TRIM/LTRIM/RTRIM` | **Mapped** |
| `substring()` | `SUBSTRING()` | **Mapped** |
| `replace()` | `REPLACE()` | **Mapped** |
| `size()` / `length()` | `LENGTH()` | **Mapped** |
| `abs/ceil/floor/round/sqrt/sign` | Direct equivalents | **Mapped** |
| `coalesce()` | `COALESCE()` | **Mapped** |
| `toString()` | `CAST(... AS VARCHAR)` | **Mapped but needs fix** |
| `toInteger()` | `CAST(... AS INT)` | **Mapped but needs fix** |
| `toFloat()` | `CAST(... AS DOUBLE)` | **Mapped but needs fix** |
| `toBoolean()` | `CASE WHEN` | **Mapped but incomplete** |
| `split()` | `STRTOK_TO_TABLE` | **Wrong — STRTOK_TO_TABLE is a TVF, not scalar** |

### Needed Fixes

1. **CAST functions**: The mapper says `CAST` but `translate_expression` just
   emits `CAST(arg)` without the target type. Fix:
   ```python
   if fn == "tostring": return f"CAST({args[0]} AS VARCHAR(4096))"
   if fn == "tointeger": return f"CAST({args[0]} AS INTEGER)"
   if fn == "tofloat": return f"CAST({args[0]} AS DOUBLE)"
   ```

2. **split()**: IRIS's `$PIECE` is the closest equivalent, but it's ObjectScript,
   not SQL. In SQL, use `%SYSTEM.SQL.SPLIT()` if available in your IRIS version,
   or handle in Python post-processing.

3. **toBoolean()**: Map to `CASE WHEN {arg} IN ('true','1','yes') THEN 1 ELSE 0 END`.

4. **size() on collections**: `size(collect(g))` → the `collect()` already maps
   to `JSON_ARRAYAGG`, so `size()` should wrap it with
   `JSON_LENGTH(JSON_ARRAYAGG(...))`.

**Steve's take**: Fix the CAST functions immediately — it's a 10-line fix. The
split/toBoolean edge cases can wait.

#### Effort: **S** (Small) for critical fixes, **M** for full coverage
- CAST fixes: ~15 lines
- split/toBoolean: ~30 lines
- Tests: 8 cases

---

## Implementation Priority (Steve-and-Dan Verdict)

| Priority | Gap | Effort | Approach | Justification |
|----------|-----|--------|----------|---------------|
| **P0** | #8 COUNT(DISTINCT) | XS | Verify existing | Probably already works. Free win. |
| **P0** | #10 CAST fixes | S | Translator fix | 10-line fix, unblocks type coercion |
| **P1** | #3 CASE WHEN | S | Parser + translator | 1:1 SQL mapping, high value, low risk |
| **P1** | #1 Variable-length paths | L | Hybrid BFSFast CTE | Highest biomedical value. Hardest feature. |
| **P2** | #2 UNION | M | Parser + post-assembly | Needed for multi-type result sets |
| **P2** | #4 EXISTS {} | M | Correlated subquery | Important for subgraph existence checks |
| **P2** | #5 Pattern comprehension | M | Correlated JSON_ARRAYAGG | Nice-to-have, complex parser work |
| **P3** | #6 Quantified paths | S | Desugar (needs #1) | Sugar on top of variable-length |
| **P3** | #7 REDUCE | M | Python post-process | Niche use case, workaround exists |
| **P4** | #9 FOREACH | S | Desugar to UNWIND | UNWIND already does this |

### Recommended Sprint Plan

**Sprint 1** (1-2 days): P0 items
- Verify COUNT(DISTINCT) → add tests
- Fix CAST functions in `_CYPHER_FN_MAP` handling
- Fix `split()` mapping

**Sprint 2** (2-3 days): CASE WHEN
- Add CASE/WHEN/THEN/ELSE/END tokens to lexer
- Parse CaseExpression
- Translate to SQL CASE

**Sprint 3** (5-7 days): Variable-Length Paths
- Create SQL function wrapper for BFSFastJson
- Implement CTE bridge in translator when VariableLength is present
- Handle single-source and multi-source cases
- Extensive testing (this is the riskiest feature)

**Sprint 4** (3-4 days): UNION + EXISTS
- UNION: parser loop + SQL assembly
- EXISTS {}: correlated subquery generation

---

## Appendix: Key Files to Modify

| File | Gaps |
|------|------|
| `cypher/lexer.py` | #2 (UNION), #3 (CASE/WHEN/THEN/ELSE/END), #9 (FOREACH) |
| `cypher/parser.py` | #1 (already done), #2, #3, #4, #5, #6, #7, #9 |
| `cypher/ast.py` | #2 (UnionQuery), #3 (CaseExpression), #4 (ExistsExpression), #5 (PatternComprehension), #7 (ReduceExpression), #9 (ForEachClause) |
| `cypher/translator.py` | #1 (CTE bridge), #2 (assembly), #3 (CASE SQL), #4 (correlated EXISTS), #5 (correlated JSON_ARRAYAGG), #10 (CAST fixes) |
| `cypher/algorithms/paths.py` | #1 (BFS integration reference) |
| `iris_src/src/Graph/KG/Traversal.cls` | #1 (SQL function wrapper for BFSFastJson) |
| `engine.py` | #7 (post-process reduce) |

## Appendix: Test Matrix for Variable-Length Paths (#1)

| Test Case | Cypher | Expected Behavior |
|-----------|--------|-------------------|
| Fixed source, typed | `MATCH ({id:'A'})-[:REL*1..2]->(t) RETURN t` | BFSFastJson('A', '["REL"]', 2) |
| Fixed source, untyped | `MATCH ({id:'A'})-[*1..3]->(t) RETURN t` | BFSFastJson('A', '', 3) |
| Label-filtered source | `MATCH (d:Drug)-[:TARGETS*1..2]->(g:Gene) RETURN d,g` | Per-drug BFSFast with dstLabel='Gene' |
| Bidirectional | `MATCH (a)-[*1..2]-(b) WHERE a.id='X' RETURN b` | Need to walk both ^KG("out") and ^KG("in") |
| Min > 1 | `MATCH ({id:'A'})-[:REL*2..3]->(t) RETURN t` | Filter results WHERE step >= 2 |
| Path variable | `MATCH p=({id:'A'})-[:REL*1..3]->(t) RETURN p` | Collect path edges from BFS results |
| With WHERE on target | `MATCH ({id:'A'})-[*1..2]->(t) WHERE t.name = 'X' RETURN t` | Post-filter on target properties |
| Chained after fixed | `MATCH (a)-[:R1]->(b)-[:R2*1..2]->(c) RETURN c` | Fixed JOIN for R1, BFS for R2 |
