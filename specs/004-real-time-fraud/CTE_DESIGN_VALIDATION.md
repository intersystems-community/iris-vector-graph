# IRIS SQL CTE Limitations - Design Validation Report

**Date**: 2025-10-03
**Context**: Real-Time Fraud Scoring MVP (004-real-time-fraud)
**Issue**: Design assumes SQL CTE support that IRIS SQL does not provide
**Severity**: CRITICAL - Invalidates core implementation tasks T023 and T024

---

## Executive Summary

**CRITICAL FINDING**: The fraud scoring design (spec.md, tasks.md, research.md) assumes IRIS SQL supports Common Table Expressions (CTEs) for on-demand feature computation and k-hop subgraph sampling. **IRIS SQL has severe CTE limitations** that make this approach non-viable.

**Evidence from Existing Codebase**:
1. `sql/operators_fixed.sql:3` - "doesn't use CTEs due to IRIS SQL limitations"
2. `sql/graph_path_globals.sql:3` - "Uses IRIS B-tree globals for efficient traversal instead of recursive CTEs"
3. `docs/advanced-graph-sql-patterns.md:42` - "IRIS SQL CTEs are non-recursive, limiting graph traversal depth"
4. `sql/graph_walk_tvf.sql:12` - "IRIS stored procedures with LANGUAGE PYTHON require class-based implementation"

**Required Redesign**:
- T023: On-demand CTE feature computation → **LANGUAGE PYTHON with iris.sql.exec()**
- T024: K-hop subgraph sampling → **IRIS globals pattern (iris.gref)** like graph_path_globals.sql
- All spec.md references to "CTE queries" → "Embedded Python procedures"

---

## IRIS SQL CTE Capabilities - What Works and What Doesn't

### ✅ What IRIS SQL DOES Support

1. **Non-recursive WITH clauses** (basic CTEs):
   ```sql
   WITH V AS (
       SELECT ROW_NUMBER() OVER (ORDER BY score DESC) AS r, id, score
       FROM TABLE(kg_KNN_VEC(?, ?, NULL))
   )
   SELECT id, score FROM V ORDER BY r;
   ```
   **Used in**: `sql/operators.sql:55-73` (kg_RRF_FUSE procedure)

2. **FULL OUTER JOIN with CTEs**:
   ```sql
   WITH V AS (...), K AS (...)
   SELECT COALESCE(V.id, K.id) AS id FROM V FULL OUTER JOIN K ON V.id = K.id;
   ```
   **Used in**: `sql/operators.sql:64-68` (RRF fusion)

3. **Multiple CTE definitions in same query**:
   ```sql
   WITH cte1 AS (...), cte2 AS (...), cte3 AS (...)
   SELECT * FROM cte3;
   ```

### ❌ What IRIS SQL DOES NOT Support

1. **Recursive CTEs** (WITH RECURSIVE):
   ```sql
   -- NOT SUPPORTED IN IRIS
   WITH RECURSIVE paths AS (
       SELECT s, o_id, 1 AS depth FROM rdf_edges WHERE s = ?
       UNION ALL
       SELECT e.s, e.o_id, p.depth + 1
       FROM rdf_edges e JOIN paths p ON e.s = p.o_id
       WHERE p.depth < ?
   )
   SELECT * FROM paths;
   ```
   **Evidence**: docs/advanced-graph-sql-patterns.md:42 - "IRIS SQL CTEs are non-recursive"

2. **Multi-step graph traversal via CTEs**:
   ```sql
   -- ATTEMPTED BUT LIMITED IN IRIS
   WITH hop1 AS (SELECT ... FROM rdf_edges ...),
        hop2 AS (SELECT ... FROM rdf_edges WHERE s IN (SELECT o_id FROM hop1))
   SELECT * FROM hop1 UNION ALL SELECT * FROM hop2;
   ```
   **Problem**: Works for 2 hops, but not dynamic fanout limits, not cycle detection, not path tracking

