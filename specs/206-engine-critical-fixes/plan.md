# Implementation Plan: IVG Engine Critical Fixes

**Branch**: `206-engine-critical-fixes` | **Date**: 2026-09-01 | **Spec**: [spec.md](spec.md)

## Summary

Three targeted fixes: one ObjectScript line (A9 data-loss), two Python lines
(A12 perf), one new ObjectScript classmethod + Python wrapper (A8.3 retention).
No schema changes. No public API breakage. All three are independently shippable.

## Technical Context

**Language/Version**: Python 3.10+ (3.13 verified), ObjectScript (IRIS 2024.1+)
**Primary Dependencies**: `intersystems-irispython` (DBAPI + native), `pytest>=7.4`
**Storage**: `^KG` globals (ObjectScript), `Graph_KG.*` SQL tables (Python)
**Testing**: `pytest` — unit (no container) + E2E against `ivg-iris-enterprise`
**Target Platform**: IRIS 2024.1+ / HealthShare enterprise NoPWS (2026.3.0AI)
**Performance Goals**: A12 — 100-node batch delete in <2 s on 331k-row table
**Constraints**: A9 one-line change; A12 two-line change; A8.3 additive only

## Constitution Check

- [x] Named container `ivg-iris-enterprise` managed by `scripts/enterprise-container.sh`
- [x] E2E test phase covering all three user stories (non-optional)
- [x] `SKIP_IRIS_TESTS` defaults to `"false"` in new test files
- [x] IRIS ports resolved via env var / OrbStack hostname, not hardcoded

## Project Structure

### Documentation (this feature)

```text
specs/206-engine-critical-fixes/
  spec.md
  plan.md          ← this file
  research.md
  data-model.md
  tasks.md
```

### Source files touched

```text
iris_src/src/Graph/KG/TraversalBuild.cls   # A9: Kill ^KG → Kill five subscripts
iris_src/src/Graph/KG/TemporalIndex.cls    # A8.3: add PurgeBucketRange classmethod
iris_vector_graph/_engine/nodes_edges.py   # A12: split OR delete in bulk_delete_nodes
iris_vector_graph/_engine/temporal.py      # A8.3: add purge_bucket_range() wrapper
tests/unit/test_engine_critical_fixes.py   # new unit test file
tests/integration/test_engine_critical_fixes_e2e.py  # new E2E test file
CHANGELOG.md
```

---

## Phase 0: Research

### Findings

**A9 — exact location**: `TraversalBuild.cls` line 10: `Kill ^KG`. Everything
after this line rebuilds only `^KG("label")`, `^KG("prop")`, `^KG("out")`,
`^KG("in")`, `^KG("deg")`. The fix is to replace the single `Kill ^KG` with
five explicit kills:

```objectscript
Kill ^KG("label"), ^KG("prop"), ^KG("out"), ^KG("in"), ^KG("deg")
```

`^KG("deg2p")` and `^KG("deg2p_exact")` are killed later (lines 140–197) in
`BuildNKG`/`Build2HopStats` — those are fine as-is.

**A12 — exact location**: `nodes_edges.py` `bulk_delete_nodes()` lines 1153–1155.
Two OR queries: one on `rdf_reifications` (subquery), one on `rdf_edges` direct.
The `rdf_reifications` subquery also uses OR — must split that too. Fix:

```python
# rdf_reifications subquery — split into two DELETE statements
cursor.execute(
    f"DELETE FROM {self._t('rdf_reifications')} WHERE edge_id IN "
    f"(SELECT edge_id FROM {self._t('rdf_edges')} WHERE s IN ({phs}))",
    batch,
)
cursor.execute(
    f"DELETE FROM {self._t('rdf_reifications')} WHERE edge_id IN "
    f"(SELECT edge_id FROM {self._t('rdf_edges')} WHERE o_id IN ({phs}))",
    batch,
)
# rdf_edges — split into two DELETE statements
cursor.execute(
    f"DELETE FROM {self._t('rdf_edges')} WHERE s IN ({phs})", batch
)
cursor.execute(
    f"DELETE FROM {self._t('rdf_edges')} WHERE o_id IN ({phs})", batch
)
```

