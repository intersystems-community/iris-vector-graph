# Feature Specification: IVG Engine Critical Fixes

**Feature Branch**: `206-engine-critical-fixes`
**Created**: 2026-09-01
**Status**: Draft

## Overview

Three fixes to `iris-vector-graph` surfaced by opsreview production adoption.
Two are P1: a silent data-loss hazard in `BuildKG` and a 100× delete slowdown.
One is P2: `PurgeBucketRange` for non-destructive aggregate expiry, which compounds
in cost with each day of fleet ingest.

---

## User Scenarios & Testing

### User Story 1 — BuildKG preserves temporal data (Priority: P1)

An operator calls `engine.sync()` (or `BuildKG` directly) after loading graph
data. Today, any temporal edges, aggregates, and label sets written before that
call are silently and permanently destroyed — there is no rebuild path and no
warning. After this fix, `sync()` is safe to call on any container regardless
of whether it holds temporal data.

**Why this priority**: Data-loss hazard. One `sync()` call erases every metric,
span, log, and billing edge with no recovery path. Highest severity, smallest
diff (one line of ObjectScript).

**Independent Test**: Write temporal edges, call BuildKG, assert temporal globals
are intact.

**Acceptance Scenarios**:

1. **Given** a graph with temporal edges in `^KG("tout"/"tin"/"tagg"/"bucket"/"labelset")`,
   **When** `BuildKG()` is called, **Then** all five temporal subscripts are
   unchanged and no data is lost.
2. **Given** `BuildKG()` completes, **When** the adjacency globals are inspected,
   **Then** `^KG("label")`, `^KG("prop")`, `^KG("out")`, `^KG("in")`,
   `^KG("deg")` are rebuilt and all other subscripts are untouched.
3. **Given** an empty temporal index, **When** `BuildKG()` is called, **Then**
   behaviour is identical to the current implementation (no regression).

---

### User Story 2 — bulk_delete_nodes runs at index speed (Priority: P1)

An operator deletes a batch of nodes. Today each batch issues a single DELETE
with `WHERE s IN (...) OR o_id IN (...)`. The OR disables both indexes and forces
a full table scan, measured at ~19 s per batch on 331k rows regardless of batch
size — ~20 nodes/s. After this fix, each DELETE hits a single indexed column and
the operation scales with match count, not table size.

**Why this priority**: 100× measured slowdown. A 205k-node cleanup took ~2.5 h
on the opsreview container. The fix is two lines.

**Independent Test**: Mock the SQL layer and assert two DELETE calls are issued
per batch (one for `s`, one for `o_id`), never a single OR query.

**Acceptance Scenarios**:

1. **Given** a batch of node IDs to delete, **When** `bulk_delete_nodes` runs,
   **Then** exactly two DELETE statements are issued per batch — one for `s IN (...)`
   and one for `o_id IN (...)` — with no OR clause combining them.
2. **Given** a node ID that appears as both source and target in `rdf_edges`,
   **When** `bulk_delete_nodes` runs, **Then** all matching rows are removed
   (functional equivalence to the old OR query).
3. **Given** an empty batch, **When** `bulk_delete_nodes` runs, **Then** no SQL
   is issued and the function returns 0.

---

### User Story 3 — PurgeBucketRange: aggregate-only expiry (Priority: P2)

An operator needs to expire stale `^KG("tagg")` aggregate buckets — for example,
junk buckets written at ~6e9 by the broken ms-mode path, or buckets older than a
13-month retention window. Today the only available method (`PurgeBefore`) also
deletes raw edges, making it unusable for aggregate-only retention. After this
fix, `PurgeBucketRange(bucketStart, bucketEnd)` removes only `^KG("tagg")` and
`^KG("bucket")` entries in the given range; raw edges are untouched.

**Why this priority**: Unblocks opsreview T041a (13-month aggregate retention,
~55 GB of the ~80 GB 100-node budget). Cost compounds with every day of fleet
ingest — all other items on the list are flat.

**Independent Test**: Write edges + aggregates, call `PurgeBucketRange`, assert
`^KG("tagg")` entries in range are gone, raw `^KG("tout"/"tin")` entries survive.

**Acceptance Scenarios**:

1. **Given** aggregate buckets in the range `[bucketStart, bucketEnd]`, **When**
   `PurgeBucketRange(bucketStart, bucketEnd)` is called, **Then** all
   `^KG("tagg")` and `^KG("bucket")` entries with `bucketStart <= bucket <= bucketEnd`
   are deleted and the method returns the count of buckets removed.
