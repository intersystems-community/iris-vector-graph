# Spec 096: Large-Output Chunked Read for NKGAccel Methods

**Feature Branch**: `096-large-output-chunked-read`
**Created**: 2026-05-04
**Status**: Revised after P0 crash fix — council-approved Option C
**Council review**: Dan Pasco, Tim Leavitt, Steve Morrison — 2026-05-04 (revised)

## Root Cause (Updated)

`$ZF(-5)` generated wrapper code in `rzf_codegen_utils/fn_wrapping.rs` line 62 had no
bounds check before `copy_nonoverlapping`. When `ret_string.len() > IRIS_MAXSTRLEN (32767)`,
the copy writes past the end of the output buffer, corrupting IRIS process memory and
causing a crash. This is undefined behavior — not graceful truncation.

**P0 fix applied**: `fn_wrapping.rs` now clamps at `IRIS_MAXSTRLEN` before copy. This makes
oversized returns truncate instead of crash. Filed against rzf-main.

## Revised Architecture (Option C)

`StoreLargeOut` in ObjectScript cannot fix the root problem because ObjectScript only sees
the `$ZF` return value AFTER the damage is done — the overflow crashes before returning.

The correct architecture moves chunking BEFORE the `$ZF` boundary:

```
BFSJson → BFSComputeToGlobal (new $ZF entry) → writes chunks to ^ArnoKG("bfs_result",N)
                                               → returns "CHUNKED:BFS:N" (always short)
        → ObjectScript reads chunks via BFSReadChunk (existing pattern)
```

No large strings ever cross the `$ZF(-5)` output boundary.

### Two new $ZF entry points in arno (`kg_ffi.rs` + `iris_zf_wrapper.c`)

**`kg_bfs_compute`** — runs BFS, writes result to `^ArnoKG("bfs_result",N)` chunks via
callin `IrisSet`, returns `"CHUNKED:BFS:N"` or inline JSON if ≤ `IRIS_MAXSTRLEN - 100` chars:

```rust
pub fn ffi_kg_bfs_compute(
    adj_global: String,   // "^ArnoKG" — adjacency chunks
    result_global: String, // "^ArnoKG" — same global, different subscript
    seed: String,
    predicates_json: String,
    max_hops: i64,
    max_results: i64,
) -> String {
    let (nodes, out_adj) = match native_algos::read_kg_adjacency_auto(&adj_global) {
        Ok(data) => data,
        Err(_) => return "[]".to_string(),
    };
    let adj_with_preds: Vec<Vec<(usize, usize)>> = out_adj.iter()
        .map(|nbrs| nbrs.iter().map(|&dst| (dst, 0usize)).collect())
        .collect();
    let results = native_algos::bfs_on_adj(&nodes, &adj_with_preds,
        &std::collections::HashSet::new(), &seed,
        max_hops as usize, max_results as usize);
    let json = serde_json::to_string(&results).unwrap_or_else(|_| "[]".to_string());

    // Inline if safe; chunk to global if large
    if json.len() < rzf::IRIS_MAXSTRLEN - 100 {
        return json;
    }
    match write_result_chunks(&json, &result_global, "bfs_result") {
        Ok(n) => format!("CHUNKED:BFS:{n}"),
        Err(_) => json[..rzf::IRIS_MAXSTRLEN - 100].to_string(),
    }
}
```

**`write_result_chunks`** — writes JSON to `^global("subscript",N)` chunks via raw callin
`IrisSet`. This is safe: raw `IrisSet` is a direct kernel call (same as `IrisGet` used by
`read_nkg_adjacency`); the crash seen earlier was from the `rzf NameSpace::set` wrapper
which does extra namespace/transaction work. Using the raw `IrisSet` pointer from
`store_iris_pointers` avoids the wrapper.

**`kg_bfs_read_chunk`** (existing `ReadLargeOutChunk` pattern, new $ZF entry) — reads
chunk N from `^ArnoKG("bfs_result",N)`. Returns chunk or `""`.

