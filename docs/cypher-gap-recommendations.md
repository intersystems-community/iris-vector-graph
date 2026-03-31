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
serializes to JSON. Consider adding a `BFSFastJsonDirect` variant that builds
the JSON string directly via `$ListGet` + string concatenation, bypassing all
`%DynamicObject` allocation. See the **Addendum: JSON_TABLE Serialization
Overhead & Acceleration Options** section below for the full analysis and a
concrete ObjectScript implementation.

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

## Addendum: JSON_TABLE Serialization Overhead & Acceleration Options

**From**: arno team (Steve-and-Dan perspective)
**Date**: 2026-03-31
**Context**: The Gap #1 recommendation proposes wrapping `BFSFastJson` in
`JSON_TABLE()` to expose BFS results as SQL rows. This addendum analyzes the
serialization overhead in that approach and evaluates alternatives.

### The Problem: Unnecessary Serialize-Deserialize Roundtrip

BFSFast already materializes results in a process-private global:

```
^||BFS.Results(1) = $ListBuild("nodeA", "TARGETS", "nodeB", 1.0, 1)
^||BFS.Results(2) = $ListBuild("nodeB", "TARGETS", "nodeC", 1.0, 2)
...
```

The proposed JSON_TABLE approach adds two unnecessary conversions:

```
^||BFS.Results (process-private global, $ListBuild format)
    → %DynamicObject construction (one per row)           ← allocation overhead
    → %DynamicArray.%Push() per row                       ← array growth overhead
    → %ToJSON() → JSON string                             ← serialization cost
    → JSON_TABLE() parses JSON → virtual SQL rows         ← deserialization cost
    → SQL engine consumes rows                            ← finally useful
```

For small results (<100 rows), this overhead is negligible (~0.1ms). For large
traversals (10K+ paths on NCIT-scale graphs), the %DynamicObject allocation and
JSON serialization dominate. Profiling on the NCIT graph shows:
- BFSFast traversal: ~0.2ms for 2-hop from a typical node
- BFSFastJson overhead (JSON construction): ~0.5ms for ~200 results
- JSON_TABLE parsing: ~0.1ms (IRIS JSON_TABLE is fast)

At scale (1000+ source nodes × 200 results each), this adds up.

### Shadow Global Insight

**^KG is already a shadow global of rdf_edges.** The `GraphIndex.cls` functional
index maintains ^KG in lockstep with every SQL write:

```objectscript
// GraphIndex.cls — fires on every INSERT to rdf_edges
ClassMethod InsertIndex(pID, s, p, o, qualifiers)
{
    Set ^KG("out", s, p, o) = weight
    Set ^KG("in", o, p, s) = weight
    Set ^NKG(-1, sIdx, -(pIdx+1), oIdx) = weight   // integer-keyed mirror
    ...
}
```

This means BFSFast over ^KG is **reading from an already-synchronized shadow
structure** — it's not going behind the SQL engine's back. The data is
consistent by construction.

The question is: can we get BFS results back INTO the SQL engine without the
JSON detour?

### Evaluated Alternatives

#### Option 1: JSON_TABLE (Current Recommendation) — KEEP AS DEFAULT

```sql
VarPath0 AS (
  SELECT j.* FROM JSON_TABLE(
    Graph_KG.BFS_Path(?, ?, ?, ?),
    '$[*]' COLUMNS(s VARCHAR(512) PATH '$.s', ...)
  ) j
  WHERE j.step >= ? AND j.step <= ?
)
```

**Pros**: Simple to implement. JSON_TABLE is well-tested in IVG (PPR CTE uses
it today). SQL function wrapper is ~5 lines of ObjectScript. Works with existing
translator CTE infrastructure.

**Cons**: Serialize-deserialize overhead. %DynamicObject allocation per row.

**When to use**: Default choice. Good enough for single-source traversals and
result sets < 1000 rows.

#### Option 2: Optimized JSON String Builder — RECOMMENDED IMPROVEMENT

Replace `BFSFastJson`'s %DynamicObject construction with direct string building:

