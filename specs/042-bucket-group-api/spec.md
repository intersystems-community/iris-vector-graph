# Feature Specification: Bucket Group API Enhancements

**Feature Branch**: `042-bucket-group-api`  
**Created**: 2026-04-07  
**Status**: Draft  
**Input**: Three IVG enhancements identified during opsreview 006-rtn-correlation implementation

## Background

`GetBucketGroups` is a core temporal aggregation query — given a predicate and time window, it returns per-source statistics. During opsreview integration three gaps were found: (1) no way to filter by source prefix, forcing callers to fetch all tenants then filter in Python; (2) no way to ask "which targets does source X connect to?" over a time window; (3) the Python wrapper has no documentation of its return shape.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Filter Bucket Groups by Source Prefix (Priority: P1)

A caller querying `CALLED_BY` edges for a specific customer (`Routine:AcmeCorp:PROD:*`) currently receives results for every customer in the namespace and must discard irrelevant rows in application code. When many customers share a namespace this is O(all-customers) work for an O(one-customer) query.

**Why this priority**: Correctness concern — without this filter, the idempotency pre-flight check in opsreview can return a false positive (another customer's edges trigger an early-return, silently skipping ingest for the target customer). Also the most impactful of the three changes.

**Independent Test**: Call `get_bucket_groups(predicate="CALLED_BY", ts_start=..., ts_end=..., source_prefix="Routine:AcmeCorp:")` on a namespace containing edges for two customers. Verify only AcmeCorp rows are returned.

**Acceptance Scenarios**:

1. **Given** edges exist for `Routine:AcmeCorp:PROD:X` and `Routine:OtherCo:PROD:Y` with predicate `CALLED_BY`, **When** `GetBucketGroups("CALLED_BY", ts0, ts1, "Routine:AcmeCorp:")` is called, **Then** only the AcmeCorp row is returned.
2. **Given** the same data, **When** `GetBucketGroups("CALLED_BY", ts0, ts1, "")` is called (empty prefix), **Then** all rows are returned (backward-compatible).
3. **Given** no edges match the prefix in the time window, **When** called with that prefix, **Then** an empty list is returned (not an error).
4. **Given** a prefix that is a substring of some sources but not a leading prefix, **When** called, **Then** only sources that start with the exact prefix string are included.

---

### User Story 2 — Query Distinct Targets for a Source Over a Time Window (Priority: P2)

A caller that has identified a hot routine (`Routine:AcmeCorp:PROD:ProcessHL7`) wants to know which query groups it called into during a time window. Currently there is no IVG API for this — the only options are full edge scans or application-side joins.

**Why this priority**: Enables drill-down from routine to query group in opsreview reports. Independent of P1.

**Independent Test**: Ingest edges `ProcessHL7 → G1`, `ProcessHL7 → G2`, `OtherRoutine → G3`. Call `get_bucket_group_targets("Routine:AcmeCorp:PROD:ProcessHL7", "CALLED_BY", ts0, ts1)`. Verify `["QueryGroup:AcmeCorp:PROD:G1", "QueryGroup:AcmeCorp:PROD:G2"]` are returned (order irrelevant, G3 absent).

**Acceptance Scenarios**:

1. **Given** a source node with edges to two distinct targets under predicate `CALLED_BY` in the window, **When** `GetBucketGroupTargets(source, "CALLED_BY", ts0, ts1)` is called, **Then** both target node IDs are returned (deduplicated).
2. **Given** the same source has edges in two different time buckets both within the window, **When** called, **Then** each target appears only once (deduplication across buckets).
3. **Given** no edges exist for the source in the window, **When** called, **Then** an empty list is returned.
4. **Given** edges outside the time window exist for the source, **When** called with a window that excludes them, **Then** those targets are not returned.

---

### User Story 3 — Documented Return Schema for `get_bucket_groups()` (Priority: P3)

A developer calling `engine.get_bucket_groups()` has no way to know the shape of the returned dicts without reading the ObjectScript source. The field `"sum"` was confused with `"total"` and `"total_weight"` during opsreview integration, causing a test failure.

**Why this priority**: Pure documentation — no behavior change, zero risk, but prevents recurrence of the integration confusion.

**Independent Test**: Read the Python docstring on `engine.get_bucket_groups()`. Verify it names all keys: `source`, `predicate`, `count`, `sum`, `avg`, `min`, `max`.

**Acceptance Scenarios**:

1. **Given** a developer reads `help(engine.get_bucket_groups)` or inspects the source, **When** they need to know what keys are in each result dict, **Then** the docstring lists all keys with brief descriptions.
2. **Given** the return shape changes in a future IVG version, **When** the ObjectScript is updated, **Then** the docstring is the single place to update.

---

### Edge Cases

- `sourcePrefix` empty string → no filter applied, full backward compatibility.
- `tsStart > tsEnd` → empty list, consistent with existing `GetBucketGroups` behavior.
- Source has edges in multiple buckets within the window → `GetBucketGroupTargets` deduplicates across all buckets.
- `sourcePrefix` containing `:` or `^` characters → safe, these are valid in IRIS global subscripts.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `GetBucketGroups` MUST accept an optional `sourcePrefix As %String = ""` fourth parameter.
- **FR-002**: When `sourcePrefix` is non-empty, `GetBucketGroups` MUST exclude source nodes whose string value does not begin with `sourcePrefix`.
- **FR-003**: When `sourcePrefix` is empty, `GetBucketGroups` MUST return all source nodes (existing behavior preserved).
- **FR-004**: The Python engine wrapper `get_bucket_groups()` MUST accept an optional `source_prefix: str = ""` keyword argument and pass it to `GetBucketGroups`.
- **FR-005**: A new `GetBucketGroupTargets(source, predicate, tsStart, tsEnd)` class method MUST be added to `Graph.KG.TemporalIndex`, returning a JSON array of distinct target node ID strings for the given source+predicate over the time window.
- **FR-006**: `GetBucketGroupTargets` MUST deduplicate target node IDs across time buckets within the window.
- **FR-007**: The Python engine MUST expose `get_bucket_group_targets(source: str, predicate: str, ts_start: int, ts_end: int) -> list[str]`.
- **FR-008**: The Python `get_bucket_groups()` method MUST have a docstring documenting the return type (`list[dict]`) and all dict keys: `source`, `predicate`, `count`, `sum`, `avg`, `min`, `max`.
- **FR-009**: ObjectScript changes MUST be compiled and tested against the IVG project container (`iris-vector-graph-main`) via the standard `conftest.py` fixture. The opsreview caller (`RoutineTimelineStore`) will be updated in a separate follow-on opsreview commit after this spec merges.

### Key Entities

- **BucketGroup**: Aggregated statistics for a (source, predicate) pair over a time window. Keys: `source` (node ID), `predicate` (edge type), `count` (edge count), `sum` (total weight), `avg` (mean weight), `min` (minimum weight), `max` (maximum weight).
- **TemporalIndex globals**: `^KG("tagg", bucket, source, predicate, metric)` for forward aggregates; `^KG("tin", ts, target, predicate, source)` reverse index used by `GetBucketGroupTargets`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A query for one customer's edges in a two-customer namespace returns only that customer's rows — zero false-positive results from other tenants.
- **SC-002**: The opsreview `RoutineTimelineStore` idempotency pre-flight check produces no false positives in a multi-tenant namespace.
- **SC-003**: `get_bucket_group_targets()` returns the correct set of distinct targets for a source in a time window, verified by unit test covering deduplication and window boundary exclusion.
- **SC-004**: A developer reading the `get_bucket_groups()` docstring can identify the correct field name for the total weight (`"sum"`) without consulting ObjectScript source.
- **SC-005**: All existing `GetBucketGroups` callers (opsreview + IVG tests) continue to pass without modification.

## Assumptions

- `sourcePrefix` filtering uses `$Extract(src, 1, $Length(sourcePrefix)) = sourcePrefix` in the ObjectScript inner loop — prefix match, not regex or substring.
- `GetBucketGroupTargets` scans `^KG("tin")` which is already maintained by `bulk_create_edges_temporal`; no schema change required.
- `source_prefix` is passed as the fourth positional argument to the ObjectScript class method call.
- No data migration needed — purely additive changes.

## Clarifications

### Session 2026-04-07

- Q: Which IRIS container should this spec target for compile/test? → A: IVG's own `iris-vector-graph-main` container for the IVG test suite; `opsreview-iris` is the downstream consumer and will be validated in the follow-on opsreview commit.
- Q: Should `RoutineTimelineStore` in opsreview be updated to use `source_prefix` in the same branch? → A: No — follow-on commit in opsreview after this IVG PR merges; keeps this PR self-contained and reviewable without opsreview context.
