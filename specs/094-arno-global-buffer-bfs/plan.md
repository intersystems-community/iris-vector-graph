# Implementation Plan: Spec 094 — Arno BFSJson Global-Buffer Transfer

**Branch**: `094-arno-global-buffer-bfs` | **Date**: 2026-05-04 | **Spec**: `specs/094-arno-global-buffer-bfs/spec.md`

## Summary

Fix `NKGAccel.BFSJson` hitting `<MAX $ZF STRING>` on 10K+ node graphs by replacing the
inline `$ZF` argument string with a chunked global buffer. ObjectScript writes
`\x1f`-delimited adjacency lines to `^ArnoKG("KG","nkg_adj",N)` chunks; Rust reads them
via a new 15-line helper and feeds the existing BFS parser unchanged.

## Technical Context

**Languages**: ObjectScript (IRIS), Rust 1.75+  
**Primary Dependencies**:
- `arno/iris-integration/arno-callout/src/kg_ffi.rs` — Rust BFS implementation
- `iris_src/src/Graph/KG/NKGAccel.cls` — ObjectScript BFS entry point
- `^ArnoKG("KG","nkg_adj",N)` — existing IPC channel (used by PageRank/PPR)
- `rzf` crate — Rust `$ZF` callout macro framework
- `iris_callin.rs` / `zf_global.rs` — global read via callin API (already initialized)

**Storage**: `^NKG` (integer adjacency index), `^ArnoKG` (IPC buffer global)  
**Testing**: pytest + `iris-devtester`, `iris-vector-graph-enterprise` container (port 2972)  
**Target Platform**: IRIS 2026.1+ enterprise (Linux ARM64), `libarno_callout.so` required  
**Performance Goal**: BFS depth=3 on 10K/50K graph — no `<MAX $ZF STRING>`, result in <30ms  
**Constraints**: Must not break dataset S (1K/5K); fallback to `BFSFastJson` when arno unloaded  
**Scope**: 2 files changed (kg_ffi.rs + NKGAccel.cls), ~80 lines total

## Wire Format

ObjectScript `WriteAdjToGlobal` writes and Rust `ffi_kg_bfs_global` reads:

```
node_256\x1fR\x1fnode_512\n
node_256\x1fR\x1fnode_128\n
```

Format: `src_id \x1f(0x1F) pred_name \x1f dst_id \n`  
Chunk size: 28,000 chars (safe margin below IRIS 32KB string limit)  
Global: `^ArnoKG("KG","nkg_adj") = chunkCount`, `^ArnoKG("KG","nkg_adj",N) = chunk`

**Note**: This is the format the existing `ffi_kg_bfs_global` `\x1f`-parser already handles.
The only change is WHERE the string comes from (chunks vs `$ZF` arg).

## Architecture

### Before (broken at M scale)
```
BFSJson → ExportAdjacencyFromSeed → adjStr (>32KB) → $ZF(-5, ..., adjStr, ...) → <MAX $ZF STRING>
```

### After (fixed)
```
BFSJson → WriteAdjToGlobal → ^ArnoKG("KG","nkg_adj",1..N)
        → $ZF(-5, ..., "^ArnoKG", ...)
        → Rust: read_nkg_adj_chunks_as_str("^ArnoKG") → adjStr
        → existing \x1f parser → BFS → results
```

## File Structure

```
iris_src/src/Graph/KG/
└── NKGAccel.cls              MODIFY — BFSJson + new WriteAdjToGlobal method

arno/iris-integration/arno-callout/src/
└── kg_ffi.rs                 MODIFY — read_nkg_adj_chunks_as_str + ffi_kg_bfs_global

tests/
└── e2e/
    └── test_arno_bfs_global.py    NEW — integration test (enterprise container)
```

## Constitution Check

- [x] Named IRIS container: `iris-vector-graph-enterprise` (port 2972), managed by iris-devtester
- [x] e2e test phase: `test_arno_bfs_global.py` covers all 5 acceptance criteria
- [x] `SKIP_IRIS_TESTS` defaulting to `"false"` in new test file

## Phases

### Phase 1: Rust changes (kg_ffi.rs)
1. Add `read_nkg_adj_chunks_as_str(global_name: &str) -> Result<String, String>`
2. Modify `ffi_kg_bfs_global` — replace inline parser preamble with chunk reader call
3. Build arno with `cargo build` for aarch64-unknown-linux-gnu
4. Deploy new `libarno_callout.so` to enterprise container

### Phase 2: ObjectScript changes (NKGAccel.cls)
1. Add `WriteAdjToGlobal(seed, maxHops, predsJson)` private method
2. Modify `BFSJson` — replace `ExportAdjacencyFromSeed` call with `WriteAdjToGlobal`
3. Compile on enterprise container via iris-dev MCP

### Phase 3: Tests
1. Write `tests/e2e/test_arno_bfs_global.py`
2. Run against `iris-vector-graph-enterprise` (port 2972)
3. Verify all 5 SC pass

### Phase 4: Benchmark validation
1. Run spec 093 bench.py on enterprise (port 2972)
2. Confirm SC-008/009/010 now pass (arno path live, no MAXSTRING)
3. Record actual numbers in specs/093-arno-acceleration-benchmark/results.md