```objectscript
ClassMethod BFSFastJsonDirect(srcId, predsJson, maxHops, dstLabel) As %String
{
    Set count = ..BFSFast(srcId, .preds, maxHops, dstLabel)
    If count = 0 Return "[]"

    Set result = "["
    For i = 1:1:count {
        Set lb = $Get(^||BFS.Results(i))
        If lb = "" Continue
        If i > 1 Set result = result _ ","
        // Direct string concatenation — no %DynamicObject allocation
        Set result = result _ "{""s"":""" _ $ListGet(lb,1)
            _ """,""p"":""" _ $ListGet(lb,2)
            _ """,""o"":""" _ $ListGet(lb,3)
            _ """,""w"":" _ +$ListGet(lb,4)
            _ ",""step"":" _ +$ListGet(lb,5) _ "}"
    }
    Return result _ "]"
}
```

**Pros**: Eliminates all %DynamicObject and %DynamicArray allocation. String
concatenation in ObjectScript is fast (IRIS uses rope-like internals). Still
compatible with JSON_TABLE — no translator changes needed. Drop-in replacement.

**Cons**: Still has JSON serialization + JSON_TABLE deserialization. Fragile
string construction (must handle escaping if node IDs contain quotes — rare
in practice for NCIT-style `C12345` IDs, but worth a note).

**Expected speedup**: 2-4x for the JSON construction phase. Total query time
improvement: 20-40% for large result sets.

**Steve's take**: Do this first. It's a 15-line ObjectScript change, zero risk
to the translator, and immediately benefits all JSON_TABLE consumers (PPR too).

#### Option 3: %SQL.CustomResultSet — FUTURE OPTION (Not Recommended Yet)

Create an ObjectScript class extending `%SQL.CustomResultSet` that reads
`^||BFS.Results` directly and yields rows to the SQL engine without JSON:

```objectscript
Class Graph.KG.BFSResultSet Extends %SQL.CustomResultSet
{
    Property CurrentRow As %Integer [ InitialExpression = 0 ];
    Property MaxRow As %Integer;
    // Column properties...

    Method %OpenCursor() As %Status
    {
        Set ..MaxRow = ..BFSFast(srcId, preds, maxHops, dstLabel)
        Return $$$OK
    }

    Method %Next(ByRef sc As %Status) As %Integer
    {
        Set ..CurrentRow = ..CurrentRow + 1
        If ..CurrentRow > ..MaxRow Return 0
        Set lb = $Get(^||BFS.Results(..CurrentRow))
        Set ..s = $ListGet(lb,1), ..p = $ListGet(lb,2), ...
        Return 1
    }
}
```

Then use it in SQL as: `FROM Graph_KG.BFSResultSet(?, ?, ?, ?)`

**Pros**: Zero serialization overhead. SQL engine reads rows directly from
the process-private global via the cursor interface. Theoretically optimal.

**Cons**: `%SQL.CustomResultSet` has limitations in IRIS:
- Cannot be used as a CTE source — only as a FROM clause table source
- JOIN optimization with CustomResultSet is limited (IRIS doesn't know the
  cardinality or statistics)
- Requires more complex ObjectScript (cursor lifecycle management)
- Not well-tested in the JSON_TABLE CTE pattern the translator uses
- **Process-private global caveat**: `^||BFS.Results` is per-process, so the
  BFS must run in the SAME process as the SQL query. This works for JSON_TABLE
  (function call is inline) but may not for CustomResultSet if IRIS spawns
  the cursor in a different context.

**Dan's take**: Don't do this now. The JSON_TABLE approach with Option 2's
string optimization is good enough. CustomResultSet adds complexity for
marginal gain at IVG's current scale. Revisit if JSON serialization becomes
a measured bottleneck at >100K path results.

#### Option 4: Temp Table Bridge — NOT RECOMMENDED

Write BFS results to a temp SQL table, then JOIN:

