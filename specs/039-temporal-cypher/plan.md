# Implementation Plan: Cypher Temporal Edge Filtering

**Branch**: `039-temporal-cypher` | **Date**: 2026-04-03 | **Spec**: [spec.md](spec.md)

---

## Summary

Add temporal awareness to the IVG Cypher translator so `WHERE r.ts >= $start AND r.ts <= $end` routes to `^KG("tout")` B-tree traversal instead of `rdf_edges` SQL scan. Translation strategy: detect temporal relationship variable at parse time, call `QueryWindow`/`QueryWindowInbound` via the engine, inject results as a `SELECT ... UNION ALL SELECT ...` CTE, then JOIN that CTE into the existing SQL query.

---

## Technical Context

**Language/Version**: Python 3.11 (translator), ObjectScript (TemporalIndex — already complete in v1.41.0)
**Primary Dependencies**: `iris_vector_graph.cypher.{ast,lexer,parser,translator}`, `iris_vector_graph.engine`
**Storage**: IRIS `^KG("tout"/"tin")` globals via `Graph.KG.TemporalIndex.QueryWindow/QueryWindowInbound`
**Testing**: pytest, `iris-devtester`, `iris-vector-graph-main` container (Constitution IV)
**Target Platform**: Python library, any IRIS 2024.1+
**Performance Goals**: Total wall-clock latency ≤2× `get_edges_in_window()`, warm cache, median 10 runs (SC-003)
**Constraints**: IRIS SQL does not support `VALUES` in CTEs — must use `SELECT ... UNION ALL SELECT ...`; truncate at 10,000 edges (FR-012)

---

## Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Library-First | ✅ | All changes in `iris_vector_graph/` |
| II. Compatibility-First | ✅ | Non-temporal MATCH unchanged (FR-006); `execute_cypher` signature unchanged |
| III. Test-First | ✅ | Tests written before implementation in each phase |
| IV. E2E Testing | ✅ | Live IRIS tests required (translator touches SQL layer); container: `iris-vector-graph-main` per conftest.py:161 |
| V. Simplicity | ✅ | Two functions added to translator; no new classes; no new ObjectScript |
| VI. Grounding | ✅ | Container name verified from `tests/conftest.py:161` (`iris-vector-graph-main`); IRIS SQL `VALUES` in CTE rejected — verified live; UNION ALL CTE confirmed working |

**Gate**: All green. Proceed to design.

---

## Phase 0: Research Findings

### Decision 1: CTE Injection Mechanism

**Decision**: `SELECT 's1','p1','o1',1000,42.0 UNION ALL SELECT ...` CTE
**Rationale**: IRIS SQL rejects `VALUES` inside a CTE (`WITH x AS (VALUES ...)`). `UNION ALL SELECT` literals are supported and produce identical results. Verified live on `iris-vector-graph-main`.
**Alternatives considered**: `VALUES` (rejected by IRIS), temp table (unnecessary complexity given 10K truncation), stored procedure table-valued function (requires DDL changes).

### Decision 2: Detection Point

**Decision**: Detect temporal routing at `translate_match_pattern()` time in `translator.py`
**Rationale**: The match pattern function already has access to the relationship variable name (`rel.variable`) and the WHERE clause is accessible via the `context`. Detection scans the WHERE expression tree for `PropertyReference(variable=rel.variable, property_name='ts')` with a comparison operator. This is a single traversal of the already-parsed AST — no re-parsing needed.
**Key AST facts (verified)**:
- `rel.variable` = the bound name (`r`, `rel`, `edge`, etc.)
- WHERE: `BooleanExpression(>=, PropertyReference(r, ts), Literal(1000))`
- RETURN: `PropertyReference(r, ts)` and `PropertyReference(r, weight)` detectable by variable match

### Decision 3: Result Injection Strategy

**Decision**: Pre-execute `QueryWindow`/`QueryWindowInbound` inside the translator, build `UNION ALL SELECT` CTE, register it as a CTE alias in `TranslationContext`, replace the `rdf_edges` JOIN with a JOIN on the CTE.
**Implementation**: `translate_to_sql` receives an optional `engine` parameter (or engine is threaded through context). When temporal routing is detected, call `engine.get_edges_in_window()` early, build the CTE string, proceed with SQL construction using the CTE alias instead of `rdf_edges`.
**Alternative approach**: Two-phase execution — translate to SQL with a sentinel, execute, detect sentinel, replace. Rejected: too complex, requires second parse pass.

### Decision 4: `engine` threading

**Decision**: Add optional `engine` parameter to `translate_to_sql(query, params, engine=None)`. When `None` (default), temporal routing raises `TemporalQueryRequiresEngine` — a clear error. `execute_cypher` already has `self` (engine) and passes it through. No API breakage.
**Rationale**: The translator is currently stateless and used directly in tests without an engine. This preserves that usage while enabling temporal routing through `execute_cypher`.

