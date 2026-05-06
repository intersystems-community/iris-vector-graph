# Spec 100: Cypher Variable-Length Path Routing to BFS

**Feature Branch**: `100-cypher-vl-path-to-bfs`  
**Created**: 2026-05-04  
**Status**: Draft  
**Priority**: P0 — current behavior is 600× slower than it should be

---

## Problem

`MATCH (a)-[:R*1..N]-(b)` in IVG's Cypher translator produces SQL with JSON_TABLE and
multi-level JOINs that is catastrophically slow and often crashes the SQL optimizer.

**Measured on LDBC SF10 (1.94M knows edges):**

| Query | IVG current | Expected | Root cause |
|-------|------------|----------|-----------|
| `MATCH (a)-[:KNOWS*1..2]-(b) RETURN DISTINCT b LIMIT 50` | **608ms** | ~1ms | SQL JOIN chain |
| `MATCH (a)-[:KNOWS*1..3]-(b) RETURN count(b)` | crash (SQLCODE -400) | ~15ms | >24 JOINs |

GES SF100 for equivalent IC3/IC5 patterns: **15-17ms**.  
IVG at 608ms is **40× slower** than GES on a smaller graph.

---

## Root Cause

The Cypher translator's variable-length path handler generates:

```sql
WITH Stage0 AS (SELECT node_id AS n0 FROM Graph_KG.nodes WHERE node_id = ?),
Stage1 AS (
  SELECT j.o_id AS n1 FROM Stage0
  CROSS JOIN Graph_KG.rdf_edges e1 ON e1.s = Stage0.n0 AND e1.p = 'KNOWS'
),
Stage2 AS (
  SELECT j.o_id AS n2 FROM Stage1
  CROSS JOIN Graph_KG.rdf_edges e2 ON e2.s = Stage1.n1 AND e2.p = 'KNOWS'
)
SELECT DISTINCT n2 FROM Stage2 LIMIT 50
```

For depth=2 on 62K persons × avg degree 40: Stage1 = 40 rows, Stage2 = 40² = 1,600 rows.
Each CTE scans `rdf_edges` (1.94M rows) — 2 full scans. That's ~4M row reads for a
query BFS handles in ~2,880 `^NKG` index reads.

For depth=3 with 4+ CTEs, IRIS hits the 24-JOIN optimizer limit → SQLCODE -400.

---

## Solution

**Route variable-length path patterns to arno BFSJson / BFSFastJson instead of SQL.**

The translator detects:
```
MATCH (a {node_id: $src})-[r:PRED*minHops..maxHops]-(b) RETURN DISTINCT b.node_id
```
or:
```
MATCH (a)-[:PRED*1..N]->(b) WHERE a.node_id = $src RETURN b.node_id
```

And emits a call to `Graph.KG.NKGAccel.BFSJson` (arno) or `Graph.KG.Traversal.BFSFastJson`
(ObjectScript fallback) instead of a SQL CTE chain.

### Translator change

In `iris_vector_graph/cypher/translator.py`, the `_translate_variable_length_path`
function currently builds the CTE chain. Change it to:

```python
def _translate_variable_length_path(match_pattern, context):
    # Extract: src node variable, predicate(s), min/max hops, dst variable
    src_var, predicates, min_hops, max_hops, dst_var = parse_vl_pattern(match_pattern)
    
    # Build: CALL ivg.bfs($src, predicates, maxHops) YIELD node
    # which already routes to NKGAccel.BFSJson or BFSFastJson
    return _translate_as_bfs_call(src_var, predicates, max_hops, context)
```

The `CALL ivg.bfs(...)` pattern already exists in the CALL procedure handler.
The variable-length path is syntactic sugar for the same operation.

### BFS CALL translation (already works)

`CALL ivg.bfs($seed, ['KNOWS'], 2) YIELD node` already routes correctly to BFSJson.
The fix is making `MATCH (a)-[:KNOWS*1..2]-(b)` produce the same execution plan.

### Result mapping

BFSJson returns `[{s, p, o, w, step}]`. The translator must map the dst variable `b`
to the `o` field, and expose `step` as the path length if needed.

---

## Scope

### In scope
- `MATCH (a)-[r:PRED*minHops..maxHops]->(b)` patterns (directed)
- `MATCH (a)-[:PRED*minHops..maxHops]-(b)` patterns (undirected)
- `RETURN DISTINCT b.node_id` — return destination node IDs
- `RETURN count(b)` — return count of reachable nodes
- `LIMIT N` — pass as `max_results` to BFSJson

### Out of scope (deferred)
- Paths where intermediate nodes need properties (`MATCH (a)-[r*1..2]-(b) RETURN r`)
- Mixed-predicate paths `MATCH (a)-[:A|B*1..2]-(b)` — multiple predicates
- `WHERE` filters on hop-level properties
- `allShortestPaths(...)` — separate function, separate spec