```objectscript
// In BFSFast, instead of ^||BFS.Results:
&sql(INSERT INTO %SYSTEM.Temp.BFSResults (s,p,o,w,step) VALUES (:s,:p,:o,:w,:step))
```

**Pros**: Standard SQL JOIN, full optimizer statistics available.

**Cons**: 
- INSERT overhead per row (worse than JSON_TABLE for small results)
- Temp table lifecycle management (create/drop per query)
- DDL in the middle of a query path — risky for concurrent users
- IRIS doesn't support `CREATE TEMPORARY TABLE` like PostgreSQL — you'd need
  a real table with session isolation via `%SYSTEM.Process.ProcessId()`

**Steve's take**: Hard no. This adds more overhead than it saves, and the
concurrency story is terrible. JSON_TABLE is fundamentally the right pattern
for IRIS — it's how IRIS exposes non-SQL data to the SQL engine.

### Arno's Proven Pattern: Rust-Accelerated ^KG Traversal via $ZF(-6)

**This is NOT a theoretical possibility — arno already does this for PageRank,
PPR, WCC, and CDLP on ^KG.** The pattern is proven and deployed.

#### How It Works Today (lib.rs + kg_ffi.rs)

arno-callout exposes `_global` variants of every graph algorithm:

```rust
// lib.rs — $ZF(-6) entry points
#[rzf] pub fn kg_pagerank_global(global_name: String, damping: f64, max_iter: i64) -> String
#[rzf] pub fn kg_ppr_global(global_name: String, seeds_json: String, ...) -> String
#[rzf] pub fn kg_wcc_global(global_name: String) -> String
#[rzf] pub fn kg_subgraph_global(global_name: String, seeds_json: String, ...) -> String
#[rzf] pub fn kg_khop_sample(global_name: String, seeds_json: String, ...) -> String
#[rzf] pub fn kg_random_walk(global_name: String, seeds_json: String, ...) -> String
```

Each of these:
1. **Reads ^KG directly** via rzf `$ORDER`/`$DATA` (no SQL, no ObjectScript)
2. **Builds in-memory adjacency** in Rust (HashMap<String, Vec<usize>>)
3. **Runs the algorithm** in pure Rust at C speed
4. **Returns JSON result** string back to the IRIS process

The Rust BFS over ^KG runs at ~10-100x the speed of ObjectScript `$ORDER`
loops because:
- No ObjectScript interpreter overhead per iteration
- Rust HashMap lookups vs IRIS B-tree global traversal (amortized via batch reads)
- Zero `%DynamicObject` / `%DynamicArray` allocation
- The JSON string is built via `serde_json` (zero-copy where possible)

#### The Missing Piece: `kg_bfs_global`

arno already has `kg_khop_sample` and `kg_subgraph_global` which do k-hop
traversal over ^KG in Rust. **A `kg_bfs_global` function that matches
BFSFast's semantics is a natural extension:**

```rust
// Proposed new $ZF function (arno-callout, closed source)
#[rzf]
pub fn kg_bfs_global(
    global_name: String,    // "^KG"
    src_id: String,         // source node
    preds_json: String,     // '["TARGETS"]' or "" for all
    max_hops: i64,          // maximum traversal depth
    dst_label: String,      // filter destination by label ("Gene") or ""
    min_hops: i64,          // minimum hop count filter
) -> String {
    // Reads ^KG("out", src, pred, obj) directly via rzf
    // Applies predicate filter, label filter, hop range
    // Returns JSON: [{"s":"A","p":"TARGETS","o":"B","w":1.0,"step":1}, ...]
}
```

#### How IVG Would Use It (MIT-safe interface)

The IVG translator doesn't need to know this is Rust. It calls it as a SQL
function, exactly like it calls `BFSFastJson` today:

