# Spec 097: Lazy Node Name Resolution in Arno Algorithms

**Feature Branch**: `097-lazy-node-resolution`
**Created**: 2026-05-04
**Status**: Draft — ready for implementation
**Location**: `arno/iris-integration/arno-callout/src/kg_ffi.rs`
**Council pre-approved pattern** — generalizes a confirmed bottleneck

---

## Context: IRIS Globals Are In-Memory

IRIS globals (`^NKG`, `^KG`, `^ArnoKG`) live in IRIS's global buffer cache —
hot data is memory-resident. Raw `IrisGet` speed inside `$ZF(-5)` context:

```
Measured: 8.8 million reads/sec (enterprise IRIS, ^NKG, hot cache)
```

At that rate, pre-loading 500K node names takes ~57ms — not 50s as originally estimated.
The bottleneck was the **Python DBAPI RPC overhead** (0.1ms/call) when calling from
outside IRIS, not the global read speed itself.

This changes the framing: `NodeResolver` is not about avoiding slow I/O. It is about
**not doing work that is not needed** — a correctness-of-design principle, not a
performance emergency.

---

## Problem: Eager Resolution Does Unnecessary Work

Every arno algorithm currently pre-loads ALL node names before running:

```
read_nkg_adjacency()
  → parse adjacency chunks (fast, sequential)
  → read_node_dictionary(ns, n)   ← resolves ALL n nodes upfront
      8.8M reads/sec inside $ZF → 57ms for 500K nodes
  → algorithm runs on (nodes: Vec<String>, adj)
  → output uses nodes[idx] for string names
```

**The waste:** BFS returning 50 results from a 500K-node graph pays to name all 500K
nodes. PPR on a 10K-node graph pays to name all 10K nodes before iterating. WCC labels
all components by name before returning only the top-k.

**Why this still matters at scale:**

| Graph | Nodes | Pre-load @8.8M/sec | Result set | Needed reads |
|-------|-------|-------------------|------------|-------------|
| XL BFS max=50 | 500K | 57ms | 50 results | **51 reads** |
| XL PPR | 500K | 57ms | 500K scores | 500K reads (same, unavoidable) |
| LDBC SF10 | ~3M | **340ms** | k results | k+1 reads |
| LDBC SF100 | ~30M | **3.4s** | k results | k+1 reads |

At SF10+ (30M nodes), the pre-load is 3.4 seconds of pure overhead even when the
algorithm returns only 50 results. This is wasteful regardless of storage speed.

**Seed lookup waste:** `nodes.iter().position(|s| s == seed)` is an O(n) linear scan.
`^NKG("$NI", seed)` gives the index in one read. This is an O(n) → O(1) improvement
regardless of read speed.

---

## Solution: `NodeResolver` — Lazy On-Demand Resolution

Replace `Vec<String>` with `NodeResolver` — a demand-paged name cache backed by
IRIS globals. Resolution happens when output is produced, not when the graph loads.

### Core type

```rust
pub struct NodeResolver {
    cache: HashMap<usize, String>,
    ns: NameSpace,
    n: usize,
}

impl NodeResolver {
    /// Zero-cost construction — no reads on creation.
    pub fn new(ns: NameSpace, n: usize) -> Self {
        Self { cache: HashMap::new(), ns, n }
    }

    /// Resolve integer index → string name. Reads ^NKG("$ND", idx) on cache miss.
    pub fn name(&mut self, idx: usize) -> &str {
        self.cache.entry(idx).or_insert_with(|| {
            let mut g = GlobalRef::new("NKG");
            g.push(IRISData::Text("$ND".to_string()));
            g.push(IRISData::Int(idx as i32));
            match self.ns.get(&g) {
                Ok(Some(IRISData::Text(s))) => s,
                _ => format!("{idx}"),
            }
        })
    }

    /// Find index for seed string. Reads ^NKG("$NI", seed) — ONE read, O(1).
    pub fn lookup_seed(&self, seed: &str) -> Option<usize> {
        let mut g = GlobalRef::new("NKG");
        g.push(IRISData::Text("$NI".to_string()));
        g.push(IRISData::Text(seed.to_string()));
        match self.ns.get(&g) {
            Ok(Some(IRISData::Int(i))) => Some(i as usize),
            Ok(Some(IRISData::Text(s))) => s.parse().ok(),
            _ => None,
        }
    }

    pub fn len(&self) -> usize { self.n }
}
```