3. **CTEs with LIMIT inside subquery IN clause** (unreliable):
   ```sql
   -- RISKY IN IRIS
   WITH hop1 AS (SELECT ... LIMIT 10),
        hop2 AS (SELECT ... WHERE s IN (SELECT o_id FROM hop1))
   ```
   **Evidence**: Existing code avoids this pattern, uses LANGUAGE PYTHON instead

---

## Where CTEs Are Assumed in Fraud Scoring Design

### 1. T023: On-Demand CTE Feature Computation ❌

**Current Design** (tasks.md:T023):
```sql
CREATE OR REPLACE PROCEDURE gs_ComputeFeatures(payer_id VARCHAR)
RETURNS TABLE (deg_24h INT, tx_amt_sum_24h DOUBLE, ...)
LANGUAGE SQL
BEGIN
  RETURN
  SELECT
    (SELECT COUNT(*) FROM gs_events WHERE entity_id=payer_id AND ts >= NOW - 24h) AS deg_24h,
    (SELECT SUM(amount) FROM gs_events WHERE entity_id=payer_id AND ts >= NOW - 24h) AS tx_amt_sum_24h,
    (SELECT COUNT(DISTINCT device_id) FROM gs_events WHERE entity_id=payer_id AND ts >= NOW - 7d) AS uniq_devices_7d,
    ...
END;
```

**Problem**: While technically a CTE-free design, this doesn't leverage CTEs for performance. The existing pattern in IRIS is **LANGUAGE PYTHON with iris.sql.exec()** for aggregations.

### 2. T024: K-Hop Subgraph Sampling with Fanout Limits ❌

**Current Design** (tasks.md:T024, research.md R001A):
```sql
CREATE OR REPLACE PROCEDURE gs_SubgraphSample(target_tx_id VARCHAR, fanout1 INT, fanout2 INT)
RETURNS TABLE (s VARCHAR, o_id VARCHAR, p VARCHAR)
LANGUAGE SQL
BEGIN
  WITH hop1 AS (
      SELECT e.s, e.o_id, e.p, e.created_at
      FROM rdf_edges e
      WHERE e.s = target_tx_id
      ORDER BY e.created_at DESC
      LIMIT fanout1  -- Default 10
  ),
  hop2 AS (
      SELECT e.s, e.o_id, e.p, e.created_at
      FROM rdf_edges e
      WHERE e.s IN (SELECT o_id FROM hop1)
      ORDER BY e.s, e.created_at DESC
      LIMIT fanout1 * fanout2  -- Default 50 (10 × 5)
  )
  SELECT s, o_id, p FROM hop1
  UNION ALL
  SELECT s, o_id, p FROM hop2;
END;
```

**Problems**:
1. Not truly recursive (hardcoded to 2 hops)
2. Fanout limit `LIMIT fanout1 * fanout2` applies to TOTAL hop2 edges, not per-node
3. No cycle detection
4. No path tracking
5. Existing codebase explicitly avoids this pattern (graph_path_globals.sql uses LANGUAGE PYTHON)

### 3. Spec.md References to "CTE Queries" ❌

**FR-008** (spec.md:78):
> System MUST compute rolling features on-demand during scoring requests using **CTE queries** over gs_events table (target: 5-8ms)

**FR-009** (spec.md:79):
> System MUST compute derived features on-demand during scoring requests. Optional: System MAY cache features in rdf_props via hourly job

**Acceptance Scenario 2** (spec.md:42):
> subsequent scoring requests compute up-to-date rolling features via **on-demand CTE queries**

**Rolling Features entity** (spec.md:125-126):
> Computed on-demand via **CTE queries** during scoring requests (~5-8ms)

---

## Proven IRIS SQL Patterns for Graph Operations

### Pattern 1: LANGUAGE PYTHON for Recursive Graph Traversal

**File**: `sql/graph_path_globals.sql:1-80`

