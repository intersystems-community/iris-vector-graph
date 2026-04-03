# Tasks: Cypher Temporal Edge Filtering

**Input**: Design documents from `/specs/039-temporal-cypher/`
**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, quickstart.md ✅

**Organization**: Tasks grouped by user story. Tests written first per Constitution III (TDD non-negotiable).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1–US4 from spec.md

---

## Phase 1: Setup

**Purpose**: New file scaffolding and translator signature change — no logic yet.

- [ ] T001 Add `TemporalBound` dataclass and `TemporalQueryRequiresEngine` exception to `iris_vector_graph/cypher/translator.py` (after existing dataclass/exception definitions)
- [ ] T002 Add `temporal_rel_ctes: Dict[str, str]` field to `TranslationContext.__init__` in `iris_vector_graph/cypher/translator.py` — initialised to `{}`; copy from parent like other dict fields
- [ ] T003 Add optional `engine=None` parameter to `translate_to_sql(query, params, engine=None)` signature in `iris_vector_graph/cypher/translator.py` — default None, backward compatible
- [ ] T004 Pass `engine=self` in `execute_cypher` call to `translate_to_sql` in `iris_vector_graph/engine.py`
- [ ] T005 Create empty test file `tests/unit/test_temporal_cypher.py` with SKIP_IRIS_TESTS guard, imports, and two empty test classes: `TestTemporalCypherUnit` and `TestTemporalCypherE2E`

**Checkpoint**: `pytest tests/unit/ -q` still passes (no logic changed yet)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: `_extract_temporal_bounds` and `_build_temporal_cte` — the two core helpers that all user stories depend on. Tests written first.

⚠️ CRITICAL: No user story work can begin until this phase is complete.

- [ ] T006 [US1] Write unit test `test_extract_bounds_returns_bound_for_range_filter` in `tests/unit/test_temporal_cypher.py`: parse `WHERE r.ts >= 1000 AND r.ts <= 2000`, call `_extract_temporal_bounds(where_expr, 'r')`, assert returns `TemporalBound(ts_start=1000, ts_end=2000, rel_variable='r', ...)` — must FAIL before T010
- [ ] T007 [US1] Write unit test `test_extract_bounds_returns_none_without_ts_filter` in `tests/unit/test_temporal_cypher.py`: parse `WHERE a.id = 'x'`, assert `_extract_temporal_bounds` returns `None` — must FAIL before T010
- [ ] T008 [US1] Write unit test `test_extract_bounds_any_variable_name` in `tests/unit/test_temporal_cypher.py`: parse `WHERE rel.ts >= 100 AND rel.ts <= 200`, call `_extract_temporal_bounds(where_expr, 'rel')`, assert returns `TemporalBound(rel_variable='rel', ...)` — must FAIL before T010
- [ ] T009 [US1] Write unit test `test_build_temporal_cte_empty` in `tests/unit/test_temporal_cypher.py`: call `_build_temporal_cte([], 'te0')`, assert returns SQL with `WHERE 1=0` — must FAIL before T011
- [ ] T010 Implement `_extract_temporal_bounds(where_expr, rel_var: str) -> Optional[TemporalBound]` in `iris_vector_graph/cypher/translator.py`: walk `BooleanExpression(AND, ...)` recursively; detect `PropertyReference(variable=rel_var, property_name='ts')` with `>=`/`<=`/`>`/`<`/`=` operators; extract `ts_start`/`ts_end` from `Literal.value` or `context.input_params[$param]`; return `TemporalBound` or `None`
- [ ] T011 Implement `_build_temporal_cte(edges: list, cte_name: str, metadata) -> str` in `iris_vector_graph/cypher/translator.py`: build `SELECT 's' AS s, 'p' AS p, 'o' AS o, ts AS ts, w AS w UNION ALL SELECT ...` string; truncate to 10,000 and append warning to metadata if exceeded; empty list → `SELECT NULL AS s, NULL AS p, NULL AS o, NULL AS ts, NULL AS w WHERE 1=0`
- [ ] T012 [P] Run `pytest tests/unit/test_temporal_cypher.py -v -k "extract_bounds or build_cte"` — T006–T009 must all pass after T010–T011