### What changes

| Current | With NodeResolver | Reads for BFS(max=50) |
|---------|-----------------|----------------------|
| `read_node_dictionary(ns, n)` at load | `NodeResolver::new(ns, n)` | **0 → 0** |
| `nodes.iter().position(seed)` O(n) scan | `resolver.lookup_seed(seed)` 1 read | **n → 1** |
| `nodes[idx]` × n pre-loaded | `resolver.name(idx)` × result_count | **n → result_count** |

For BFS returning k results: **n reads → k+1 reads**.  
For PPR (all n scores): same n reads, spread across computation not blocked upfront.  
For WCC/CDLP top-k: n → k reads.

---

## Algorithm Signature Changes

All `*_on_adj` functions change `nodes: &[String]` → `resolver: &mut NodeResolver`:

```rust
// Before
pub fn bfs_on_adj(
    nodes: &[String],
    adj: &[Vec<(usize, usize)>],
    ...
) -> Vec<serde_json::Value>

// After  
pub fn bfs_on_adj(
    resolver: &mut NodeResolver,
    adj: &[Vec<(usize, usize)>],
    ...
) -> Vec<serde_json::Value>
```

Internal: `nodes.iter().position(seed)` → `resolver.lookup_seed(seed)?`  
Internal: `nodes[src]`, `nodes[dst]` → `resolver.name(src)`, `resolver.name(dst)`

Same change applies to: `ppr_on_adj`, WCC, CDLP, random walk, subgraph.

---

## `read_nkg_adjacency` changes

```rust
// Before: returns (Vec<String>, Vec<Vec<usize>>)
// After:  returns (NodeResolver, Vec<Vec<usize>>)

pub fn read_nkg_adjacency() -> Result<(NodeResolver, Vec<Vec<usize>>), String> {
    // ... read + parse chunks (unchanged) ...
    let (n, adj) = parse_nkg_adjacency_string(&full)?;
    let resolver = NodeResolver::new(ns, n);   // was: read_node_dictionary(ns, n)
    Ok((resolver, adj))
}
```

---

## The General Principle

`NodeResolver` is the canonical pattern for any arno function that:

1. Operates on integer-indexed adjacency
2. Needs string names only in **output**, not during computation
3. Does not benefit from having all n names available simultaneously

All future `*_on_adj` functions should take `resolver: &mut NodeResolver`, not
`nodes: &[String]`. This is the architectural standard going forward.

**Exception:** algorithms that need ALL node names during internal computation
(e.g., label propagation that hashes on string labels) may pre-populate the cache
explicitly: `for i in 0..n { resolver.name(i); }` — same total reads, same speed,
but explicit rather than hidden.

---

## Performance

| Graph | BFS max=50 pre-load | BFS max=50 after 097 | PPR (all nodes) |
|-------|--------------------|--------------------|-----------------|
| M 10K | 57µs | **0µs + 51 reads** | same 57µs, no blocking |
| XL 1M | 57ms | **0µs + 51 reads** | 57ms, no blocking |
| SF10 30M | 340ms | **0µs + 51 reads** | 340ms, no blocking |
| SF100 300M | 3.4s | **0µs + 51 reads** | 3.4s, no blocking |

Note: at 8.8M reads/sec inside `$ZF`, "51 reads" = ~6µs regardless of graph size.

---

## Files Changed

| File | Change |
|------|--------|
| `kg_ffi.rs` `native_algos` mod | Add `NodeResolver` struct (+50 lines) |
| `kg_ffi.rs` `read_nkg_adjacency` | Return `NodeResolver` not `Vec<String>` |
| `kg_ffi.rs` `bfs_on_adj` | `nodes` → `resolver`, 3 line changes |
| `kg_ffi.rs` `ppr_on_adj` | Same |
| `kg_ffi.rs` WCC, CDLP, random walk, subgraph | Same each |
| `kg_ffi.rs` `ffi_kg_bfs_compute`, `ffi_kg_bfs_global` | Pass `&mut resolver` |
| `kg_ffi.rs` `ffi_kg_ppr_global`, WCC/CDLP entries | Same |

~90 lines changed. Zero new infrastructure.

---

## Acceptance Criteria

- **SC-001**: `ffi_kg_bfs_compute` at XL (1M/10M) cold start completes in < 500ms
  (was timing out at ~50s from Python DBAPI path)