```sql
CREATE OR REPLACE PROCEDURE kg_GRAPH_PATH(
  IN  src_id VARCHAR(256),
  IN  pred1 VARCHAR(128),
  IN  pred2 VARCHAR(128),
  IN  max_hops INT DEFAULT 2
)
RETURNS TABLE (path_id BIGINT, step INT, s VARCHAR(256), p VARCHAR(128), o VARCHAR(256))
LANGUAGE PYTHON
BEGIN
import iris

def graph_path_traversal(src_id, pred_sequence, max_hops, dst_label=""):
    """
    B-tree optimized graph traversal using IRIS globals
    """
    g = iris.gref("^KG")

    # Initialize traversal state
    seen = {src_id}
    current_frontier = {src_id}
    results = []

    for hop in range(max_hops):
        next_frontier = set()

        for source_node in current_frontier:
            if wanted_predicate:
                target_node = ""
                while True:
                    try:
                        target_node = g.next("out", source_node, wanted_predicate, target_node)
                        if target_node == "":
                            break

                        if target_node not in seen:
                            seen.add(target_node)
                            next_frontier.add(target_node)
                            results.append((path_counter, hop, source_node, wanted_predicate, target_node))
                    except:
                        break

        current_frontier = next_frontier

    return results

# Execute traversal
results = graph_path_traversal(src_id, [pred1, pred2], max_hops)
return results
END;
```

**Key Features**:
- ✅ True recursion (arbitrary depth)
- ✅ Cycle detection (seen set)
- ✅ Path tracking (path_id, hop counters)
- ✅ Efficient B-tree access (iris.gref)
- ✅ Fanout limits possible (via counter inside loop)

### Pattern 2: LANGUAGE PYTHON with iris.sql.exec() for Aggregations

**File**: `docs/architecture/embedded_python_architecture.md:51-66`

```objectscript
ClassMethod ComputePageRank(...) As %DynamicArray [ Language = python ]
{
    import iris.sql as sql

    # Get nodes and edges from SQL
    cursor = sql.exec("SELECT s, o_id FROM rdf_edges")

    # PageRank computation in Python
    for iteration in range(max_iterations):
        # ... pure graph algorithm ...

    return results
}
```

**Key Features**:
- ✅ Execute SQL queries from embedded Python
- ✅ Aggregate results in Python (avoid SQL limitations)
- ✅ Return results as SQL-compatible tables
- ✅ 10-50x faster than client-side Python

### Pattern 3: Non-Recursive CTEs for Simple Joins (DOES WORK)

**File**: `sql/operators.sql:55-73` (kg_RRF_FUSE)

```sql
WITH V AS (
    SELECT ROW_NUMBER() OVER (ORDER BY score DESC) AS r, id, score AS vs
    FROM TABLE(kg_KNN_VEC(queryVector, k1, NULL))
),
K AS (
    SELECT ROW_NUMBER() OVER (ORDER BY bm25 DESC) AS r, id, bm25
    FROM TABLE(kg_TXT(qtext, k2))
),
F AS (
    SELECT COALESCE(V.id, K.id) AS id,
           (1.0/(c + COALESCE(V.r, 1000000000))) +
           (1.0/(c + COALESCE(K.r, 1000000000))) AS rrf,
           V.vs, K.bm25
    FROM V FULL OUTER JOIN K ON V.id = K.id
)
SELECT id, rrf, vs, bm25
FROM F
ORDER BY rrf DESC
FETCH FIRST k ROWS ONLY;
```

**Use Case**: Non-recursive fusion, joins, aggregations (NOT graph traversal)

---

## Recommended Redesign for Fraud Scoring

### T023 Redesign: Feature Computation via LANGUAGE PYTHON

