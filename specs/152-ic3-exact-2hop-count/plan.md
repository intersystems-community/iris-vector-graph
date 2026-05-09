# Implementation Plan: IC3 Exact 2-Hop COUNT

**Branch**: `152-ic3-exact-2hop-count` | **Date**: 2026-05-07 | **Spec**: [spec.md](./spec.md)

## Summary

Add `^KG("deg2p_exact", src, pred)` — exact 2-hop distinct neighbor count, precomputed at `BuildNKG` time. Query-time lookup is O(1) `$Get` → <1ms. Precomputation implemented in Rust (`ffi_kg_build_2hop_exact`) for SF10 performance target (≤30s total BuildNKG). ObjectScript `Build2HopExactStats` calls Rust or falls back to a slow O(n×d²) scan for non-Rust deployments.

## Technical Context

**Language/Version**: Rust (arno workspace) + ObjectScript (IRIS 2025.1+) + Python 3.11
**Rust workspace**: `~/ws/arno/iris-integration/arno-callout/` — `kg_ffi.rs` + `lib.rs`
**IRIS target**: `iris-enterprise-2026` (port 4972), `libarno_callout.so` at `/tmp/`
**Testing**: LDBC SF10, seed `p_28587302384882` (1553 KNOWS, 37276 exact 2-hop)
**Performance targets**: Query <1ms p50; BuildNKG ≤30s (Rust), ≤90s (ObjectScript fallback)

## Constitution Check

**Principle II (Compatibility-First)**: ✅ `KHop2CountExact` falls back to `KHop2Count` when `^KG("deg2p_exact")` missing. All existing tests pass unchanged.

**Principle III (Test-First)**: ✅ Correctness test (vs `KHop2Count`) and perf test written before implementation.

**Principle IV (IRIS e2e)**:
- [x] Container: `iris-enterprise-2026` / `iris_vector_graph` (verified from `docker-compose.yml`)
- [x] Port: `4972` enterprise (LDBC data) / `1972` community (unit tests)

**Principle VI (Grounding)**:
- `ffi_kg_build_nkg` pattern at `kg_ffi.rs:1833` — exact implementation template
- `KG_BUILD_NKG_WRAPPER` exported in `lib.rs:358` — new function follows same registration
- Container: `iris-enterprise-2026` ← `docker-compose.enterprise-2026.yml`
- arno builder: `arno-builder` Docker image (32h old, freshly verified in spec 094)

## Phase 0: Research

### Decision Log

**D-001: Rust for precomputation (not ObjectScript)**
- Decision: `ffi_kg_build_2hop_exact` in Rust, called from `Build2HopExactStats` ObjectScript wrapper
- Rationale: 238M ops in ObjectScript ≈ 24s (unacceptable if added to current 19s BuildNKG).
  Rust HashMap dedup over integer-indexed nodes: ~5-8s. Total BuildNKG ≤27s.
- Pattern: directly follows `ffi_kg_build_nkg` pattern — reads `^KG("out",0,...)`, writes `^KG("deg2p_exact",...)`

**D-002: Separate global `^KG("deg2p_exact")` not merged into `deg2p`**
- Decision: New subscript key `^KG("deg2p_exact", src, pred)` alongside existing `^KG("deg2p", src, pred)`
- Rationale: `deg2p` (upper bound, 0.07ms) and `deg2p_exact` (exact, <0.1ms after spec) serve different use cases. Callers choose explicitly. No ambiguity.

**D-003: `execute_cypher` fast-path routes to KHop2CountExact**
- Decision: Update `_2HOP_COUNT_RE` routing in `_try_khop_fast_path` to call `KHop2CountExact` instead of `KHop2Count`
- Rationale: Users calling `MATCH (s)-[:P*2]->(n) RETURN count(n)` expect exact results. The fast path should deliver both correctness and speed.

**D-004: Rust algorithm**
- Algorithm: For each source `s`, for each predicate `p`:
  1. Collect hop-1 set: `H1 = {o : ^KG("out",0,s,p,o) exists}` as `HashSet<String>`
  2. For each mid ∈ H1, collect hop-2 candidates: `{o2 : ^KG("out",0,mid,p,o2) exists} \ H1 \ {s}`
  3. Union all hop-2 candidates into a dedup `HashSet<String>`, take len()
  4. Write `^KG("deg2p_exact", s, p) = len`
- Complexity: O(nodes × avg_degree²) time, O(max_degree) space per node (reuse HashSet)
- Expected: 5-8s on SF10 (vs 24s ObjectScript)

## Phase 1: Design

### New global structure

```
^KG("deg2p_exact", src, pred) = exact_count   // integer, distinct 2-hop neighbors via pred
```

### New ObjectScript methods (Traversal.cls)