**ObjectScript `BFSJson` (revised)**:
```objectscript
ClassMethod BFSJson(...) As %String
{
    // ... load .so, fnid check, CacheNKGAdj — unchanged ...
    Kill ^ArnoKG("bfs_result")
    Set raw = $ZF(-5, dllid, compute_fnid, "^ArnoKG", "^ArnoKG",
                   seed, predicatesJson, maxHops, maxResults)
    If $Extract(raw, 1, 8) = "CHUNKED:" {
        Set n = $Piece(raw, ":", 3)
        Set result = ""
        For i = 1:1:n {
            Set chunk = $ZF(-5, dllid, read_fnid, "^ArnoKG", i)
            Set result = result _ chunk
        }
        Return result
    }
    Return raw
}
```

## Why Raw IrisSet is Safe (Not rzf NameSpace::set)

`store_iris_pointers` (called from `GetZFTable` at IRIS startup) stores pointers to
`IrisGet`, `IrisSet`, `IrisOrder`, etc. These are the same kernel functions used by the
`iris session` callin API. The `rzf NameSpace::set` wrapper crashes because it calls
`IrisExecW` for namespace switching, which conflicts with the active callout context.
Using the stored `IrisSet` pointer directly (same as `IrisGet` in `read_nkg_adjacency`)
is safe.

## Methods covered

| Method | Output path | Change |
|--------|------------|--------|
| `BFSJson` (arno) | Option C — two $ZF calls | Two new $ZF entry points |
| `PPRJson` | ObjectScript `StoreLargeOut` | Already correct |
| `KHopJson` | ObjectScript `StoreLargeOut` | Already correct |
| `RandomWalkJson` | ObjectScript `StoreLargeOut` | Already correct |

`PPRJson`, `KHopJson`, `RandomWalkJson` do NOT go through `$ZF(-5)` — they run in pure
ObjectScript. `StoreLargeOut` for those methods is correct and working.

## Acceptance Criteria

- **SC-001**: `BFSJson` depth=3 on M (10K/50K) returns all reachable nodes, no crash
- **SC-002**: `PPRNative` returns at least 1 score (StoreLargeOut working)
- **SC-003**: `RandomWalkJson` 10 walks × 20 steps returns 10 walks
- **SC-004**: Inline path (≤ IRIS_MAXSTRLEN - 100 chars) returns JSON directly — no read loop
- **SC-005**: `_call_classmethod_large` assembles chunked BFS correctly
- **SC-006**: Two connections do NOT share `^||LargeOut` (process-private for PPR/KHop/RW)
- **SC-007**: rzf P0 crash fix: strings > IRIS_MAXSTRLEN truncate instead of crashing IRIS

## Known Gaps

- `BFSFastJson` (pure ObjectScript fallback) DBAPI truncation — separate problem, deferred
- Predicate-filtered BFS via arno — collapsed adjacency has no pred info; falls back to BFSFastJson

## Dependencies

Spec 094 (arno BFS) + rzf P0 fix (applied).


**Feature Branch**: `096-large-output-chunked-read`
**Created**: 2026-05-04
**Status**: Draft — pending council review
**Cross-reference**: Spec 094 (arno BFS global buffer), Spec 093 (benchmark)
**Council review**: Dan Pasco, Tim Leavitt, Steve Morrison — 2026-05-04

## Problem

`classMethodString` / `classMethodValue` over the IRIS DBAPI has a **~9535-char output
limit**. Any `NKGAccel` method that returns O(graph-size) JSON silently truncates at that
boundary, producing malformed JSON or data loss.

### Affected methods (complete catalog)

