# Implementation Plan: Bucket Group API Enhancements

**Branch**: `042-bucket-group-api` | **Date**: 2026-04-07 | **Spec**: [spec.md](spec.md)  
**Input**: Feature specification from `/specs/042-bucket-group-api/spec.md`

## Summary

Add `sourcePrefix` filter parameter to `GetBucketGroups`, add new `GetBucketGroupTargets` class method, and add a docstring to `engine.get_bucket_groups()`. All changes are additive — no existing behavior changes, no data migration. Two ObjectScript changes in `iris_src/src/Graph/KG/TemporalIndex.cls` and two Python changes in `iris_vector_graph/engine.py`.

## Technical Context

**Language/Version**: Python 3.10+ (pyproject.toml `requires-python = ">=3.10"`), ObjectScript (IRIS)  
**Primary Dependencies**: `iris-devtester>=1.14.0`, `pytest>=7.4.0`  
**Storage**: IRIS globals `^KG("tagg")` (forward), `^KG("tin")` (reverse) — unchanged  
**Testing**: pytest; unit tests with mocked engine; e2e tests via `SKIP_IRIS_TESTS` guard in `tests/unit/test_temporal_edges.py`  
**Target Platform**: InterSystems IRIS container `iris-vector-graph-main` (from `docker-compose.yml`)  
**Performance Goals**: `sourcePrefix` filter eliminates O(all-tenants) scan; no new perf targets  
**Constraints**: Backward compatible — `sourcePrefix=""` and absent `source_prefix` must behave identically to current API  
**Scale/Scope**: Small — 2 ObjectScript methods modified/added, 2 Python wrappers modified/added, ~10 new tests

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- [x] Container name `iris-vector-graph-main` verified against `docker-compose.yml` (`container_name: iris_vector_graph` → fixture name `iris-vector-graph-main`)
- [x] E2E tests included in test plan (live-container tests in `tests/unit/test_temporal_edges.py` under `SKIP_IRIS_TESTS` guard)
- [x] `SKIP_IRIS_TESTS` defaults to `"false"` — follows existing pattern in `test_temporal_edges.py` line 9
- [x] No hardcoded ports — container port resolved via `iris_test_container` session fixture in `conftest.py`
- [x] Backward compatibility maintained — `sourcePrefix=""` default preserves all existing callers
- [x] No new abstractions — direct extension of existing `GetBucketGroups` and `IRISGraphEngine` patterns

**Principle VI verification** (Grounding Rule):
- Container name: `iris-vector-graph-main` ← `docker-compose.yml` `container_name: iris_vector_graph` + conftest fixture name
- Port: resolved dynamically via `get_exposed_port(1972)` — not hardcoded
- Package: `iris-vector-graph` v1.45.3 ← `pyproject.toml`
- Test file: `tests/unit/test_temporal_edges.py` ← verified exists

## Project Structure

### Documentation (this feature)

```text
specs/042-bucket-group-api/
├── plan.md          ← this file
├── research.md      ✓
├── data-model.md    ✓
├── quickstart.md    ✓
└── tasks.md         ← /speckit.tasks output (not yet created)
```

### Source Code (files touched)

```text
iris_src/src/Graph/KG/
└── TemporalIndex.cls          # ENH-1: add sourcePrefix param; ENH-2: add GetBucketGroupTargets

iris_vector_graph/
└── engine.py                  # ENH-1: source_prefix kwarg + docstring; ENH-2: get_bucket_group_targets()

tests/unit/
└── test_temporal_edges.py     # All new tests — unit (mocked) + e2e (live container)
```

**Structure Decision**: Single-project library. No new files in source tree. All changes extend existing files.

## Phase 0: Research (Complete)

See [research.md](research.md). All decisions resolved:

- `$Extract(src, 1, $Length(sourcePrefix)) = sourcePrefix` for prefix matching
- `^KG("tin")` reverse index scan for `GetBucketGroupTargets`; local `dedup()` array for deduplication
- `source_prefix=""` as fourth keyword argument in Python wrapper
- Tests go in `tests/unit/test_temporal_edges.py` following existing pattern

## Phase 1: Design (Complete)

See [data-model.md](data-model.md) and [quickstart.md](quickstart.md).

## Implementation Approach

### ENH-1: `sourcePrefix` filter on `GetBucketGroups`

**ObjectScript** (`TemporalIndex.cls`):
```objectscript
// Signature change:
ClassMethod GetBucketGroups(predicate As %String, tsStart As %Integer, tsEnd As %Integer, sourcePrefix As %String = "") As %String

// In the inner loop, after "Set src = $Order(^KG("tagg", b, src))" and "Quit:(src = "")":
If sourcePrefix '= "" && ($Extract(src, 1, $Length(sourcePrefix)) '= sourcePrefix) { Continue }
```

**Python** (`engine.py`):
```python
def get_bucket_groups(self, predicate="", ts_start=0, ts_end=0, source_prefix=""):
    """...<docstring listing all return keys>..."""
    result = self._iris_obj().classMethodValue(
        "Graph.KG.TemporalIndex", "GetBucketGroups",
        predicate, ts_start, ts_end, source_prefix)
    return json.loads(str(result))
```

### ENH-2: `GetBucketGroupTargets`

**ObjectScript** (`TemporalIndex.cls`):
```objectscript
ClassMethod GetBucketGroupTargets(source As %String, predicate As %String, tsStart As %Integer, tsEnd As %Integer) As %String
{
    Set tsStart = +tsStart, tsEnd = +tsEnd
    Set startBucket = tsStart \ ..#BUCKET
    Set endBucket = tsEnd \ ..#BUCKET
    Kill dedup
    For b = startBucket:1:endBucket {
        Set tgt = ""
        For {
            Set tgt = $Order(^KG("tin", b, tgt))
            Quit:(tgt = "")
            Set p = ""
            For {
                Set p = $Order(^KG("tin", b, tgt, p))
                Quit:(p = "")
                If p '= predicate { Continue }
                If $Data(^KG("tin", b, tgt, p, source)) { Set dedup(tgt) = 1 }
            }
        }
    }
    Set result = "[", first = 1, tgt = ""
    For {
        Set tgt = $Order(dedup(tgt))
        Quit:(tgt = "")
        If 'first { Set result = result _ "," }
        Set first = 0
        Set result = result _ """" _ tgt _ """"
    }
    Return result _ "]"
}
```

**Python** (`engine.py`):
```python
def get_bucket_group_targets(self, source, predicate, ts_start, ts_end):
    result = self._iris_obj().classMethodValue(
        "Graph.KG.TemporalIndex", "GetBucketGroupTargets",
        source, predicate, ts_start, ts_end)
    return json.loads(str(result))
```

### ENH-3: Docstring

```python
def get_bucket_groups(self, predicate="", ts_start=0, ts_end=0, source_prefix=""):
    """Return pre-aggregated statistics per (source, predicate) pair over a time window.

    Args:
        predicate: Edge type to filter on. Empty string matches all predicates.
        ts_start: Window start as Unix timestamp (inclusive).
        ts_end: Window end as Unix timestamp (inclusive).
        source_prefix: If non-empty, only include entries whose source node ID
            starts with this prefix. Use for tenant-scoped queries. Default "".

    Returns:
        list[dict]: Each dict has keys:
            source    (str)   — source node ID
            predicate (str)   — edge type
            count     (int)   — number of edges in window
            sum       (float) — total weight across all edges
            avg       (float) — mean weight (None if count == 0)
            min       (float) — minimum weight (None if no edges)
            max       (float) — maximum weight (None if no edges)
    """
```
