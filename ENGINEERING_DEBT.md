# IVG Engineering Debt
Last updated: 2026-05-07

Review at the start of each IVG session.

---

## LONG-TERM ARCHITECTURAL DIRECTION

### Pydantic-typed Public API (P1 — incremental)

**Progress so far (v1.84–v1.86):**
- `SQLQuery` + `QueryMetadata` → Pydantic BaseModel (`translator.py`)
- `IndexHandle` → Pydantic BaseModel with `Literal[type]` validation (`index_protocol.py`)
- `IVGResult` → Pydantic BaseModel for `execute_cypher` return type (v1.86.0)
  - Backward-compatible: `__getitem__`, `__contains__`, `.get()` overrides
  - `bool(result)` = True on success, False on error

**Next increment:** Input validation at boundary — `node_id: NonEmptyStr`, `k: PositiveInt` on key engine methods.

---

## RESOLVED

- [x] **IC2 1-hop fast path** — `KHopCount` + `KHopNeighborIds`: COUNT 0.29ms (was 2.8ms)
- [x] **IC3 2-hop fast path** — `KHop2Count` + `KHop2NeighborIds`: LIMIT 1000 = 1.2ms (was 14–22ms, 3.5× faster than GES 4.19ms)
- [x] **Streaming BFS for unbounded queries** — v1.85.0. `max_results==0` → `_bfs_stream_pages`; bounded → `ReadBFSResults` fast path. 5/5 tests.
- [x] **IVGResult Pydantic model** — v1.86.0. `execute_cypher` returns `IVGResult` not `Dict[str,Any]`.
- [x] **Spec 094: Arno BFSJson chunk-read loop** — T006 fixed; cross-build with `arno-builder`; `_detect_arno` calls `ArnoAccel.Load()`. 5/5 e2e tests on `iris-enterprise-2026`.
- [x] **Spec 105: Index Protocol Unification** — `engine.index(name)` → `IndexHandle` (Pydantic), `IVGIndex` Protocol, PLAID renames, all `*_info` return `"type"` key. v1.84.0.
- [x] **IVFIndex.Insert() + Delete()** — nearest-centroid single-pass; 5/5 e2e tests.
- [x] **IVF `<STRINGSTACK>` on 768-dim** — `AddBatch` + `FinalizeIndex`; `build_batch_size=500`.
- [x] **Pattern Comprehension + REDUCE** — all 10 openCypher gaps closed. AGENTS.md SQL constraints added.
- [x] **bulk_ingest_edges() / rebuild_nkg()** — engine wrappers with `_nkg_dirty` flag.
- [x] **Open Exchange readiness** — Docker-first README, unified QUICKSTART with working demo, root cleaned, module.xml v1.86.0.
- [x] **test_sc003 VL path test** — replaced raw `NKGAccel.BFSJson` call with engine-only determinism check; fixture calls `rebuild_nkg()` for sync guarantee.

---

## OPEN DEBT

### P1 — Performance

- [x] **BuildNKG 422s → 19s via Rust** — `BuildNKGRust()` uses `KG_BUILD_NKG_WRAPPER` from
  `libarno_callout.so`. `engine.rebuild_nkg()` auto-uses Rust path when `rust_callout=True`.
  `ArnoAccel.Load()` now called in `_detect_arno()` so `rust_callout` is correctly detected.
  Requires `libarno_callout.so` deployed to `/tmp/` on the IRIS container.

- [x] **IC3 2-hop COUNT pre-aggregation** — `BackfillDegp()` + `Build2HopStats()` added.
  `^KG("deg2p", src, pred)` = sum of 1-hop neighbor degrees (upper bound for 2-hop count).
  `KHop2CountFast(src, pred)` = **0.07ms** (O(1) `$Get` on `^KG("deg2p")`).
  Upper bound: ~3.7× overcount on LDBC KNOWS (136K vs 37K exact). Suitable for threshold
  detection, NOT for exact reporting. Use `KHop2Count` (70ms) for exact.
  Engine: `engine.khop2_count_fast(node_id, pred)` + `engine.backfill_degp()` + `rebuild_nkg()`
  now calls `Build2HopStats` automatically.
  `BulkIngestEdges` now also writes `^KG("degp")` and `^KG("deg")` so future bulk loads
  don't need `BackfillDegp`.