| Method | Output bound | Risk at M (10K/50K) | Current workaround |
|--------|-------------|--------------------|--------------------|
| `BFSJson` | O(edges in BFS subgraph) | depth≥2: 3583 nodes × ~57 chars = 204KB | **50-result hard cap — silent data loss** |
| `BFSFastJson` | O(edges in BFS subgraph) | depth≥2: hits 9535 | None — truncates to malformed JSON |
| `PPRNative` | O(nodes) | 10K nodes × ~30 chars = 300KB | topK=20 cap — silent data loss |
| `KHopNeighbors` | O(nodes) | max_nodes=1000 × ~30 chars = 30KB | Marginal — near limit |
| `RandomWalkJson` | O(walks × steps × id_len) | No max param — 100 walks × 100 steps × "node_9999" = 1.8MB | No protection at all |

The `BFSFastJson` path (ObjectScript, not `$ZF`) also hits the limit via `classMethodString`
but for different reasons — the full 3.6MB IRIS string fits in memory but DBAPI truncates it.

**All five methods have the same root cause**: DBAPI output cap. The fix must be uniform.

## Prior Art in IVG

`Graph.KG.Snapshot.ReadFileChunk(filePath, offset, chunkSize)` is already the established
IVG pattern for large output:

```python
size_raw = _call_classmethod(conn, "Graph.KG.Snapshot", "GetFileSize", path)
file_size = int(size_raw)
chunks = []
offset = 0
while offset < file_size:
    chunk = _call_classmethod(conn, "Graph.KG.Snapshot", "ReadFileChunk", path, offset, 512*1024)
    chunks.append(str(chunk))
    offset += 512 * 1024
result = "".join(chunks)
```

This pattern works because:
1. Each `ReadFileChunk` call returns ≤512KB (well under DBAPI limit if chunk_size is set correctly)
2. Chunks are reassembled Python-side — no IRIS memory pressure
3. The persistent connection (same IRIS job) keeps the file accessible between calls

The spec 096 solution uses the same pattern, but with `^||BFSResult` (process-private
global) instead of a filesystem file.

## Solution: Option D — Two-Call Chunked Read

### Design

**Phase 1 — Compute + store**:  
Any large-output `NKGAccel` method computes its result, and if the JSON exceeds 9000 chars:
1. Writes JSON chunks to `^||LargeOut(methodTag, chunkNum)` process-private global
2. Returns `"CHUNKED:TAG:N"` (where TAG is the method tag and N = chunk count)

If the result is ≤9000 chars, returns inline JSON as today (no second call needed).

**Phase 2 — Read** (only if Phase 1 returned `"CHUNKED:TAG:N"`):  
Python calls `NKGAccel.ReadLargeOutChunk(tag, chunkNum)` in a loop to reassemble.

**ObjectScript side** — shared infrastructure added to `NKGAccel.cls`:
```objectscript
ClassMethod StoreLargeOut(tag As %String, json As %String) As %String [ Private ]
{
    If $Length(json) <= 9000 { Return json }
    Kill ^||LargeOut(tag)
    Set chunkSize = 9000, pos = 1, n = 0
    While pos <= $Length(json) {
        Set n = n + 1
        Set ^||LargeOut(tag, n) = $Extract(json, pos, pos + chunkSize - 1)
        Set pos = pos + chunkSize
    }
    Return "CHUNKED:" _ tag _ ":" _ n
}

ClassMethod ReadLargeOutChunk(tag As %String, chunkNum As %Integer) As %String
{
    Return $Get(^||LargeOut(tag, chunkNum), "")
}
```

Each large-output method simply wraps its return:
```objectscript
Return ..StoreLargeOut("BFS", resultJson)
Return ..StoreLargeOut("PPR", resultJson)
Return ..StoreLargeOut("KHOP", resultJson)
```

### Why `^||LargeOut` (process-private, not `^ArnoKG`)

- `^||` process-private globals are per-IRIS-job, not per-process or per-thread
- The Python DBAPI holds one persistent connection → one IRIS job → `^||` accessible
  across all `classMethodString` calls on the same `conn` object