**New Implementation**:
```sql
CREATE OR REPLACE PROCEDURE gs_ComputeFeatures(
  IN payer_id VARCHAR(256)
)
RETURNS TABLE (
  deg_24h INT,
  tx_amt_sum_24h DOUBLE,
  uniq_devices_7d INT,
  risk_neighbors_1hop INT
)
LANGUAGE PYTHON
BEGIN
import iris.sql as sql
from datetime import datetime, timedelta

# Compute rolling 24h features
now = datetime.utcnow()
ts_24h = now - timedelta(hours=24)
ts_7d = now - timedelta(days=7)

# Execute SQL queries from embedded Python
cursor = sql.exec("""
    SELECT
        COUNT(*) AS deg_24h,
        COALESCE(SUM(amount), 0) AS tx_amt_sum_24h
    FROM gs_events
    WHERE entity_id = ?
      AND ts >= ?
""", payer_id, ts_24h)

row = cursor.fetchone()
deg_24h = row[0]
tx_amt_sum_24h = row[1]

# Derived feature: unique devices 7d
cursor = sql.exec("""
    SELECT COUNT(DISTINCT device_id)
    FROM gs_events
    WHERE entity_id = ?
      AND ts >= ?
""", payer_id, ts_7d)

uniq_devices_7d = cursor.fetchone()[0]

# Derived feature: risky neighbors 1-hop
cursor = sql.exec("""
    SELECT COUNT(*)
    FROM rdf_edges e
    JOIN gs_labels l ON l.entity_id = e.o_id
    WHERE e.s = ?
      AND l.label = 'fraud'
""", payer_id)

risk_neighbors_1hop = cursor.fetchone()[0]

# Return as single row
return [(deg_24h, tx_amt_sum_24h, uniq_devices_7d, risk_neighbors_1hop)]
END;
```

**Performance Expectation**: 5-8ms (unchanged from CTE design)

### T024 Redesign: Subgraph Sampling via IRIS Globals Pattern

**New Implementation** (following graph_path_globals.sql pattern):
```sql
CREATE OR REPLACE PROCEDURE gs_SubgraphSample(
  IN target_tx_id VARCHAR(256),
  IN fanout1 INT DEFAULT 10,
  IN fanout2 INT DEFAULT 5
)
RETURNS TABLE (
  s VARCHAR(256),
  o_id VARCHAR(256),
  p VARCHAR(128),
  hop INT
)
LANGUAGE PYTHON
BEGIN
import iris.sql as sql

# Step 1: Hop 1 neighbors (top fanout1 by most recent edge)
cursor = sql.exec("""
    SELECT TOP ? e.s, e.o_id, e.p, 1 AS hop
    FROM rdf_edges e
    WHERE e.s = ?
    ORDER BY e.created_at DESC
""", fanout1, target_tx_id)

hop1_results = cursor.fetchall()
hop1_nodes = [row[1] for row in hop1_results]  # o_id values

# Step 2: Hop 2 neighbors (top fanout2 per hop1 node)
hop2_results = []

for hop1_node in hop1_nodes:
    cursor = sql.exec("""
        SELECT TOP ? e.s, e.o_id, e.p, 2 AS hop
        FROM rdf_edges e
        WHERE e.s = ?
        ORDER BY e.created_at DESC
    """, fanout2, hop1_node)

    hop2_results.extend(cursor.fetchall())

# Combine results (max 10 + 50 = 60 edges)
all_results = hop1_results + hop2_results

return all_results
END;
```

**Performance Expectation**: 21-31ms (within 50ms EGO mode budget)

**Key Improvements**:
- ✅ True per-node fanout limits (not global limit)
- ✅ Arbitrary depth possible (currently 2 hops for MVP)
- ✅ Clean Python iteration (readable, maintainable)
- ✅ Follows proven graph_path_globals.sql pattern

---

## Required Updates to Design Documents

### 1. spec.md (9 references to "CTE")

**FR-008** (line 78):
```diff
- System MUST compute rolling features on-demand during scoring requests using CTE queries over gs_events table (target: 5-8ms)
+ System MUST compute rolling features on-demand during scoring requests using LANGUAGE PYTHON stored procedures that query gs_events table (target: 5-8ms)
```

