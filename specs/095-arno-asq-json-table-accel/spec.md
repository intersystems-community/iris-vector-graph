# Spec 095: Arno ASQ as JSON_TABLE Accelerator

**Feature Branch**: `095-arno-asq-json-table-accel`
**Created**: 2026-05-04
**Status**: Ready for 2nd opinion
**Cross-reference**: Spec 093 (Benchmark), Spec 094 (BFS global buffer)

## Background

IVG's Cypher translator emits IRIS SQL with `JSON_TABLE()` in several hot paths:

| Pattern | Generated SQL | Problem |
|---------|--------------|---------|
| `UNWIND list AS x` | `CROSS JOIN JSON_TABLE(expr, '$[*]' COLUMNS(x ...)) alias` | IRIS JSON_TABLE is slow on large lists |
| `CALL ivg.ppr(...)` | `FROM JSON_TABLE(Graph_KG.kg_PPR(...), '$[*]' COLUMNS(...))` | Stored proc returns JSON → SQL re-parses it |
| `CALL ivg.bm25.search(...)` | `FROM JSON_TABLE(Graph_KG.kg_BM25(...), '$[*]' COLUMNS(...))` | Same |
| `CALL ivg.ivf.search(...)` | `FROM JSON_TABLE(Graph_KG.kg_IVF(...), '$[*]' COLUMNS(...))` | Same |
| `MATCH (n) WHERE n.id IN list` | `CROSS JOIN JSON_TABLE($param, '$[*]' COLUMNS(...))` | Param expansion |

In all cases: ObjectScript produces a JSON array → IRIS SQL engine parses it with
`JSON_TABLE` → produces rows. The JSON parse is done inside the IRIS SQL engine's
C layer — not terrible, but the round-trip adds latency and SQL optimizer complexity
(IRIS SQLCODE -400 deep-join limit is triggered by complex `JSON_TABLE` CTEs).

## What Arno's ASQ Engine Provides

Arno has `query_docdb` (line 318 in lib.rs) + `query_native` + the full ASQ engine
(`asq.rs`): a **Rust JSONPath evaluator** that reads IRIS globals directly via callin
and returns extracted values as JSON arrays.

Key capabilities:
- `query_docdb(global_name, asq_expr)` — reads `^global(id) = "{JSON}"` or DocDB format,
  applies ASQ path expression, returns `[value, value, ...]` JSON array
- `NativeGlobalProvider` — reads globals lazily via `global_order`/`global_get` callin
- Predicate pushdown: `$.items[*].keyvalue()?(@.key starts with "node_")` — filter in Rust
  before returning to IRIS
- Early termination: `set_limit(k)` — stop after k results (for TOP-k queries)

## The Acceleration Opportunity

### Pattern 1: CALL ivg.bm25.search / ivg.ppr / ivg.ivf.search

**Today** (translator.py lines 543, 587, 649):
```sql
WITH BM25 AS (
  SELECT j.node_id, j.score
  FROM JSON_TABLE(
    Graph_KG.kg_BM25('index', 'query', 10),
    '$[*]' COLUMNS(node_id VARCHAR(256) PATH '$.id', score DOUBLE PATH '$.score')
  ) j
)
SELECT ... FROM BM25 JOIN ...
```

Flow: ObjectScript BM25 → JSON string → IRIS SQL JSON_TABLE parse → rows → JOIN

**With ASQ acceleration** (proposed):
```sql
WITH BM25 AS (
  SELECT j.node_id, j.score
  FROM JSON_TABLE(
    Graph_KG.kg_BM25_arno('index', 'query', 10),
    '$[*]' COLUMNS(node_id VARCHAR(256) PATH '$.id', score DOUBLE PATH '$.score')
  ) j
)
```

Where `kg_BM25_arno` is a stored procedure that:
1. Calls `Graph.KG.BM25Index.Search()` → writes results to `^||ArnoBM25(N)` chunks
2. Calls `$ZF(-5, dllid, fnid, "^||ArnoBM25", asq_expr)` → Rust reads global, applies
   ASQ `'$[*]'` pattern, returns filtered JSON array
3. Returns the JSON array — same format as today, transparent to SQL engine

**OR** — bypass SQL entirely:

The Cypher translator detects `CALL ivg.bm25.search` patterns and instead of emitting
`JSON_TABLE(stored_proc(...))`, emits a **direct $ZF call via IRIS stored procedure**:

```sql
WITH BM25 AS (
  SELECT j.node_id, j.score
  FROM JSON_TABLE(
    Graph_KG.kg_BM25_arno_direct(?, ?, ?),  -- writes to ^||ArnoBM25, reads via ASQ
    '$[*]' COLUMNS(node_id VARCHAR(256) PATH '$.id', score DOUBLE PATH '$.score')
  ) j
)
```

This is marginal — the IRIS SQL engine still parses the JSON_TABLE output.

### Pattern 2: UNWIND with Large Lists

**Today**: `UNWIND $node_ids AS x` → `CROSS JOIN JSON_TABLE(?, '$[*]' COLUMNS(x ...)) u`

When `$node_ids` is a 500-element list, IRIS JSON_TABLE materializes all 500 rows then
joins. This is also where SQLCODE -400 (too many JOINs) is triggered.

**With ASQ**: The translator could emit a dedicated IRIS stored procedure
`Graph_KG.kg_ArnoUnwind(json_array, limit)` that:
1. Writes the array to `^||ArnoUnwind(N)` chunks
2. Calls ASQ `query_native("^||ArnoUnwind", "$[*]")` → Rust returns filtered list
3. Returns the list

Again marginal for the JOIN count problem — the SQL engine still sees the same shape.

### Pattern 3: CALL ivg.ppr — The Best Target

