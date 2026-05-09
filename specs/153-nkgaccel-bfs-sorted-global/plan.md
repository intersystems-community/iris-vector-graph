# Implementation Plan: NKGAccel BFS Unified Output via Sorted Global

**Branch**: `153-nkgaccel-bfs-sorted-global` | **Date**: 2026-05-07 | **Spec**: [spec.md](./spec.md)

## Summary

Change `NKGAccel.BFSJson` to write results to `^ArnoKG("bfs_r", tag, step, o)` and return `"SORTED:tag"` instead of assembling chunks and returning `"CHUNKED:N"`. Remove the `"CHUNKED:N"` handling branch from `engine.py`. Both BFS paths (Rust and ObjectScript) then use identical output format and the engine has one unified read path.

## Technical Context

**Language/Version**: ObjectScript (IRIS 2025.1+) + Python 3.11
**Primary files**: `iris_src/src/Graph/KG/NKGAccel.cls`, `iris_vector_graph/engine.py`
**Arno Rust**: No changes — `kg_bfs_compute` and `kg_bfs_read_chunk` used as-is
**Testing**: `iris_vector_graph` container (port 1972), enterprise container for Rust path
**Performance targets**: Bounded BFS within 20% of baseline; unbounded BFS no MAXSTRING
**Constraints**: No breaking changes to external API; fallback path unchanged

## Constitution Check

**Principle II (Compatibility-First)**: ✅ `NKGAccel.BFSJson` callers get the same result content; only return format changes (internal). Engine handles both.

**Principle III (Test-First)**: ✅ e2e tests written before `NKGAccel.BFSJson` change.

**Principle IV (IRIS e2e)**:
- [x] Container: `iris_vector_graph` (community, port 1972) for ObjectScript fallback tests
- [x] Container: `iris-enterprise-2026` (port 4972, LDBC) for Rust path tests
- [x] Both verified from `docker-compose.yml` and enterprise docker config

**Principle VI (Grounding)**:
- `NKGAccel.BFSJson` at `iris_src/src/Graph/KG/NKGAccel.cls:453`
- `_execute_var_length_cypher` CHUNKED handling at `iris_vector_graph/engine.py:1580`
- `^ArnoKG("bfs_r", tag, step, o)` format: `Traversal.cls:501`
- `ReadBFSResults(tag)`: `Traversal.cls:507`
- `_bfs_stream_pages`: `engine.py:32`

## Phase 0: Research

### Decision Log

**D-001: ObjectScript conversion from JSON chunks to sorted global**
- Decision: After assembling chunks via `kg_bfs_read_chunk`, parse the JSON in ObjectScript and write to `^ArnoKG("bfs_r", tag, step, o)`. Then return `"SORTED:tag"`.
- Rationale: No Rust changes needed. The assembled chunk JSON is already in `{s, p, o, step, w}` format — straightforward to iterate and write the sorted global.
- Alternative rejected: Having Rust write `bfs_r` directly — requires arno changes, out of scope.

**D-002: Tag generation**
- Decision: Tag = `$Job _ "_bfs"` — unique per IRIS job, reused per job (sequential calls overwrite, which is fine since BFS calls are synchronous).
- Rationale: Consistent with existing BFSFastJsonSorted tag pattern (`$Job`).

**D-003: JSON parsing in ObjectScript**
- Decision: Use `%DynamicArray.%FromJSON()` on the assembled chunk string to iterate results.
- Rationale: Already used elsewhere in NKGAccel. The chunk JSON is a flat array of `{s,p,o,step,w}` objects.

**D-004: Engine routing change requires two-point update**
- The Rust path returns the result of `NKGAccel.BFSJson` to `engine.py:1539` as `bfs_json`. Currently: `_json.loads(str(bfs_json))`. After T010: `bfs_json` starts with `"SORTED:tag"` — must detect this and route to `ReadBFSResults`/`_bfs_stream_pages` instead of direct parse.
- The stale `"CHUNKED:"` branch at line 1575 is from `BFSFastJsonChunked` (NOT from Rust path) — remove it separately in T014.
- Rationale: Two distinct changes. T013 = update Rust path routing. T014 = remove dead legacy branch.

**D-005: ObjectScript conversion must be benchmarked (T010a/T010b gates)**
- `%DynamicArray.%FromJSON()` on 15K items + 15K `$Set` writes = significant ObjectScript work.
- If >20% slower than baseline, the ObjectScript conversion approach is rejected and a Rust-side alternative (have Rust write `bfs_r` directly) will be a separate spec.
- Decision: Proceed with ObjectScript conversion, benchmark gates mandatory.

## Phase 1: Design

### NKGAccel.BFSJson change

```objectscript
// BEFORE: assembles chunks, returns raw JSON
Set result = "" For i=1:1:n { Set result = result _ $ZF(...readFn, "^ArnoKG", i) }
Kill ^ArnoKG("bfs_result")
Return result   // raw JSON string

// AFTER: converts to sorted global, returns "SORTED:tag"
Set result = "" For i=1:1:n { Set result = result _ $ZF(...readFn, "^ArnoKG", i) }
Kill ^ArnoKG("bfs_result")
// Convert to sorted global
Set tag = $Job _ "_bfs"
Kill ^ArnoKG("bfs_r", tag)
Set arr = ##class(%DynamicArray).%FromJSON(result)
Set n = arr.%Size()
For i = 0:1:(n-1) {
    Set item = arr.%Get(i)
    Set step = +item.%Get("step")
    Set o    = item.%Get("o")
    Set s    = item.%Get("s")
    Set p    = item.%Get("p")
    Set w    = +item.%Get("w")
    Set ^ArnoKG("bfs_r", tag, step, o) = $ListBuild(s, p, w)
}
Return "SORTED:" _ tag
```

### Engine change

Remove the `"CHUNKED:"` detection + reassembly block (~15 lines). The existing `"SORTED:"` handling already covers the Rust path after this change.

Add a short deprecation guard:
```python
if resp.startswith("CHUNKED:"):
    logger.warning("Received legacy CHUNKED BFS response — upgrade NKGAccel.cls")
    # fall through to ObjectScript fallback
    bfs_results = None
```

## Implementation Task Groups

### A. Tests (test-first)
1. Write `test_arno_bfs_unified_output.py` — Rust BFS returns same format as ObjectScript, unbounded + bounded

### B. NKGAccel.cls change
1. Update `BFSJson` to convert chunks → sorted global → return `"SORTED:tag"`
2. Compile, test

### C. Engine change
1. Remove `"CHUNKED:"` branch, add deprecation guard
2. Run full e2e regression

### D. Validate + publish
1. Full test suite, bump version
