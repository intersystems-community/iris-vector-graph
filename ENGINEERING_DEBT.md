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

- [ ] **IC3 2-hop COUNT: 70ms → <10ms**
  `KHop2Count` bottlenecked by 38K-node `$Order` dedup walk in ObjectScript (~60ms).
  Fix: `BuildNKG2HopStats` pre-aggregation at `BuildNKG` time, or accept `approx_count_distinct` (5.3ms, ~89% accuracy on social graphs — good enough for threshold detection).

- [ ] **BuildNKG 422s on SF10**
  Rust `ffi_kg_build_nkg` written and compiles clean. Needs Linux build deployed to enterprise container.
  Also need: `BuildNKGIncremental` (only processes edges added since last version number).

- [ ] **Spec 094 benchmark numbers still missing**
  `^NKG` not populated on enterprise container — needs `BuildNKG` after LDBC reload.
  From previous sessions: Arno BFSJson ~3.5s for 15K results (no `<MAXSTRING>`); BFSFastJson ~4.3s.
  Need formal numbers in `specs/093-arno-acceleration-benchmark/results.md`.

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