### Not changed
- `shortestPath(...)` — already routes correctly to ShortestPathNKG
- `CALL ivg.bfs(...)` — already works
- Single-hop `MATCH (a)-[:KNOWS]->(b)` — already fast via SQL

---

## Implementation

### Step 1: Detect variable-length pattern in AST

In `iris_vector_graph/cypher/ast.py`, `RelationshipPattern` already has `min_hops`
and `max_hops` fields. The translator must check these and branch.

### Step 2: Extract BFS parameters

From the pattern:
- `predicates` → relationship type(s) as JSON list
- `max_hops` → depth limit
- `min_hops` → lower bound (if > 1, filter results by step >= min_hops)
- `src_node` → the anchor node variable (resolved to `node_id`)
- `direction` → `out`, `in`, or `both`

### Step 3: Emit BFS execution

Instead of CTEs, emit a call to the IVG BFS engine:

```python
# In the translated execution plan:
raw = _call_classmethod_large(
    iris_obj, "Graph.KG.NKGAccel", "BFSJson",
    src_node_id,      # resolved from anchor variable
    json.dumps(predicates),  # ["KNOWS"] or [] for all
    max_hops,
    0                 # uncapped, LIMIT applied post-BFS
)
results = [r for r in json.loads(raw) if r['step'] >= min_hops]
if limit: results = results[:limit]
```

### Step 4: Handle RETURN clause

Map BFS output to Cypher variable bindings:
- `RETURN DISTINCT b.node_id` → deduplicate `r['o']` values
- `RETURN b.node_id, step` → return `r['o']` and `r['step']`
- `RETURN count(b)` → `len({r['o'] for r in results})`
- `RETURN b` → return full node dict (requires separate lookup)

---

## Performance Targets

After fix, on LDBC SF10 (1.94M knows edges, 62K persons):

| Query | Before | After | Target |
|-------|--------|-------|--------|
| `[*1..2]` limit 50 | 608ms | ~1ms | < 5ms |
| `[*1..3]` count | crash | ~15ms | < 20ms |
| `[*1..4]` count | crash | ~45ms | < 60ms |

GES SF100 IC3 (friends in location, uses 2-hop): 15ms.  
After fix, IVG IC3 equivalent: ~1ms for the BFS portion (filter on location is additive).

---

## Test Plan

### Failing tests (write first — RED before implementation)

```python
def test_vl_path_uses_bfs_not_sql(iris_conn):
    engine = IRISGraphEngine(...)
    t0 = time.perf_counter()
    r = engine.execute_cypher(
        'MATCH (a {node_id:$src})-[:KNOWS*1..2]-(b) RETURN DISTINCT b.node_id LIMIT 50',
        {'src': 'p_933'})
    ms = (time.perf_counter()-t0)*1000
    assert ms < 10, f"VL path took {ms:.0f}ms — still using SQL not BFS"
    assert len(r['rows']) > 0

def test_vl_path_depth3_no_crash(iris_conn):
    engine = IRISGraphEngine(...)
    r = engine.execute_cypher(
        'MATCH (a {node_id:$src})-[:KNOWS*1..3]-(b) RETURN count(DISTINCT b) AS c',
        {'src': 'p_933'})
    assert r['rows'][0][0] > 0

def test_vl_path_matches_bfs_results(iris_conn):
    engine = IRISGraphEngine(...)
    bfs = engine.execute_cypher(
        'CALL ivg.bfs($src, ["KNOWS"], 2) YIELD node RETURN node', {'src': 'p_933'})
    vl = engine.execute_cypher(
        'MATCH (a {node_id:$src})-[:KNOWS*1..2]-(b) RETURN DISTINCT b.node_id',
        {'src': 'p_933'})
    bfs_nodes = {r[0] for r in bfs['rows']}
    vl_nodes = {r[0] for r in vl['rows']}
    assert bfs_nodes == vl_nodes, "VL path results must match BFS results"
```

---

## Acceptance Criteria

- **SC-001**: `MATCH (a)-[:R*1..2]-(b) RETURN DISTINCT b.node_id LIMIT 50` completes in < 10ms
- **SC-002**: `MATCH (a)-[:R*1..3]-(b) RETURN count(b)` does not crash (SQLCODE -400 eliminated)
- **SC-003**: Results match `CALL ivg.bfs(...)` results exactly (correctness)
- **SC-004**: IC3-style query (2-hop + location filter) runs end-to-end without crash
- **SC-005**: No regression on existing single-hop `MATCH (a)-[:R]->(b)` queries

---

## Related Specs

- Spec 097 — Lazy NodeResolver (improves BFS output resolution performance)
- Spec 098 — ShortestPathNKG (IC13 already routed correctly)
- Spec 099 — LDBC Full Schema Loader (enables IC3/IC6/IC12 end-to-end measurement)