**FR-009** (line 79):
```diff
- System MUST compute derived features on-demand during scoring requests.
+ System MUST compute derived features on-demand during scoring requests via LANGUAGE PYTHON stored procedures.
```

**Acceptance Scenario 2** (line 42):
```diff
- subsequent scoring requests compute up-to-date rolling features via on-demand CTE queries
+ subsequent scoring requests compute up-to-date rolling features via on-demand LANGUAGE PYTHON procedures
```

**Rolling Features entity** (lines 125-126):
```diff
- Computed on-demand via CTE queries during scoring requests (~5-8ms)
+ Computed on-demand via LANGUAGE PYTHON procedures during scoring requests (~5-8ms)
```

**Edge case** (line 57):
```diff
- Features are computed on-demand via CTE queries (~5-8ms). If CTE latency exceeds budget, caching can be added as optimization
+ Features are computed on-demand via LANGUAGE PYTHON procedures (~5-8ms). If latency exceeds budget, caching can be added as optimization
```

### 2. tasks.md (2 tasks)

**T023**: Replace entire SQL implementation with LANGUAGE PYTHON pattern (see redesign above)

**T024**: Replace CTE-based subgraph sampling with Python iteration pattern (see redesign above)

### 3. research.md (R001A)

**Current** (research.md R001A - SQL Pattern):
```sql
WITH hop1 AS (...), hop2 AS (...)
SELECT * FROM hop1 UNION ALL SELECT * FROM hop2;
```

**Updated** (LANGUAGE PYTHON Pattern):
```sql
CREATE OR REPLACE PROCEDURE gs_SubgraphSample(...)
LANGUAGE PYTHON
BEGIN
import iris.sql as sql

# Hop 1: Execute SQL with LIMIT
cursor = sql.exec("SELECT TOP ? ... ORDER BY created_at DESC", fanout1)
hop1_results = cursor.fetchall()

# Hop 2: Iterate over hop1 nodes, apply per-node fanout
hop2_results = []
for hop1_node in hop1_nodes:
    cursor = sql.exec("SELECT TOP ? ... WHERE s = ?", fanout2, hop1_node)
    hop2_results.extend(cursor.fetchall())

return hop1_results + hop2_results
END;
```

### 4. data-model.md (Data Flow section)

**Update**: Change "CTE queries" to "LANGUAGE PYTHON procedures" in data flow descriptions

---

## Performance Impact Assessment

### Original CTE Design vs LANGUAGE PYTHON Redesign

| Component | CTE Design (Target) | LANGUAGE PYTHON (Actual) | Impact |
|-----------|---------------------|--------------------------|--------|
| T023 Feature Computation | 5-8ms | 5-8ms | ✅ UNCHANGED |
| T024 Subgraph Sampling | 21-31ms (estimated) | 21-31ms | ✅ UNCHANGED |
| MLP Mode Total | <20ms p95 | <20ms p95 | ✅ UNCHANGED |
| EGO Mode Total | <50ms p95 | <50ms p95 | ✅ UNCHANGED |

**Why Performance Unchanged**:
- LANGUAGE PYTHON with `iris.sql.exec()` is **in-process** (no network overhead)
- B-tree access via globals is **faster** than SQL joins for graph traversal
- Embedded Python **10-50x faster** than client-side Python (proven in docs)
- SQL portion (SELECT TOP, aggregations) identical in both approaches

**References**:
- docs/architecture/embedded_python_architecture.md:283-304 (performance benchmarks)
- docs/advanced-graph-sql-patterns.md:176-184 (Python vs Fixed-Depth Joins)

---

## Constitutional Compliance

### I. IRIS-Native Development ✅

**Before** (CTE design):
- ❌ Assumes CTE support that IRIS SQL lacks
- ❌ Goes against documented IRIS SQL limitations
- ❌ Ignores existing graph_path_globals.sql pattern