### Decision 5: `r.ts` in RETURN without WHERE filter

**Decision (from clarification Q1)**: Route to `rdf_edges` (no temporal routing); `r.ts` and `r.weight` return `NULL`; emit query-level warning.
**Implementation**: After translation, if `PropertyReference(r, ts)` appears in RETURN but no temporal filter was detected, append a warning to `QueryMetadata.warnings`. The `PropertyReference` resolution for `r.ts` falls through to the `rdf_props` JOIN path, which will return NULL (since `rdf_edges` rows have no `ts` property in `rdf_props`).

---

## Phase 1: Data Model

No new tables, globals, or schema changes. This feature is purely a translator change.

**New Python entities:**

```python
# In translator.py

@dataclass
class TemporalBound:
    """Extracted ts bounds from a WHERE clause temporal filter."""
    ts_start: Any          # int literal, $param name, or None
    ts_end: Any            # int literal, $param name, or None  
    rel_variable: str      # the relationship variable name (e.g. "r", "rel")
    predicate: Optional[str]  # rel type if specified, else None
    direction: str         # "out" or "in"

class TemporalQueryRequiresEngine(ValueError):
    """Raised when a temporal Cypher query is translated without an engine."""
    pass
```

**No changes to**:
- `ast.py` — existing AST nodes sufficient
- `TemporalIndex.cls` — `QueryWindow`/`QueryWindowInbound` already exist (v1.41.0)
- `rdf_edges`, `nodes`, or any SQL schema

---

## Phase 2: Contracts

### Python API contracts (no new public methods — changes are internal to translator)

`execute_cypher(cypher_query, parameters)` — **unchanged signature**.

Internal change: `translate_to_sql(query, params, engine=None)` gains optional `engine` parameter. Return type `SQLQuery` unchanged.

**Warning surface**: `SQLQuery.query_metadata` gains a `warnings: list[str]` field. `execute_cypher` logs warnings via `logger.warning()`.

**New exception**: `TemporalQueryRequiresEngine(ValueError)` — raised when temporal query translated without engine.

### Behavior contracts

| Input | Routing | Output |
|-------|---------|--------|
| `WHERE r.ts >= $s AND r.ts <= $e` | temporal → QueryWindow | edges from ^KG("tout") |
| `WHERE rel.ts >= $s AND rel.ts <= $e` | temporal → QueryWindow (any var name) | edges from ^KG("tout") |
| `(b)<-[r]-(a) WHERE r.ts >= $s` | temporal → QueryWindowInbound | edges from ^KG("tin") |
| `RETURN r.ts` without WHERE r.ts filter | rdf_edges + warning | r.ts = NULL |
| `MATCH (a)-[r]->(b)` no r.ts anywhere | rdf_edges (no change) | current behavior |
| Result > 10K edges | truncate to 10K + warning | 10K rows |
| No engine, temporal query | raise TemporalQueryRequiresEngine | — |

---

## Phase 3: Implementation Plan

### Step 1: `_extract_temporal_bounds(where_expr, rel_var)` in `translator.py`

Walk a WHERE expression tree, return `TemporalBound` if `<rel_var>.ts` appears with `>=`/`<=`/`>`/`<`/`=` operators, else `None`. Also extract `r.weight` post-filter conditions if present.

**Key logic**:
- Walk `BooleanExpression(AND, ...)` recursively
- Detect `BooleanExpression(>=, PropertyReference(rel_var, 'ts'), Literal/Parameter)`
- Detect `BooleanExpression(<=, PropertyReference(rel_var, 'ts'), Literal/Parameter)`
- Resolve parameter values from `context.input_params`
- Return `TemporalBound(ts_start, ts_end, rel_variable, predicate, direction)`

### Step 2: `_build_temporal_cte(edges, cte_name)` in `translator.py`

Given a list of edge dicts (`[{"s":..., "p":..., "o":..., "ts":..., "w":...}]`), build:
```sql
SELECT 'svc:a' AS s, 'CALLS_AT' AS p, 'svc:b' AS o, 1705000000 AS ts, 42.0 AS w
UNION ALL SELECT ...
```
If empty: `SELECT NULL AS s, NULL AS p, NULL AS o, NULL AS ts, NULL AS w WHERE 1=0`
Truncate to 10,000 rows; append warning to metadata if truncated.

### Step 3: Modify `translate_match_pattern()` temporal branch

After determining `edge_alias` and `rel.variable`, before the `rdf_edges` JOIN:
1. Call `_extract_temporal_bounds(context.where_clause, rel.variable)`
2. If `TemporalBound` found:
   a. Require `engine` (raise `TemporalQueryRequiresEngine` if None)
   b. Resolve `ts_start`/`ts_end` from params
   c. Call `engine.get_edges_in_window(source_filter, predicate_filter, ts_start, ts_end, direction)`
   d. Build `_build_temporal_cte(edges, cte_name)`  
   e. Add CTE to `context.cte_clauses`
   f. JOIN on `{cte_name}` instead of `{_table('rdf_edges')}`
   g. Remove the `r.ts`/`r.weight` bounds from `context.where_conditions` (handled via CTE)
