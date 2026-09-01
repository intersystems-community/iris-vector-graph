# Feature Specification: TemporalIndex API Gaps

**Feature Branch**: `204-temporal-index-gaps`
**Created**: 2026-08-31
**Status**: Draft

## Overview

`Graph.KG.TemporalIndex` has four API gaps that force the opsreview aggregator
to manipulate `^KG` globals it does not own. This creates a coupling between
opsreview's release cycle and IVG's internal global layout; any change to
subscript order or naming silently corrupts opsreview's data.

This feature closes all four gaps, giving opsreview zero direct `^KG` access
after migration. It also fixes a latent correctness bug in bucket-boundary
calculations that affects any caller using millisecond timestamps.

## User Scenarios & Testing

### User Story 1 — Separate raw vs. aggregate retention (Priority: P1)

An operator wants to keep raw metric samples for 48 hours (for debugging) but
retain 13-month aggregate rollups (for trend analysis). Today both are deleted
together by `PurgeBefore`, making independent retention windows impossible.

**Why this priority**: Largest code reduction in opsreview (8 direct `^KG`
statements in `MetricPurge.cls` all blocked on this). No other gap can be
fully exercised until purge is safe to call.

**Independent Test**: Call `PurgeRawBefore(tsEnd)` with a mixed dataset
containing both raw edges and aggregates. Verify raw edges older than `tsEnd`
are gone, aggregates survive, and the method returns the correct deleted count.

**Acceptance Scenarios**:

1. **Given** raw edges at ts=100, 200, 300 and aggregates in buckets covering
   those timestamps, **When** `PurgeRawBefore(250)` is called, **Then** edges
   at ts=100 and ts=200 are deleted, edge at ts=300 survives, all aggregates
   survive, and the return value is 2.
2. **Given** no raw edges exist before tsEnd, **When** `PurgeRawBefore` is
   called, **Then** nothing is deleted and 0 is returned.
3. **Given** an edge at ts=tsEnd exactly, **When** `PurgeRawBefore(tsEnd)` is
   called, **Then** that edge is NOT deleted (strict `<` boundary).
4. **Given** edges with `edgeprop` attributes, **When** `PurgeRawBefore` is
   called, **Then** `^KG("edgeprop")` entries for deleted edges are also
   removed.

---

### User Story 2 — Skip reverse index for write-only predicates (Priority: P2)

An ingest pipeline writes high-volume `METRIC_AT` edges that are never queried
inbound. Writing `^KG("tin")` for these edges doubles write IOPS with no
reader. Today the only workaround is to write the edge and immediately kill
`^KG("tin")`, paying full write cost plus a kill plus a hard dependency on
IVG's exact global layout.

**Why this priority**: Simple additive parameter, zero compatibility risk.
Reduces ingest write amplification by half for the dominant predicate.

**Independent Test**: Call `InsertEdge` with `suppressReverseIndex=1`. Verify
`^KG("tout")` entry exists, `^KG("tin")` entry does not exist, and
`QueryWindow` (outbound) still returns the edge while `QueryWindowInbound`
does not.

**Acceptance Scenarios**:

1. **Given** `suppressReverseIndex=0` (default), **When** `InsertEdge` is
   called, **Then** both `^KG("tout")` and `^KG("tin")` are written (unchanged
   behavior).
2. **Given** `suppressReverseIndex=1`, **When** `InsertEdge` is called,
   **Then** `^KG("tout")` is written and `^KG("tin")` is NOT written.
3. **Given** a `BulkInsert` batch item with `"suppress_reverse": 1`, **When**
   `BulkInsert` is called, **Then** that item's reverse index is suppressed.
4. **Given** `suppressReverseIndex=1`, **When** `InsertEdge` is called,
   **Then** `tagg`, `bucket`, `out`, `in`, `deg`, `edgeprop`, and HLL are all
   still written normally.

---

### User Story 3 — Intern label sets under IVG ownership (Priority: P3)

High-cardinality metric streams repeat the same label sets (e.g.,
`{"region":"us-east","env":"prod"}`) on every sample. Storing the full JSON
on every edge is the dominant storage cost. Callers need a canonicalize-hash-
deduplicate primitive owned by IVG so they don't write into `^KG("labelset")`
directly.

**Why this priority**: Largest design surface (retention contract). Implement
last, deliberately.

**Independent Test**: Call `InternLabelSet` with two JSON strings that are
logically identical but differ in key order. Verify both return the same hash.
Call `ResolveLabelSet` with that hash and verify it returns canonical JSON.
Call `ResolveLabelSet` with an unknown hash and verify it returns `""`.

**Acceptance Scenarios**:

1. **Given** `{"b":2,"a":1}` and `{"a":1,"b":2}`, **When** `InternLabelSet`
   is called on each, **Then** both return the same SHA1 hex hash.