**A8.3 — model from PurgeBefore**: `PurgeBefore` (line 293) walks `^KG("tout")`
by timestamp, computing `bucket = ts \ tBucketDiv` and killing `^KG("tagg", bucket)`.
`PurgeBucketRange` skips the raw-edge walk entirely — it walks `^KG("tagg")` by
bucket key directly, which is O(range) not O(raw-edges):

```objectscript
// PurgeBucketRange(bucketStart, bucketEnd) -- O(range), returns count
ClassMethod PurgeBucketRange(
    bucketStart As %Integer, bucketEnd As %Integer) As %Integer
{
    Set bucketStart = +bucketStart, bucketEnd = +bucketEnd
    Set tCount = 0
    If bucketStart > bucketEnd Quit 0
    Set bucket = bucketStart - 1
    For {
        Set bucket = $Order(^KG("tagg", bucket))
        Quit:(bucket="")!(bucket > bucketEnd)
        Kill ^KG("tagg", bucket)
        Kill ^KG("bucket", bucket)
        Set tCount = tCount + 1
    }
    Quit tCount
}
```

Python wrapper in `TemporalMixin`:

```python
def purge_bucket_range(self, bucket_start: int, bucket_end: int) -> int:
    result = self._iris_obj().classMethodValue(
        "Graph.KG.TemporalIndex", "PurgeBucketRange", bucket_start, bucket_end
    )
    return int(str(result))
```

---

## Phase 1: Design & Contracts

### data-model.md

No schema changes. Globals affected:

<!-- markdownlint-disable MD013 -->
| Global                                         | A9                       | A12          | A8.3                        |
| ---------------------------------------------- | ------------------------ | ------------ | --------------------------- |
| `^KG("tout"/"tin"/"tagg"/"bucket"/"labelset")` | preserved (was wiped)    | no change    | tagg+bucket deleted in range|
| `^KG("label"/"prop"/"out"/"in"/"deg")`         | rebuilt as before        | no change    | no change                   |
| `Graph_KG.rdf_edges`                           | no change                | split DELETE | no change                   |
| `Graph_KG.rdf_reifications`                    | no change                | split DELETE | no change                   |
<!-- markdownlint-enable MD013 -->

### contracts

No new public HTTP/SQL contracts. New Python method:
`engine.purge_bucket_range(bucket_start, bucket_end) -> int`.

### quickstart.md

```python
# A9: sync() is now safe with temporal data present
engine.sync()   # temporal edges survive

# A12: bulk_delete_nodes is 100× faster (no code change at callsite)
engine.bulk_delete_nodes(node_ids)

# A8.3: expire stale aggregate buckets by bucket number (not timestamp)
# bucket = timestamp // BUCKET_SIZE (300 for seconds, 300000 for ms)
n = engine.purge_bucket_range(bucket_start=0, bucket_end=5_999_999)
print(f"Removed {n} junk buckets")
```

---

## Implementation Strategy

### Phase ordering

1. **A9** first — data-loss fix, one line, independent. Unblocks any sync() usage.
2. **A12** second — perf fix, two lines (four with reifications split), independent.
3. **A8.3** last — new classmethod, slightly more code, dependent on TemporalIndex.

Each phase: unit tests first → implementation → E2E tests → phase gate.

### Test strategy

**Unit (no container)**:

- A9: mock `^KG` globals via `classMethodVoid` mock; assert temporal subscripts
  present after `BuildKG()` call.
- A12: mock cursor; assert `execute()` is called with two separate DELETE
  statements, never a single OR query.
- A8.3: mock `classMethodValue`; assert wrapper passes args and returns int.

**E2E (ivg-iris-enterprise)**:

- A9: insert temporal edges, call `engine.sync()`, assert edges still present.
- A12: insert nodes/edges, call `bulk_delete_nodes`, assert correct deletions.
- A8.3: insert edges + let aggregates accumulate, call `purge_bucket_range`,
  assert targeted buckets gone and raw edges intact.