- Auto-cleaned on job end — no manual cleanup needed
- Keyed by `tag` (`"BFS"`, `"PPR"`, etc.) so multiple large outputs don't collide
- This is exactly the same guarantee `Snapshot.ReadFileChunk` relies on

### Chunk size: 9000 chars

- 9000 chars per chunk stays safely under the 9535-char DBAPI output limit
- A 4-hop BFS returning 6000 nodes × 57 chars = 342KB → ~38 chunk reads
- Each `classMethodString` call is ~0.1ms → 38 × 0.1ms = 3.8ms reassembly overhead
- Acceptable: reassembly is O(n_chunks) network round-trips, each tiny

## Methods to update

### Priority 1 — immediate data loss risk
- `BFSJson` — remove 50-result cap, wrap with `StoreLargeOut("BFS", ...)`
- `PPRNative` — remove topK cap from the output path, wrap with `StoreLargeOut("PPR", ...)`

### Priority 2 — latent risk
- `KHopNeighbors` — wrap with `StoreLargeOut("KHOP", ...)`
- `RandomWalkJson` — add `maxResults` param + wrap with `StoreLargeOut("RW", ...)`

### Out of scope
- `BFSFastJson` — pure ObjectScript, no `$ZF` involved. Its limit is IRIS `$MAXSTRING`
  (3.6MB), not DBAPI. That's a separate problem already tracked. Not changed here.

## Python-side changes

### `IRISGraphEngine._arno_call` — upgrade existing entry point

`_arno_call` already exists in `engine.py` (line 5321) and is the single entry point for
all `NKGAccel` calls. Update it to handle CHUNKED responses transparently:

```python
def _arno_call(self, cls: str, method: str, *args) -> str:
    raw = str(self._iris_obj().classMethodValue(cls, method, *args))
    if not raw.startswith("CHUNKED:"):
        return raw
    _, tag, n_str = raw.split(":", 2)
    n = int(n_str)
    iris_obj = self._iris_obj()
    return "".join(
        str(iris_obj.classMethodValue(cls, "ReadLargeOutChunk", tag, i))
        for i in range(1, n + 1)
    )
```

No new method name. No scattered call sites. All existing `self._arno_call(...)` callers
get chunked-read transparently — zero diff in business code.

### `iris_vector_graph/schema.py` — `_call_classmethod_large` (for non-engine callers)

For callers outside `IRISGraphEngine` (benchmarks, tests) that call `NKGAccel` directly:

```python
def _call_classmethod_large(iris_obj, cls: str, method: str, *args) -> str:
    raw = str(iris_obj.classMethodValue(cls, method, *args))
    if not raw.startswith("CHUNKED:"):
        return raw
    _, tag, n_str = raw.split(":", 2)
    n = int(n_str)
    return "".join(
        str(iris_obj.classMethodValue(cls, "ReadLargeOutChunk", tag, i))
        for i in range(1, n + 1)
    )
```

Tag is extracted **from the response** (`"CHUNKED:BFS:43"` → tag=`"BFS"`), not derived
from the method name. No fragile `method[:3]` hack.

All existing `classMethodString("Graph.KG.NKGAccel", ...)` calls in `engine.py` become
`self._nkgaccel(...)`. No other call site is affected.

### `iris_vector_graph/schema.py` — `_call_classmethod_large` (optional, for non-engine callers)

For callers outside `IRISGraphEngine` (benchmarks, tests) that need the same behaviour:

```python
def _call_classmethod_large(conn, cls, method, iris_obj, *args):
    raw = str(iris_obj.classMethodString(cls, method, *args))
    if not raw.startswith("CHUNKED:"):
        return raw
    n = int(raw[8:])
    return "".join(
        str(iris_obj.classMethodString(cls, "ReadLargeOutChunk", method[:3], i))
        for i in range(1, n + 1)
    )
```