---

## Phase 3: User Story 1 — Temporal Window Filter (P1)

**Goal**: `WHERE r.ts >= $start AND r.ts <= $end` routes to `^KG("tout")`, returns `r.ts` and `r.weight`

**Independent test**: Insert known temporal edges; execute temporal Cypher query; verify only edges in window returned with correct values.

- [ ] T013 [US1] Write unit test `test_translate_temporal_raises_without_engine` in `tests/unit/test_temporal_cypher.py`: call `translate_to_sql(temporal_query_tree, {}, engine=None)`, assert raises `TemporalQueryRequiresEngine` — must FAIL before T016
- [ ] T014 [US1] Write unit test `test_translate_temporal_builds_union_all_cte` in `tests/unit/test_temporal_cypher.py`: mock `engine.get_edges_in_window` to return 2 edges; call `translate_to_sql(temporal_tree, {}, engine=mock_engine)`; assert generated SQL contains `WITH` and `UNION ALL` — must FAIL before T016
- [ ] T015 [US1] Write unit test `test_nontemporal_match_unchanged` in `tests/unit/test_temporal_cypher.py`: translate `MATCH (a)-[r]->(b) RETURN a.id`; assert SQL contains `rdf_edges` and does NOT contain `UNION ALL` (regression guard) — must FAIL if T016 introduces regression
- [ ] T016 [US1] Modify `translate_match_pattern()` in `iris_vector_graph/cypher/translator.py`: after computing `edge_alias` and `rel.variable`, call `_extract_temporal_bounds(context.pending_where, rel.variable)` (use a context field to thread the WHERE clause); if `TemporalBound` found: raise `TemporalQueryRequiresEngine` if `engine` is None; call `engine.get_edges_in_window(source_filter, predicate_filter, ts_start, ts_end, direction)`; call `_build_temporal_cte(edges, cte_name, metadata)`; register CTE in `context.cte_clauses`; JOIN on CTE alias instead of `rdf_edges`; register `rel.variable → cte_alias` in `context.temporal_rel_ctes`; remove r.ts bounds from `context.where_conditions`
- [ ] T017 [US1] Thread WHERE clause access through `TranslationContext`: add `context.pending_where = None`; set it in `translate_to_sql` before calling `translate_match_pattern`; used by `_extract_temporal_bounds` calls inside `translate_match_pattern`
- [ ] T018 [US1] Modify `translate_expression()` in `iris_vector_graph/cypher/translator.py`: when resolving `PropertyReference(variable, 'ts')` or `PropertyReference(variable, 'weight')`, check `context.temporal_rel_ctes.get(variable)`; if found → return `{cte_alias}.ts` or `{cte_alias}.w`; if not found and `ts`/`weight` → return SQL `NULL` and append warning to metadata
- [ ] T019 [US1] Run unit tests: `pytest tests/unit/test_temporal_cypher.py -v -k "US1 or translate_temporal or nontemporal"` — T013–T015 must all pass
- [ ] T020 [P] [US1] Write E2E test `test_temporal_window_returns_correct_edges` in `tests/unit/test_temporal_cypher.py::TestTemporalCypherE2E`: insert 2 edges at T1 and T2; query with `$start=T1-1, $end=T1+1`; assert 1 row with correct r.ts and r.weight (US1 AC1–AC4) — must FAIL before T021 (needs live IRIS with updated CLS)
- [ ] T021 [P] [US1] Write E2E test `test_empty_window_returns_zero_rows` in `tests/unit/test_temporal_cypher.py::TestTemporalCypherE2E`: query with window containing no edges; assert `len(rows) == 0` (US1 AC5, FR-010)
- [ ] T022 [P] [US1] Write E2E test `test_parameter_binding_works` in `tests/unit/test_temporal_cypher.py::TestTemporalCypherE2E`: query using `$start` and `$end` as parameters (not literals); assert correct results (US1 AC6, FR-007)
- [ ] T023 [US1] Compile `TemporalIndex.cls` into `iris-vector-graph-main` container (already at v1.41.0 — verify with `Do $system.OBJ.Load(...)`) and run E2E tests: `pytest tests/unit/test_temporal_cypher.py -v -k "E2E and (window or empty or parameter)"` — T020–T022 must pass
- [ ] T024 [US1] Run full unit suite regression check: `pytest tests/unit/ -q --timeout=30` — all 322+ tests must pass

