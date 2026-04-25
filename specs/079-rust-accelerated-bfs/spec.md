# Feature Specification: Rust-Accelerated BFS via Arno/NKG

**Feature Branch**: `079-rust-accelerated-bfs`
**Created**: 2026-04-25
**Status**: Spec-reviewed (council 2026-04-25)

## Motivation

Current BFS (`Graph.KG.Traversal.BFSFastJson`) measured at **112–128ms p50** on 10K nodes / 50K edges, returning 6,300–6,600 results. The bottleneck is ObjectScript `$ORDER` loop overhead plus JSON serialization over the DBAPI wire — not the graph traversal itself (measured at ~20µs/result, consistent with ObjectScript string-key global iteration + JSON building).

Arno already reads `^NKG` integer adjacency via callin and runs PPR/PageRank/WCC in Rust (<5ms at same scale). Adding BFS to the same path eliminates the ObjectScript loop and Serde-based JSON serialization is ~10× faster. Target: **<30ms p50** for the same workload.

**Caching assumption**: `read_nkg_adjacency_auto` loads the entire graph into a Rust `Vec` on each call (no caching). At 10K/50K this adds ~2–5ms. The <30ms target assumes this load time is acceptable and excludes caching as future work.

## Architecture

### Current path (128ms)
```
execute_cypher → BFSFastJson (ObjectScript) → $ORDER ^NKG → builds JSON → DBAPI → Python parse
```

### New path with Arno (target <30ms)
```
execute_cypher → NKGAccel.BFSJson ($ZF shim) → kg_bfs_global() in Rust
              → read_nkg_adjacency_with_preds() via callin → BFS on (target, pred) pairs
              → compact JSON → DBAPI → Python parse
```

Fallback when Arno not loaded: unchanged `BFSFastJson` path.

## ^NKG Layout (Spec 028 definition)

```
^NKG(-1, sIdx, -(pIdx+1), oIdx) = weight   ← out-edges
^NKG(-2, oIdx, -(pIdx+1), sIdx) = weight   ← in-edges
^NKG("$NI", nodeId) = idx                  ← node string→int
^NKG("$ND", idx)    = nodeId               ← node int→string
^NKG("$LI", label)  = idx                  ← predicate string→int
^NKG("$LS", idx)    = label                ← predicate int→string
```

The predicate is encoded as `-(pIdx+1)` — negative to distinguish from node indices (positive). `read_nkg_adjacency_auto` (used by PPR) strips the predicate subscript and returns only `Vec<Vec<usize>>` (target indices). This is correct for PPR but **cannot support predicate-filtered BFS**.

**Spec 048 dependency**: Spec 079 explicitly depends on `^NKG(-1, sIdx, -(pIdx+1), oIdx)` layout being unchanged by spec 048. Spec 048 adds `^KG("out", 0, s, p, o)` shard routing to the string triple store — `^NKG` is the integer acceleration layer and has no shard subscript. This must be confirmed with the spec 048 author before task cutoff. If `^NKG` also gains a shard subscript, spec 079 requires a layout update.

## What Changes

### 1. `arno/iris-integration/arno-callout/src/kg_ffi.rs`

**New function: `read_nkg_adjacency_with_preds`** — returns `(Vec<String>, Vec<Vec<(usize, usize)>>)` where each inner tuple is `(target_idx, pred_idx)`. Walks `^NKG(-1, sIdx, -(pIdx+1), oIdx)` preserving the predicate index. Used for predicate-filtered BFS.

**New function: `ffi_kg_bfs_global(global_name, seed, predicates_json, max_hops, max_results)`**:
- If `predicates_json` is non-empty: calls `read_nkg_adjacency_with_preds()`, filters edges by `pred_idx` matching one of the requested predicates (resolved via `^NKG("$LI", pred)` callin lookup)
- If `predicates_json` is empty (all predicates): calls existing `read_nkg_adjacency_auto()` — no predicate info needed
- BFS using `std::collections::VecDeque`
- Honors `max_results` cap
- Returns `[{"s": src_id, "p": pred_name, "o": dst_id, "w": 1.0, "step": N}, ...]` — **matches `BFSFastJson` output format exactly** so Python consumer requires no changes