```objectscript
ClassMethod KHop2CountExact(srcId As %String, pred As %String = "") As %Integer
{
    If pred '= "" {
        Set cnt = $Get(^KG("deg2p_exact", srcId, pred), -1)
        If cnt >= 0 Return +cnt
    }
    Return ..KHop2Count(srcId, pred)   // fallback
}

ClassMethod Build2HopExactStats() As %Integer
{
    Kill ^KG("deg2p_exact")
    // Try Rust path first
    If ##class(Graph.KG.ArnoAccel).IsAvailable() {
        Set dllid = $Get(^||ArnoAccel("dllid"), 0)
        If dllid > 0 {
            Set fid = $ZF(-4, 3, dllid, "kg_build_2hop_exact")
            If fid > 0 {
                Set result = $ZF(-5, dllid, fid)
                // result = {"nodes": N, "entries": M}
                Return +$Piece($Piece(result, "entries", 2), "}", 1) \ 1
            }
        }
    }
    // ObjectScript fallback (slow — O(n×d²), acceptable at build time)
    Set src = ""
    Set cnt = 0
    For {
        Set src = $Order(^KG("out", 0, src))
        Quit:src=""
        Set pred = ""
        For {
            Set pred = $Order(^KG("out", 0, src, pred))
            Quit:pred=""
            Kill ^||h2s
            Set ^||h2s(src) = ""
            Set mid = ""
            For { Set mid = $Order(^KG("out", 0, src, pred, mid)) Quit:mid="" Set ^||h2s(mid) = "" }
            Set exact = 0
            Set mid = ""
            For {
                Set mid = $Order(^KG("out", 0, src, pred, mid))
                Quit:mid=""
                Set o2 = ""
                For {
                    Set o2 = $Order(^KG("out", 0, mid, pred, o2))
                    Quit:o2=""
                    If '$Data(^||h2s(o2)) { Set ^||h2s(o2) = "" Set exact = exact + 1 }
                }
            }
            Kill ^||h2s
            If exact > 0 { Set ^KG("deg2p_exact", src, pred) = exact Set cnt = cnt + 1 }
        }
    }
    Return cnt
}
```

### New Rust function (kg_ffi.rs)

```rust
pub fn ffi_kg_build_2hop_exact() -> String {
    use crate::zf_global::{DatabaseOps, GlobalRef, IRISData, NameSpace};
    use std::collections::HashSet;

    let ns = match NameSpace::try_new("USER") { Ok(n) => n, Err(e) => return format!("...") };

    let mut src_root = GlobalRef::new("KG");
    src_root.push(IRISData::Text("out".into()));
    src_root.push(IRISData::Int(0));
    src_root.push(IRISData::Text(String::new()));

    let src_keys: Vec<String> = ns.keys(&src_root)
        .filter_map(|k| if let IRISData::Text(t) = k { Some(t) } else { None })
        .collect();

    let mut entries = 0i64;
    for s in &src_keys {
        let mut pred_root = ...;  // walk preds
        for p in pred_keys {
            // build H1
            let h1: HashSet<String> = hop1_neighbors(&ns, &s, &p);
            // build exact 2-hop dedup
            let mut h2: HashSet<String> = HashSet::new();
            for mid in &h1 {
                for o2 in hop1_neighbors(&ns, mid, &p) {
                    if !h1.contains(&o2) && o2 != *s { h2.insert(o2); }
                }
            }
            if !h2.is_empty() {
                let mut g = GlobalRef::new("KG");
                g.push(IRISData::Text("deg2p_exact".into()));
                g.push(IRISData::Text(s.clone()));
                g.push(IRISData::Text(p.clone()));
                let _ = ns.set(&g, IRISData::Int(h2.len() as i32));
                entries += 1;
            }
        }
    }
    format!("{{\"nodes\":{}, \"entries\":{}}}", src_keys.len(), entries)
}
```

### Engine additions

```python
def khop2_count_exact(self, node_id: str, predicate: str = "") -> int:
    KHop2Input(node_id=node_id)
    return int(self._iris_obj().classMethodValue(
        "Graph.KG.Traversal", "KHop2CountExact", node_id, predicate
    ))

def backfill_deg2p_exact(self) -> int:
    return int(self._iris_obj().classMethodValue(
        "Graph.KG.Traversal", "Build2HopExactStats"
    ))
```

Update `rebuild_nkg()` to call `Build2HopExactStats` after `BuildNKG`.

Update `_try_khop_fast_path` `_2HOP_COUNT_RE` to call `KHop2CountExact`.

## Implementation Task Groups

### A. Write failing tests (test-first)
- Correctness test: `KHop2CountExact == KHop2Count` for multiple seeds
- Perf test: `KHop2CountExact` < 1ms after `Build2HopExactStats`

### B. Rust implementation
- Add `ffi_kg_build_2hop_exact()` to `kg_ffi.rs`
- Register `kg_build_2hop_exact` in `lib.rs`
- Cross-build with `arno-builder`, deploy to enterprise container

### C. ObjectScript implementation
- Add `KHop2CountExact` and `Build2HopExactStats` to `Traversal.cls`
- Add `Build2HopExactStats` call in `BuildNKG` (after `Build2HopStats`)
- Compile, test

### D. Engine wiring
- `khop2_count_exact()` public method
- `backfill_deg2p_exact()` public method
- `rebuild_nkg()` calls `Build2HopExactStats`
- `_try_khop_fast_path` routes `[:P*2] RETURN count(n)` to `KHop2CountExact`

### E. Validation
- Run full e2e + unit tests
- Benchmark: IC3 COUNT before/after on SF10