2. **Given** a hash returned by `InternLabelSet`, **When** `ResolveLabelSet`
   is called, **Then** it returns the canonical JSON (keys sorted ascending,
   no extra whitespace).
3. **Given** the same canonical JSON called twice, **When** `InternLabelSet`
   is called twice, **Then** the storage entry is written only once (idempotent).
4. **Given** an arbitrary unknown hash, **When** `ResolveLabelSet` is called,
   **Then** it returns `""`.
5. **Given** a `PurgeRawBefore` call, **When** it completes, **Then**
   `^KG("labelset")` entries are NOT touched (label sets outlive raw edges).

---

### User Story 4 — Correct bucket width for millisecond timestamps (Priority: P2)

The `BUCKET = 300` parameter assumes second-precision timestamps. Callers using
millisecond timestamps (per spec 027 FR-002) produce 300-millisecond buckets
instead of 5-minute buckets, inflating aggregate storage ~1000× and corrupting
`GetAggregate` / `GetBucketGroups` results.

**Why this priority**: Latent correctness bug that worsens over time as
aggregate storage grows. Cheap to fix now; expensive after data accumulates.
Affects bucket boundary in `PurgeBefore` and `PurgeRawBefore` too.

**Independent Test**: Configure the class with `TSUNIT="ms"`. Insert an edge
with a millisecond timestamp and verify it lands in the correct 5-minute bucket
(bucket = ts ÷ 300000). Verify `GetAggregate` spans the correct wall-clock
range.

**Acceptance Scenarios**:

1. **Given** `TSUNIT="ms"` and an edge at ts=1,000,000 ms (1000 s epoch),
   **When** inserted, **Then** `bucket = 1000000 \ 300000 = 3` (not `3333`).
2. **Given** `TSUNIT="ms"`, **When** `PurgeBefore(tsEnd)` or
   `PurgeRawBefore(tsEnd)` is called, **Then** the bucket kill uses
   `tsEnd \ BUCKETMS` (not `tsEnd \ 300`).
3. **Given** `tsEnd` falls mid-bucket, **When** `PurgeBefore` or
   `PurgeRawBefore` is called, **Then** aggregates for the bucket containing
   `tsEnd` are NOT destroyed (only fully-expired buckets are killed).
4. **Given** `TSUNIT=""` (default, unset), **When** any method is called,
   **Then** behavior is identical to the current second-precision behavior
   (`BUCKET = 300`).

---

### Edge Cases

- What happens when `PurgeRawBefore` is called with `tsEnd = 0`? (Nothing
  deleted; strict `<` means no edge qualifies.)
- What happens when `InternLabelSet` receives invalid JSON? (Return `""` or
  signal error; do not write corrupt data.)
- What happens when `BulkInsert` receives a batch item missing the
  `suppress_reverse` field? (Treat as 0 — default behavior preserved.)
- What happens when `TSUNIT="ms"` but a caller inserts a second-precision
  timestamp by mistake? (Silent misbucket — IVG cannot detect this;
  document the contract clearly.)
- What happens when `PurgeBefore` kills `^KG("bucket")` for a mid-boundary
  bucket? (With the fix, the bucket containing `tsEnd` is never killed —
  only strictly earlier buckets.)

## Requirements

### Functional Requirements

#### Gap 1 — PurgeRawBefore

- **FR-001**: `TemporalIndex` MUST expose `PurgeRawBefore(tsEnd As %Integer)
  As %Integer` that deletes `^KG("tout")`, `^KG("tin")`, and
  `^KG("edgeprop")` entries for all timestamps strictly less than `tsEnd`.
- **FR-002**: `PurgeRawBefore` MUST leave `^KG("tagg")` and `^KG("bucket")`
  untouched.
- **FR-003**: `PurgeRawBefore` MUST leave the non-temporal adjacency globals
  (`^KG("out")`, `^KG("in")`, `^KG("deg")`) untouched.
- **FR-004**: `PurgeRawBefore` MUST return the count of raw edges deleted.
- **FR-005**: `PurgeRawBefore` MUST use strict `<` (not `<=`) matching the
  boundary semantics of `PurgeBefore`.

#### Gap 2 — suppressReverseIndex

- **FR-006**: `InsertEdge` MUST accept an optional `suppressReverseIndex
  As %Boolean = 0` parameter as the last positional argument.
- **FR-007**: When `suppressReverseIndex = 1`, `InsertEdge` MUST skip writing
  `^KG("tin")` and only write `^KG("tout")`.
- **FR-008**: All other writes (tagg, bucket, out, in, deg, edgeprop, HLL)
  MUST be unaffected by `suppressReverseIndex`.
- **FR-009**: `BulkInsert` batch items MUST support a `suppress_reverse`
  boolean field (default 0); when 1, the same suppression applies to that
  item's `^KG("tin")` write.
- **FR-010**: Default value of `suppressReverseIndex = 0` MUST preserve all
  existing caller behavior without modification.