---

## Phase 4: User Story 2 — r.ts in RETURN Without Range Filter (P2)

**Goal**: `RETURN r.ts` without WHERE filter → NULL + warning; no regression on existing queries

**Independent test**: Execute `MATCH (a)-[r:CALLS_AT]->(b) RETURN r.ts` (no WHERE); verify r.ts column is NULL; verify warning in metadata.

- [ ] T025 [US2] Write unit test `test_rts_return_without_where_gives_null_and_warning` in `tests/unit/test_temporal_cypher.py`: translate `MATCH (a)-[r:CALLS_AT]->(b) RETURN a.id, r.ts` with mock engine; assert SQL contains `NULL AS r_ts`; assert metadata contains warning string — must FAIL before T026
- [ ] T026 [US2] Ensure `translate_expression()` already handles `PropertyReference(r, 'ts')` when `r` not in `temporal_rel_ctes` → SQL `NULL` (T018 should cover this; verify and add a NULL alias so the column appears in result set with name `r_ts`)
- [ ] T027 [P] [US2] Write E2E test `test_rts_return_without_filter_gives_null` in `tests/unit/test_temporal_cypher.py::TestTemporalCypherE2E`: execute `MATCH (a)-[r:CALLS_AT]->(b) RETURN a.id, r.ts LIMIT 5`; assert all r.ts values are None; assert result["metadata"] warnings non-empty (FR-011)
- [ ] T028 [US2] Run phase tests: `pytest tests/unit/test_temporal_cypher.py -v -k "without_where or without_filter"` — T025 and T027 must pass

---

## Phase 5: User Story 3 — Temporal + Property Filter (P2)

**Goal**: `WHERE r.ts >= $s AND r.ts <= $e AND r.weight > 1000` applies weight as post-filter on temporal results

**Independent test**: Insert edges with mixed weights in window; query with r.weight > threshold; verify only high-weight edges returned.

- [ ] T029 [US3] Write unit test `test_extract_bounds_with_weight_postfilter` in `tests/unit/test_temporal_cypher.py`: parse `WHERE r.ts >= 100 AND r.ts <= 200 AND r.weight > 50`; assert `_extract_temporal_bounds` returns `TemporalBound` (ts bounds only); assert weight condition NOT consumed (remains in WHERE) — must FAIL before T031
- [ ] T030 [P] [US3] Write E2E test `test_weight_postfilter_applied` in `tests/unit/test_temporal_cypher.py::TestTemporalCypherE2E`: insert 3 edges in window with weights 50, 200, 1500; query with `r.weight > 100`; assert 2 rows returned (weights 200 and 1500) (US3 AC1, FR-005)
- [ ] T031 [US3] Ensure `r.weight` post-filter in WHERE is preserved in `context.where_conditions` after temporal routing — `translate_match_pattern` removes only the `r.ts >= ...` and `r.ts <= ...` conditions; `r.weight > expr` maps to `{cte_alias}.w > ?` via `translate_expression` (covered by T018); add explicit test for this path
- [ ] T032 [P] [US3] Write E2E test `test_order_by_weight_desc` in `tests/unit/test_temporal_cypher.py::TestTemporalCypherE2E`: insert 3 edges with different weights in window; query `ORDER BY r.weight DESC`; assert rows in descending weight order (US3 AC3, FR-004)
- [ ] T033 [US3] Run phase tests: `pytest tests/unit/test_temporal_cypher.py -v -k "weight or order_by"` — T029, T030, T032 must pass

---

## Phase 6: User Story 4 — Inbound Direction (P3)

**Goal**: `(b)<-[r:CALLS_AT]-(a) WHERE r.ts >= $s AND r.ts <= $e` routes to `QueryWindowInbound`

**Independent test**: Insert edges A→B; query using inbound direction on B; verify same edges returned as outbound query on A.