PPR is the strongest case because the result JSON can be very large (1000s of nodes × score)
AND the downstream MATCH is typically `JOIN nodes n ON n.node_id = PPR.node_id`.

**Today**: PPR result JSON → JSON_TABLE → SQL JOIN

**With ASQ + global buffer**: PPR writes directly to `^ArnoKG("ppr_result",N)` chunks,
ASQ reads and filters in Rust (e.g. top-k by score), returns only the needed node IDs.
The IRIS SQL engine sees a much smaller JSON_TABLE input → fewer rows → faster JOIN.

This is a real win: PPR at 10K nodes returns 10K rows of JSON. Top-50 filter in Rust
before JSON_TABLE means IRIS SQL processes 50 rows instead of 10K.

## Actual Opportunity: PPR Result Filtering

The concrete, high-value change is **post-PPR filtering in Rust before returning to SQL**:

```objectscript
// Today: ObjectScript PPR → full JSON → SQL parses all rows
ClassMethod kg_PPR(seedJson, alpha, maxIter, ...) As %String
{
    // ... run PPR, return JSON of all node scores ...
}

// Proposed: ObjectScript PPR → ^ArnoKG chunks → Rust top-k filter → small JSON
ClassMethod kg_PPR_TopK(seedJson, alpha, maxIter, k) As %String
{
    Set pprJson = ..RunPPR(seedJson, alpha, maxIter)
    // Write to ^ArnoKG("ppr_result", N) chunks
    // Call $ZF(-5, ..., "kg_topk_filter", "^ArnoKG", "$.score", k)
    // Rust reads chunks, sorts by $.score, returns top-k JSON
    Return topKJson  // k rows instead of 10K
}
```

New Rust function:
```rust
#[rzf]
pub fn kg_topk_filter(global_name: String, score_path: String, k: i64) -> String {
    // read global chunks (same as read_nkg_adjacency)
    // parse JSON array
    // sort by score_path (ASQ evaluation on each item)
    // return top-k as JSON array
}
```

This reuses `query_docdb` logic (already reads globals, applies ASQ path) + adds sort+limit.

## Recommendation

**Spec 095 should implement two things:**

### Part A: `kg_topk_filter` — Top-K Filtering in Rust (High Value)

For PPR, BM25, IVF results where caller only needs top-k:
- New `kg_topk_filter(global_name, score_field, k)` Rust function
- New `Graph_KG.kg_PPR_TopK` / `kg_BM25_TopK` / `kg_IVF_TopK` stored procedures
- Translator emits top-k variant when `CALL ivg.ppr(...) YIELD node, score` has a
  downstream `ORDER BY score DESC LIMIT k`

Expected win: PPR CTE result size 10K rows → k rows (k=50 typical). SQL JOIN processes
50 rows instead of 10K — significant for multi-hop PPR+MATCH queries.

### Part B: `query_docdb` for rdf_props — Node Property Lookup (Medium Value)

`rdf_props` lookups in Cypher (`MATCH (n:Gene) RETURN n.name`) currently join SQL tables.
For large label scans, `query_docdb("^KGProps", "$.name")` reading a denormalized
`^KGProps(nodeId) = "{name: ..., type: ...}"` global could bypass SQL entirely.

This requires: (1) maintaining `^KGProps` in sync with `rdf_props` SQL on write, (2)
translator detecting label+property patterns and routing to the new stored proc.

More invasive — deferred to a follow-on spec.

## What Is NOT Worth Doing

- Accelerating `UNWIND` via ASQ: IRIS SQL engine still sees the JSON_TABLE output shape,
  SQLCODE -400 JOIN limit is unchanged. Not worth the complexity.
- Replacing JSON_TABLE entirely: IRIS SQL needs it for the column schema. ASQ is a
  pre-filter / result-shrinker, not a SQL replacement.

## Acceptance Criteria (Part A only)

- **SC-001**: `CALL ivg.ppr($seeds) YIELD node, score ORDER BY score DESC LIMIT 50`
  executes in ≤20ms on 10K-node graph (today: PPR 62ms + SQL JSON_TABLE parse)
- **SC-002**: Top-50 result set identical to sorting full PPR output by score DESC
- **SC-003**: `kg_topk_filter` handles empty global gracefully (returns `[]`)
- **SC-004**: No regression on existing `CALL ivg.ppr` without LIMIT (uses current path)

## Dependencies

- Spec 094 (global buffer for BFS) must land first — establishes the `^ArnoKG` chunk
  write + Rust global read pattern that Part A reuses.

---

## VERDICT: PERMANENTLY PARKED

**Date**: 2026-05-04  
**Reason**: Benchmark stress test showed JSON_TABLE is not the bottleneck.

```
JSON_TABLE parse: 10–10,000 items  →  0.17ms–0.32ms (flat)
JSON_TABLE on real PPR (1K nodes)  →  0.19ms
UNWIND + JOIN: 10–1,000 nodes      →  0.17ms–0.19ms (flat)
TOP-k from JSON_TABLE              →  0.02ms

vs.

ObjectScript PPRNative computation →  348ms
SQL kg_PPR computation             →  122ms
```

IRIS's JSON_TABLE is already well-optimized. Replacing it with Rust would save ~0.13ms
while PPR computation costs 122ms. The ratio is 1000:1 in favor of accelerating
computation, not parsing.

**Council verdict (Dan Pasco, Tim Leavitt, Steve Morrison — 2026-05-04)**:
> "This is exactly why you run benchmarks before building."

The correct acceleration targets remain:
1. Spec 094 — arno BFS global buffer (fixes `<MAX $ZF STRING>`)
2. Spec 079 — arno BFS computation (projected 128ms → <30ms)
3. Existing `kg_ppr_global` — arno PPR computation (122ms → ~5ms)