- [ ] **IC3 2-hop COUNT: 70ms exact (target <10ms)**
  `KHop2Count` exact scan still 70ms. Pre-aggregated upper bound (`KHop2CountFast`) is
  0.07ms but ~3.7× overcount. For exact: needs dedup at `Build2HopStats` time — only
  feasible if `^NKG` data is available (store deduplicated 2-hop counts per node).
  Alternative: expose `approx_count_2hop(n)` in Cypher alongside `approx_count_distinct`.

### P2 — Accuracy

- [ ] **HLL union bias ~89% on LDBC social graphs**
  `approx_count_distinct` systematically under-estimates for correlated friend-of-friend sets.
  Fix: HyperMinHash or KMV sketches in `UpdateStructuralHLL`.
  Low urgency — approximate is fine for threshold detection; exact path is `KHop2Count`.

### P3 — API / DX

- [ ] **Input validation at boundary** — next Pydantic increment.
  `node_id: NonEmptyStr`, `k: PositiveInt` on `create_node`, `ivf_search`, `bm25_search`, etc.
  Catches footguns early rather than getting ObjectScript errors at call time.

- [ ] **BulkIngestEdges `[ Internal ]` in ObjectScript**
  `engine.bulk_ingest_edges()` is battle-tested. Mark raw `BulkIngestEdges` in `EdgeScan.cls`
  as `[ Internal ]` so it stops appearing in external callers' autocomplete.

- [ ] **37 untested public engine methods**
  Highest risk: `vec_*` full lifecycle (9 methods), `khop`, `ppr`, `random_walk`,
  `materialize_inference`, `retract_inference`, `restore_snapshot`.

- [ ] **`kg_KNN_VEC` in `engine.index()` protocol**
  Native HNSW available on Community + Advanced Server is not yet in `_index_registry`.
  Low priority — IVF covers the same tier as fallback.

- [ ] **NKGAccel `bfs_result` chunks → `bfs_r` sorted global**
  BFS in `NKGAccel.cls` still uses the older `^ArnoKG("bfs_result", chunkNum)` pattern.
  KHop is done with the sorted global approach; BFS should follow (spec needed).

---

## BENCHMARK NUMBERS (for reference)

Hardware: MacBook Pro (M3 Ultra, 128GB RAM), LDBC SF10, IRIS 2025.1 Enterprise in Docker.
Comparison: GES/GraphScope published SF1000 numbers on large server cluster.

| Query | IVG p50 | GES SF1000 p50 | Notes |
|---|---|---|---|
| IC13 ShortestPath (SF1) | 0.22ms | 2.69ms | IVG faster |
| IC13 ShortestPath (SF10) | 2.1–3.2ms | 2.69ms | Comparable |
| IC2 1-hop COUNT (`KHopCount`) | 0.29ms | 0.14ms | Competitive (was 2.8ms) |
| IC2 1-hop IDs (`KHopNeighborIds`) | 0.9ms | — | Fast path |
| IC3 2-hop LIMIT 1000 (`KHop2NeighborIds`) | **1.2ms** | 4.19ms | 3.5× faster than GES |
| IC3 2-hop COUNT (`KHop2Count`) | 70ms | — | Was 195ms; <10ms target needs pre-agg |
| approx_count_distinct 2-hop | 5.3ms | — | 74× vs exact; ~89% accuracy on social graphs |
| BulkIngestEdges | 190–312K edges/s | — | Fast; `^NKG` stale until `rebuild_nkg()` |
| BuildNKG (SF10) | 422s | — | Rust fix ready, needs Linux build + deploy |
| Arno BFSJson 2-hop (no MAXSTRING) | ~3.5s | — | SF10 15K results; chunk-read loop working |