- **SC-002**: `NodeResolver.lookup_seed` makes exactly 1 callin read regardless of graph size
- **SC-003**: BFS with `max_results=50` makes ≤ 52 callin reads total
- **SC-004**: All existing arno unit tests pass — no regression on S/M/L datasets
- **SC-005**: PPR returns identical results before and after (no data loss from lazy resolution)
- **SC-006**: `NodeResolver` cache dedups: same index never read twice

---

## LDBC Scale Viability (After 097)

With `NodeResolver` + `BulkIngestEdges` (spec 096/engine):

| Dataset | Edges | Ingest time | BFS cold | BFS hot |
|---------|-------|------------|---------|---------|
| XL (1M/10M) | 10M | 71s | < 500ms | < 1ms |
| SF0.1 (0.3M/1.7M) | 1.7M | ~13s | < 100ms | < 1ms |
| **SF1 (3.18M/17M)** | **17M** | **~2min** | **< 1s** | **< 1ms** |
| SF10 (30M/177M) | 177M | ~22min | ~340ms cold | < 1ms |

SF1 is fully viable on the current enterprise container (128GB RAM, IRIS global cache).
SF10 requires more RAM or global cache tuning but is architecturally unblocked.

**No new spec needed for SF1** — 097 + 096 + 094 + the existing IVG stack is sufficient.
The LDBC SNB data loader (mapping LDBC CSV → IVG edges) is a one-day engineering task,
not a new architectural spec.


**Feature Branch**: `097-lazy-node-resolution`
**Created**: 2026-05-04
**Status**: Draft — ready for implementation
**Location**: `arno/iris-integration/arno-callout/src/kg_ffi.rs`
**Council pre-approved pattern** — generalizes a confirmed bottleneck

---

## Problem

Every arno algorithm (`bfs_on_adj`, `ppr_on_adj`, WCC, CDLP, random walk, subgraph) shares
the same data pipeline:

```
read_nkg_adjacency()
  → parse adjacency chunks from ^ArnoKG (fast: sequential reads)
  → read_node_dictionary(ns, n)          ← THE BOTTLENECK
      reads ^NKG("$ND", 0..n-1) via callin — one round-trip per node
  → Vec<String> nodes with all n names resolved upfront
  → algorithm runs on (nodes, adj)
  → output references nodes[idx] for string names
```

`read_node_dictionary` makes **n callin reads before any algorithm work begins**.

| Graph size | Nodes | Callin reads | Time @ 0.1ms/read |
|-----------|-------|-------------|-------------------|
| S (1K/5K) | ~620 | 620 | 62ms — acceptable |
| M (10K/50K) | ~6,700 | 6,700 | 670ms — slow |
| L (100K/500K) | ~45,000 | 45,000 | 4.5s — painful |
| **XL (1M/10M)** | **~500,000** | **500,000** | **50s — unusable** |
| LDBC SF1 (3M/17M) | ~3M | ~3M | ~5min — never finishes |

The pattern is general: **any algorithm that loads a full node dictionary upfront will
fail at LDBC-scale**. The same bottleneck will appear in:
- BFS (current: pre-resolves all n nodes, uses only `result_count` of them)
- PPR (pre-resolves all n nodes, uses all of them — inherently O(n) but does it via callin)
- WCC, CDLP (pre-resolves all n nodes for component labeling output)
- Random walk (pre-resolves all n nodes for walk path output)
- Subgraph extraction (pre-resolves reachable set)

---

## Root Cause

`nodes: Vec<String>` serves two purposes that were conflated:

1. **Seed lookup**: `nodes.iter().position(|s| s == seed)` — find seed string in the array
2. **Output resolution**: `nodes[idx]` — convert integer index to string in results

Both are currently solved by loading ALL node names upfront. They can be solved
independently and lazily.

---

## Solution: `NodeResolver` — Lazy Callin-Based Name Resolution

Replace `Vec<String>` with a `NodeResolver` struct that:
- Resolves names on-demand via `^NKG("$ND", idx)` callin
- Caches resolved names (LRU or simple HashMap)
- Provides a `lookup_seed(seed: &str) → Option<usize>` that reads `^NKG("$NI", seed)`
  directly (one callin, not a linear scan through all n nodes)

### Core type