3. Else: existing `rdf_edges` JOIN (no change)

### Step 4: Modify `translate_expression()` for `PropertyReference(rel_var, 'ts'/'weight')`

When translating `r.ts` or `r.weight` in a SELECT context:
- If `r` is registered as a temporal CTE alias: return `{cte_alias}.ts` or `{cte_alias}.w`
- Else: return NULL literal + add warning to metadata

### Step 5: Track temporal CTE aliases in `TranslationContext`

Add `self.temporal_rel_ctes: Dict[str, str] = {}` — maps rel variable name → CTE alias name.
`translate_expression` checks this dict when resolving `PropertyReference(rel_var, 'ts'/'weight'/'source'/'target'/'predicate')`.

### Step 6: `execute_cypher` passes `self` to `translate_to_sql`

```python
sql_query = translate_to_sql(parsed, parameters, engine=self)
```

---

## Phase 4: Test Plan

### Unit tests (no IRIS required) — `tests/unit/test_temporal_cypher.py`

| # | Test | Verifies |
|---|------|---------|
| U1 | `_extract_temporal_bounds` returns TemporalBound for `r.ts >= 1000 AND r.ts <= 2000` | Detection logic |
| U2 | `_extract_temporal_bounds` returns None for WHERE with no `r.ts` | No false positives |
| U3 | `_extract_temporal_bounds` works with any variable name (`rel.ts`, `edge.ts`) | FR-001, Q5 |
| U4 | `_build_temporal_cte([])` returns empty-result CTE | Edge case |
| U5 | `_build_temporal_cte(edges_10001)` truncates to 10K + sets warning | FR-012 |
| U6 | `translate_to_sql(temporal_query, {}, engine=None)` raises `TemporalQueryRequiresEngine` | Error path |
| U7 | `r.ts` in RETURN without WHERE filter → NULL + warning in metadata | FR-011, Q1 |
| U8 | Non-temporal `MATCH (a)-[r]->(b)` unchanged (no regression) | FR-006 |

### E2E tests (live IRIS) — `tests/unit/test_temporal_cypher.py::TestTemporalCypherE2E`

| # | Test | Verifies |
|---|------|---------|
| E1 | Temporal query returns correct edges from KGBENCH data | SC-001, US1 |
| E2 | `ORDER BY r.ts DESC` returns edges in correct order | US1 AC3, FR-004 |
| E3 | `r.weight > 1000` post-filter applied correctly | US3 AC1, FR-005 |
| E4 | Inbound direction `(b)<-[r]-(a)` routes to QueryWindowInbound | US4, FR-008 |
| E5 | Empty window returns 0 rows | FR-010 |
| E6 | Non-temporal MATCH returns same result as before (regression) | FR-006, SC-006 |
| E7 | Mixed pattern `(a)-[r1:CALLS_AT]->(b), (b)-[r2:STATIC]->(c)` works | Edge case, Q2 |
| E8 | Warning emitted when `r.ts` in RETURN but no WHERE filter | FR-011 |
| E9 | SC-003 benchmark: temporal Cypher ≤2× `get_edges_in_window()` latency | NFR-001, SC-003 |

---

## Phase 5: File Changeset

| File | Change |
|------|--------|
| `iris_vector_graph/cypher/translator.py` | Add `TemporalBound`, `TemporalQueryRequiresEngine`; add `_extract_temporal_bounds()`, `_build_temporal_cte()`; modify `translate_match_pattern()`, `translate_expression()`, `translate_to_sql()` signature; add `temporal_rel_ctes` to `TranslationContext` |
| `iris_vector_graph/engine.py` | Pass `engine=self` to `translate_to_sql()` in `execute_cypher()` |
| `tests/unit/test_temporal_cypher.py` | New file: 8 unit + 9 E2E tests |
| `iris_vector_graph/cypher/ast.py` | No changes |
| `iris_src/src/Graph/KG/TemporalIndex.cls` | No changes (QueryWindow/QueryWindowInbound already in v1.41.0) |
| `pyproject.toml` | Version bump → 1.42.0 |
| `README.md` | Add Cypher temporal filtering example to Cypher section |

---

## Phase 6: Version and Delivery

**Version**: `1.42.0`
**Checklist**:
- [ ] 8 unit tests written (TDD — fail first)
- [ ] 9 E2E tests written (TDD — fail first)
- [ ] `_extract_temporal_bounds` + `_build_temporal_cte` implemented
- [ ] `translate_match_pattern` modified
- [ ] `translate_expression` modified for r.ts/r.weight
- [ ] `execute_cypher` passes engine
- [ ] All 330+ existing tests still pass
- [ ] SC-003 benchmark measured and passes
- [ ] Version bumped, README updated, committed, published
