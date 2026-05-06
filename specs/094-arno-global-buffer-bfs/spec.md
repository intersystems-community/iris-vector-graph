# Spec 094: Arno BFSJson — Global-Buffer Transfer

**Feature Branch**: `094-arno-global-buffer-bfs`
**Created**: 2026-05-04
**Status**: Council-approved (2026-05-04) — ready to implement
**Cross-reference**: Spec 079 (Rust BFS), Spec 093 (Benchmark)
**Council review**: Dan Pasco, Tim Leavitt, Steve Morrison — 2026-05-04

## Problem

`NKGAccel.BFSJson` hits `<MAX $ZF STRING>` at 10K nodes / 50K edges on every depth and
every seed. Measured: 177–366ms of work ending in failure every call.

Root cause — line 476 of `NKGAccel.cls`:
```objectscript
Set adjStr = ..ExportAdjacencyFromSeed(seed, maxHops + 1, predicatesJson)
Return $ZF(-5, dllid, fnid, adjStr, seed, predicatesJson, maxHops, maxResults)
```

`$ZF(-5, ...)` argument strings are capped at ~32KB by IRIS. Per-seed adjacency for any
realistic seed on a 10K+ graph exceeds this at depth ≥2. Per-seed scoping (v1.65.4)
does not fix the transport bottleneck — the limit is in the `$ZF` argument channel.

## Prior Art in arno

`Graph.KG.ArnoAccelNKG.CacheNKGAdj()` already solves this for PageRank/PPR/WCC:

1. ObjectScript writes adjacency to `^ArnoKG("KG","nkg_adj",1..N)` in 32KB chunks
2. `$ZF` call passes only the global name (`"^ArnoKG"` — 9 chars)
3. Rust reads `^ArnoKG("KG","nkg_adj",N)` chunks via `read_nkg_adjacency()` in `kg_ffi.rs`

`kg_bfs_global` in `lib.rs` already has the right external signature:
```rust
pub fn kg_bfs_global(global_name: String, seed: String, predicates_json: String,
                     max_hops: i64, max_results: i64) -> String
```

But `ffi_kg_bfs_global` in `kg_ffi.rs` (line 1855) still treats the first argument as an
**inline adjacency string** passed via `$ZF`. The fix is to read from `^ArnoKG` chunks
instead — but with an important wire format constraint.

## Wire Format Compatibility (Council Clarification)

The BFS parser in `ffi_kg_bfs_global` (line 1892) uses `\x1f`-delimited string triplets:
```
node_256\x1fR\x1fnode_512\n
node_256\x1fR\x1fnode_128\n
```
Format: `src_string_id \x1f predicate_string \x1f dst_string_id \n`

`read_nkg_adjacency()` (line 980) reads `^ArnoKG("KG","nkg_adj",N)` chunks but stores
**integer adjacency** (`srcIdx:dstIdx1,dstIdx2\n`) — **incompatible format, no predicates**.

`read_nkg_adjacency_with_preds()` (line 1747) reads `^NKG(-1,sIdx,predEncoded,oIdx)`
directly — **not from ^ArnoKG chunks** (council-confirmed spec defect in first draft).

**Correct approach**: `WriteAdjToGlobal` writes the **same `\x1f`-delimited string format**
that the existing BFS parser already handles. Rust reads chunks from `^ArnoKG`, concatenates
into a single string, feeds the existing `\x1f` parser. **Zero change to BFS logic.**

## Solution — Three Changes

### Change 1: `kg_ffi.rs` — `read_nkg_adj_chunks_as_str`

New helper that reads `^ArnoKG("KG","nkg_adj",N)` chunks and returns the raw concatenated
string (variant of `read_nkg_adjacency` that skips deserialization):

```rust
fn read_nkg_adj_chunks_as_str(global_name: &str) -> Result<String, String> {
    let ns = NameSpace::try_new("USER")?;
    let gname = global_name.strip_prefix('^').unwrap_or(global_name);

    let mut g_root = GlobalRef::new(gname);
    g_root.push(IRISData::Text("KG".to_string()));
    g_root.push(IRISData::Text("nkg_adj".to_string()));

    let n_chunks: i32 = match ns.get(&g_root) {
        Ok(Some(IRISData::Int(n))) => n,
        Ok(Some(IRISData::Text(s))) => s.parse().unwrap_or(0),
        _ => return Err("^ArnoKG(KG,nkg_adj) not found".to_string()),
    };
    if n_chunks <= 0 { return Err("empty".to_string()); }

    let mut full = String::new();
    for i in 1..=n_chunks {
        let mut g_chunk = GlobalRef::new(gname);
        g_chunk.push(IRISData::Text("KG".to_string()));
        g_chunk.push(IRISData::Text("nkg_adj".to_string()));
        g_chunk.push(IRISData::Int(i));
        match ns.get(&g_chunk) {
            Ok(Some(IRISData::Text(s))) => full.push_str(&s),
            _ => return Err(format!("chunk {i} read failed")),
        }
    }
    Ok(full)
}
```

~15 lines. Extracted from `read_nkg_adjacency` — same chunk-reading logic, returns `String`
instead of deserializing.

### Change 2: `kg_ffi.rs` — `ffi_kg_bfs_global`

Replace the inline string parser with `read_nkg_adj_chunks_as_str`:

