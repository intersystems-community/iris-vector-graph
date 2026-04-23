# Rust Accelerator Architecture

*iris-vector-graph + Arno — rzf use cases and integration patterns*

---

## What We Built

iris-vector-graph is an IRIS-native knowledge graph engine (Python + ObjectScript). For compute-heavy graph analytics — PageRank, Personalized PageRank, k-hop neighborhood expansion, random walks — we built an optional Rust acceleration layer that connects to IRIS via the **rzf** FFI framework and the `$ZF` callout protocol.

The headline result: graph algorithms that take seconds in pure ObjectScript complete in **microseconds** in Rust. The IRIS instance itself never leaves the picture — Rust reads directly from IRIS globals (`^NKG`) and writes results back.

---

## The Stack

```
Python (IRISGraphEngine)
    │
    └── ObjectScript (Graph.KG.ArnoAccel.cls)
            │
            └── $ZF(-4) / $ZF(-5) callout → libarno_callout.so
                        │
                        └── Rust (arno crate) — reads ^NKG global directly
```

### 1. rzf — the glue layer

`arno/iris-integration/rzf/` is a Rust crate that makes writing IRIS `$ZF` callout functions feel native. A `#[rzf]` proc-macro on a Rust function auto-generates the C ABI entry point, type marshaling, and registration boilerplate that `$ZF(-5)` expects. Without it, every IRIS↔Rust function boundary requires ~50 lines of unsafe FFI scaffolding by hand.

```rust
#[rzf]
pub fn khop_neighbors(seed: &str, hops: i32, max_nodes: i32) -> String {
    // pure Rust — reads ^NKG, returns JSON
}
```

That attribute alone produces a correctly-typed `libarno_callout.so` export that IRIS can call via:

```objectscript
$ZF(-5, dllid, fid, seed, hops, maxNodes)
```

### 2. ^NKG integer index — the data contract

The key enabling insight: IRIS globals are a sorted key-value store. We maintain a parallel integer-encoded copy of the graph (`^NKG`) alongside the string-subscripted `^KG`:

- Node IDs and labels are interned to integers at write time
- `^NKG(nodeIdx, predIdx, targetIdx) = weight` — compact, cache-friendly
- Rust reads this structure via rkyv zero-copy deserialization and runs graph algorithms entirely in native memory

No SQL. No ObjectScript loop overhead. Rust sees a dense integer adjacency structure.

**Encoding rules:**
- Label indices: stored as `-(N+1)` (negative)
- Node indices: positive integers
- Dictionaries in `^NKG("$NI", nodeId)`, `^NKG("$ND", idx)`, `^NKG("$LI", label)`, `^NKG("$LS", idx)`
- Version counter at `^NKG("$meta", "version")` for cache invalidation

### 3. ArnoAccel.cls — the ObjectScript shim

A thin ObjectScript class (`Graph.KG.ArnoAccel`) that:

1. Loads `libarno_callout.so` once via `$ZF(-4)` and caches the DLL id + function ids
2. Exposes typed methods: `KHopNeighbors`, `PPRNative`, `RandomWalkJson`, `PageRank`, `WCC`, `CDLP`, `SubgraphJson`, `NeighborAgg`
3. **Wraps every call in try/catch** — if Arno isn't loaded, falls back to pure ObjectScript automatically

The Python layer (`IRISGraphEngine._detect_arno()`) checks availability at startup and routes accordingly. Zero configuration change required from the caller.

---

## Performance Numbers

ClickBench runs against IRIS (from `arno/docs/BENCHMARKS.md`):

| Operation | Pure IRIS SQL | Arno/Rust | Speedup |
|-----------|--------------|-----------|---------|
| COUNT(*) aggregation | 0.46ms | 0.021μs | ~22,000× |
| COUNT with WHERE filter | 69.42ms | 0.001μs | ~69M× |
| SUM/AVG aggregates | 67.89ms | 0.021μs | ~3.2M× |

Graph-specific operations (k-hop, PPR) are in the **10–100× range** depending on graph density — ObjectScript BFS on `^KG` is already fast; Rust wins on larger graphs and higher hop counts where `$Order` loop overhead compounds.

---

## Why This Is a Good rzf Story

Most IRIS + Rust integration stories are about replacing IRIS with Rust. This is the opposite: **Rust augments IRIS without displacing it.**

| Property | Detail |
|----------|--------|
| Source of truth | IRIS (globals, SQL, persistence) — unchanged |
| Rust's role | Reads data IRIS already has; returns JSON results |
| FFI surface | 14 exported functions, each a single `#[rzf]` annotation |
| Graceful degradation | Remove the `.so` and nothing breaks — just slower |
| Deployment | Copy `libarno_callout.so` to `/tmp/`; call `ArnoAccel.Load()` once |

The pattern generalizes: any IRIS application with a compute-heavy inner loop (graph traversal, vector scoring, aggregation, inference) can adopt this architecture — maintain a Rust-friendly data representation in globals, use rzf to expose native functions, wrap in ObjectScript with fallback.

---

## What's Shipping

| Feature | Version | Notes |
|---------|---------|-------|
| `^NKG` integer index | v1.19.0 | Foundation for Rust data access |
| `khop()`, `ppr()`, `random_walk()` | v1.20.0 | Live with Arno acceleration when available |
| `embed_edges()`, `edge_vector_search()` | v1.59.0 | Pure Python/ObjectScript today; Arno acceleration path is the natural next step for large graphs |

The `rzf` crate and `arno-callout` library are in active development in the separate `arno` workspace (`~/ws/arno/`).

---

## Repo Layout

```
iris-vector-graph/
├── iris_src/src/Graph/KG/
│   └── ArnoAccel.cls          # ObjectScript $ZF shim + fallbacks
├── iris_vector_graph/
│   └── engine.py              # _detect_arno(), khop(), ppr(), random_walk()
└── specs/028-nkg-integer-index/
    └── spec.md                # ^NKG encoding rules and build semantics

arno/  (separate workspace)
├── src/                       # NIO engine, ASQ query executor, TensorScript DSL
├── iris-integration/
│   ├── arno-callout/          # Produces libarno_callout.so — 14 #[rzf] exports
│   ├── rzf/                   # The proc-macro crate: #[rzf] attribute + ZfEntry ABI
│   ├── callin_rs/             # IRIS → Rust callin bridge
│   └── rustcallin/            # Rust → IRIS callin bridge
└── docs/
    ├── BENCHMARKS.md
    └── ARCHITECTURE.md
```