### 2. `arno/iris-integration/arno-callout/src/lib.rs`
New `#[rzf] pub fn kg_bfs_global(global_name, seed, predicates_json, max_hops, max_results) -> String` wrapping `ffi_kg_bfs_global`.

### 3. `Graph.KG.NKGAccel.cls`
New `BFSJson(seed, predicatesJson, maxHops, maxResults)` class method. Same `$ZF(-5)` pattern as `PPRNative`. Returns raw JSON string.

### 4. `Graph.KG.NKGAccel.Capabilities`
Add `"bfs": true` to the capabilities JSON.

### 5. `iris-vector-graph/iris_vector_graph/engine.py`
In `_execute_var_length_cypher`: after `_detect_arno()`, if `"bfs"` in capabilities, call `NKGAccel.BFSJson` passing predicate list from `vl["types"]`. Parse result as `bfs_results` list. Fall back to `BFSFastJson` on any error.

## Output Format

Rust `ffi_kg_bfs_global` must return the same JSON structure as `BFSFastJson`:
```json
[{"s": "node_a", "p": "BINDS", "o": "node_b", "w": 1.0, "step": 1}, ...]
```
`"s"` and `"w"` are included even though no current Python caller reads them — matching the format exactly avoids silent breakage if callers are added.

## Profiling Data (Baseline)

BFSFastJson at 10K/50K, 6,300–6,600 results: **128ms p50**.
At ~20µs/result, the breakdown is consistent with:
- ^NKG $ORDER traversal: ~40ms (6,300 nodes × ~6µs/node for ObjectScript global iteration)
- JSON construction in ObjectScript: ~60ms (6,300 JSON objects via %DynamicArray)
- DBAPI wire + Python parse: ~28ms

Rust path projection:
- `read_nkg_adjacency_with_preds` load: ~3ms (50K edges into Vec)
- BFS on Vec<Vec<(usize,usize)>>: ~1ms
- Serde JSON serialization of 6,300 results: ~2ms
- DBAPI wire + Python parse: ~15ms (same network, smaller JSON)
- **Total projected: ~21ms** → comfortably under 30ms target

## Acceptance Criteria

- **SC-001**: BFS 1..3 hops on 10K/50K graph: p50 < 30ms (baseline: 128ms)
- **SC-002**: Arno BFS result set identical to ObjectScript BFS result set (same node IDs, same steps)
- **SC-003**: `[:BINDS*1..3]` returns only BINDS-reachable nodes (predicate-aware adjacency required)
- **SC-004**: `LIMIT N` in Cypher honored via `max_results` parameter
- **SC-005**: Fallback to `BFSFastJson` transparent when Arno not loaded
- **SC-006**: `^NKG` not populated → falls back gracefully (no error surfaced to caller)
- **SC-007**: Output format matches `BFSFastJson` exactly (all five fields: s, p, o, w, step)

## Clarifications (Council 2026-04-25)

- **Predicate encoding**: `read_nkg_adjacency_auto` (used by PPR) collapses all predicates and cannot support SC-003. A new `read_nkg_adjacency_with_preds()` returning `Vec<Vec<(usize, usize)>>` is required. The no-predicate path reuses `read_nkg_adjacency_auto`.
- **^NKG layout stability**: Spec 079 explicitly depends on `^NKG(-1, sIdx, -(pIdx+1), oIdx)` being unchanged by spec 048. Spec 048 only modifies `^KG("out", 0, ...)` — `^NKG` is unaffected. Confirmed: no blocking dependency.
- **Output format**: Must match `BFSFastJson` exactly including `"s"` and `"w"` fields.
- **Caching**: `read_nkg_adjacency_auto` is not cached; load time excluded from <30ms target. Caching is future work.