2. **Given** raw edges (`^KG("tout"/"tin"/"edgeprop")`) whose timestamps fall
   within the purged bucket range, **When** `PurgeBucketRange` runs, **Then**
   those raw edges are completely unchanged.
3. **Given** aggregate buckets outside the range, **When** `PurgeBucketRange` runs,
   **Then** those buckets are untouched.
4. **Given** `bucketStart > bucketEnd`, **When** `PurgeBucketRange` is called,
   **Then** the method returns 0 and performs no deletions.

### Edge Cases

- `BuildKG` called on a container with only temporal data (no adjacency edges):
  temporal data survives; adjacency globals are empty afterward — no error.
- `bulk_delete_nodes` with a batch where all IDs are source-only or target-only:
  only the relevant DELETE executes rows; the other is a no-op (not an error).
- `PurgeBucketRange` with `bucketStart == bucketEnd`: deletes exactly one bucket's
  aggregates if it exists.
- `PurgeBucketRange` on a range with no matching buckets: returns 0.

---

## Requirements

### Functional Requirements

**FR-001 (A9)** — `BuildKG` must kill only the five adjacency subscripts it
rebuilds: `^KG("label")`, `^KG("prop")`, `^KG("out")`, `^KG("in")`, `^KG("deg")`.
It must not kill `^KG` at the root, and must not touch any other subscript.

**FR-002 (A12)** — `bulk_delete_nodes` must issue separate DELETE statements for
`s IN (...)` and `o_id IN (...)` (never combined with OR). Functional result must
be identical to the current single-statement form.

**FR-003 (A8.3)** — A new `PurgeBucketRange(bucketStart, bucketEnd)` classmethod
must delete `^KG("tagg", bucket)` and `^KG("bucket", bucket)` for all `bucket` in
`[bucketStart, bucketEnd]` (inclusive both ends). Raw edge globals must not be
touched. Returns integer count of buckets removed.

**FR-004** — All three fixes must be accompanied by unit tests verifiable without
an IRIS container.

**FR-005** — At least one E2E integration test per fix must pass against
`ivg-iris-enterprise`.

### Non-Functional Requirements

**NFR-001** — No change to the public API surface of `IRISGraphEngine` for A9 and
A12. `PurgeBucketRange` is additive — no existing signatures change.

**NFR-002** — A9 fix is a one-line ObjectScript change. Any patch larger than
five lines in `TraversalBuild.cls` is a scope creep signal.

**NFR-003** — A12 fix is two lines in `iris_sql_store.py`. Any patch larger than
ten lines in the delete path is a scope creep signal.

---

## Success Criteria

1. `engine.sync()` (which calls `BuildKG`) on a container with 1 000 temporal edges
   completes with all 1 000 edges still present in `^KG("tout")`.
2. `bulk_delete_nodes` on a 331k-edge table deletes a 100-node batch in under
   2 seconds (vs ~19 s before the fix).
3. `PurgeBucketRange(b_start, b_end)` removes all targeted `^KG("tagg")` buckets
   and returns the correct count; a subsequent `QueryWindow` over the same time
   range still returns all raw edges.
4. The full test suite (unit + E2E) passes with no regressions.

---

## Assumptions

- `^KG("tout")`, `^KG("tin")`, `^KG("tagg")`, `^KG("bucket")`, `^KG("labelset")`
  are the complete set of temporal subscripts that must survive `BuildKG`. Any new
  subscripts added in future specs must be explicitly added to both the kill-list
  guard and this assumption.
- `rdf_edges` has covering indexes on `s` and `o_id` individually; the split-DELETE
  approach is only a win if both indexes exist. Schema is assumed unchanged.
- `PurgeBucketRange` range semantics: closed interval `[bucketStart, bucketEnd]`,
  consistent with existing `PurgeBefore` bucket arithmetic.
- opsreview tripwire tests in `tests/test_028_ms_buckets.py` flip from pass to
  fail when each item ships — this is by design and is the delivery signal.

---

## Dependencies

- `iris_src/src/Graph/KG/TraversalBuild.cls` (A9)
- `iris_vector_graph/stores/iris_sql_store.py` (A12)
- `iris_src/src/Graph/KG/TemporalIndex.cls` (A8.3)
- Python engine wrapper for `PurgeBucketRange` in `iris_vector_graph/_engine/temporal.py`