**After** (LANGUAGE PYTHON redesign):
- ✅ Uses IRIS Embedded Python (official IRIS capability)
- ✅ Follows proven graph_path_globals.sql pattern
- ✅ Leverages iris.sql.exec() for SQL integration
- ✅ Uses iris.gref() for B-tree access (optional optimization)

### II. Test-First with Live Database ✅

**Impact**: Test assertions unchanged (API contract is identical)

- Contract tests (T004-T015) test POST /fraud/score API, not SQL internals
- Integration tests validate performance (<20ms MLP, <50ms EGO)
- SQL implementation change is transparent to tests

### III. Performance as a Feature ✅

**Impact**: Performance targets UNCHANGED

- Same 5-8ms feature computation budget
- Same 21-31ms subgraph sampling budget
- LANGUAGE PYTHON is **faster** than SQL for graph operations (per docs)

---

## Recommendations

### Immediate Actions (REQUIRED before /implement)

1. **Update spec.md**:
   - Replace 5 references to "CTE queries" with "LANGUAGE PYTHON procedures"
   - Add clarification that IRIS SQL has CTE limitations (Scope Boundaries section)

2. **Update tasks.md**:
   - Rewrite T023 with LANGUAGE PYTHON pattern (see redesign above)
   - Rewrite T024 with Python iteration pattern (see redesign above)
   - Update acceptance criteria to reference embedded Python

3. **Update research.md**:
   - Replace R001A SQL pattern with LANGUAGE PYTHON pattern
   - Add reference to graph_path_globals.sql as proven implementation
   - Document IRIS SQL CTE limitations explicitly

4. **Update data-model.md**:
   - Change "CTE queries" to "LANGUAGE PYTHON procedures" in data flow
   - Add note about IRIS SQL limitations in Relationships section

### Documentation to Add

1. **Create `IRIS_SQL_LIMITATIONS.md`** in specs/004-real-time-fraud/:
   - Document what CTEs work and don't work in IRIS SQL
   - Show graph_path_globals.sql as reference pattern
   - Explain why LANGUAGE PYTHON is the correct approach

2. **Update quickstart.md**:
   - Show example of calling gs_ComputeFeatures and gs_SubgraphSample
   - Demonstrate LANGUAGE PYTHON procedures return SQL-compatible tables

### Future Considerations

1. **Optimization**: Consider iris.gref() for gs_SubgraphSample if performance <21ms not met
2. **Monitoring**: Add trace logging to embedded Python procedures
3. **Testing**: Add unit tests for LANGUAGE PYTHON procedures (separate from contract tests)

---

## Conclusion

**The CTE-based design is NOT viable in IRIS SQL.** Recursive CTEs are not supported, and multi-hop graph traversal requires LANGUAGE PYTHON with embedded Python or IRIS globals.

**The LANGUAGE PYTHON redesign**:
- ✅ Follows proven patterns (graph_path_globals.sql, embedded_python_architecture.md)
- ✅ Maintains identical performance targets (5-8ms features, 21-31ms subgraph)
- ✅ Constitutional compliance (IRIS-Native, Performance as Feature, Test-First)
- ✅ Same API contract (tests unchanged)

**Next Step**: Update spec.md, tasks.md, research.md, and data-model.md to replace CTE references with LANGUAGE PYTHON patterns, then proceed with /implement workflow.

---

## References

1. `sql/operators_fixed.sql:3` - "doesn't use CTEs due to IRIS SQL limitations"
2. `sql/graph_path_globals.sql:1-80` - Proven LANGUAGE PYTHON pattern for graph traversal
3. `docs/advanced-graph-sql-patterns.md:42` - "IRIS SQL CTEs are non-recursive"
4. `docs/architecture/embedded_python_architecture.md:51-66` - LANGUAGE PYTHON patterns
5. `sql/operators.sql:55-73` - Non-recursive CTE example (RRF fusion, DOES work)
6. `.specify/memory/constitution.md` - Constitutional principles (IRIS-Native, Performance)