Wait — `ReadLargeOutChunk(tag, chunkNum)` — tag is `"BFS"` not the full method name.
Use the first 3 chars as tag, or pass tag explicitly. Tag = first 3 chars of method name
is fragile. Better: the method returns `"CHUNKED:TAG:N"` where TAG is explicit.

**Revised sentinel format**: `"CHUNKED:BFS:43"` — unambiguous.

**Tag constraint**: Tags must be alphanumeric only, no colons. Current tags: `BFS`, `PPR`,
`KHOP`, `RW`. The Python helper splits on `:` exactly twice (`split(":", 2)`) — any colon
in the tag would corrupt the parse.

### `StoreLargeOut` revised:
```objectscript
ClassMethod StoreLargeOut(tag As %String, json As %String) As %String [ Private ]
{
    If $Length(json) <= 9000 { Return json }
    Kill ^||LargeOut(tag)
    Set chunkSize = 9000, pos = 1, n = 0
    While pos <= $Length(json) {
        Set n = n + 1
        Set ^||LargeOut(tag, n) = $Extract(json, pos, pos + chunkSize - 1)
        Set pos = pos + chunkSize
    }
    Return "CHUNKED:" _ tag _ ":" _ n
}
```

### Python helper (revised):
```python
def _call_classmethod_large(iris_obj, cls, method, *args):
    raw = str(iris_obj.classMethodString(cls, method, *args))
    if not raw.startswith("CHUNKED:"):
        return raw
    _, tag, n_str = raw.split(":", 2)
    n = int(n_str)
    return "".join(
        str(iris_obj.classMethodString(cls, "ReadLargeOutChunk", tag, i))
        for i in range(1, n + 1)
    )
```

## Caller impact

**Engine callers** (`engine.py`): No change needed — all route through `self._arno_call()`
which is updated in place. Zero diff in business code.

**External callers** (`tests/benchmarks/bench.py`, `tests/e2e/test_arno_bfs_global.py`):
Switch from direct `iris_obj.classMethodString("Graph.KG.NKGAccel", ...)` to
`_call_classmethod_large(iris_obj, "Graph.KG.NKGAccel", ...)` from `schema.py`.

## Acceptance Criteria

- **SC-001**: `BFSJson` depth=3 on M (10K/50K) returns all reachable nodes (≥3000 distinct),
  not capped at 50
- **SC-002**: `PPRNative` on 10K-node graph returns all node scores, not capped at topK=20
- **SC-003**: `RandomWalkJson` with 10 walks × 20 steps returns 200 path entries
- **SC-004**: Inline path (≤9000 char result) returns JSON directly — no second call needed
- **SC-005**: `_call_classmethod_large` transparently handles both inline and chunked
- **SC-006**: Two calls on different `conn` objects do NOT share `^||LargeOut` data
  (process-private guarantee — test by verifying chunk disappears after `conn.close()`)
- **SC-007**: Removing 50-result cap from `BFSJson` does not break `test_arno_bfs_global.py`
  (tests must be updated to expect full result counts)

## Out of Scope

- `BFSFastJson` MAXSTRING fix — separate problem, tracked separately
- Streaming results (AsyncIO) — out of scope, single-call semantics preserved
- Multi-connection concurrency safety — `^||` is per-job by design; concurrent jobs
  have independent `^||LargeOut` namespaces

## Known Gaps (out of scope for 096)

- **`BFSFastJson` DBAPI truncation at M scale when arno not loaded**: When arno is not
  available, `BFSJson` falls back to `BFSFastJson`. At M scale depth≥2, `BFSFastJson`
  returns 342KB which DBAPI truncates to malformed JSON at the 9535-char boundary. This
  is a pre-existing gap. After 094 ships, `BFSFastJson` is only the fallback path (arno
  handles M scale). Fixing `BFSFastJson` itself requires streaming from ObjectScript —
  a completely different approach. Tracked separately.

## Dependency

Spec 094 (arno BFS) must be merged first — spec 096 removes the 50-result cap that
spec 094 introduced as a temporary workaround.