```rust
pub struct NodeResolver {
    /// Cache of resolved names: idx → name
    cache: HashMap<usize, String>,
    /// Namespace handle for callin reads
    ns: NameSpace,
    /// Total node count (from adjacency header)
    n: usize,
}

impl NodeResolver {
    /// Resolve integer index to string name. Reads ^NKG("$ND", idx) on cache miss.
    pub fn name(&mut self, idx: usize) -> &str {
        self.cache.entry(idx).or_insert_with(|| {
            let mut g = GlobalRef::new("NKG");
            g.push(IRISData::Text("$ND".to_string()));
            g.push(IRISData::Int(idx as i32));
            match self.ns.get(&g) {
                Ok(Some(IRISData::Text(s))) => s,
                _ => format!("{idx}"),
            }
        })
    }

    /// Find integer index for a string seed. Reads ^NKG("$NI", seed) — ONE callin.
    pub fn lookup_seed(&self, seed: &str) -> Option<usize> {
        let mut g = GlobalRef::new("NKG");
        g.push(IRISData::Text("$NI".to_string()));
        g.push(IRISData::Text(seed.to_string()));
        match self.ns.get(&g) {
            Ok(Some(IRISData::Int(i))) => Some(i as usize),
            Ok(Some(IRISData::Text(s))) => s.parse().ok(),
            _ => None,
        }
    }

    /// Total node count in the graph (from adjacency header)
    pub fn len(&self) -> usize { self.n }
}
```

### What changes

| Current | Lazy replacement | Callin cost |
|---------|-----------------|-------------|
| `read_node_dictionary(ns, n)` → `Vec<String>` (n reads upfront) | `NodeResolver::new(ns, n)` (0 reads) | **0 → n** amortized |
| `nodes.iter().position(seed)` — O(n) scan | `resolver.lookup_seed(seed)` — 1 read | **n → 1** |
| `nodes[idx]` in output loop | `resolver.name(idx)` — cache miss = 1 read | **0 (already loaded) → 1 on first use** |
| PPR: uses all n nodes | `resolver.name(idx)` × n — same n reads, but spread across computation | **n reads, same total, no upfront** |

For BFS returning k results from a graph of n nodes: cost drops from **n reads** to **k + 1 reads** (1 seed lookup + k output resolutions). For k << n (deep BFS saturates, but result set << total nodes), this is a massive win.

For PPR (needs all n scores): still O(n) reads, but spread lazily across the computation
rather than blocking upfront. No blocking cold start.

---

## Algorithm Signature Changes

All algorithm functions change `nodes: &[String]` → `resolver: &mut NodeResolver`:

```rust
// Before
pub fn bfs_on_adj(
    nodes: &[String],
    adj: &[Vec<(usize, usize)>],
    pred_filter: &HashSet<usize>,
    seed: &str,
    max_hops: usize,
    max_results: usize,
) -> Vec<serde_json::Value>

// After
pub fn bfs_on_adj(
    resolver: &mut NodeResolver,
    adj: &[Vec<(usize, usize)>],
    pred_filter: &HashSet<usize>,
    seed: &str,
    max_hops: usize,
    max_results: usize,
) -> Vec<serde_json::Value>
```

Internal changes:
- `nodes.iter().position(|s| s == seed)` → `resolver.lookup_seed(seed)?`
- `nodes[src]`, `nodes[dst]` in output → `resolver.name(src)`, `resolver.name(dst)`

Same changes for `ppr_on_adj`, WCC, CDLP, random walk, subgraph.

---

## `read_nkg_adjacency` changes

```rust
// Before: returns (Vec<String>, Vec<Vec<usize>>)
// After: returns (NodeResolver, Vec<Vec<usize>>)

pub fn read_nkg_adjacency() -> Result<(NodeResolver, Vec<Vec<usize>>), String> {
    // ... read chunks, parse adjacency ... (unchanged)
    let (n, adj) = parse_nkg_adjacency_string(&full)?;
    let resolver = NodeResolver::new(ns, n);   // 0 callin reads — was n reads
    Ok((resolver, adj))
}
```

---

## `ffi_kg_bfs_compute` changes

```rust
pub fn ffi_kg_bfs_compute(...) -> String {
    let (mut resolver, out_adj) = match native_algos::read_nkg_adjacency() {
        Ok(data) => data,
        Err(_) => return "[]".to_string(),
    };
    // ... build adj_with_preds ...
    let results = native_algos::bfs_on_adj(
        &mut resolver,    // was: &nodes
        &adj_with_preds,
        &HashSet::new(),
        &seed,
        max_hops as usize,
        max_results as usize,
    );
    serde_json::to_string(&results).unwrap_or_else(|_| "[]".to_string())
}
```