- [ ] T034 [US4] Write unit test `test_extract_bounds_detects_inbound_direction` in `tests/unit/test_temporal_cypher.py`: parse `MATCH (b)<-[r:CALLS_AT]-(a) WHERE r.ts >= 100 AND r.ts <= 200`; assert `_extract_temporal_bounds` returns `TemporalBound(direction='in', ...)` — must FAIL before T036
- [ ] T035 [P] [US4] Write E2E test `test_inbound_direction_routes_to_querywindowinbound` in `tests/unit/test_temporal_cypher.py::TestTemporalCypherE2E`: insert edge A→B; query `(b)<-[r]-(a) WHERE r.ts >= $s AND r.ts <= $e RETURN a.id, b.id`; assert returns the same edge (US4, FR-008)
- [ ] T036 [US4] Modify `_extract_temporal_bounds` call site in `translate_match_pattern()`: pass `rel.direction` to `TemporalBound`; when `direction=INCOMING`, call `engine.get_edges_in_window(..., direction='in')` instead of `direction='out'` (v1.41.0 API)
- [ ] T037 [US4] Run phase tests: `pytest tests/unit/test_temporal_cypher.py -v -k "inbound"` — T034 and T035 must pass

---

## Phase 7: Polish & Cross-Cutting

**Purpose**: 10K truncation, ORDER BY r.ts, mixed patterns, SC-003 benchmark, version bump

- [ ] T038 [P] Write unit test `test_build_cte_truncates_at_10k` in `tests/unit/test_temporal_cypher.py`: call `_build_temporal_cte` with 10,001 fake edges; assert CTE contains exactly 10,000 `SELECT` fragments; assert metadata warning message contains "truncated" — must FAIL before T039 confirms T011 already handles this
- [ ] T039 Verify T011 (`_build_temporal_cte`) already truncates at 10,000 — run T038; if fails, fix `_build_temporal_cte` (FR-012)
- [ ] T040 [P] Write E2E test `test_order_by_ts_desc` in `tests/unit/test_temporal_cypher.py::TestTemporalCypherE2E`: insert 3 edges at T1 < T2 < T3; query `ORDER BY r.ts DESC`; assert rows ordered T3, T2, T1 (US1 AC3, FR-004)
- [ ] T041 [P] Write E2E test `test_mixed_temporal_and_static_match` in `tests/unit/test_temporal_cypher.py::TestTemporalCypherE2E`: create nodes with static edge (rdf_edges) and temporal edge (tout); query `MATCH (a)-[r1:CALLS_AT]->(b), (a)-[r2:RELATED]->(c) WHERE r1.ts >= $s AND r1.ts <= $e RETURN a.id, b.id, c.id`; assert returns correct join (Edge case, Q2)
- [ ] T042 [P] Write E2E test `test_nontemporal_match_regression` in `tests/unit/test_temporal_cypher.py::TestTemporalCypherE2E`: execute `MATCH (a:Service)-[r]->(b) RETURN a.id LIMIT 5`; assert produces same results as before this feature (SC-006, FR-006)
- [ ] T043 Run full unit + E2E test suite: `pytest tests/unit/ -q --timeout=30` — all tests must pass (SC-002: existing 322+ + new tests)
- [ ] T044 Run SC-003 latency benchmark from `specs/039-temporal-cypher/quickstart.md`: measure temporal Cypher vs `get_edges_in_window()` on KGBENCH; assert cypher_lat ≤ baseline × 2; document measured values in `specs/039-temporal-cypher/spec.md` §Clarifications
- [ ] T045 Update `README.md`: add temporal Cypher example to Cypher section showing `WHERE r.ts >= $start AND r.ts <= $end` pattern and ORDER BY
- [ ] T046 Bump version in `pyproject.toml` to `1.42.0`
- [ ] T047 Commit: `feat: v1.42.0 — Cypher temporal edge filtering (WHERE r.ts, ORDER BY r.ts, r.weight)`
- [ ] T048 Tag `v1.42.0`, build with `python3 -m build`, publish with `twine upload dist/iris_vector_graph-1.42.0*`