```rust
pub fn ffi_kg_bfs_global(
    global_name: String,   // "^ArnoKG" — global holding \x1f-delimited chunks
    seed: String,
    predicates_json: String,
    max_hops: i64,
    max_results: i64,
) -> String {
    let adj_str = match native_algos::read_nkg_adj_chunks_as_str(&global_name) {
        Ok(s) => s,
        Err(_) => return "[]".to_string(),
    };
    // Feed existing \x1f parser — zero change to BFS logic below this line
    // ... existing for line in adj_str.split('\n') { ... } loop unchanged
}
```

### Change 3: `Graph.KG.NKGAccel.cls` — `BFSJson` + `WriteAdjToGlobal`

Replace per-seed string export with chunked global write in `\x1f`-delimited format:

```objectscript
ClassMethod BFSJson(seed, predicatesJson="[]", maxHops=3, maxResults=0) As %String
{
    // ... load .so, get fnid — unchanged ...
    If fnid = 0 { Return ##class(Graph.KG.Traversal).BFSFastJson(seed, predicatesJson, maxHops) }

    Do ..WriteAdjToGlobal(seed, maxHops + 1, predicatesJson)
    If '$Data(^ArnoKG("KG", "nkg_adj")) {
        Return ##class(Graph.KG.Traversal).BFSFastJson(seed, predicatesJson, maxHops)
    }
    Return $ZF(-5, dllid, fnid, "^ArnoKG", seed, predicatesJson, maxHops, maxResults)
}

ClassMethod WriteAdjToGlobal(seed As %String, maxHops As %Integer, predsJson As %String) [ Private ]
{
    // BFS to collect reachable edges within maxHops, write as \x1f-delimited to ^ArnoKG chunks
    // Format per line: srcId \x1f predName \x1f dstId \n
    Kill ^ArnoKG("KG", "nkg_adj")
    Set chunkSize = 28000, chunk = "", chunkNum = 0

    // BFS frontier using ^NKG integer index for speed
    Kill ^||BFSAdj
    Set seedIdx = $Get(^NKG("$NI", seed), "")
    If seedIdx = "" Quit  // seed not in graph

    Set ^||frontier(seedIdx) = seed
    Set ^||seen(seedIdx) = ""

    For hop = 1:1:maxHops {
        Kill ^||nextFrontier
        Set sIdx = ""
        For {
            Set sIdx = $Order(^||frontier(sIdx))
            Quit:sIdx=""
            Set sId = ^||frontier(sIdx)
            Set predEncoded = ""
            For {
                Set predEncoded = $Order(^NKG(-1, sIdx, predEncoded))
                Quit:predEncoded=""
                Set predIdx = -predEncoded - 1
                Set predName = $Get(^NKG("$LS", predIdx), "R")
                // predicate filter
                If predsJson '= "[]" && predsJson '= "" {
                    // skip if predName not in predsJson
                }
                Set oIdx = ""
                For {
                    Set oIdx = $Order(^NKG(-1, sIdx, predEncoded, oIdx))
                    Quit:oIdx=""
                    If $Data(^||seen(oIdx)) Continue
                    Set oId = $Get(^NKG("$ND", oIdx), "node_"_oIdx)
                    Set line = sId_$Char(31)_predName_$Char(31)_oId_$Char(10)
                    If $Length(chunk) + $Length(line) > chunkSize {
                        Set chunkNum = chunkNum + 1
                        Set ^ArnoKG("KG", "nkg_adj", chunkNum) = chunk
                        Set chunk = ""
                    }
                    Set chunk = chunk _ line
                    Set ^||nextFrontier(oIdx) = oId
                    Set ^||seen(oIdx) = ""
                }
            }
        }
        Merge ^||frontier = ^||nextFrontier
        Kill ^||nextFrontier
    }
    If chunk '= "" {
        Set chunkNum = chunkNum + 1
        Set ^ArnoKG("KG", "nkg_adj", chunkNum) = chunk
    }
    Set ^ArnoKG("KG", "nkg_adj") = chunkNum
    Kill ^||frontier, ^||seen, ^||BFSAdj
}
```

Note: `$Char(31)` = `\x1f`. This matches the delimiter the existing Rust parser expects.

## Why ^ArnoKG (Not ^||)

`read_nkg_adj_chunks_as_str` reads `^ArnoKG` by that literal name (same as `read_nkg_adjacency`).
The pattern is established. `^ArnoKG("KG","nkg_adj")` is overwritten before each `$ZF(-5,...)`
call. No race: IRIS jobs are single-threaded, `$ZF(-5,...)` is synchronous.

## Acceptance Criteria

- **SC-001**: `NKGAccel.BFSJson` on dataset M (10K/50K, any seed, depths 2–4) returns correct results — no `<MAX $ZF STRING>`
- **SC-002**: Result set == `BFSFastJson` result set on same seed/depth (spec 093 correctness gate)
- **SC-003**: `[:R*1..3]` predicate-filtered BFS returns only R-labeled edges
- **SC-004**: Fallback to `BFSFastJson` when fnid = 0 (arno .so not loaded)
- **SC-005**: No regression on dataset S (1K/5K)

## What This Unlocks

Spec 093 benchmark SC-008/SC-009/SC-010 (currently BLOCKED) can run.
Spec 079 projection (<30ms at dataset M depth=3) can be validated for the first time.