```sql
-- Option A: ObjectScript wrapper calls $ZF(-6) internally
CREATE FUNCTION Graph_KG.BFS_Path(src VARCHAR, preds VARCHAR, maxHops INT,
                                   label VARCHAR, minHops INT)
RETURNS VARCHAR(MAXLEN)
LANGUAGE OBJECTSCRIPT
{
    // Try arno-accelerated path first, fall back to ObjectScript
    Set result = $ZF(-6, "arno.so", "kg_bfs_global", "^KG", src, preds, maxHops, label, minHops)
    If result = "" Set result = ##class(Graph.KG.Traversal).BFSFastJson(src, preds, maxHops, label)
    Return result
}
```

```sql
-- The translator emits exactly the same CTE as before:
VarPath0 AS (
  SELECT j.* FROM JSON_TABLE(
    Graph_KG.BFS_Path(?, ?, ?, ?, ?),
    '$[*]' COLUMNS(s VARCHAR(512) PATH '$.s', ...)
  ) j
)
```

**The MIT boundary is clean**: IVG's translator emits SQL that calls a SQL
function. Whether that function internally calls ObjectScript BFSFast or
arno Rust via `$ZF(-6)` is an implementation detail hidden behind the SQL
function interface. IVG has zero arno code. arno-callout is a separately
deployed binary.

#### Performance Impact

| Component | ObjectScript BFSFast | Arno Rust kg_bfs_global |
|-----------|---------------------|------------------------|
| ^KG traversal | ObjectScript $ORDER | rzf $ORDER (zero-copy) |
| Per-node processing | Interpreter loop | Compiled Rust |
| Result accumulation | ^||BFS.Results + $lb | Vec<BfsEntry> |
| JSON serialization | %DynamicObject loop | serde_json (batch) |
| **Expected speedup** | Baseline | **10-50x for traversal** |
| **JSON overhead** | ~0.5ms/200 rows | ~0.05ms/200 rows |

For the common case (single-source, 2-3 hops, <500 results), the total
query time is already sub-millisecond with ObjectScript. The Rust acceleration
matters when:
- **Multi-source traversal**: 1000+ Drug nodes, each doing BFS → 1000 function
  calls. Rust reduces each from ~0.5ms to ~0.05ms → 500ms → 50ms total.
- **Deep traversal**: `*1..5` on NCIT-scale graphs → ObjectScript BFS may take
  10-50ms per source; Rust cuts that by 10x.
- **Large result sets**: >10K path results → JSON serialization dominates;
  serde_json is ~10x faster than `%DynamicObject` + `%ToJSON()`.

#### What Transfers vs What Doesn't

**Transfers to IVG (MIT-safe, architectural insights)**:
- ^KG IS a shadow global — traversal over it IS predicate pushdown
- The SQL function wrapper pattern (hide implementation behind SQL interface)
- JSON_TABLE as the CTE bridge from function output to SQL rows
- The fallback pattern (try accelerated path, fall back to pure ObjectScript)

**Does NOT transfer (arno IP, closed source)**:
- The Rust `kg_bfs_global` implementation
- The `NativeGlobalProvider` and `PredicateHints` engine
- The rzf crate and `$ZF(-6)` integration code
- The `native_algos` adjacency reader and graph algorithm implementations

**IVG's ObjectScript BFSFast remains the default**. arno acceleration is an
optional, separately-deployed enhancement.

### Verdict

| Approach | Effort | Speedup | Risk | Recommendation |
|----------|--------|---------|------|----------------|
| JSON_TABLE (as-is) | Done | Baseline | None | **Keep as default** |
| Option 2: Direct string builder | S (15 lines) | 2-4x JSON phase | Low | **Do this now** |
| Option 3: CustomResultSet | L (100+ lines) | ~10x JSON phase | Medium | Future, if needed |
| Option 4: Temp table | M (50 lines) | Negative | High | **Don't do this** |

**Final Steve-and-Dan recommendation**: Keep JSON_TABLE as the translator's CTE
pattern (it's clean, composable, and proven). Optimize the ObjectScript side with
Option 2's direct string builder. If profiling on production graphs shows the
JSON path is still the bottleneck at >50K results, then evaluate CustomResultSet.

The PPR CTE in the translator already uses JSON_TABLE successfully — variable-
length paths should follow the same pattern for consistency.

---

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
