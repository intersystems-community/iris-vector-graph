# Implementation Plan: 038 Pre-Aggregated Temporal Analytics

**Branch**: `038-temporal-preagg`
**Status**: In progress — TemporalIndex.cls skeleton uncommitted, Python wrappers + tests not written

---

## Current State

The following work-in-progress exists as uncommitted changes (`git diff HEAD`):

- `iris_src/src/Graph/KG/TemporalIndex.cls`: `^KG("tagg",...)` writes in `InsertEdge`/`BulkInsert`, `GetAggregate`, `GetBucketGroups`, `Purge` updated — **no HLL yet**
- `tests/unit/test_temporal_edges.py`: `import json` added only

Nothing is committed. Python wrappers, HLL, tests all missing.

---

## Phase 1 — Unit tests (write first, run to confirm failure)

Per Constitution III (Test-First), unit tests are written before any implementation.
Run them immediately after writing — they MUST fail at this point (Python wrappers don't exist yet).

### Unit tests to add to `tests/unit/test_temporal_edges.py`

Class `TestTemporalPreAggUnit`:
- `test_get_temporal_aggregate_calls_classmethod` — mock returns "42", verify call
- `test_get_temporal_aggregate_count_returns_int` — returns int
- `test_get_temporal_aggregate_avg_returns_float` — returns float
- `test_get_temporal_aggregate_empty_avg_returns_none` — empty string → None
- `test_get_temporal_aggregate_empty_count_returns_zero` — empty string + count → 0
- `test_get_bucket_groups_returns_list` — mock JSON string → list
- `test_get_distinct_count_calls_classmethod` — mock returns "7", verify int

All 7 tests must fail before Phase 2 begins. If they pass before implementation, the mocks are wrong.

---

## Phase 2 — ObjectScript (TemporalIndex.cls)

Complete and harden the ObjectScript layer.

### What needs to be added to the existing skeleton

1. **`GetDistinctCount` method** — 16-register HLL merge + HarmonicMean estimator
2. **HLL update in `InsertEdge` and `BulkInsert`** — hash target, update register in `^KG("tagg",...,"hll")`
3. **MIN/MAX atomicity comment** — document race condition per FR-013
4. **`Purge` already kills `^KG("tagg")` ✅** — verify it also covers any HLL registers (same subscript, already covered)

### HLL implementation details

`$SYSTEM.Encryption.SHA1Hash` is **verified available** in the target IRIS instance (tested 2026-04-01). Use it directly — no fallback needed.

```objectscript
// SHA1Hash returns 20-byte binary string
Set hashBytes = $SYSTEM.Encryption.SHA1Hash(target)
Set regIdx = ($ASCII(hashBytes, 1) # 16) + 1  // 1-based, 1-16
// Count leading zeros of second byte (0-255):
Set b1 = $ASCII(hashBytes, 2)
Set lz = 1
If b1 = 0 { Set lz = 9 }           // all zeros → 9 (8 bits + 1)
Else {
    While (b1 # 2) = 0 { Set lz = lz + 1, b1 = b1 \ 2 }
}
// Read-modify-write register (racy — acceptable for Phase 1)
Set hll = $Get(^KG("tagg", bucket, source, predicate, "hll"), $ListBuild(0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0))
If lz > $List(hll, regIdx) {
    Set $List(hll, regIdx) = lz
    Set ^KG("tagg", bucket, source, predicate, "hll") = hll
}
```

For `GetDistinctCount`, merge registers across buckets (element-wise max), then apply estimator:
```objectscript
// Merge: element-wise max of 16 registers across all buckets in range
// Estimate: alpha_16 * 16^2 / SUM(2^(-reg[i]))
// alpha_16 = 0.673
Set alpha = 0.673
Set m = 16
Set Z = 0
For i = 1:1:m { Set Z = Z + (1 / (2 ** $List(merged, i))) }
Set estimate = alpha * m * m / Z
Return $Number(estimate, 0)  // round to integer
```

---

## Phase 3 — Python wrappers

Add to `iris_vector_graph/engine.py` after `get_edge_attrs`:

```python
def get_temporal_aggregate(self, source, predicate, metric, ts_start, ts_end):
    result = self._iris_obj().classMethodValue(
        "Graph.KG.TemporalIndex", "GetAggregate",
        source, predicate, metric, ts_start, ts_end)
    s = str(result)
    if s == "":
        return None if metric in ("avg", "min", "max") else 0
    return int(s) if metric == "count" else float(s)

def get_bucket_groups(self, predicate="", ts_start=0, ts_end=0):
    result = self._iris_obj().classMethodValue(
        "Graph.KG.TemporalIndex", "GetBucketGroups",
        predicate, ts_start, ts_end)
    return json.loads(str(result))

def get_distinct_count(self, source, predicate, ts_start, ts_end):
    result = self._iris_obj().classMethodValue(
        "Graph.KG.TemporalIndex", "GetDistinctCount",
        source, predicate, ts_start, ts_end)
    return int(str(result))
```

---

## Phase 4 — Run unit tests (must fail, then pass)

Order: write (Phase 1) → run fail → implement Phases 2+3 → run pass.

```bash
pytest tests/unit/test_temporal_edges.py -v -k "PreAgg"  # must be all green after Phase 3
```

---

## Phase 5 — E2E tests

Class `TestTemporalPreAggE2E` (same file, `@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")`):
- `test_aggregate_avg_correct` — insert 10 edges w/ known weights, verify avg
- `test_aggregate_count_correct` — insert 10 edges, count = 10
- `test_bucket_groups_all_sources` — insert edges for 3 sources, verify 3 groups
- `test_multi_bucket_aggregate` — insert edges in 2 different 5-min windows, verify sum
- `test_distinct_count_nonzero` — insert 20 edges to distinct targets, count > 0
- `test_purge_clears_tagg` — insert, purge, count = 0

---

## Phase 6 — Compile and E2E run

> **Container name**: `iris-vector-graph-main` — verified from `tests/conftest.py:161`.
> This is the test-attach container name, distinct from the docker-compose service name
> (`iris_vector_graph`). Do NOT use the docker-compose name for test execution.
> `SKIP_IRIS_TESTS` MUST default to `"false"` in all new test classes per Constitution IV.

```bash
# Compile — scripts/compile_cls.py does NOT exist. Use docker cp + exec (verified working):
docker cp iris_src/src/Graph/KG/TemporalIndex.cls iris-vector-graph-main:/tmp/TemporalIndex.cls
docker exec -i iris-vector-graph-main iris session IRIS -U USER \
  <<< 'Do $system.OBJ.Load("/tmp/TemporalIndex.cls","ck") Halt'
# Expected output: "Load finished successfully."

# Unit tests (no container needed)
pytest tests/unit/test_temporal_edges.py -v

# E2E tests (requires iris-vector-graph-main container running)
pytest tests/unit/test_temporal_edges.py -v -k "E2E"
# or run full suite:
pytest tests/ -v
```

---

## Phase 7 — Benchmark

Run after all tests pass:

```python
# In scripts/bench/bench_temporal_preagg.py
import time
from iris_vector_graph.engine import IRISGraphEngine

# 1. Ingest benchmark: measure edges/sec with tagg active
# 2. Query benchmark: get_temporal_aggregate on RE2-TT data
# 3. GROUP BY benchmark: get_bucket_groups on 27 services

# Document results in spec.md §8.4
```

---

## Phase 8 — Version bump and commit

1. Bump `iris_vector_graph/__init__.py` → `1.39.0`
2. Update `README.md` changelog
3. Update spec status → `Implemented`
4. Commit: `feat: v1.39.0 — Pre-aggregated temporal analytics (^KG("tagg"), GetAggregate, HLL)`
5. Tag: `v1.39.0`
6. Publish: `python3 -m build && twine upload dist/*`