---

## `ffi_kg_bfs_global` (legacy path)

Same change — `nodes` replaced with `resolver`.

---

## Performance Impact

### BFS at XL scale (1M nodes, returning ~500K reachable)

| Phase | Before | After |
|-------|--------|-------|
| `read_node_dictionary` | 500K reads × 0.1ms = **50s** | 0 reads |
| `lookup_seed` | O(n) scan = ~50ms | 1 read = **0.1ms** |
| Output resolution (500K results) | Already loaded | 500K reads × 0.1ms = **50s** |
| **Total callin cost** | **50s** (upfront) | **50s** (amortized over BFS) |

Wait — for BFS returning 500K nodes, the callin reads are still 500K. The win is:
- **No cold start**: reads happen as results are produced, not before
- **Partial results are fast**: first 1K results in ~0.1s instead of waiting 50s for full dictionary
- **When max_results caps output**: BFS returning only 50 results = **51 reads** (1 + 50) instead of 500K

For PPR (uses all n nodes in computation): same total reads but no blocking start.

### Seed lookup improvement

| Graph | Before (linear scan) | After (one callin) |
|-------|---------------------|--------------------|
| S | 620 compares | 0.1ms |
| M | 6,700 compares | 0.1ms |
| XL | 500,000 compares | 0.1ms |

---

## Compound Pattern: `^NKG("$NI")` for Reverse Lookup

The `$NI` global (`^NKG("$NI", nodeStr) = intIdx`) is already populated by `BuildNKG`.
Using it for seed lookup is simply reading one global key — the same mechanism already
proven by `read_nkg_adjacency_with_preds` (line 1784, reads `^NKG("$ND",...)` during
traversal). This is an established safe pattern from within `$ZF(-5)` context.

---

## Files Changed

| File | Change | Lines est. |
|------|--------|-----------|
| `kg_ffi.rs` | Add `NodeResolver` struct | +50 |
| `kg_ffi.rs` | `read_nkg_adjacency` returns `NodeResolver` | +5, -10 |
| `kg_ffi.rs` | `bfs_on_adj` signature + internals | +5, -5 |
| `kg_ffi.rs` | `ppr_on_adj` signature + internals | +5, -5 |
| `kg_ffi.rs` | WCC, CDLP, random walk, subgraph | +20, -20 |
| `kg_ffi.rs` | `ffi_kg_bfs_compute`, `ffi_kg_bfs_global` | +3, -3 |
| `kg_ffi.rs` | `ffi_kg_ppr_global`, WCC/CDLP entries | +3, -3 |

Total: ~90 lines changed, zero new infrastructure.

---

## Acceptance Criteria

- **SC-001**: `ffi_kg_bfs_compute` at XL (1M nodes, 10M edges) returns results in < 10s
  cold (first call, includes CacheNKGAdj) and < 1s hot (cache warm, BFS only)
- **SC-002**: Seed lookup via `NodeResolver.lookup_seed` makes exactly 1 callin read
  regardless of graph size
- **SC-003**: `bfs_on_adj` with `max_results=50` makes ≤ 52 callin reads total (1 seed + 50 output + 1 adj load)
- **SC-004**: All existing arno algorithm tests pass — no regression
- **SC-005**: PPR at XL returns scores for all reachable nodes (same as before, no data loss)
- **SC-006**: `NodeResolver` cache prevents duplicate callin reads for same index

---

## Why This Pattern Generalizes

The `NodeResolver` is the canonical solution for any arno algorithm that:
1. Operates on integer-indexed adjacency (^NKG)
2. Needs string names only in its OUTPUT
3. Does NOT need all node names during the computation itself

This covers all current and planned algorithms. Future algorithms should take
`resolver: &mut NodeResolver` not `nodes: &[String]` — this becomes the standard
signature for all `*_on_adj` functions in `native_algos`.

The one exception: algorithms that need ALL node names DURING computation (not just
output) — e.g., label propagation that maps string labels. Those can pre-populate the
cache explicitly if needed, or use a different resolution strategy.

---

## Not In Scope

- Async callin reads (not supported in `$ZF(-5)` context)
- Batched global reads (`IrisOrderedGet` or similar) — worth investigating separately
- Caching `NodeResolver` across `$ZF` calls (process-private, would need `^||` global)