#### Gap 3 — InternLabelSet / ResolveLabelSet

- **FR-011**: `TemporalIndex` MUST expose `InternLabelSet(attrsJSON As
  %String) As %String` that canonicalizes, hashes, and stores a label set.
- **FR-012**: Canonicalization MUST sort object keys ascending
  case-sensitively and emit no extra whitespace.
- **FR-013**: The hash MUST be SHA1 of the canonical form, lowercase hex.
- **FR-014**: `InternLabelSet` MUST be idempotent: calling it twice with the
  same logical label set writes storage exactly once.
- **FR-015**: `TemporalIndex` MUST expose `ResolveLabelSet(hash As %String)
  As %String` that returns the canonical JSON for a known hash, or `""` if
  unknown.
- **FR-016**: `PurgeRawBefore`, `PurgeBefore`, and `Purge` MUST NOT delete
  label set storage.
- **FR-017**: Label set storage MUST use an IVG-owned subscript that does not
  collide with any existing `^KG` subscript.

#### Gap 4 — Bucket unit

- **FR-018**: `TemporalIndex` MUST expose `Parameter TSUNIT As %String = ""`
  where `"ms"` indicates millisecond-precision timestamps.
- **FR-019**: When `TSUNIT = "ms"`, the effective bucket width MUST be
  `BUCKET * 1000` milliseconds (i.e., `BUCKETMS = BUCKET * 1000`).
- **FR-020**: All methods that compute `bucket = ts \ ..#BUCKET` MUST use
  `BUCKETMS` when `TSUNIT = "ms"`.
- **FR-021**: `PurgeBefore` and `PurgeRawBefore` MUST only kill aggregate
  buckets strictly before the bucket containing `tsEnd`; the bucket
  containing `tsEnd` MUST NOT be killed even if `tsEnd` falls mid-bucket.

#### Python wrappers

- **FR-022**: `IRISGraphEngine` MUST expose `purge_raw_before(ts_end: int)
  -> int` delegating to `PurgeRawBefore` via `_call_classmethod`.
- **FR-023**: `create_edge_temporal` and `bulk_write_temporal_edges` MUST
  accept `suppress_reverse_index: bool = False` and pass the flag through.
- **FR-024**: `IRISGraphEngine` MUST expose `intern_label_set(attrs: dict)
  -> str` and `resolve_label_set(hash_hex: str) -> str`.

### Key Entities

- **Raw edge**: A timestamped directed edge `(source, predicate, target, ts,
  weight)` stored in `^KG("tout")` / `^KG("tin")`. Subject to raw retention.
- **Aggregate bucket**: A time bucket storing count/sum/min/max/HLL for a
  `(source, predicate)` pair. Subject to aggregate retention only.
- **Label set**: A canonical JSON string representing an edge's attribute set.
  Stored once by SHA1 hash; referenced from edge attrs as `{"ls":"<hash>"}`.
  Never purged.

## Success Criteria

### Measurable Outcomes

- **SC-001**: After `PurgeRawBefore(tsEnd)`, zero raw edges with `ts < tsEnd`
  remain; all aggregate buckets survive; verified by direct inspection.
- **SC-002**: Inserting 10,000 `METRIC_AT` edges with
  `suppressReverseIndex=1` writes zero `^KG("tin")` entries.
- **SC-003**: `InternLabelSet` called 1,000 times with the same 10 logical
  label sets (in random key order) produces exactly 10 storage entries.
- **SC-004**: With `TSUNIT="ms"` and 5-minute bucket width, `GetAggregate`
  over a 1-hour window scans exactly 12 buckets.
- **SC-005**: All existing unit and integration tests pass without
  modification (backward compatibility).
- **SC-006**: opsreview `MetricPurge.cls` and `MetricIngest.cls` have zero
  direct `^KG` references after migration (verified by grep).

## Assumptions

- SHA1 is available via `$SYSTEM.Encryption.SHA1Hash` on IRIS 2024.1+ (same
  as existing HLL code in `UpdateHLL`).
- The `^KG("labelset")` subscript is currently unoccupied in IVG's global
  (opsreview's existing writes go there, but IVG does not).
- `TSUNIT` change is opt-in; existing deployments leave it unset and retain
  second-precision behavior unchanged.
- The Python `_call_classmethod` helper is available (established in spec 199).
- Integration tests target the `ivg-iris-enterprise` container
  (`IVG_TEST_CONTAINER=ivg-iris-enterprise`, port 31972).

## Out of Scope

- Changing the HLL concurrency model (min/max and HLL race condition noted in
  existing code comments — tracked separately).
- Refcounting for label set GC (never-purge is the specified contract).
- Migration tooling for existing opsreview data written under the old layout.
- Any changes to `QueryWindow`, `QueryWindowSources`, `GetAggregate`, or
  `GetBucketGroups` beyond bucket-width arithmetic.
